/* 语音 / 自动发音专项探针：最小 DOM 桩里驱动 demo/app.js
 * 运行：node tests/speech-probe.js
 * 桩补了两处：querySelectorAll 能按选择器返回真按钮、speechSynthesis 记录调用序列。
 */
const fs = require('fs');
const APP_FILES = require('./app-files.js');
const path = require('path');
const DEMO = path.join(__dirname, '..', 'demo');

/* ---------- DOM 桩 ---------- */
function El(id, tag) {
  const cls = new Set();
  const listeners = {};
  const el = {
    id, tagName: (tag || 'div').toUpperCase(), style: {}, dataset: {}, textContent: '', value: '',
    children: [], _html: '',
    appendChild(c) { el.children.push(c); },
    addEventListener(t, f) { (listeners[t] = (listeners[t] || [])).push(f); },
    removeEventListener() {},
    setPointerCapture() {}, releasePointerCapture() {}, scrollTo() {},
    closest() { return null; }
  };
  el.classList = {
    add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c),
    toggle: (c, on) => {
      if (on === undefined) { cls.has(c) ? cls.delete(c) : cls.add(c); }
      else if (on) cls.add(c); else cls.delete(c);
    }
  };
  el.__cls = cls; el.__listeners = listeners;
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) { el._html = String(v); if (!v) el.children.length = 0; }
  });
  return el;
}

let els = {};
const getEl = id => els[id] || (els[id] = El(id));

/* 按 '#segAuto button' 这类选择器返回真实按钮（模拟浏览器） */
function makeQuery(root) {
  return function (sel) {
    const out = [];
    // 段控件：#segXxx button
    let m = sel.match(/^#([\w-]+)\s+button$/);
    if (m) {
      const seg = getEl(m[1]);
      if (!seg.__segButtons) seg.__segButtons = [];   // 探查未登记的段控件时返回空数组
      seg.__segButtons.forEach(b => out.push(b));
      return out;
    }
    return out;
  };
}

const documentStub = {
  getElementById: getEl,
  createElement: tag => El('__el_' + (tag), tag),
  querySelectorAll: sel => makeQuery() (sel),
  addEventListener() {},
  hidden: false
};

let clock = 0;
const intervals = [];
const timers = [];
let timerSeq = 0;
const storageMap = {};

global.window = global;
global.document = documentStub;
global.performance = { now: () => clock };
global.localStorage = {
  getItem: k => (k in storageMap ? storageMap[k] : null),
  setItem: (k, v) => { storageMap[k] = String(v); },
  removeItem: k => { delete storageMap[k]; }
};
global.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; };
global.setTimeout = (fn, ms) => { timers.push({ id: ++timerSeq, fn, ms, at: clock }); return timerSeq; };
global.clearTimeout = id => { const i = timers.findIndex(t => t.id === id); if (i >= 0) timers[i].cancelled = true; };
global.clearInterval = () => {};
global.confirm = () => false;
global.scrollTo = () => {};
global.window.addEventListener = () => {};

function runTimers() {
  const due = timers.filter(t => !t.done && !t.cancelled);
  timers.length = 0;
  due.forEach(t => { t.done = true; t.fn(); });
}

/* saveSoon 只置标记，靠 1 秒 interval 落盘 */
function saveNow() {
  const iv = intervals.filter(i => i.ms === 1000).pop();
  if (iv) iv.fn();
}
function readState() {
  const raw = storageMap['fwords.state.v1'];
  return raw ? JSON.parse(raw) : null;
}

/* ---------- speechSynthesis 桩 ---------- */
const TTS = { calls: [], speaking: false, paused: false, cancelled: 0 };
global.speechSynthesis = {
  getVoices: () => ([
    { name: 'Microsoft Zira', lang: 'en-US' },
    { name: 'Chinese', lang: 'zh-CN' }
  ]),
  speak(u) { TTS.calls.push({ text: u.text, voice: u.voice && u.voice.name, rate: u.rate }); TTS.speaking = true; },
  cancel() { TTS.cancelled++; TTS.speaking = false; },
  resume() { TTS.paused = false; }
};
global.SpeechSynthesisUtterance = function (t) { this.text = t; };

