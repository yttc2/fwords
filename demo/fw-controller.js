/* Fwords · controller —— 事件绑定、手势、流程推进、计时循环。
 * 唯一同时接触 model 与 view 的层。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';

  /* ========== 依赖注入（别名）========== */
  /* —— 依赖 core 层 —— */
  var CLICK_PX = FW.core.CLICK_PX;
  var IDLE_GAP_MS = FW.core.IDLE_GAP_MS;
  var STORAGE_KEY = FW.core.STORAGE_KEY;
  var SWIPE_PX = FW.core.SWIPE_PX;
  var defaultState = FW.core.defaultState;
  var fmt = FW.core.fmt;
  var fmtReviewSpan = FW.core.fmtReviewSpan;
  var groups = FW.core.groups;
  var replaceGroups = FW.core.replaceGroups;
  var replaceState = FW.core.replaceState;
  var state = FW.core.state;
  var store = FW.core.store;
  var toast = FW.core.toast;
  var ui = FW.core.ui;

  /* —— 依赖 model 层 —— */
  var applyWordList = FW.model.applyWordList;
  var buildGroups = FW.model.buildGroups;
  var byId = FW.model.byId;
  var curCursor = FW.model.curCursor;
  var curGroup = FW.model.curGroup;
  var importFromText = FW.model.importFromText;
  var learnedInGroup = FW.model.learnedInGroup;
  var libData = FW.model.libData;
  var libOffset = FW.model.libOffset;
  var markLearned = FW.model.markLearned;
  var pickTrigger = FW.model.pickTrigger;
  var reviewEntries = FW.model.reviewEntries;
  var saveNow = FW.model.saveNow;
  var saveSoon = FW.model.saveSoon;
  var setCursor = FW.model.setCursor;
  var settleReview = FW.model.settleReview;
  var validSections = FW.model.validSections;

  /* —— 依赖 speech 层 —— */
  var resetHumanDown = FW.speech.resetHumanDown;
  var speak = FW.speech.speak;
  var stopSpeak = FW.speech.stopSpeak;
  var unlockTTS = FW.speech.unlockTTS;

  /* —— 依赖 view 层 —— */
  var $ = FW.view.$;
  var diagTest = FW.view.diagTest;
  var libCount = FW.view.libCount;
  var renderAuto = FW.view.renderAuto;
  var renderDebug = FW.view.renderDebug;
  var renderReview = FW.view.renderReview;
  var renderStudy = FW.view.renderStudy;
  var renderWords = FW.view.renderWords;
  var reviewTypeLabel = FW.view.reviewTypeLabel;
  var runDiag = FW.view.runDiag;
  var setAuto = FW.view.setAuto;
  var showView = FW.view.showView;
  var updateStatusBar = FW.view.updateStatusBar;


function loadLibChunk() {
  const lib = libData();
  if (!lib || !lib.raw || !lib.raw.length) { toast('词库数据未加载'); return; }
  const raw = lib.raw;
  const secs = validSections(lib);

  // 词表自带「天 / 单元」分节 → 整份载入，组数 = 节数（十份就是十天）
  if (secs.length >= 2) {
    const label = lib.name + ' · ' + raw.length + ' 词';
    if (!applyWordList(raw.join('\n'), label, lib.key, raw.length,
        { sections: secs, groupMode: 'sections' })) return;
    renderWords();
    toast('已载入 ' + raw.length + ' 词，按词表原分节切成 ' + secs.length + ' 组');
    goTo('view-home');
    return;
  }

  const n = Math.min(libCount(), raw.length);
  let off = libOffset(lib.key);
  let wrapped = false;
  if (off >= raw.length) { off = 0; wrapped = true; }

  const chunk = raw.slice(off, off + n);
  const to = off + chunk.length;
  const label = lib.name + ' · 第 ' + (off + 1) + '–' + to + ' 词';

  if (!applyWordList(chunk.join('\n'), label, lib.key, to)) return;
  renderWords();
  toast(wrapped
    ? '词库已取完，从第 1 个词重新开始'
    : '已载入 ' + chunk.length + ' 词（词库第 ' + (off + 1) + '–' + to + ' 词）');
  goTo('view-home');
}

