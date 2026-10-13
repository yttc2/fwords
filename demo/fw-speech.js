/* Fwords · speech —— 发音。
 * 主路径是「在线真人录音」（<audio>，不依赖 speechSynthesis），
 * 兜底是系统 TTS。两条路的跨平台约束见文件内注释，别合并。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';

  /* ========== 依赖注入（别名）========== */
  /* —— 依赖 core 层 —— */
  var state = FW.core.state;
  var toast = FW.core.toast;


/* ==================== 语音 ====================
 * 两条路，主次分明：
 *
 *   主路径 —— 在线真人录音（有道词典音源，返回真人朗读的 mp3）
 *     为什么是它：**不依赖 speechSynthesis**。很多手机容器（微信 / 国行厂商浏览器）
 *     压根没有 Web 语音接口，系统 TTS 在那上面是死路；而 <audio> 哪里都能播。
 *
 *   兜底 —— 系统 speechSynthesis（离线 / 音源挂了 / 加载失败时自动降级）
 *
 * 「无感」怎么做到：有道响应头**没有 Cache-Control / ETag**，浏览器不会缓存它，
 * 每次播放都是一次网络请求（约 0.4 秒）；而且响应**没有 CORS 头**，fetch 读字节会被拦，
 * 所以没法落盘缓存。能做的是 —— 保活 Audio 对象 + **预取后几个词**：
 * 你看当前这张卡的时候，后 3 个词的音频已经悄悄下好了，切过去基本张嘴就来。
 */
let voices = [];
function loadVoices() {
  /* 原地更新：view 层在加载时抓的是同一个数组的引用 */
  try {
    const list = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
    voices.length = 0;
    Array.prototype.push.apply(voices, list || []);
  } catch (e) { voices.length = 0; }
}
if (window.speechSynthesis) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

/* ---- 在线真人音源 ---- */
const YD_TYPE = { us: 2, uk: 1 };      // 有道 type 参数：1=英音 2=美音
const AUDIO_MAX = 240;                  // Audio 对象缓存上限（约 2.8 MB 音频）
const PREFETCH_AHEAD = 3;               // 预取后几个词
const audioCache = new Map();           // 'type|word' -> HTMLAudioElement
let currentAudio = null;
let humanDownUntil = 0;                 // 真人音源临时不可用（时间戳），避免每个词都干等
let humanWarned = false;

function accentType() { return YD_TYPE[state.settings.voiceAccent] === 1 ? 1 : 2; }
function humanOn() {
  return state.settings.ttsSource !== 'system' && !!window.Audio && Date.now() >= humanDownUntil;
}
function dictUrl(text, type) {
  return 'https://dict.youdao.com/dictvoice?audio=' + encodeURIComponent(text) + '&type=' + type;
}

/* 取（必要时创建）某个词的 Audio 对象。创建即触发加载，所以这也兼作预取。 */
function audioFor(text, type) {
  const key = type + '|' + text;
  let a = audioCache.get(key);
  if (a) { audioCache.delete(key); audioCache.set(key, a); return a; }   // LRU 触碰

  a = new Audio();
  a.preload = 'auto';
  a.setAttribute('playsinline', '');
  a.__word = text;
  a.addEventListener('error', function () {
    audioCache.delete(key);
    humanDownUntil = Date.now() + 60000;   // 一分钟内不再试真人音源，直接走系统 TTS
    if (currentAudio === a) currentAudio = null;
    // 当前正等着的这个词，改用系统 TTS 兜底
    if (a.__waiting) {
      a.__waiting = false;
      systemSpeak(text, false);
    }
  });
  a.src = dictUrl(text, type);
  audioCache.set(key, a);

  while (audioCache.size > AUDIO_MAX) {
    const k = audioCache.keys().next().value;
    const dead = audioCache.get(k);
    if (dead && dead !== currentAudio) { try { dead.pause(); } catch (e) { /* 忽略 */ } }
    audioCache.delete(k);
  }
  return a;
}

function playHuman(text) {
  const a = audioFor(text, accentType());
  if (currentAudio && currentAudio !== a) { try { currentAudio.pause(); } catch (e) { /* 忽略 */ } }
  currentAudio = a;
  a.__waiting = true;
  try { a.currentTime = 0; } catch (e) { /* 元数据还没到，忽略 */ }
  const p = a.play();
  if (p && p.then) {
    p.then(function () { a.__waiting = false; })
     .catch(function () { a.__waiting = false; });   // 失败交给 error 事件兜底
  } else {
    a.__waiting = false;
  }
  return a;
}

/* 预取：当前词 + 后 N 个词。列表项可带 text（学习页）或只有 id（回顾页）。 */
function prefetchList(list, idx, n, resolveText) {
  if (!humanOn()) return;
  const type = accentType();
  for (let i = 0; i <= n; i++) {
    const item = list[idx + i];
    if (!item) continue;
    const t = resolveText ? resolveText(item) : item.text;
    if (t) audioFor(t, type);
  }
}