/* ---------- 加载 ---------- */
const load = f => (0, eval)(fs.readFileSync(path.join(DEMO, f), 'utf8'));
load('words.js');
load('wordlists.js');

/* 预置 segAuto / segScale / segReview / segSource / segVoice 的按钮（浏览器里由 HTML 提供） */
function seedSegs() {
  [['segAuto', '1'], ['segAuto', '0'],
   ['segScale', '1'], ['segScale', '60'],
   ['segReview', '1'], ['segReview', '0'],
   ['segSource', 'human'], ['segSource', 'system'],
   ['segVoice', 'us'], ['segVoice', 'uk']].forEach(([id, v]) => {
    const seg = getEl(id);
    const b = El(id + '_b' + v, 'button');
    b.dataset.v = String(v);
    if (!seg.__segButtons) seg.__segButtons = [];
    seg.__segButtons.push(b);
  });
}

/* ---------- <audio> 桩 ---------- */
const AUDIO = { created: [], played: [], paused: 0 };
function AudioStub(src) {
  const el = {
    tagName: 'AUDIO', preload: '', src: src || '', currentTime: 0, _listeners: {},
    setAttribute() {},
    addEventListener(t, f) { (el._listeners[t] = el._listeners[t] || []).push(f); },
    pause() { AUDIO.paused++; },
    play() { AUDIO.played.push(el.src); return Promise.resolve(); },
    fire(t) { (el._listeners[t] || []).forEach(f => f()); }
  };
  AUDIO.created.push(el);
  return el;
}
global.Audio = AudioStub;

/* ---------- 断言 ---------- */
let pass = 0, fail = 0;
const ok = (c, label) => { c ? pass++ : fail++; console.log((c ? '  PASS  ' : '  FAIL  ') + label); };
const eq = (a, b, label) => ok(a === b, label + (a === b ? '' : '（实际 ' + JSON.stringify(a) + ' ≠ ' + JSON.stringify(b) + '）'));

console.log('\n========== 第 1 节：调试面板自动发音开关 ==========');
seedSegs();
APP_FILES.forEach(load);

const segAuto = getEl('segAuto');
const segSource = getEl('segSource');
const segVoice = getEl('segVoice');
const clickBtn = el => el.__listeners.click[0]();
const srcHuman = segSource.__segButtons[0];
const srcSystem = segSource.__segButtons[1];
const voiceUs = segVoice.__segButtons[0];
const voiceUk = segVoice.__segButtons[1];

ok(segAuto.__segButtons.length === 2, '#segAuto 造出 2 个按钮');
ok(segAuto.__segButtons.every(b => (b.__listeners.click || []).length === 1),
  '两个 segAuto 按钮都绑定了 click');
ok((segSource.__segButtons[0].__listeners.click || []).length === 1, 'segSource 按钮已绑定');
ok((segVoice.__segButtons[0].__listeners.click || []).length === 1, 'segVoice 按钮已绑定');

// 第 1~11 节测的是「系统 TTS 兜底」这条路径，先把发音源固定为 system
clickBtn(srcSystem);
runTimers();
saveNow();                      // saveSoon 只置标记，落盘靠 1 秒 interval
eq(readState().settings.ttsSource, 'system', '前置：发音源已切到 system（测兜底路径）');

