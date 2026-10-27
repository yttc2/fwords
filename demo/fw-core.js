/* Fwords · core —— 常量、存储、状态容器、通用工具。
 * 最底层，不依赖任何其它层。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';


/* ==================== 常量 ==================== */
const STORAGE_KEY = 'fwords.state.v1';
const GROUP_COUNT = 10;        // 词库切成 10 份
const T1_WORDS    = 6;         // T1：每 6 个单词
const T2_MS       = 5 * 60e3;  // T2：每 5 分钟
const T3_MS       = 30 * 60e3; // T3：每 30 分钟
const TICK_MS     = 200;
const IDLE_GAP_MS = 700;       // 单次 tick 间隔超过此值 → 判定为切后台/卡顿，不计入
const SWIPE_PX    = 60;        // 滑动判定阈值
const CLICK_PX    = 8;         // 小于此位移视为「点击」
const LOG_CAP     = 2000;

/* ==================== 存储（file:// 下不可用时降级为内存） ==================== */
const store = (function () {
  try {
    const k = '__fwords_probe__';
    localStorage.setItem(k, '1');
    localStorage.removeItem(k);
    return localStorage;
  } catch (e) {
    const mem = {};
    return {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
      setItem: function (k, v) { mem[k] = String(v); },
      removeItem: function (k) { delete mem[k]; }
    };
  }
})();

/* ==================== 状态 ==================== */
function defaultState() {
  return {
    version: 1,
    listName: '',
    rawText: '',
    words: [],
    libKey: '',           // 当前词库 id（如 'kaoyan' / '__user__'）；空则用第一个可用词库
    libOffsets: {},       // libKey -> 已从该词库取走的词数 → 下次「接着往下取一批」
    sections: [],         // [{ label, count }] 词表自带的分节（如十天词表的 Day 1..10）
    groupMode: 'auto',    // auto 均分十组 | sections 按分节 | single 整份作为一组
    groupIndex: 0,
    cursors: {},          // 每个分组的游标
    netStudyMs: 0,        // 累计净学习时长（唯一时间轴）
    phase: 'idle',        // idle | learning | reviewing
    t1Seen: 0,            // 自上次 T1 结算以来「又看了几个词」（重背旧词也算）—— T1 的唯一判据
    lastT2AtNetMs: 0,
    lastT3AtNetMs: 0,
    activeReview: null,   // 进行中的回顾 { type:'T1'|'T2'|'T3', idx }；重开页面据此原样恢复（旧存档是裸字符串，靠 normalizeActiveReview 兼容）
    learnedLog: [],       // [{ id, group, atNetMs }] 按净学习时长打点
    progress: {},         // id -> { study, review, hard }
    groupDone: {},        // idx -> true
    settings: {
      autoPlay: false,
      timeScale: 1,
      reviewEnabled: true,
      ttsSource: 'human',    // human 在线真人录音 | system 系统 TTS
      voiceAccent: 'us'      // us 美音 | uk 英音
    },
    savedAt: 0
  };
}

let state = defaultState();
let groups = [];
let ui = { review: null, resultMode: 'review', busy: false };

function fmt(ms) {
  ms = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(ms / 60), s = ms % 60;
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

/* 回顾这一段用了多久（真实耗时，不受时间加速影响） */
function fmtReviewSpan(r) {
  const sec = Math.max(0, Math.round((performance.now() - (r.t0 || performance.now())) / 1000));
  if (sec < 60) return sec + ' 秒';
  return Math.floor(sec / 60) + ' 分 ' + (sec % 60) + ' 秒';
}

/* 应用级提示 —— 这里只是个「通知通道」。
 * core 负责把消息送出去，具体怎么显示由 view 注册（setNotifier）。
 * 这样 model 层调 toast 时完全不碰 DOM，分层才是真的。 */
let notifier = null;
function setNotifier(fn) { notifier = fn; }
function toast(msg) { if (notifier) notifier(msg); }

/* ---- 状态容器的原地替换 ----
 * state / groups 被几乎所有层引用，别名要想一直有效，对象身份就不能变 ——
 * 所以重置时「清空再填」而不是整个换新对象。 */
function replaceState(next) {
  Object.keys(state).forEach(function (k) { delete state[k]; });
  Object.keys(next).forEach(function (k) { state[k] = next[k]; });
}

function replaceGroups(next) {
  groups.length = 0;
  Array.prototype.push.apply(groups, next || []);
}

/* activeReview 归一化成 { type, idx }。
 * 旧存档里是裸字符串 'T3'（只记类型、没有进度），当成第 0 个继续。
 * 不用改数据结构就能向前兼容 —— 读旧盘不会炸。 */
const REVIEW_TYPES = ['T1', 'T2', 'T3'];
function normalizeActiveReview(v) {
  if (!v) return null;
  if (typeof v === 'string') {
    return REVIEW_TYPES.indexOf(v) >= 0 ? { type: v, idx: 0 } : null;
  }
  if (typeof v === 'object' && REVIEW_TYPES.indexOf(v.type) >= 0) {
    return { type: v.type, idx: Math.max(0, Math.floor(Number(v.idx) || 0)) };
  }
  return null;
}

  FW.core = {
    CLICK_PX: CLICK_PX,
    GROUP_COUNT: GROUP_COUNT,
    IDLE_GAP_MS: IDLE_GAP_MS,
    LOG_CAP: LOG_CAP,
    STORAGE_KEY: STORAGE_KEY,
    SWIPE_PX: SWIPE_PX,
    T1_WORDS: T1_WORDS,
    T2_MS: T2_MS,
    T3_MS: T3_MS,
    TICK_MS: TICK_MS,
    defaultState: defaultState,
    fmt: fmt,
    fmtReviewSpan: fmtReviewSpan,
    groups: groups,
    normalizeActiveReview: normalizeActiveReview,
    replaceGroups: replaceGroups,
    replaceState: replaceState,
    setNotifier: setNotifier,
    state: state,
    store: store,
    toast: toast,
    ui: ui,
  };
})(window.FW = window.FW || {});
