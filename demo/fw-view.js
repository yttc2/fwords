/* Fwords · view —— 渲染与视图切换。
 * 只读 model 的数据、只写 DOM，**不改业务状态**（改状态的动作都交给 controller）。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';

  /* ========== 依赖注入（别名）========== */
  /* —— 依赖 core 层 —— */
  var GROUP_COUNT = FW.core.GROUP_COUNT;
  var T1_WORDS = FW.core.T1_WORDS;
  var T2_MS = FW.core.T2_MS;
  var T3_MS = FW.core.T3_MS;
  var fmt = FW.core.fmt;
  var groups = FW.core.groups;
  var setNotifier = FW.core.setNotifier;
  var state = FW.core.state;
  var toast = FW.core.toast;
  var ui = FW.core.ui;

  /* —— 依赖 model 层 —— */
  var byId = FW.model.byId;
  var curCursor = FW.model.curCursor;
  var curGroup = FW.model.curGroup;
  var groupLabels = FW.model.groupLabels;
  var learnedInGroup = FW.model.learnedInGroup;
  var libData = FW.model.libData;
  var libOffset = FW.model.libOffset;
  var libRegistry = FW.model.libRegistry;
  var pickTrigger = FW.model.pickTrigger;
  var reviewEntries = FW.model.reviewEntries;
  var saveSoon = FW.model.saveSoon;
  var validSections = FW.model.validSections;

  /* —— 依赖 speech 层 —— */
  var PREFETCH_AHEAD = FW.speech.PREFETCH_AHEAD;
  var accentType = FW.speech.accentType;
  var detectBrowser = FW.speech.detectBrowser;
  var dictUrl = FW.speech.dictUrl;
  var humanDownLeftMs = FW.speech.humanDownLeftMs;
  var loadVoices = FW.speech.loadVoices;
  var pickVoice = FW.speech.pickVoice;
  var prefetchList = FW.speech.prefetchList;
  var resetTtsWarned = FW.speech.resetTtsWarned;
  var speak = FW.speech.speak;
  var speakAuto = FW.speech.speakAuto;
  var stopSpeak = FW.speech.stopSpeak;
  var voices = FW.speech.voices;


/* ==================== 小工具 ==================== */
function $(id) { return document.getElementById(id); }

/* 组的显示名：词表自带分节时用分节名（如「Day 3」「List 5」），否则用「第 N 组」 */
function groupTitle(i) {
  const lab = groupLabels[i];
  return lab ? lab : ('第 ' + (i + 1) + ' 组');
}

/* 学习页顶部的组标题：「Day 3 · 第 3 / 10 组」 */
function studyGroupLabel(i) {
  const base = '第 ' + (i + 1) + ' / ' + groups.length + ' 组';
  const lab = groupLabels[i];
  return lab ? (lab + ' · ' + base) : base;
}

function libCount() {
  let n = parseInt($('libCount').value, 10);
  if (!n || n < 1) n = 60;
  return Math.max(1, n);
}