console.log('\n========== 第 1.5 节：HTML 里的按钮必须都绑上了事件 ==========');
{
  /* 老板报过「手机上按钮点不动、电脑上正常」——根因是新 HTML 配了旧 JS 缓存，
   * 按钮画出来了但没人给它绑事件。这条断言把「按钮存在却没绑定」直接变成红灯。 */
  const html = fs.readFileSync(path.join(DEMO, 'index.html'), 'utf8');

  const btnIds = [...new Set([...html.matchAll(/id="(btn[\w-]+)"/g)].map(m => m[1]))];
  const deadBtns = btnIds.filter(id => {
    const el = getEl(id);
    return !(el.__listeners.click && el.__listeners.click.length);
  });
  ok(btnIds.length >= 18, 'index.html 里找到 ' + btnIds.length + ' 个 btn* 按钮');
  ok(deadBtns.length === 0,
    '所有 btn* 按钮都绑定了 click' + (deadBtns.length ? '，未绑定：' + deadBtns.join(', ') : ''));

  const segIds = ['segAuto', 'segScale', 'segReview', 'segSource', 'segVoice'];
  const deadSegs = segIds.filter(id => {
    const btns = getEl(id).__segButtons || [];
    return !btns.length || btns.some(b => !(b.__listeners.click && b.__listeners.click.length));
  });
  ok(deadSegs.length === 0,
    '所有段控件按钮都绑定了 click' + (deadSegs.length ? '，未绑定：' + deadSegs.join(', ') : ''));

  // 段控件在 HTML 里都得存在（否则 JS 那边匹配不到，等于没绑定）
  const segInHtml = segIds.filter(id => html.indexOf('id="' + id + '"') >= 0);
  eq(segInHtml.length, segIds.length, '段控件 ' + segIds.length + ' 个在 index.html 里都在');
}

clickBtn(segAuto.__segButtons[0]);           // 开启
runTimers();
eq(getEl('autoBadge').textContent, '自动发音：开', '点「开启」→ 顶栏状态变成开');
eq(getEl('autoBadge').style.color, 'var(--accent)', '开启时顶栏状态显蓝色');
saveNow();
const st = readState();
eq(st && st.settings.autoPlay, true, 'autoPlay 落盘为 true');

clickBtn(segAuto.__segButtons[1]);           // 关闭
runTimers();
eq(getEl('autoBadge').textContent, '自动发音：关', '点「关闭」→ 顶栏状态变成关');

console.log('\n========== 第 2 节：开关是否真的驱动朗读 ==========');
clickBtn(segAuto.__segButtons[0]);           // 开启
runTimers();
TTS.calls = []; TTS.cancelled = 0;

press('btnContinue');
runTimers();
runTimers();
ok(TTS.calls.length > 0, '开启后进入学习页会自动朗读（发了 ' + TTS.calls.length + ' 次）');
if (TTS.calls.length) {
  eq(TTS.calls[0].voice, 'Microsoft Zira', '选了 en-US 音色（不是中文音色）');
  eq(TTS.calls[0].rate, 0.9, '语速 0.9');
}

console.log('\n========== 第 3 节：关闭后不再自动朗读 ==========');
clickBtn(segAuto.__segButtons[1]);
runTimers();
TTS.calls = [];
press('btnContinue');
runTimers(); runTimers();
eq(TTS.calls.length, 0, '关闭后进入学习页不自动朗读');

console.log('\n========== 第 4 节：手动喇叭始终可用 ==========');
TTS.calls = [];
clickBtn(getEl('btnSpeak'));
// 首次会多一条 volume=0 的空串「热身」，那是 unlockTTS 的副作用，只发一次
const real = TTS.calls.filter(c => c.text.trim() !== '');
ok(real.length === 1, '点卡片「朗读」发一次真实发音（' + real.length + '）');
ok(TTS.calls.length <= 2, '热身空串最多一条（总 ' + TTS.calls.length + ' 次）');

console.log('\n========== 第 5 节：cancel/speak 竞态 ==========');
// 第 3 节末尾把 autoPlay 关了，这里先重新开启，否则测的是「关闭时不发音」
clickBtn(segAuto.__segButtons[0]);
runTimers();
eq(getEl('autoBadge').textContent, '自动发音：开', '第 5 节前置：已重新开启自动发音');
TTS.calls = []; TTS.cancelled = 0;
// 模拟在卡片上快速连滑 5 次（学习页的翻词手势）
for (let i = 0; i < 5; i++) { swipe('card', -1); runTimers(); }
ok(TTS.calls.length === 5,
  '连滑 5 次 = 发 5 次音（不被 cancel 吃掉），实际 ' + TTS.calls.length);

console.log('\n========== 第 6 节：学习页卡片开关（新增入口） ==========');
clickBtn(segAuto.__segButtons[1]);   // 先用调试面板关掉
runTimers();
eq(getEl('autoStateText').textContent, '关', '调试面板关 → 学习页开关同步为「关」');
eq(getEl('btnAutoCard').__cls.has('on'), false, '学习页开关未高亮');