/* 只载入某一节（例如「第 3 天」），它本身作为一个学习单元 */
function loadLibSection(i) {
  const lib = libData();
  if (!lib) return;
  const secs = validSections(lib);
  const sec = secs[i];
  if (!sec) return;

  let start = 0;
  for (let k = 0; k < i; k++) start += secs[k].count;
  const chunk = lib.raw.slice(start, start + sec.count);
  const label = lib.name + ' · ' + sec.label;

  if (!applyWordList(chunk.join('\n'), label, lib.key, 0,
      { sections: [{ label: sec.label, count: chunk.length }], groupMode: 'single' })) return;
  renderWords();
  toast('已载入 ' + sec.label + '（' + chunk.length + ' 词），作为一个学习单元');
  goTo('view-home');
}

/* ==================== 计时主循环 ==================== */
let lastTick = performance.now();

function tick() {
  const now = performance.now();
  let delta = now - lastTick;
  lastTick = now;
  // 间隔异常 → 说明页面被切走或主线程卡死，这段时间不计入净学习时长
  if (delta > IDLE_GAP_MS || delta < 0) delta = 0;

  if (state.phase === 'learning' && delta > 0) {
    state.netStudyMs += delta * state.settings.timeScale;
    saveSoon();
  }
  updateStatusBar();
}

function openGroup(i) {
  if (!groups.length) { goTo('view-words'); return; }
  state.groupIndex = i;
  if (state.groupDone[i]) { delete state.groupDone[i]; }
  state.phase = 'learning';
  saveSoon();
  goTo('view-study');
  renderStudy();
}

function continueStudy() {
  if (!groups.length) { goTo('view-words'); return; }
  let i = state.groupIndex;
  if (state.groupDone[i]) {
    for (let k = 0; k < groups.length; k++) {
      if (!state.groupDone[k]) { i = k; break; }
    }
  }
  state.groupIndex = i;
  const len = curGroup().length;
  if (curCursor() >= len) setCursor(0);
  state.phase = 'learning';
  lastTick = performance.now();
  goTo('view-study');
  renderStudy();
  toast('继续：第 ' + (i + 1) + ' 组');
}

function advance(dir) {
  if (ui.busy) return;
  const g = curGroup();
  let c = curCursor();

  if (dir > 0) {
    const w = g[c];
    if (w) markLearned(w);
    c++;
    setCursor(c);
    saveSoon();

    // —— 软触发：等当前这个词看完（即此刻切换）再进入回顾 ——
    const t = pickTrigger();
    if (t) { startReview(t); return; }

    if (c >= g.length) { finishGroup(); return; }
  } else {
    if (c <= 0) return;
    setCursor(c - 1);
  }
  renderStudy();
}

function startReview(type) {
  const list = reviewEntries(type);
  if (!list.length) {
    // 窗口内没有词（例如 5 分钟内还没学任何新词）→ 顺延，避免空回顾
    settleReview(type);
    saveSoon();
    const nxt = pickTrigger();
    if (nxt) { startReview(nxt); return; }
    resumeAfterReview();
    return;
  }

  state.phase = 'reviewing';
  state.activeReview = type;      // 落盘；刷新/中断后可据此结算
  ui.review = { type: type, list: list, idx: 0, flipped: false, t0: performance.now() };
  saveNow();
  goTo('view-review');
  renderReview();
}

function flipCard() {
  const r = ui.review;
  if (!r) return;
  const e = r.list[r.idx];
  const w = byId(e.id);
  if (!w) return;
  r.flipped = true;
  const m = $('rcardMeaning');
  m.textContent = w.meaning;
  m.classList.remove('hidden');
  $('rcardHint').textContent = '左滑或点「下一个」→ 继续　·　右滑 → 上一个';
}

/* 回顾不判断「认识 / 不认识」：看过就往下走，只推进游标。
 * 卡片未翻面时点击先翻面，已翻面后点击再前进。 */
function reviewNext() {
  const r = ui.review;
  if (!r) return;
  const e = r.list[r.idx];
  if (e) {
    const p = state.progress[e.id] || (state.progress[e.id] = { study: 0, review: 0 });
    p.review++;
  }
  saveSoon();
  r.idx++;
  if (r.idx >= r.list.length) finishReview();
  else renderReview();
}

function reviewPrev() {
  const r = ui.review;
  if (!r || r.idx <= 0) return;
  r.idx--;
  renderReview();
}

function reviewTap() {
  const r = ui.review;
  if (!r) return;
  if (!r.flipped) flipCard(); else reviewNext();
}