function renderWords() {
  const nameEl = $('libName');
  if (!nameEl) return;

  // 词库下拉
  const sel = $('libSelect');
  const all = libRegistry();
  const cur = all.length ? libData() : null;
  if (sel) {
    sel.innerHTML = '';
    all.forEach(function (l) {
      const o = document.createElement('option');
      o.value = l.key;
      // 导入的词库加个星标：下拉里几个名字都带「十天」，不标一眼认不出来
      o.textContent = (l.source === '导入' ? '★ ' : '') + l.name + '（' + l.count + ' 词）';
      if (cur && l.key === cur.key) o.selected = true;
      sel.appendChild(o);
    });
    if (!all.length) {
      const o = document.createElement('option');
      o.textContent = '（无可用词库）';
      sel.appendChild(o);
    }
  }

  const secWrap = $('libSecWrap'), chipBox = $('libSecChips'), countRow = $('libCountRow');
  chipBox.innerHTML = '';

  if (!cur) {
    nameEl.textContent = '（词库数据未加载）';
    $('libMeta').textContent = '确认 wordlists.js 与页面一起加载';
    $('libHint').textContent = '';
    $('btnLoadLib').disabled = true;
    if (secWrap) secWrap.style.display = 'none';
    if (countRow) countRow.style.display = 'none';
    return;
  }

  $('btnLoadLib').disabled = false;
  $('libBadge').textContent = cur.source;
  nameEl.textContent = cur.name;

  const total = cur.count || cur.raw.length;
  const secs = validSections(cur);
  const off = Math.min(libOffset(cur.key), total);

  // —— 有分节：整份载入（按天分组），或单独载入某一节 ——
  if (secs.length >= 2) {
    $('libMeta').textContent = '共 ' + total + ' 词 · 自带 ' + secs.length + ' 个分节';
    if (secWrap) secWrap.style.display = 'block';
    if (countRow) countRow.style.display = 'none';
    $('libSecTitle').textContent = '点某一节可单独载入（该节作为一组）';
    secs.forEach(function (x, i) {
      const b = document.createElement('button');
      b.className = 'sec-chip';
      b.textContent = x.label + ' · ' + x.count + '词';
      b.addEventListener('click', function () { FW.controller.loadLibSection(i); });
      chipBox.appendChild(b);
    });
    $('libHint').textContent = '直接载入 = 整份 ' + total + ' 词按这 ' + secs.length +
      ' 个分节切成 ' + secs.length + ' 组。也可以点上面某一节，只载入那一节。';
    $('btnLoadLib').textContent = '载入全部 ' + total + ' 词 · 分 ' + secs.length + ' 组';
    return;
  }

  // —— 无分节：按词数分批取 ——
  if (secWrap) secWrap.style.display = 'none';
  if (countRow) countRow.style.display = 'flex';

  const n = Math.min(libCount(), total);
  const remain = total - off;
  const gcount = Math.min(n, GROUP_COUNT);
  const per = Math.floor(n / gcount) + (n % gcount > 0 ? 1 : 0);

  $('libMeta').textContent = '共 ' + total + ' 词 · 已取 ' + off + ' 词';

  if (remain > 0) {
    const to = Math.min(off + n, total);
    $('libHint').textContent = '将取第 ' + (off + 1) + '–' + to + ' 词，分 ' + gcount +
      ' 组、每组 ' + per + ' 词。每组内每 6 词触发一次 T1 回顾，每组约 ' + Math.ceil(per / T1_WORDS) + ' 次。';
    $('btnLoadLib').textContent = '载入第 ' + (off + 1) + '–' + to + ' 词';
  } else {
    $('libHint').textContent = '词库已全部取完。再载入会从第 1 个词重新开始。';
    $('btnLoadLib').textContent = '从头再取 ' + n + ' 词';
  }
}

/* ==================== 视图切换 ==================== */
const VIEWS = ['view-home', 'view-words', 'view-study', 'view-review', 'view-result', 'view-debug'];

function showView(name) {
  // 只管把界面切过去。**别在这里改业务状态** ——
  // 「离开学习页就停表」那种判断属于 controller（见 controller.goTo）。
  // 离开学习/回顾页就闭嘴，否则喇叭会对着别的页面继续念
  if (name !== 'view-study' && name !== 'view-review') stopSpeak();
  VIEWS.forEach(function (v) { $(v).classList.toggle('active', v === name); });
  window.scrollTo(0, 0);
  if (name === 'view-home') renderHome();
  if (name === 'view-words') renderWords();
  if (name === 'view-debug') renderDebug();
}

