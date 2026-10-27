/* Fwords · model —— 词表解析与分组、词库注册表、持久化、回顾调度。
 * **业务规则都在这里，不碰 DOM**（要提示用户就调 toast → core 的通知通道）。
 * 这一层是最容易原样搬去 Android 端的（Kotlin 只需要重写存储与 UI 绑定）。
 *
 * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。
 * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。
 */
;(function (FW) {
  'use strict';

  /* ========== 依赖注入（别名）========== */
  /* —— 依赖 core 层 —— */
  var GROUP_COUNT = FW.core.GROUP_COUNT;
  var LOG_CAP = FW.core.LOG_CAP;
  var STORAGE_KEY = FW.core.STORAGE_KEY;
  var T1_WORDS = FW.core.T1_WORDS;
  var T2_MS = FW.core.T2_MS;
  var T3_MS = FW.core.T3_MS;
  var defaultState = FW.core.defaultState;
  var groups = FW.core.groups;
  var replaceGroups = FW.core.replaceGroups;
  var replaceState = FW.core.replaceState;
  var state = FW.core.state;
  var store = FW.core.store;
  var toast = FW.core.toast;


/* ==================== 词库解析与分组 ==================== */

/* 把一个词条行拆成 { text, phonetic, meaning }
 * 依次尝试 | / Tab / 逗号 分隔；都没有则按「首个空白」切分 */
function unquote(t) {
  if (t.length >= 2) {
    const a = t.charAt(0), b = t.charAt(t.length - 1);
    if ((a === '"' && b === '"') || (a === "'" && b === "'")) return t.slice(1, -1).trim();
  }
  return t;
}

/* 第二列到底是音标还是释义：音标一定以 / 或 [ 开头，或含 IPA 专用字符 */
function isPhonetic(t) {
  if (!t) return false;
  if (/^[/\[]/.test(t)) return true;
  return /[ˈˌəɪʊæɑɔɛɜθðʃʒŋɒʌ]/.test(t) && !/[\u4e00-\u9fa5]/.test(t);
}

function parseLine(s, idx) {
  let parts = null;
  if (s.indexOf('|') >= 0) parts = s.split('|');
  else if (s.indexOf('\t') >= 0) parts = s.split('\t');
  else if (s.indexOf(',') >= 0 || s.indexOf('，') >= 0) parts = s.split(/[,，]/);

  let text1 = '', phonetic = '', meaning = '';
  if (parts) {
    parts = parts.map(function (t) { return unquote(t.trim()); });
    text1 = parts[0] || '';
    if (parts.length >= 3) {
      phonetic = parts[1] || '';
      meaning = parts.slice(2).join(' ').trim();
    } else if (parts.length === 2) {
      const second = parts[1] || '';
      if (isPhonetic(second)) phonetic = second; else meaning = second;
    }
  } else {
    const m = s.match(/^(\S+)(?:[\s\u3000]+([\s\S]*))?$/);
    if (!m) return null;
    text1 = m[1] || '';
    meaning = (m[2] || '').trim();
  }
  if (!text1) return null;
  return {
    id: 'w' + String(idx + 1).padStart(3, '0'),
    text: text1,
    phonetic: phonetic,
    meaning: meaning || '（无释义）'
  };
}

/* 分节标记：单独一行的 # Day 1 / ## 第1天 / [Day 1] / === Day 1 === / Day 1 */
function sectionLabel(line) {
  const s = line.trim();
  let m;
  if ((m = s.match(/^#{1,6}\s*(.+?)\s*$/))) return m[1];
  if ((m = s.match(/^\[(.+?)\]$/))) return m[1];
  if ((m = s.match(/^={2,}\s*(.+?)\s*={2,}$/))) return m[1];
  if ((m = s.match(/^-{2,}\s*(.+?)\s*-{2,}$/))) return m[1];
  if (/^(?:day|unit|list|part|week|lesson)\s*0*\d+$/i.test(s)) return s;
  if (/^第\s*0*\d+\s*[天单元组课]$/.test(s)) return s;
  return null;
}

/* 解析整份词表 → { words, sections }
 * sections 只在「确实分出了 ≥2 节」时才算数，避免把普通注释行误判成节 */
function parseList(text) {
  const words = [];
  const secs = [];
  let cur = null, preamble = 0;

  String(text || '').split(/\r?\n/).forEach(function (line) {
    const s = line.trim();
    if (!s || s.indexOf('//') === 0) return;

    const lab = sectionLabel(s);
    if (lab !== null) { cur = { label: lab, count: 0 }; secs.push(cur); return; }

    const w = parseLine(s, words.length);
    if (!w) return;
    words.push(w);
    if (cur) cur.count++; else preamble++;
  });

  let real = secs.filter(function (x) { return x.count > 0; });
  if (real.length >= 2) {
    if (preamble > 0) real = [{ label: '（开头）', count: preamble }].concat(real);
    return { words: words, sections: real };
  }
  return { words: words, sections: [] };
}

function parseWords(text) { return parseList(text).words; }

/* 分组：优先按词表自带的「天 / 单元」分节；没有才均分 10 组
 * 分节必须能完整覆盖全部词，否则退回均分（防止解析出的节是残缺的） */
let groupLabels = [];

function buildGroups() {
  const words = state.words || [];
  const total = words.length;
  groupLabels.length = 0;   // 原地清空：别层抓到的是同一个数组
  if (!total) return [];

  const secs = (state.sections || []).filter(function (x) { return x && x.count > 0; });
  let sum = 0;
  secs.forEach(function (x) { sum += x.count; });

  const bySections = secs.length && sum === total &&
    (state.groupMode === 'sections' || (state.groupMode !== 'single' && secs.length >= 2));
  const asOne = secs.length && sum === total && state.groupMode === 'single';

  if (bySections || asOne) {
    const out = [];
    let p = 0;
    secs.forEach(function (x) {
      out.push(words.slice(p, p + x.count));
      groupLabels.push(x.label);
      p += x.count;
    });
    return out;
  }

  if (total < GROUP_COUNT) return words.map(function (w) { return [w]; });
  const base = Math.floor(total / GROUP_COUNT);
  const rem = total % GROUP_COUNT;
  const out = [];
  let p = 0;
  for (let i = 0; i < GROUP_COUNT; i++) {
    const n = base + (i < rem ? 1 : 0);
    out.push(words.slice(p, p + n));
    p += n;
  }
  return out;
}

/* 用一份词表替换当前学习内容（内置词库、自定义词表、文件导入共用）
 * opts.groupMode: 'auto' 均分十组 | 'sections' 按分节 | 'single' 整份作为一组 */
function applyWordList(text, name, libKey, libOffsetTo, opts) {
  const parsed = parseList(text);
  const words = parsed.words;
  if (words.length < 2) { toast('至少需要 2 个单词'); return false; }

  const o = opts || {};
  state.words = words;
  state.listName = name;
  state.sections = o.sections || parsed.sections;
  state.groupMode = o.groupMode || 'auto';
  if (libKey) state.libKey = libKey;
  if (libKey && typeof libOffsetTo === 'number') {
    if (!state.libOffsets) state.libOffsets = {};
    state.libOffsets[libKey] = libOffsetTo;
  }
  // 原文只在不大时留着（回填输入框用）；整本词库可达上百 KB，不进存档
  state.rawText = (libKey || text.length > 40000) ? '' : text;

  state.groupIndex = 0;
  state.cursors = {};
  state.groupDone = {};
  state.learnedLog = [];
  state.netStudyMs = 0;
  state.t1Seen = 0;
  state.lastT2AtNetMs = 0;
  state.lastT3AtNetMs = 0;
  state.progress = {};
  state.phase = 'learning';
  replaceGroups(buildGroups());
  saveNow();
  return true;
}

/* ==================== 词库注册表 ====================
 * 词库本体（几百 KB）与「学习状态」分开存放：
 * 学习状态每秒都会存档，若把整份词库塞进去，每次存档都要重新序列化一遍。
 * 内置词库来自 wordlists.js / wordlists.user.js；
 * 用户导入的词库存在独立存储键里，同样支持「分批取词」。 */
const LIB_KEY = 'kaoyan';
const USER_LIB_KEY = 'fwords.lib.user.v1';

function loadUserLib() {
  try {
    const raw = store.getItem(USER_LIB_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw);
    if (!o || !Array.isArray(o.raw) || !o.raw.length) return null;
    return o;
  } catch (e) { return null; }
}

function saveUserLib(name, lines, sections) {
  const lib = { name: name, count: lines.length, raw: lines, sections: sections || [] };
  try {
    store.setItem(USER_LIB_KEY, JSON.stringify(lib));
    return true;
  } catch (e) {
    toast('词库太大，本地存不下（已保留本次学习内容）');
    return false;
  }
}

function libRegistry() {
  const out = [];
  const built = window.WORD_LISTS || {};
  Object.keys(built).forEach(function (k) {
    const l = built[k];
    if (!l || !Array.isArray(l.raw) || !l.raw.length) return;
    out.push({
      key: k, name: l.name || k, count: l.count || l.raw.length,
      raw: l.raw, sections: l.sections || [],
      // wordlists.user.js 导入的词库注册进的也是 window.WORD_LISTS，
      // 光看对象跟内置词库没区别 —— 靠 imported 标记把来源分开
      // （内置词库没有这个字段，所以默认还是「内置」）。
      source: l.imported ? '导入' : '内置'
    });
  });
  const u = loadUserLib();
  if (u) {
    out.push({
      key: '__user__', name: u.name || '我的词库', count: u.count || u.raw.length,
      raw: u.raw, sections: u.sections || [], source: '导入'
    });
  }
  return out;
}

function libData() {
  const all = libRegistry();
  if (!all.length) return null;
  let hit = null;
  all.forEach(function (l) { if (l.key === state.libKey) hit = l; });
  if (!hit) {
    hit = all[0];
    if (state.libKey !== hit.key) { state.libKey = hit.key; saveSoon(); }
  }
  return hit;
}

function libOffset(key) {
  return (state.libOffsets && state.libOffsets[key]) || 0;
}

/* JSON 词表 → 行文本；兼容 [{word,phonetic,meaning}] / [{word,translations:[{pos,translation}]}] 等常见结构 */
function jsonToList(text) {
  let d;
  try { d = JSON.parse(text); } catch (e) { return null; }
  const arr = Array.isArray(d) ? d : (d && Array.isArray(d.words) ? d.words : null);
  if (!arr) return null;

  const out = [];
  arr.forEach(function (it) {
    if (typeof it === 'string') { if (it.trim()) out.push(it.trim()); return; }
    if (!it || typeof it !== 'object') return;
    const w = it.word || it.text || it.en || it.name || it.spelling || '';
    if (!w) return;
    const ph = it.phonetic || it.usphone || it.phone || it.us || it.uk || it.pron || '';
    let mn = it.meaning || it.translation || it.cn || it.def || it.interpretation || '';
    if (!mn && Array.isArray(it.translations)) {
      mn = it.translations.map(function (t) {
        if (typeof t === 'string') return t;
        return (t.pos ? t.pos + ' ' : '') + (t.translation || t.tran || '');
      }).join(' / ');
    }
    if (Array.isArray(mn)) mn = mn.join(' / ');
    if (mn && typeof mn === 'object') mn = '';
    out.push(ph ? (w + '|' + ph + '|' + mn) : (w + '|' + mn));
  });
  return out.length ? out.join('\n') : null;
}

/* 导入一份词表：存成可复用的「我的词库」，并立即开始学习 */
function importFromText(rawText, baseName) {
  let text = String(rawText || '').trim();
  if (!text) { toast('内容为空'); return ''; }

  if (text.charAt(0) === '[' || text.charAt(0) === '{') {
    const conv = jsonToList(text);
    if (conv === null) { toast('JSON 解析失败：需要是词条数组'); return ''; }
    text = conv;
  }

  const parsed = parseList(text);
  const words = parsed.words;
  if (words.length < 2) { toast('至少需要 2 个单词'); return ''; }

  const name = (baseName || '我的词表') + ' · ' + words.length + ' 词';
  const lines = words.map(function (w) {
    return w.phonetic ? (w.text + '|' + w.phonetic + '|' + w.meaning) : (w.text + '|' + w.meaning);
  });

  const prev = loadUserLib();
  if (prev && prev.count > words.length * 2) {
    if (!confirm('已有导入词库「' + prev.name + '」（' + prev.count + ' 词）。\n' +
        '这次只有 ' + words.length + ' 词，导入会覆盖它。继续？')) return '';
  }
  saveUserLib(name, lines, parsed.sections);

  const mode = parsed.sections.length >= 2 ? 'sections' : 'auto';
  applyWordList(text, name, '__user__', 0, { sections: parsed.sections, groupMode: mode });

  const extra = parsed.sections.length >= 2 ? ('，按原有 ' + parsed.sections.length + ' 个分节分组') : '';
  return '已导入 ' + words.length + ' 词，分成 ' + groups.length + ' 组' + extra;
}

/* 分节必须能完整覆盖词库，否则视为不可信，退回均分十组 */
function validSections(lib) {
  if (!lib || !lib.raw) return [];
  const secs = (lib.sections || []).filter(function (x) { return x && x.count > 0; });
  let sum = 0;
  secs.forEach(function (x) { sum += x.count; });
  return (secs.length && sum === lib.raw.length) ? secs : [];
}

function curGroup() { return groups[state.groupIndex] || []; }
function curCursor() { return state.cursors[state.groupIndex] || 0; }
function setCursor(v) { state.cursors[state.groupIndex] = v; }

function learnedInGroup(idx) {
  const seen = {};
  let n = 0;
  state.learnedLog.forEach(function (e) {
    if (e.group === idx && !seen[e.id]) { seen[e.id] = 1; n++; }
  });
  return n;
}

/* ==================== 持久化 ==================== */
let savePending = false;
function saveSoon() { savePending = true; }

function saveNow() {
  state.savedAt = Date.now();
  if (state.learnedLog.length > LOG_CAP) {
    state.learnedLog = state.learnedLog.slice(-LOG_CAP);
  }
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(state));
    savePending = false;
  } catch (e) {
    savePending = false;
  }
}

function loadState() {
  const raw = store.getItem(STORAGE_KEY);
  if (!raw) return false;
  try {
    const obj = JSON.parse(raw);
    if (!obj || !Array.isArray(obj.words) || !obj.words.length) return false;
    const base = defaultState();
    replaceState(Object.assign({}, base, obj));
    state.settings = Object.assign({}, base.settings, obj.settings || {});
    state.cursors = obj.cursors || {};
    state.progress = obj.progress || {};
    state.groupDone = obj.groupDone || {};
    state.learnedLog = Array.isArray(obj.learnedLog) ? obj.learnedLog : [];
    state.sections = Array.isArray(obj.sections) ? obj.sections : [];
    if (!state.groupMode) state.groupMode = 'auto';
    if (!state.libOffsets || typeof state.libOffsets !== 'object') state.libOffsets = {};
    // 旧版存档迁移：sourceKey/sourceOffset → libKey/libOffsets
    if (obj.sourceKey && !obj.libKey) state.libKey = obj.sourceKey;
    if (obj.sourceOffset && !state.libOffsets[state.libKey]) {
      state.libOffsets[state.libKey] = obj.sourceOffset;
    }
    delete state.sourceKey;
    delete state.sourceOffset;
    // 重开页面一定落在首页 —— 先停表，点「继续背诵」才恢复计时。
    // （以前这里把 reviewing 恢复成 learning，结果在首页干坐着也在累加时长。）
    // 未做完的回顾由 app.boot 按 state.activeReview 重建（词表可重算，位置也记着）
    state.phase = 'idle';
    return true;
  } catch (e) {
    return false;
  }
}

/* ==================== 回顾触发判定 ==================== */
function pickTrigger() {
  if (!state.settings.reviewEnabled) return null;
  if (state.netStudyMs - state.lastT3AtNetMs >= T3_MS) return 'T3';
  if (state.netStudyMs - state.lastT2AtNetMs >= T2_MS) return 'T2';
  if (state.t1Seen >= T1_WORDS) return 'T1';
  return null;
}

function reviewEntries(type) {
  if (type === 'T1') return state.learnedLog.slice(-T1_WORDS);
  const win = (type === 'T2') ? T2_MS : T3_MS;
  const from = state.netStudyMs - win;
  return state.learnedLog.filter(function (e) { return e.atNetMs >= from; });
}

function byId(id) {
  for (let i = 0; i < state.words.length; i++) if (state.words[i].id === id) return state.words[i];
  return null;
}

/* 记一个词「看过了」。
 *
 * 两个计数器口径不同，别混：
 *   learnedLog —— 学过的**不同词**，每词一条（去重键 id + 分组），用来算覆盖率和 T2/T3 窗口；
 *   t1Seen     —— 自上次 T1 以来**又看了几个**（重背旧词也算），T1 的唯一判据。
 *
 * 为什么 T1 不能拿 learnedLog 的长度当判据（踩过）：
 *   重背已经学过的组时，每个词都在 learnedLog 里 → 长度一动不动 → 「每 6 个词回顾一次」
 *   彻底哑掉，一路学下去一次都不弹。老板 2026-10-27 报的就是这个。
 *
 * 为什么重背时要把条目挪到末尾：
 *   T1 回顾取的是 learnedLog 最近 6 条。不挪的话，重背第 1 个词时取到的还是很久以前
 *   那批词，复习的完全不是刚看的东西。
 */
function markLearned(word) {
  const g = state.groupIndex;
  const p = state.progress[word.id] || (state.progress[word.id] = { study: 0, review: 0, hard: 0 });
  p.study++;
  state.t1Seen++;

  for (let i = 0; i < state.learnedLog.length; i++) {
    const e = state.learnedLog[i];
    if (e.id === word.id && e.group === g) {
      state.learnedLog.splice(i, 1);
      e.atNetMs = state.netStudyMs;
      state.learnedLog.push(e);
      saveSoon();
      return false;     // 不是新词（已学过），但确实又看了一遍
    }
  }
  state.learnedLog.push({ id: word.id, group: g, atNetMs: state.netStudyMs });
  saveSoon();
  return true;
}

/* ==================== 回顾页 ==================== */

/* 回顾计数器复位。一次回顾（做完 / 中断 / 窗口为空顺延）都必须推进对应计数器，
 * 否则同一次回顾会反复弹。
 *
 * 为什么 T2、T3 要连同 T1 的计数一起清零：
 *   三种回顾复习的是**同一批词的不同切法** —— T1 取最近 6 个词，T2 取最近 5 分钟的词。
 *   慢节奏下（比如 60 秒/词）T2 在第 5 个词就到点，复习掉第 1~5 个词；此时 T1 的
 *   「满 6 词」只差 1 个 → 切下一个词立刻弹 T1，复习的还是刚看过的那批词。
 *   所以 T2/T3 完成后要把 T1 的计数归零：「这些词刚复习过，重新起算」。
 *   这和「T3 完成后连 T2 一起复位」是同一条道理，只是方向反过来。
 */
function settleReview(type) {
  if (type === 'T1') {
    state.t1Seen = 0;
  } else if (type === 'T2') {
    state.lastT2AtNetMs = state.netStudyMs;
    state.t1Seen = 0;
  } else if (type === 'T3') {
    state.lastT3AtNetMs = state.netStudyMs;
    state.lastT2AtNetMs = state.netStudyMs;
    state.t1Seen = 0;
  }
}

/* ==================== 初始化 ==================== */
function initWords(force) {
  if (force || !state.words.length) {
    const builtin = window.BUILTIN_WORDLIST;
    state.words = parseWords(builtin.raw.join('\n'));
    state.rawText = builtin.raw.join('\n');
    state.listName = builtin.name;
    state.sections = [];
    state.groupMode = 'auto';
  }
  replaceGroups(buildGroups());
}

/* 每秒落一次盘，只在有改动（savePending）时真正写。
 * 放在 model 内部：savePending 是本层状态，别层读到的只是别名快照。 */
function startAutoSave() {
  setInterval(function () { if (savePending) saveNow(); }, 1000);
}

  FW.model = {
    LIB_KEY: LIB_KEY,
    USER_LIB_KEY: USER_LIB_KEY,
    applyWordList: applyWordList,
    buildGroups: buildGroups,
    byId: byId,
    curCursor: curCursor,
    curGroup: curGroup,
    groupLabels: groupLabels,
    importFromText: importFromText,
    initWords: initWords,
    isPhonetic: isPhonetic,
    jsonToList: jsonToList,
    learnedInGroup: learnedInGroup,
    libData: libData,
    libOffset: libOffset,
    libRegistry: libRegistry,
    loadState: loadState,
    loadUserLib: loadUserLib,
    markLearned: markLearned,
    parseLine: parseLine,
    parseList: parseList,
    parseWords: parseWords,
    pickTrigger: pickTrigger,
    reviewEntries: reviewEntries,
    saveNow: saveNow,
    savePending: savePending,
    saveSoon: saveSoon,
    saveUserLib: saveUserLib,
    sectionLabel: sectionLabel,
    setCursor: setCursor,
    settleReview: settleReview,
    startAutoSave: startAutoSave,
    unquote: unquote,
    validSections: validSections,
  };
})(window.FW = window.FW || {});