function finishReview() {
  const r = ui.review;
  if (!r) { resumeAfterReview(); return; }

  settleReview(r.type);
  state.activeReview = null;

  ui.resultMode = 'review';
  $('resValue').textContent = r.list.length;
  $('resLabel').textContent = '个词回顾完成';
  $('resDesc').textContent = reviewTypeLabel(r.type) +
    ' 回顾完毕，这一段用了 ' + fmtReviewSpan(r) + '。回顾期间计时已暂停。';

  // 列出这次回顾过的词，方便扫一眼
  const wrap = $('resHardWrap');
  const chips = $('resHardChips');
  chips.innerHTML = '';
  if (r.list.length) {
    wrap.style.display = 'block';
    $('resHardTitle').textContent = '本次回顾的 ' + r.list.length + ' 个词';
    r.list.forEach(function (e) {
      const w = byId(e.id);
      if (!w) return;
      const c = document.createElement('span');
      c.className = 'chip neutral';
      c.textContent = w.text;
      chips.appendChild(c);
    });
  } else {
    wrap.style.display = 'none';
  }
  $('btnResContinue').textContent = '继续学习';
  ui.review = null;
  saveNow();
  goTo('view-result');
}

function resumeAfterReview() {
  state.phase = 'learning';
  lastTick = performance.now();
  const t = pickTrigger();
  if (t) { startReview(t); return; }
  if (!groups.length) { goTo('view-home'); return; }
  if (curCursor() >= curGroup().length) { finishGroup(); return; }
  goTo('view-study');
  renderStudy();
}

/* ==================== 分组完成 ==================== */
function finishGroup() {
  state.phase = 'idle';
  state.groupDone[state.groupIndex] = true;
  ui.resultMode = 'group';
  const n = learnedInGroup(state.groupIndex);
  $('resValue').textContent = n;
  $('resLabel').textContent = '个词已背完';
  $('resDesc').textContent = '第 ' + (state.groupIndex + 1) + ' 组完成。累计净学习时长 ' + fmt(state.netStudyMs) + '（回顾时间不计入）。';
  $('resHardWrap').style.display = 'none';
  $('btnResContinue').textContent = (state.groupIndex + 1 < groups.length) ? ('进入第 ' + (state.groupIndex + 2) + ' 组') : '返回首页';
  saveNow();
  goTo('view-result');
}

function resultContinue() {
  if (ui.resultMode === 'review') { resumeAfterReview(); return; }
  if (state.groupIndex + 1 < groups.length) {
    state.groupIndex++;
    state.phase = 'learning';
    lastTick = performance.now();
    goTo('view-study');
    renderStudy();
  } else {
    state.phase = 'idle';
    goTo('view-home');
  }
}

/* ==================== 手势 ==================== */
function attachSwipe(el, onLeft, onRight, onClick) {
  if (!el || !el.addEventListener) return;      // 元素缺失时直接跳过，别抛异常连累后面的绑定
  let st = null;

  el.addEventListener('pointerdown', function (e) {
    if (e.target.closest && e.target.closest('button')) return;
    st = { x: e.clientX, y: e.clientY, dx: 0 };
    el.classList.remove('animating');
    el.classList.add('dragging');
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* 忽略 */ }
  });

  el.addEventListener('pointermove', function (e) {
    if (!st) return;
    st.dx = e.clientX - st.x;
    const dy = e.clientY - st.y;
    if (Math.abs(st.dx) > Math.abs(dy)) {
      el.style.transform = 'translateX(' + st.dx + 'px) rotate(' + (st.dx * 0.03) + 'deg)';
      el.style.opacity = String(Math.max(0.45, 1 - Math.abs(st.dx) / 700));
    }
  });

  function reset() {
    el.classList.remove('dragging');
    el.classList.add('animating');
    el.style.transform = '';
    el.style.opacity = '';
    setTimeout(function () { el.classList.remove('animating'); }, 200);
  }

  el.addEventListener('pointerup', function (e) {
    if (!st) return;
    const dx = st.dx, dy = e.clientY - st.y;
    st = null;
    reset();
    if (Math.abs(dx) >= SWIPE_PX) { if (dx < 0) onLeft(); else onRight(); }
    else if (Math.abs(dx) < CLICK_PX && Math.abs(dy) < CLICK_PX) { onClick(); }
  });

  el.addEventListener('pointercancel', function () {
    if (!st) return;
    st = null;
    reset();
  });
}