/* ==================== 首页 ==================== */
function renderHome() {
  const g = curGroup();
  $('home-list-name').textContent = state.listName || '（未载入词库）';
  $('home-list-meta').textContent = state.words.length
    ? (state.words.length + ' 词 · 分 ' + groups.length + ' 组' +
       (groupLabels.length ? ' · 按词表原有分节' : ' · 每组约 ' + (g.length || 0) + ' 词'))
    : '请先导入词库';

  const seen = {}, uniq = [];
  state.learnedLog.forEach(function (e) { if (!seen[e.id]) { seen[e.id] = 1; uniq.push(e.id); } });
  $('home-learned').textContent = uniq.length;
  $('home-time').textContent = fmt(state.netStudyMs);

  let done = 0;
  groups.forEach(function (_, i) { if (state.groupDone[i]) done++; });
  $('home-done').textContent = done + '/' + groups.length;

  const sub = $('groupSub');
  if (sub) sub.textContent = '分组进度 · 共 ' + groups.length + ' 组' +
    (groupLabels.length ? '（' + groupLabels.length + ' 个分节）' : '');

  const grid = $('groupGrid');
  grid.innerHTML = '';
  groups.forEach(function (list, i) {
    const cell = document.createElement('button');
    cell.className = 'group-cell';
    if (state.groupDone[i]) cell.classList.add('done');
    else if (i === state.groupIndex) cell.classList.add('cur');

    const lab = groupLabels[i];
    const n = document.createElement('div');
    n.className = 'n' + (lab ? ' lbl' : '');
    n.textContent = lab || String(i + 1);

    const s = document.createElement('div');
    s.style.fontSize = '11px';
    s.textContent = learnedInGroup(i) + '/' + list.length;

    cell.title = (lab ? lab + ' · ' : '') + '第 ' + (i + 1) + ' 组 · ' + list.length + ' 词';
    cell.appendChild(n);
    cell.appendChild(s);
    cell.addEventListener('click', function () { FW.controller.openGroup(i); });
    grid.appendChild(cell);
  });
}

/* ==================== 学习页 ==================== */
function renderStudy() {
  const g = curGroup();
  const len = g.length;
  const c = curCursor();

  if (!len || c >= len) { FW.controller.finishGroup(); return; }

  renderAuto();   // 学习页底部的自动发音开关始终同步

  const w = g[c];
  $('study-group-label').textContent = studyGroupLabel(state.groupIndex);
  $('study-pos').textContent = (c + 1) + ' / ' + len;
  $('studyBar').style.width = (c / len * 100) + '%';
  $('cardCounter').textContent = (c + 1) + ' / ' + len;
  $('cardWord').textContent = w.text;
  $('cardPhonetic').textContent = w.phonetic || '';
  $('cardMeaning').textContent = w.meaning;
  $('cardMeaning').classList.remove('hidden');
  $('cardHint').textContent = '点击卡片或左滑 → 下一个　·　右滑 → 上一个';

  prefetchList(g, c, PREFETCH_AHEAD);      // 提前下好后几个词 → 切词无感
  if (state.settings.autoPlay) speakAuto(w.text);
}

function reviewTypeLabel(t) {
  if (t === 'T1') return 'T1 · 每 6 个单词';
  if (t === 'T2') return 'T2 · 每 5 分钟';
  return 'T3 · 每 30 分钟';
}

function renderReview() {
  const r = ui.review;
  if (!r) return;
  const e = r.list[r.idx];
  if (!e) { FW.controller.finishReview(); return; }
  const w = byId(e.id);
  if (!w) { r.idx++; renderReview(); return; }

  const tag = $('reviewTag');
  tag.textContent = reviewTypeLabel(r.type);
  tag.className = 'review-tag tag-' + r.type;

  $('reviewPos').textContent = (r.idx + 1) + ' / ' + r.list.length;
  $('reviewBar').style.width = (r.idx / r.list.length * 100) + '%';
  $('rcardCounter').textContent = (r.idx + 1) + ' / ' + r.list.length;
  $('rcardWord').textContent = w.text;
  $('rcardPhonetic').textContent = w.phonetic || '';

  r.flipped = false;
  const m = $('rcardMeaning');
  m.textContent = '点击卡片显示释义';
  m.classList.add('hidden');
  $('rcardHint').textContent = '先自己回忆一遍，再点击卡片看释义';

  const last = (r.idx + 1 >= r.list.length);
  $('btnNextR').textContent = last ? '完成回顾' : '下一个';

  prefetchList(r.list, r.idx, PREFETCH_AHEAD, function (e) {
    const ww = byId(e.id);
    return ww ? ww.text : '';
  });
  if (state.settings.autoPlay) speakAuto(w.text);
}