clickBtn(getEl('btnAutoCard'));     // 从学习页开回来
runTimers();
eq(getEl('autoStateText').textContent, '开', '点学习页开关 → 变「开」');
eq(getEl('btnAutoCard').__cls.has('on'), true, '学习页开关高亮');
eq(getEl('autoBadge').textContent, '自动发音：开', '顶栏状态也跟着变开（三处同源）');

console.log('\n========== 第 7 节：手动朗读必须同步（iOS 硬约束） ==========');
TTS.calls = [];
clickBtn(getEl('btnSpeak'));
ok(TTS.calls.length >= 1, '点卡片「朗读」立刻发声，共 ' + TTS.calls.length + ' 次');
// 关键：同步路径不排定时器，发声必须发生在 click 调用栈内
const queued = timers.filter(t => !t.done && !t.cancelled).length;
eq(queued, 0, '手动朗读不排队（同一 tick 内已 speak，' + queued + ' 个待执行定时器）');

console.log('\n========== 第 8 节：自动播放才允许排队（躲 Chrome 竞态） ==========');
TTS.calls = [];
press('btnContinue');
runTimers();   // 进学习页 → 自动发音排队
eq(TTS.calls.length, 1, '自动发音延迟后仍发声 1 次');
ok(segAuto.__segButtons.length === 2, '段控件状态未错乱');

console.log('\n========== 第 9 节：语音诊断 ==========');
getEl('diagOut').textContent = '';
press('btnDiag');
const diag = getEl('diagOut').textContent;
ok(diag.indexOf('speechSynthesis 接口') >= 0, '诊断输出了接口检测');
ok(diag.indexOf('当前浏览器') >= 0, '诊断报告了当前浏览器');
ok(diag.indexOf('存在') >= 0, '有 speechSynthesis 桩时判定为存在');
ok(diag.indexOf('英文音色') >= 0, '诊断报告了英文音色数量');
ok(diag.indexOf('Microsoft Zira') >= 0, '列出了英文音色名字');
ok(diag.indexOf('本地离线音色') >= 0, '报告了本地离线音色（桩里没有 localService）');
getEl('diagOut').textContent = '';
press('btnDiagTest');
ok(getEl('diagOut').textContent.indexOf('试读') >= 0, '点「试读一句」输出了试读提示');

console.log('\n========== 第 10 节：浏览器识别（不支持时给换浏览器指引） ==========');
const brCases = [
  // 注意：这里必须是**字符串**，之前写成正则字面量的话 navigator.userAgent
  // 拿到的是 [object Object]，所有识别都会退化成「未知浏览器」
  ['Mozilla/5.0 (iPhone) MicroMessenger/8.0.49(0x18003128) NetType/WIFI', '微信内置浏览器', false],
  ['Mozilla/5.0 (iPhone) QQ/8.9.30', 'QQ 内置浏览器', false],
  ['Mozilla/5.0 (Linux; Android 12; HUAWEI) AppleWebKit/537.36 Mobile Safari/537.36 HuaweiBrowser/14.0', '华为浏览器', false],
  ['Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36', 'Chrome', true],
  ['Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36 Edg/126.0', 'Edge', true],
  ['Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15', 'Safari / macOS', true]
];
const detectBrowser = global.window.__fwords.detectBrowser;
// Node 22 的 global.navigator 是只读访问器，必须用 defineProperty 覆盖
function withUA(ua, fn) {
  const desc = Object.getOwnPropertyDescriptor(global, 'navigator');
  Object.defineProperty(global, 'navigator', { value: { userAgent: ua }, configurable: true, writable: true });
  try { return fn(); }
  finally {
    if (desc) Object.defineProperty(global, 'navigator', desc);
    else delete global.navigator;
  }
}