/* ---- 系统 TTS 兜底 ----
 * speechSynthesis 的跨平台约束（踩过坑，别改回去）：
 * A) iOS Safari / 部分安卓 WebView：**必须在用户手势的同步调用栈里** speak()。
 *    放进 setTimeout 就哑掉 —— 连手动点喇叭都会没声音。
 * B) 桌面 Chrome：cancel() 之后同一 tick 立刻 speak()，新 utterance 会被一起 cancel 掉。
 * —— 所以手动路径同步、自动路径延迟，两条不能合并。
 * C) 长时间朗读会被浏览器静默暂停 → resume() 保活。
 * D) getVoices() 首次返回空数组 → 第一次发音会退回系统默认音色。
 */
let speakSeq = 0;
let speakTimer = null;
let keepAliveTimer = null;
let ttsUnlocked = false;
let ttsWarned = false;

function pickVoice() {
  if (!voices.length) loadVoices();
  for (let i = 0; i < voices.length; i++) if (/^en[-_]US$/i.test(voices[i].lang)) return voices[i];
  for (let i = 0; i < voices.length; i++) if (/^en[-_]GB$/i.test(voices[i].lang)) return voices[i];
  for (let i = 0; i < voices.length; i++) if (/^en/i.test(voices[i].lang)) return voices[i];
  return null;
}

function stopKeepAlive() {
  if (keepAliveTimer) { clearInterval(keepAliveTimer); keepAliveTimer = null; }
}

/* 停掉一切发声（真人音频 + 系统 TTS） */
function stopSpeak() {
  speakSeq++;                       // 作废所有还没执行的排队朗读
  if (speakTimer) { clearTimeout(speakTimer); speakTimer = null; }
  stopKeepAlive();
  if (currentAudio) { try { currentAudio.pause(); } catch (e) { /* 忽略 */ } }
  try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* 忽略 */ }
}

function startKeepAlive() {
  stopKeepAlive();
  keepAliveTimer = setInterval(function () {
    const s = window.speechSynthesis;
    if (!s) { stopKeepAlive(); return; }
    if (!s.speaking) { stopKeepAlive(); return; }
    if (s.paused) { try { s.resume(); } catch (e) { /* 忽略 */ } }
  }, 4000);
}

/* 系统 TTS 发声。userGesture=true 表示调用方就在手势栈里（iOS 必需）。 */
function systemSpeak(text, userGesture) {
  if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'en-US';
    u.rate = 0.9;
    const v = pickVoice();
    if (v) u.voice = v;
    u.onstart = function () { ttsUnlocked = true; };
    u.onerror = function (ev) {
      if (ttsWarned) return;
      ttsWarned = true;
      const err = ev && ev.error ? String(ev.error) : '';
      if (err === 'not-allowed' || err === 'synthesis-unavailable' || err === 'synthesis-failed') {
        toast('系统没有可用的英文语音引擎，请检查手机「文字转语音 / 语音朗读」设置');
      }
    };
    window.speechSynthesis.speak(u);
    if (userGesture) ttsUnlocked = true;
    startKeepAlive();
  } catch (e) {
    if (!ttsWarned) { ttsWarned = true; toast('当前浏览器不支持语音朗读'); }
  }
}

/* 首次用户手势时用一个极短的空串「热」一下语音通道，让之后的自动播放更容易被放行。 */
function unlockTTS() {
  if (ttsUnlocked) return;
  ttsUnlocked = true;
  try {
    if (!window.speechSynthesis) return;
    const u = new SpeechSynthesisUtterance(' ');
    u.volume = 0;
    window.speechSynthesis.speak(u);
  } catch (e) { /* 忽略 */ }
}

/* 自动播放入口（切词时）。音频元素没有 cancel/speak 竞态，所以直接同步播。 */
function speakAuto(text) {
  const t = String(text == null ? '' : text).trim();
  if (!t) return;

  if (humanOn()) {
    stopSpeak();
    playHuman(t);
    return;
  }
  // 兜底：系统 TTS 走延迟路径，避开桌面 Chrome 的 cancel/speak 竞态
  stopSpeak();
  const seq = speakSeq;
  speakTimer = setTimeout(function () {
    speakTimer = null;
    if (seq !== speakSeq) return;               // 期间又翻页了，丢弃
    systemSpeak(t, false);
  }, 80);
}

/* 手动朗读入口：调用方在 click 处理函数里，同步发声，绝不加延迟（iOS 硬约束） */
function speak(text) {
  const t = String(text == null ? '' : text).trim();
  if (!t) return;

  unlockTTS();
  stopSpeak();

  if (humanOn()) { playHuman(t); return; }

  if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
    if (!humanWarned) {
      humanWarned = true;
      const br = detectBrowser();
      toast('「' + br.name + '」不支持语音，请换 Chrome / Edge / Safari 打开');
    }
    return;
  }
  systemSpeak(t, true);
}