/* ==================== 顶栏状态 ==================== */
function updateStatusBar() {
  const s = $('statStudy');
  if (!s) return;
  s.textContent = fmt(state.netStudyMs);
  $('statGroup').textContent = learnedInGroup(state.groupIndex);

  const on = state.settings.reviewEnabled;
  $('statT2').textContent = on ? fmt(Math.max(0, T2_MS - (state.netStudyMs - state.lastT2AtNetMs))) : '关闭';
  $('statT3').textContent = on ? fmt(Math.max(0, T3_MS - (state.netStudyMs - state.lastT3AtNetMs))) : '关闭';

  const hint = $('pendingHint');
  const t = pickTrigger();
  if (t && state.phase === 'learning') {
    const cnt = reviewEntries(t).length;
    hint.textContent = '已到回顾节点（' + reviewTypeLabel(t) + '，' + cnt + ' 个词）· 切到下一个词后自动开始回顾';
    hint.classList.add('show');
  } else {
    hint.classList.remove('show');
  }

  if ($('view-debug').classList.contains('active')) {
    $('dbgCounters').textContent = 'T1 ' + (state.learnedLog.length - state.t1Baseline) + '/6 · T2 ' +
      fmt(state.netStudyMs - state.lastT2AtNetMs) + '/05:00 · T3 ' + fmt(state.netStudyMs - state.lastT3AtNetMs) + '/30:00';
  }
}

/* ==================== 语音诊断（手机上没声音时先跑这个） ==================== */
function diagLine(k, v, bad) {
  return (bad ? '✗ ' : '✓ ') + k + '：' + v;
}

function renderDiag(out) {
  const el = $('diagOut');
  if (el) el.textContent = out.join('\n');
}

function runDiag() {
  const out = [];
  const ss = window.speechSynthesis;

  const br = detectBrowser();
  out.push('当前浏览器：' + br.name);
  out.push('');

  // 主路径：在线真人录音，不依赖 speechSynthesis
  const useHuman = state.settings.ttsSource !== 'system';
  out.push(diagLine('音频播放（<audio>）', window.Audio ? '可用' : '不可用', !window.Audio));
  out.push(diagLine('发音源', useHuman ? '在线真人录音（有道音源）'
    : '系统语音引擎（手动选的）', !useHuman));
  if (useHuman && window.Audio) {
    out.push('  → 这条路径不需要 speechSynthesis，微信/内置浏览器里也能出声。');
    if (humanDownLeftMs() > 0) {
      out.push('  注意：真人音源刚失败过，暂时降级到系统语音，约 ' +
        Math.ceil((humanDownUntil - Date.now()) / 1000) + ' 秒后重试。');
    }
  }
  out.push('');

  if (!ss || !window.SpeechSynthesisUtterance) {
    out.push(diagLine('speechSynthesis 接口', '不存在（只影响「系统」发音源）', true));
    if (br.why) out.push('  原因：' + br.why);
    if (!useHuman || !window.Audio) {
      out.push('');
      out.push('【结论】两条路都不可用，只能换浏览器：');
      out.push('  iPhone  → 用 Safari（微信里点右上角「在 Safari 中打开」）');
      out.push('  安卓    → 用 Chrome / Edge，去应用商店装');
      out.push('  微信里  → 点右上角「···」→「在浏览器打开」');
    } else {
      out.push('');
      out.push('但当前用的是「真人」发音源，不依赖这个接口，可以正常出声。');
    }
    renderDiag(out);
    toast('诊断完成');
    return;
  }

  out.push(diagLine('speechSynthesis 接口', '存在（系统发音源可用）'));
  out.push('');

  if (!voices.length) loadVoices();
  const eng = voices.filter(v => /^en/i.test(v.lang));
  out.push(diagLine('检测到的音色总数', String(voices.length), voices.length === 0));
  out.push(diagLine('其中英文音色', eng.length + ' 个', eng.length === 0));

  if (eng.length) {
    // 只列前 8 个，别刷屏
    const list = eng.slice(0, 8).map(v => v.name + '(' + v.lang + ')');
    out.push('  ' + list.join('\n  '));
  } else {
    out.push('  → 系统里没有安装英文语音包，这就是没声音的原因。');
    out.push('  iOS：设置 → 辅助功能 → 朗读内容 → 语音 → English → 下载');
    out.push('  安卓：设置 → 辅助功能 → 文字转语音输出 → 引擎选「Google 文字转语音」→ 安装英语语音数据');
    out.push('  国行手机还要装：应用商店搜「Google 文字转语音」装上引擎');
  }

  const local = voices.filter(v => v.localService).length;
  out.push(diagLine('本地离线音色', local + ' 个', local === 0));
  if (local === 0) out.push('  → 没有离线音色时，朗读可能需要联网，且更容易被系统忽略');

  out.push(diagLine('手动朗读路径', '同步发声（iOS 安全）'));
  out.push(diagLine('自动发音路径', '延迟 80ms（iOS 上可能被系统忽略）'));
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) {
    out.push('');
    out.push('检测到 iOS：自动发音可能不出声（Safari 要求用户手势），');
    out.push('请用卡片左上角的「朗读」按钮，那个是同步调用，一定能响。');
  }

  renderDiag(out);
  toast('诊断完成');
}