/* 逐个绑定，缺元素只告警不抛异常。
 * 为什么：裸写 $('x').addEventListener，一旦某个元素不存在（新旧文件混搭、
 * 缓存了旧 HTML 等），异常会直接中断 bind()，**它后面所有绑定全部静默失效** ——
 * 表现为「页面上有这个按钮，但按下去毫无反应」。 */
function on(id, ev, fn) {
  const el = $(id);
  if (!el) { warnMissing('#' + id); return false; }
  el.addEventListener(ev, fn);
  return true;
}

/* 安全版 querySelectorAll(...).forEach：选择器没匹配到只告警，不抛异常。
 * 回调拿到的是元素本身，监听由调用方自己挂（保持原来的嵌套写法不变）。 */
function eachOf(sel, fn) {
  const list = document.querySelectorAll(sel);
  if (!list || !list.length) { warnMissing(sel); return 0; }
  Array.prototype.forEach.call(list, fn);
  return list.length;
}

function warnMissing(what) {
  try { if (window.console) console.warn('[Fwords] 缺少元素 ' + what + '，已跳过绑定'); } catch (e) { /* 忽略 */ }
}

function bind() {
  // 首页
  on('btnContinue', 'click', continueStudy);
  on('btnGoWords', 'click', function () {
    $('importText').value = state.rawText || '';
    goTo('view-words');
  });
  on('btnStartFresh', 'click', function () {
    if (!confirm('将清空所有背诵进度（词库保留），确定继续？')) return;
    const keepName = state.listName, keepRaw = state.rawText, keepWords = state.words;
    const keepSettings = state.settings;
    const keepSections = state.sections, keepMode = state.groupMode;
    const keepLib = state.libKey, keepOffsets = state.libOffsets;
    replaceState(defaultState());
    state.listName = keepName; state.rawText = keepRaw;
    state.words = keepWords; state.settings = keepSettings;
    state.sections = keepSections; state.groupMode = keepMode;
    state.libKey = keepLib; state.libOffsets = keepOffsets;
    replaceGroups(buildGroups());
    state.phase = 'learning';
    state.groupIndex = 0;
    saveNow();
    goTo('view-study');
    renderStudy();
  });

  // 词库
  on('btnBackHome1', 'click', function () { goTo('view-home'); });

  on('libSelect', 'change', function () {
    state.libKey = $('libSelect').value;
    saveSoon();
    renderWords();
  });
  on('btnLoadLib', 'click', loadLibChunk);
  on('libCount', 'input', renderWords);
  on('libCount', 'change', function () {
    const lib = libData();
    const total = lib ? (lib.count || lib.raw.length) : 600;
    let v = parseInt($('libCount').value, 10);
    if (!v || v < 1) v = 60;
    $('libCount').value = String(Math.max(1, Math.min(total, v)));
    renderWords();
  });

  on('btnLoadBuiltin', 'click', function () {
    $('importText').value = window.BUILTIN_WORDLIST.raw.join('\n');
    toast('已填入示例词表，点「导入并开始」生效');
  });

  // 从文件读取（txt / csv / tsv / json / md）
  on('btnPickFile', 'click', function () { $('fileImport').click(); });
  on('fileImport', 'change', function () {
    const f = $('fileImport').files && $('fileImport').files[0];
    if (!f) return;
    const fr = new FileReader();
    fr.onload = function () {
      const text = String(fr.result || '');
      $('importText').value = text.length > 200000 ? text.slice(0, 200000) : text;
      const t = importFromText(text, f.name.replace(/\.[^.]+$/, ''));
      if (t) toast(t);
    };
    fr.onerror = function () { toast('文件读取失败'); };
    fr.readAsText(f, 'utf-8');
    $('fileImport').value = '';
  });

  on('btnImport', 'click', function () {
    const t = importFromText($('importText').value, '我的词表');
    if (t) { toast(t); goTo('view-home'); }
  });

  // 学习
  on('btnExitStudy', 'click', function () {
    // 回顾途中退出：也要结算，否则下一次切词会把同一个回顾再弹一遍
    if (ui.review) { settleReview(ui.review.type); ui.review = null; }
    state.activeReview = null;
    state.phase = 'idle';
    saveNow();
    goTo('view-home');
  });
  on('btnSpeak', 'click', function () {
    unlockTTS();
    const w = curGroup()[curCursor()];
    if (w) speak(w.text);
  });
  on('btnSpeakR', 'click', function () {
    unlockTTS();
    const r = ui.review;
    if (!r) return;
    const w = byId(r.list[r.idx].id);
    if (w) speak(w.text);
  });

  attachSwipe($('card'),
    function () { advance(1); },
    function () { advance(-1); },
    function () { advance(1); }
  );
  attachSwipe($('rcard'),
    function () { reviewNext(); },   // 左滑 → 下一个
    function () { reviewPrev(); },   // 右滑 → 上一个
    function () { reviewTap(); }     // 点击 → 未翻面先翻面，已翻面前进
  );

  // 回顾
  on('btnNextR', 'click', reviewNext);

  // 结果
  on('btnResContinue', 'click', resultContinue);

  // 调试
  on('btnDebug', 'click', function () { goTo('view-debug'); });
  on('btnBackHome2', 'click', function () { goTo('view-home'); });
  eachOf('#segScale button', function (b) {
    b.addEventListener('click', function () {
      state.settings.timeScale = Number(b.dataset.v);
      saveSoon(); renderDebug();
      toast(state.settings.timeScale === 1 ? '时间加速已关闭（真实速度）' : '时间加速 ×60：1 秒 = 1 分钟');
    });
  });
  eachOf('#segReview button', function (b) {
    b.addEventListener('click', function () {
      state.settings.reviewEnabled = Number(b.dataset.v) === 1;
      saveSoon(); renderDebug();
      toast(state.settings.reviewEnabled ? '回顾机制已开启' : '回顾机制已关闭（纯背单词模式）');
    });
  });
  on('btnReset', 'click', function () {
    if (!confirm('清空全部数据并重置到初始状态？')) return;
    store.removeItem(STORAGE_KEY);
    replaceState(defaultState());
    initWords(true);
    goTo('view-home');
    toast('已重置');
  });

  // 自动发音：学习页卡片下的开关 + 调试面板段控件，同一份状态
  eachOf('#segAuto button', function (b) {
    b.addEventListener('click', function () { setAuto(Number(b.dataset.v) === 1); });
  });
  on('btnAutoCard', 'click', function () {
    setAuto(!state.settings.autoPlay);
  });
  on('btnDiag', 'click', runDiag);
  on('btnDiagTest', 'click', diagTest);
  eachOf('#segSource button', function (b) {
    b.addEventListener('click', function () {
      state.settings.ttsSource = b.dataset.v;
      resetHumanDown();                   // 手动切换时清掉临时降级
      if (state.settings.ttsSource === 'system') stopSpeak();
      saveSoon(); renderDebug();
      toast(state.settings.ttsSource === 'human' ? '发音源：在线真人录音' : '发音源：系统语音引擎（兜底）');
    });
  });
  eachOf('#segVoice button', function (b) {
    b.addEventListener('click', function () {
      state.settings.voiceAccent = b.dataset.v;
      stopSpeak();
      saveSoon(); renderDebug();
      toast(b.dataset.v === 'uk' ? '口音：英音' : '口音：美音');
    });
  });
  renderAuto();
}