/* 识别当前容器 —— 决定为什么没有 speechSynthesis */
function detectBrowser() {
  const ua = navigator.userAgent || '';
  const inWeixin = /MicroMessenger/i.test(ua);
  const inQQ = /\bQQ\//.test(ua) || /QQBrowser/i.test(ua);
  const inWeibo = /Weibo/i.test(ua);
  const inAlipay = /AlipayClient/i.test(ua);
  const inWxWork = /wxwork/i.test(ua);
  const iOS = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && 'ontouchend' in document);
  const android = /Android/i.test(ua);

  // 内置 app 浏览器容器
  if (inWeixin) return { name: '微信内置浏览器', ok: false, why: '微信的 X5 内核不提供 Web 语音接口' };
  if (inQQ) return { name: 'QQ 内置浏览器', ok: false, why: 'QQ 内核不提供 Web 语音接口' };
  if (inWeibo) return { name: '微博内置浏览器', ok: false, why: '微博内置浏览器不提供 Web 语音接口' };
  if (inAlipay) return { name: '支付宝内置浏览器', ok: false, why: '支付宝内置浏览器不提供 Web 语音接口' };
  if (inWxWork) return { name: '企业微信内置浏览器', ok: false, why: '企业微信内置浏览器不提供 Web 语音接口' };

  // 国行安卓的厂商内置浏览器（华为/小米/OPPO/vivo）常砍掉 speechSynthesis
  const vendor = [
    [/HuaweiBrowser|Huawei/i, '华为浏览器'],
    [/MiuiBrowser|XiaoMi|MI\s/i, '小米浏览器'],
    [/HeyTapBrowser|OPPO/i, 'OPPO 浏览器'],
    [/VivoBrowser/i, 'vivo 浏览器'],
    [/QuarkBrowser|Quark/i, '夸克浏览器'],
    [/UCBrowser|UCWEB/i, 'UC 浏览器'],
    [/MQQBrowser/i, 'QQ 浏览器'],
    [/DroidStar/i, '百度浏览器'],
    [/2345/i, '2345 浏览器'],
    [/Slecker/i, '猎豹浏览器']
  ];
  for (const [re, name] of vendor) {
    if (re.test(ua)) return { name: name, ok: false, why: '国行厂商浏览器常裁剪 speechSynthesis 接口' };
  }

  if (/Edg\//i.test(ua)) return { name: 'Edge', ok: true };
  if (/OPR\//i.test(ua)) return { name: 'Opera', ok: true };
  if (/Chrome\//i.test(ua)) {
    if (iOS) return { name: 'Chrome（iOS）', ok: false, why: 'iOS 上所有浏览器都用 WebKit，语音能力受限；建议改用 Safari' };
    return { name: 'Chrome', ok: true };
  }
  if (/Firefox\//i.test(ua)) return { name: 'Firefox', ok: true };
  if (iOS) return { name: 'Safari', ok: true };
  if (/Safari\//i.test(ua)) return { name: 'Safari / macOS', ok: true };
  if (android) return { name: '安卓未知浏览器', ok: false, why: '无法识别的内核，可能不支持 Web 语音' };
  return { name: '未知浏览器', ok: false, why: '' };
}

/* ---- 给别层用的访问器 ----
 * humanDownUntil / ttsWarned 是原始值，别名会「快照」住旧值，
 * 跨层读写只能走函数，不能直接暴露变量。 */
function resetHumanDown() { humanDownUntil = 0; }
function humanDownLeftMs() { return Math.max(0, humanDownUntil - Date.now()); }
function resetTtsWarned() { ttsWarned = false; }

/* 供 app 层的调试钩子使用 */
function getVoicesList() { return voices; }

  FW.speech = {
    AUDIO_MAX: AUDIO_MAX,
    PREFETCH_AHEAD: PREFETCH_AHEAD,
    YD_TYPE: YD_TYPE,
    accentType: accentType,
    audioCache: audioCache,
    audioFor: audioFor,
    currentAudio: currentAudio,
    detectBrowser: detectBrowser,
    dictUrl: dictUrl,
    getVoicesList: getVoicesList,
    humanDownLeftMs: humanDownLeftMs,
    humanDownUntil: humanDownUntil,
    humanOn: humanOn,
    humanWarned: humanWarned,
    keepAliveTimer: keepAliveTimer,
    loadVoices: loadVoices,
    pickVoice: pickVoice,
    playHuman: playHuman,
    prefetchList: prefetchList,
    resetHumanDown: resetHumanDown,
    resetTtsWarned: resetTtsWarned,
    speak: speak,
    speakAuto: speakAuto,
    speakSeq: speakSeq,
    speakTimer: speakTimer,
    startKeepAlive: startKeepAlive,
    stopKeepAlive: stopKeepAlive,
    stopSpeak: stopSpeak,
    systemSpeak: systemSpeak,
    ttsUnlocked: ttsUnlocked,
    ttsWarned: ttsWarned,
    unlockTTS: unlockTTS,
    voices: voices,
  };
})(window.FW = window.FW || {});