/* 试读：优先测真人音源，再测系统 TTS。
 * 把 onstart/onerror / canplaythrough 打出来 —— 这是判定「到底哪一环坏了」的关键证据。 */
function diagTest() {
  const out = [];
  out.push('正在试读 abandon…');
  out.push('（先测在线真人音源，再测系统语音引擎）');
  out.push('');
  renderDiag(out);

  // ---- 第一段：在线真人音源 ----
  if (window.Audio) {
    const t0 = Date.now();
    const a = new Audio();
    a.preload = 'auto';
    a.src = dictUrl('abandon', accentType());
    let settled = false;
    const done = function (line) {
      if (settled) return;
      settled = true;
      out.push(line);
      out.push('');
      out.push('--- 以下是系统语音引擎检测 ---');
      renderDiag(out);
      setTimeout(systemPart, 1200);
    };
    a.addEventListener('canplaythrough', function () {
      done('✓ 真人音源：音频已就绪（' + (Date.now() - t0) + ' ms）' +
        '\n  → 这就是「无感」依赖的路径，数字越小切词越跟手');
    });
    a.addEventListener('error', function () {
      const ms = Date.now() - t0;
      if (!navigator.onLine) {
        done('✗ 真人音源：当前离线（' + ms + ' ms）\n  → 已自动降级到系统语音引擎');
      } else {
        done('✗ 真人音源：加载失败（' + ms + ' ms）\n  → 网络被拦或音源不可用，已降级到系统语音引擎');
      }
    });
    const p = a.play();
    if (p && p.catch) p.catch(function () { /* 交给 error / 超时处理 */ });
    // 3 秒还没结果就当作失败
    setTimeout(function () { done('✗ 真人音源：3 秒内没有就绪\n  → 网络可能很慢，或音源被拦'); }, 3000);
  } else {
    out.push('✗ 当前环境没有 <audio>，只能用系统语音引擎');
    out.push('');
    renderDiag(out);
    setTimeout(systemPart, 100);
  }

  // ---- 第二段：系统 TTS ----
  function systemPart() {
    if (!window.speechSynthesis || !window.SpeechSynthesisUtterance) {
      out.push('✗ 系统语音引擎：这个浏览器没有 speechSynthesis 接口');
      out.push('  → 只影响「系统」发音源；用「真人」发音源不受影响');
      renderDiag(out);
      return;
    }
    if (!voices.length) loadVoices();
    resetTtsWarned();
    try {
      const u = new SpeechSynthesisUtterance('abandon');
      u.lang = 'en-US';
      u.rate = 0.9;
      const v = pickVoice();
      if (v) u.voice = v;
      u.onstart = function () {
        out.push('✓ 系统语音引擎：发声成功');
        out.push('  音色：' + (v ? v.name + ' (' + v.lang + ')' : '系统默认'));
        renderDiag(out);
      };
      u.onerror = function (ev) {
        const e = ev && ev.error ? ev.error : 'unknown';
        out.push('✗ 系统语音引擎 onerror：' + e);
        if (e === 'synthesis-unavailable') out.push('  → 系统没装英文语音包，去系统设置里下载');
        else if (e === 'synthesis-failed') out.push('  → 引擎在但合成失败，试着更新引擎 / 重启手机');
        else if (e === 'not-allowed') out.push('  → 被浏览器拦了，刷新页面再点一次');
        else if (e === 'canceled' || e === 'interrupted') out.push('  → 被新的朗读打断，正常现象');
        renderDiag(out);
      };
      window.speechSynthesis.speak(u);
    } catch (e) {
      out.push('✗ 系统语音引擎抛异常：' + (e && e.message ? e.message : e));
      renderDiag(out);
    }
  }
}