/* 重置计时基准。app 层启动时要调（跨层写原始值，所以做成函数） */
function resetClock() { lastTick = performance.now(); }

/* 切换视图的唯一入口。
 * 「离开学习页就停表」放在这里而不是 view 里 —— 改业务状态是 controller 的职责，
 * view 只负责把界面画出来（那条规矩由 tests/layers-test.js 盯着）。 */
function goTo(name) {
  if (name !== 'view-study' && state.phase === 'learning') {
    state.phase = 'idle';
    saveSoon();
  }
  showView(name);
}

  FW.controller = {
    advance: advance,
    attachSwipe: attachSwipe,
    bind: bind,
    continueStudy: continueStudy,
    eachOf: eachOf,
    finishGroup: finishGroup,
    finishReview: finishReview,
    flipCard: flipCard,
    goTo: goTo,
    lastTick: lastTick,
    loadLibChunk: loadLibChunk,
    loadLibSection: loadLibSection,
    on: on,
    openGroup: openGroup,
    resetClock: resetClock,
    resultContinue: resultContinue,
    resumeAfterReview: resumeAfterReview,
    reviewNext: reviewNext,
    reviewPrev: reviewPrev,
    reviewTap: reviewTap,
    startReview: startReview,
    tick: tick,
    warnMissing: warnMissing,
  };
})(window.FW = window.FW || {});
