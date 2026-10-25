/* Fwords · 引导 —— 组装各层并启动。
 * 加载顺序由 index.html 保证：core → model → speech → view → controller → app。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';

  /* ========== 依赖注入（别名）========== */
  /* —— 依赖 core 层 —— */
  var TICK_MS = FW.core.TICK_MS;
  var fmt = FW.core.fmt;
  var normalizeActiveReview = FW.core.normalizeActiveReview;
  var state = FW.core.state;
  var toast = FW.core.toast;

  /* —— 依赖 model 层 —— */
  var initWords = FW.model.initWords;
  var loadState = FW.model.loadState;
  var reviewEntries = FW.model.reviewEntries;
  var saveNow = FW.model.saveNow;
  var saveSoon = FW.model.saveSoon;
  var settleReview = FW.model.settleReview;
  var startAutoSave = FW.model.startAutoSave;

  /* —— 依赖 speech 层 —— */
  var detectBrowser = FW.speech.detectBrowser;
  var stopSpeak = FW.speech.stopSpeak;

  /* —— 依赖 view 层 —— */
  var renderHome = FW.view.renderHome;
  var reviewTypeLabel = FW.view.reviewTypeLabel;
  var runDiag = FW.view.runDiag;

  /* —— 依赖 controller 层 —— */
  var bind = FW.controller.bind;
  var resetClock = FW.controller.resetClock;
  var startReview = FW.controller.startReview;
  var tick = FW.controller.tick;


function boot() {
  const restored = loadState();
  initWords(false);

  // 上次是「回顾中途」被切后台杀掉 / 关闭 / 刷新 —— ui.review 不持久化，
  // 但 state.activeReview 里有类型和进度，能原样重建（回顾停表，词表可重算）。
  // 只有重建不出来（词窗空了、进度越界）才结算跳过，否则用户会莫名其妙被踢回首页。
  let aborted = null, resumed = null;
  const pending = normalizeActiveReview(state.activeReview);
  if (pending) {
    const list = reviewEntries(pending.type);
    // 用户已经把「回顾机制」关掉了 → 不再拿旧回顾拦人，静默作废
    if (!state.settings.reviewEnabled) {
      settleReview(pending.type);
      state.activeReview = null;
    } else if (list.length && pending.idx < list.length) {
      resumed = pending;
    } else {
      settleReview(pending.type);
      state.activeReview = null;
      aborted = pending.type;
    }
  }

  state.phase = 'idle';   // boot 落在首页，先停表（loadState 已置 idle，这里兜底）

  bind();
  renderHome();
  resetClock();

  if (resumed) {
    // 直接回到回顾页第 resumed.idx 个词，不经过 pickTrigger（否则会从 0 重来）
    startReview(resumed.type, resumed.idx);
    toast('已回到上次的回顾：' + reviewTypeLabel(resumed.type) +
      '　第 ' + (resumed.idx + 1) + ' 个');
  }

  saveSoon();   // 首次启动也落一次盘，保证状态可恢复

  setInterval(tick, TICK_MS);
  startAutoSave();

  window.addEventListener('pagehide', function () { saveNow(); stopSpeak(); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { saveNow(); stopSpeak(); } });

  if (aborted) {
    toast('上次的回顾词表已失效，已跳过（' + reviewTypeLabel(aborted) + '）');
  } else if (!resumed && restored) {
    const done = Object.keys(state.groupDone).length;
    toast('已恢复上次进度：第 ' + (state.groupIndex + 1) + ' 组 · 净学习 ' + fmt(state.netStudyMs) +
      (done ? ' · 已完成 ' + done + ' 组' : ''));
  }
}

boot();

/* 测试钩子：IIFE 内部的函数在 Node 测试桩里拿不到，这里开一个口子。
 * 只挂纯函数，不碰任何状态。 */
if (typeof window !== 'undefined') {
  window.__fwords = { detectBrowser: detectBrowser, runDiag: runDiag };
}

  FW.app = {
    boot: boot,
  };
})(window.FW = window.FW || {});