/* ==================== 调试面板 ==================== */
function renderAuto() {
  const on = !!state.settings.autoPlay;
  const label = on ? '开' : '关';

  const badge = $('autoBadge');
  if (badge) {
    badge.textContent = '自动发音：' + label;
    badge.style.color = on ? 'var(--accent)' : '';
  }
  const card = $('btnAutoCard');
  if (card) card.classList.toggle('on', on);
  const txt = $('autoStateText');
  if (txt) txt.textContent = label;

  document.querySelectorAll('#segAuto button').forEach(function (b) {
    b.classList.toggle('on', Number(b.dataset.v) === (on ? 1 : 0));
  });
}

function setAuto(on) {
  state.settings.autoPlay = !!on;
  saveSoon();
  renderAuto();
  toast(state.settings.autoPlay
    ? '已开启自动发音：每次切到新词自动朗读'
    : '已关闭自动发音');
}

function renderDebug() {
  document.querySelectorAll('#segScale button').forEach(function (b) {
    b.classList.toggle('on', Number(b.dataset.v) === state.settings.timeScale);
  });
  document.querySelectorAll('#segReview button').forEach(function (b) {
    b.classList.toggle('on', Number(b.dataset.v) === (state.settings.reviewEnabled ? 1 : 0));
  });
  document.querySelectorAll('#segSource button').forEach(function (b) {
    b.classList.toggle('on', b.dataset.v === (state.settings.ttsSource === 'system' ? 'system' : 'human'));
  });
  document.querySelectorAll('#segVoice button').forEach(function (b) {
    b.classList.toggle('on', b.dataset.v === (state.settings.voiceAccent === 'uk' ? 'uk' : 'us'));
  });

  const bd = $('dbgBuild');
  if (bd) bd.textContent = buildStamp();

  renderAuto();
}

/* ==================== 事件绑定 ==================== */
/* 构建版本戳：tools/pack.py 打包时写进 <meta name="fwords-build">。
 * 手机上排查「新 HTML + 旧 JS 混搭」时，先看调试面板这一行对不对得上。
 * 注意 document.querySelector 在测试桩里不存在，必须判存在性。 */
function buildStamp() {
  try {
    if (!document.querySelector) return 'dev';
    const m = document.querySelector('meta[name="fwords-build"]');
    return (m && m.content) ? m.content : 'dev';
  } catch (e) { return 'dev'; }
}

/* ---- 提示条的真正渲染 ----
 * core 的 toast 只是个通知通道，这里把「怎么显示」注册进去。
 * 这样 model 层调 toast 时完全不需要知道 DOM 的存在。 */
let toastTimer = null;
function renderToastDom(msg) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove('show'); }, 1800);
}
setNotifier(renderToastDom);

  FW.view = {
    $: $,
    VIEWS: VIEWS,
    buildStamp: buildStamp,
    diagLine: diagLine,
    diagTest: diagTest,
    groupTitle: groupTitle,
    libCount: libCount,
    renderAuto: renderAuto,
    renderDebug: renderDebug,
    renderDiag: renderDiag,
    renderHome: renderHome,
    renderReview: renderReview,
    renderStudy: renderStudy,
    renderWords: renderWords,
    reviewTypeLabel: reviewTypeLabel,
    runDiag: runDiag,
    setAuto: setAuto,
    showView: showView,
    studyGroupLabel: studyGroupLabel,
    updateStatusBar: updateStatusBar,
  };
})(window.FW = window.FW || {});