brCases.forEach(([ua, wantName, wantOk]) => {
  const r = withUA(ua, () => detectBrowser());
  eq(r.name, wantName, '识别 ' + wantName);
  eq(r.ok, wantOk, wantName + ' 的 ok 标记正确');
});
console.log('\n========== 第 11 节：离开页面即闭嘴 ==========');
TTS.cancelled = 0;
clickBtn(getEl('btnExitStudy'));
ok(TTS.cancelled > 0, '退出学习页触发 cancel（' + TTS.cancelled + ' 次）');

console.log('\n========== 第 12 节：切到在线真人音源（主路径） ==========');
clickBtn(srcHuman);
runTimers();
saveNow();
eq(readState().settings.ttsSource, 'human', '发音源切到 human 并落盘');

AUDIO.played = []; AUDIO.created = []; TTS.calls = [];
clickBtn(getEl('btnSpeak'));
eq(AUDIO.played.length, 1, '点「朗读」走 <audio> 播放（实际 ' + AUDIO.played.length + ' 次）');
ok(/dictvoice/.test(AUDIO.played[0] || ''), '播放的是在线音源 URL');
ok(/type=2/.test(AUDIO.played[0] || ''), '默认美音 → type=2');
eq(TTS.calls.filter(c => c.text.trim() !== '').length, 0, '真人音源下不再走 speechSynthesis');

console.log('\n========== 第 13 节：口音切换 ==========');
clickBtn(voiceUk);
runTimers();
saveNow();
eq(readState().settings.voiceAccent, 'uk', '口音切到英音并落盘');
AUDIO.played = [];
clickBtn(getEl('btnSpeak'));
ok(/type=1/.test(AUDIO.played[0] || ''), '英音 → URL type=1');
clickBtn(voiceUs);
runTimers();

console.log('\n========== 第 14 节：预取后几个词（「无感」的关键） ==========');
clickBtn(segAuto.__segButtons[0]);   // 打开自动发音
runTimers();
AUDIO.created = [];
press('btnContinue');                // 进学习页 → 渲染当前词时预取
runTimers();
ok(AUDIO.created.length >= 3,
  '进入学习页即预取当前词 + 后 3 个（新建 ' + AUDIO.created.length + ' 个 Audio）');

AUDIO.created = []; AUDIO.played = [];
clickBtn(getEl('btnSpeak'));          // 当前词在渲染时已预取
eq(AUDIO.created.length, 0, '朗读当前词无需新建 Audio（已预取，省掉网络等待）');
eq(AUDIO.played.length, 1, '预取过的词照样能播');

console.log('\n========== 第 15 节：音源加载失败 → 自动降级到系统语音 ==========');
AUDIO.created = [];
swipe('card', -1);                    // 翻到新词，会新建 Audio
runTimers();
const fresh = AUDIO.created[AUDIO.created.length - 1];
ok(!!fresh, '翻词后新建了 Audio 对象');
if (fresh) fresh.fire('error');       // 模拟网络失败
AUDIO.played = []; TTS.calls = [];
clickBtn(getEl('btnSpeak'));
ok(TTS.calls.filter(c => c.text.trim() !== '').length >= 1,
  '音源失败后自动改用系统语音（降级生效）');
eq(AUDIO.played.length, 0, '降级期间不再尝试 <audio>');

// 复位：切一下发音源会清掉临时降级
clickBtn(srcSystem); runTimers();
clickBtn(srcHuman); runTimers();
eq(readState().settings.ttsSource, 'human', '复位：发音源回到 human');

console.log('\n----------------------------------------');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('----------------------------------------');
process.exit(fail ? 1 : 0);

function press(id) {
  const b = getEl(id);
  if (b.__listeners.click && b.__listeners.click.length) b.__listeners.click[0]();
}

/* 模拟一次滑动手势：pointerdown → move → up。dir=-1 左滑（下一个） */
function swipe(id, dir) {
  const el = getEl(id);
  const L = el.__listeners;
  if (!L.pointerdown || !L.pointermove || !L.pointerup) return false;
  L.pointerdown[0]({ pointerId: 1, clientX: 300, clientY: 200, target: el });
  L.pointermove[0]({ pointerId: 1, clientX: 300 + dir * 160, clientY: 200, target: el });
  L.pointerup[0]({ pointerId: 1, clientX: 300 + dir * 160, clientY: 200, target: el });
  return true;
}