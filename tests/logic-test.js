/* 核心机制自测：用最小 DOM 桩在 Node 中直接运行 demo/app.js，驱动状态机并断言。
 * 运行：node tests/logic-test.js
 */
const fs = require('fs');
const APP_FILES = require('./app-files.js');
const path = require('path');

const DEMO = path.join(__dirname, '..', 'demo');

/* ---------------- 最小 DOM 桩 ---------------- */
function El(id) {
  const cls = new Set();
  const listeners = {};
  const el = {
    id, style: {}, dataset: {}, textContent: '', value: '',
    children: [],
    appendChild(c) { el.children.push(c); },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
    removeEventListener() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    scrollTo() {},
    closest() { return null; }
  };
  el.classList = {
    add: c => cls.add(c),
    remove: c => cls.delete(c),
    contains: c => cls.has(c),
    toggle: (c, on) => { if (on === undefined) { cls.has(c) ? cls.delete(c) : cls.add(c); } else if (on) cls.add(c); else cls.delete(c); }
  };
  el.__cls = cls;
  el.__listeners = listeners;
  Object.defineProperty(el, 'innerHTML', {
    get() { return el.__html || ''; },
    set(v) { el.__html = String(v); if (!v) el.children.length = 0; }
  });
  return el;
}

let els = {};
function getEl(id) { return els[id] || (els[id] = El(id)); }

let elSeq = 0;
const documentStub = {
  getElementById: getEl,
  createElement: tag => El('__el' + (++elSeq) + '_' + tag),
  querySelectorAll: () => [],
  addEventListener() {},
  hidden: false
};

let clock = 0;
let intervals = [];
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
global.setTimeout = () => 0;
global.clearTimeout = () => {};
let confirmAnswer = false;
global.confirm = () => confirmAnswer;
global.scrollTo = () => {};
global.window.scrollTo = () => {};
global.window.addEventListener = () => {};

/* ---------------- 加载被测代码 ---------------- */
const load = f => (0, eval)(fs.readFileSync(path.join(DEMO, f), 'utf8'));
load('words.js');
load('wordlists.js');

let tickFn, saveFn;
function wireIntervals() {
  tickFn = intervals.filter(i => i.ms === 200).pop().fn;
  saveFn = intervals.filter(i => i.ms === 1000).pop().fn;
}
function bootApp() {
  intervals = []; clock = 0;
  APP_FILES.forEach(load);
  wireIntervals();
  saveFn();                      // boot() 里已置 savePending，这里手动落一次盘
}
bootApp();

/* ---------------- 驱动工具 ---------------- */
function advanceClock(ms) { clock += ms; }
function flowTime(ms) {                    // 让时间「正常流动」（每步都小于空闲阈值 → 计入）
  for (let t = 0; t < ms; t += 200) { advanceClock(200); tickFn(); }
  saveFn();
}
function fire(el, type, ev) {
  (el.__listeners[type] || []).forEach(f => f(Object.assign({ target: { closest: () => null } }, ev || {})));
}
function click(el) {
  fire(el, 'pointerdown', { clientX: 100, clientY: 100, pointerId: 1 });
  fire(el, 'pointerup', { clientX: 100, clientY: 100, pointerId: 1 });
}
function press(id) { getEl(id).__listeners.click[0](); }
function isActive(id) { return getEl(id).classList.contains('active'); }
function reviewTotal() { return Number(getEl('rcardCounter').textContent.split('/')[1].trim()); }
function saved() { return JSON.parse(storageMap['fwords.state.v1']); }
function selectLib(key) {
  getEl('libSelect').value = key;
  fire(getEl('libSelect'), 'change');
}
/* 模拟滑动手势：dir = -1 左滑、+1 右滑 */
function swipe(el, dir) {
  const x = 200 + dir * 100;
  fire(el, 'pointerdown', { clientX: 200, clientY: 100, pointerId: 1 });
  fire(el, 'pointermove', { clientX: x, clientY: 100, pointerId: 1 });
  fire(el, 'pointerup', { clientX: x, clientY: 100, pointerId: 1 });
}
/* 造一份「十天搞定」形态的词表：days × per 词，按天分节 */
function tenDays(days, per) {
  const lines = [];
  for (let d = 1; d <= days; d++) {
    lines.push('# Day ' + d);
    for (let i = 1; i <= per; i++) lines.push('d' + d + 'w' + i + '|/test/|第' + d + '天第' + i + '词');
  }
  return lines.join('\n');
}
function fmtMs(ms) {
  ms = Math.max(0, Math.floor(ms / 1000));
  return String(Math.floor(ms / 60)).padStart(2, '0') + ':' + String(ms % 60).padStart(2, '0');
}
/* 把待做的回顾全部做完，直到回到学习页 */
function settle() {
  let guard = 0;
  while (guard++ < 30) {
    if (isActive('view-review')) {
      const n = reviewTotal();
      for (let i = 0; i < (n || 0); i++) press('btnNextR');
    } else if (isActive('view-result')) {
      press('btnResContinue');
    } else { break; }
  }
  return guard < 30;
}

/* ---------------- 断言 ---------------- */
let pass = 0, fail = 0;
function ok(cond, label, extra) {
  if (cond) { pass++; console.log('  PASS  ' + label); }
  else { fail++; console.log('  FAIL  ' + label + (extra ? '  → ' + extra : '')); }
}
function eq(a, b, label) { ok(a === b, label, 'got ' + JSON.stringify(a) + ', want ' + JSON.stringify(b)); }

/* ================= 用例 ================= */
console.log('\n=== Fwords 核心机制自测 ===');

console.log('\n[1] 词库解析与分组');
{
  const st = saved();
  eq(st.words.length, 120, '内置词库 120 词');
  eq(st.words[0].text, 'abandon', '单词解析正确');
  eq(st.words[0].phonetic, '/əˈbændən/', '音标解析正确');
  eq(st.words[0].meaning, 'vt. 放弃，抛弃', '释义解析正确');
  eq(st.words.length / 10, 12, '每组 12 词');
}

console.log('\n[2] 连续看完 6 个词 → 软触发 T1');
{
  press('btnContinue');
  ok(isActive('view-study'), '进入学习页');
  eq(getEl('cardWord').textContent, 'abandon', '首词正确');

  for (let i = 0; i < 5; i++) click(getEl('card'));
  ok(isActive('view-study'), '看完第 5 个词仍在学习页（未提前打断）');
  eq(getEl('cardWord').textContent, 'accept', '第 6 个词正确');

  click(getEl('card'));
  ok(isActive('view-review'), '看完第 6 个词 → 自动进入回顾页');
  eq(getEl('reviewTag').textContent, 'T1 · 每 6 个单词', '回顾类型 = T1');
  eq(reviewTotal(), 6, 'T1 回顾正好 6 个词');
  eq(getEl('rcardWord').textContent, 'abandon', 'T1 从最早学的词开始');
  eq(getEl('rcardMeaning').textContent, '点击卡片显示释义', '回顾模式释义默认隐藏');
}

console.log('\n[3] 回顾期间计时暂停');
{
  const before = saved().netStudyMs;
  flowTime(60000);
  eq(saved().netStudyMs, before, '回顾中推进 60 秒，净学习时长不变');
  ok(isActive('view-review'), '仍停留在回顾页');
}

console.log('\n[4] 回顾结束 → 计时恢复');
{
  click(getEl('rcard'));
  ok(getEl('rcardMeaning').textContent.indexOf('放弃') >= 0, '点击卡片翻出释义');

  for (let i = 0; i < 6; i++) press('btnNextR');
  ok(isActive('view-result'), '回顾完成进入结果页');
  eq(Number(getEl('resValue').textContent), 6, '结果页显示 6 个词');

  press('btnResContinue');
  ok(isActive('view-study'), '点击继续回到学习页');

  const before = saved().netStudyMs;
  flowTime(10000);
  ok(saved().netStudyMs > before, '回顾结束后计时恢复累加');
}

let t2WindowCount = 0;
console.log('\n[5] 以 60 秒/词的节奏学习 → 5 分钟节点触发 T2，且只覆盖 5 分钟窗口');
{
  let guard = 0;
  while (!isActive('view-review') && guard++ < 20) { flowTime(60000); click(getEl('card')); }
  ok(isActive('view-review'), '到达 5 分钟节点后切词 → 进入回顾页');
  eq(getEl('reviewTag').textContent, 'T2 · 每 5 分钟', '回顾类型 = T2');
  eq(getEl('rcardWord').textContent, 'access', 'T2 排除了已滑出 5 分钟窗口的前 6 个词');
  t2WindowCount = reviewTotal();
  eq(t2WindowCount, 5, 'T2 窗口内为第 7~11 个词');
}

console.log('\n[6] 切后台不计入净学习时长');
{
  ok(settle(), '先结算全部待做回顾，回到学习页');
  ok(isActive('view-study'), '已回到学习页');
  const before = saved().netStudyMs;
  advanceClock(600000);          // 模拟切后台 10 分钟
  tickFn(); saveFn();
  eq(saved().netStudyMs, before, '一次 10 分钟的空档未被计入');
}

console.log('\n[7] 持续学习满 30 分钟 → 触发 T3，覆盖范围大于 T2');
{
  /* 循环要同时处理「回顾页」和「结果页」：
   * 组学完会停在结果页，不点「继续」就永远学不到下一个词。
   * （早期版本只处理回顾页，靠一次偶然触发的 T1 把自己带出来 —— 那是巧合，不是逻辑。） */
  let clicks = 0, hit = false;
  while (clicks < 300) {
    if (isActive('view-review')) {
      if (/^T3/.test(getEl('reviewTag').textContent)) { hit = true; break; }
      const n = reviewTotal();
      for (let i = 0; i < n; i++) press('btnNextR');   // 中途到期的 T1 / T2 照常做完
      continue;
    }
    if (isActive('view-result')) { press('btnResContinue'); continue; }
    if (!isActive('view-study')) break;                // 跑到别的页面了，别再空转
    flowTime(150000);
    click(getEl('card'));
    clicks++;
  }
  ok(hit, '持续学习满 30 分钟 → 触发 T3');
  eq(getEl('reviewTag').textContent, 'T3 · 每 30 分钟', '回顾类型 = T3（优先于同时到期的 T2）');
  ok(reviewTotal() > t2WindowCount,
    'T3 覆盖 ' + reviewTotal() + ' 个词，多于 T2 的 ' + t2WindowCount + ' 个（窗口更长）',
    '应大于 ' + t2WindowCount);
  ok(reviewTotal() >= 10, 'T3 窗口内容足够丰富（' + reviewTotal() + ' 个词）');
}

console.log('\n[8] T3 完成后不会立刻重复触发 T2');
{
  for (let i = 0; i < reviewTotal(); i++) press('btnNextR');
  press('btnResContinue');
  const next = isActive('view-review') ? getEl('reviewTag').textContent : '学习页';
  ok(!/^T2/.test(next), 'T3 结束后不会紧跟一次冗余 T2（下一个是：' + next + '）');
  settle();
  ok(isActive('view-study') || isActive('view-result'), '状态正常推进，回到学习/结果页');
}

console.log('\n[9] 断点续背：模拟重新打开页面');
{
  const before = saved();
  const uniq = new Set(before.learnedLog.map(e => e.id)).size;

  els = {};                      // 清空 DOM 桩
  bootApp();                     // 重新加载 app.js → 走 loadState 恢复

  eq(getEl('home-time').textContent, fmtMs(before.netStudyMs), '恢复后的净学习时长一致');
  eq(Number(getEl('home-learned').textContent), uniq, '恢复后的已学词数一致');
  eq(saved().words.length, 120, '词库保持一致');

  press('btnContinue');
  ok(isActive('view-study'), '点「继续背诵」直接回到学习页');
  ok(getEl('cardWord').textContent.length > 0, '当前单词已恢复：' + getEl('cardWord').textContent);
}

console.log('\n[10] 词数不整除 10 的分组（47 词）');
{
  const lines = [];
  for (let i = 1; i <= 47; i++) lines.push('word' + i + '|/test/|释义 ' + i);
  getEl('importText').value = lines.join('\n');
  press('btnImport');

  const grid = getEl('groupGrid');
  eq(grid.children.length, 10, '切成 10 组');
  const sizes = grid.children.map(c => Number(c.children[1].textContent.split('/')[1]));
  eq(sizes.join(','), '5,5,5,5,5,5,5,4,4,4', '每组数量为 7×5 + 3×4');
  eq(sizes.reduce((a, b) => a + b, 0), 47, '词不丢不重');

  press('btnContinue');
  ok(isActive('view-study'), '可正常进入学习页');
}

console.log('\n[11] 内置考研词库：分批载入，不把整个词库塞进学习状态');
{
  const lib = window.WORD_LISTS && window.WORD_LISTS.kaoyan;
  ok(!!lib, 'wordlists.js 提供了 WORD_LISTS.kaoyan');
  eq(lib.count, lib.raw.length, 'count 字段与词条数一致');
  ok(lib.raw.length > 4000, '考研词库共 ' + lib.raw.length + ' 词', '应 > 4000');
  eq(lib.raw.filter(s => s.split('|').length !== 3).length, 0, '所有词条都是三段式：单词|音标|释义');
  // 源数据里有 9 个词缺音标（mercury / jeans / christmas 等），属数据源本身的情况，
  // 不自行编造填补，只保证覆盖率足够高。
  const noPhonetic = lib.raw.filter(s => !s.split('|')[1]).length;
  ok(noPhonetic <= Math.floor(lib.raw.length * 0.01),
    '音标覆盖率 > 99%（源数据缺 ' + noPhonetic + ' 个）',
    '缺失数应 <= ' + Math.floor(lib.raw.length * 0.01));
  eq(lib.raw.filter(s => !s.split('|')[2]).length, 0, '所有词条都有释义');

  els = {};
  bootApp();
  getEl('libCount').value = '60';
  press('btnGoWords');
  ok(isActive('view-words'), '进入词库页');
  ok(getEl('libSelect').children.length >= 1, '词库下拉里有可选词库');
  selectLib('kaoyan');                      // 明确选用内置考研词库
  ok(getEl('libMeta').textContent.indexOf(String(lib.raw.length)) >= 0,
    '词库页显示总词数：' + getEl('libMeta').textContent);

  press('btnLoadLib');
  const st = saved();
  eq(st.libKey, 'kaoyan', '记录了词库来源');
  eq(st.words.length, 60, 'state 只保存本次目标的 60 词（不是整个词库）');
  eq(st.libOffsets.kaoyan, 60, '已取词数推进到 60');
  ok(st.listName.indexOf('第 1–60 词') >= 0, '词库名标注了取词范围：' + st.listName);
  eq(st.rawText, '', '分批载入不保存原文（避免存档无谓翻倍）');

  const storeSize = (storageMap['fwords.state.v1'] || '').length;
  const libSize = lib.raw.join('\n').length;
  ok(storeSize < 200 * 1024,
    '存档 ' + Math.round(storeSize / 1024) + ' KB，远小于词库本身 ' + Math.round(libSize / 1024) + ' KB',
    '应 < 200 KB');

  eq(getEl('groupGrid').children.length, 10, '首页切成 10 组');
  eq(Number(getEl('groupGrid').children[0].children[1].textContent.split('/')[1]), 6, '每组 6 词');

  const batch1First = st.words[0].text;
  press('btnGoWords');
  press('btnLoadLib');
  const st2 = saved();
  eq(st2.libOffsets.kaoyan, 120, '第二批后已取词数 = 120');
  eq(st2.words.length, 60, '第二批仍取 60 词');
  eq(st2.words[0].text, lib.raw[60].split('|')[0], '第二批首词 = 词库第 61 词');
  ok(st2.words[0].text !== batch1First, '第二批与第一批不重复（' + batch1First + ' → ' + st2.words[0].text + '）');

  // 把存档里的 offset 直接推到末尾附近，验证「取完剩余」与「回绕」
  const tail = JSON.parse(storageMap['fwords.state.v1']);
  tail.libOffsets = { kaoyan: lib.raw.length - 10 };
  storageMap['fwords.state.v1'] = JSON.stringify(tail);

  els = {};
  bootApp();
  getEl('libCount').value = '60';
  press('btnGoWords');
  press('btnLoadLib');
  const st3 = saved();
  eq(st3.libOffsets.kaoyan, lib.raw.length, '末尾处只取剩余的 10 词，已取词数到达词库末尾');
  eq(st3.words.length, 10, '最后一批为剩余的 10 词');

  press('btnGoWords');
  press('btnLoadLib');
  const st4 = saved();
  eq(st4.libOffsets.kaoyan, 60, '词库取完后回绕：已取词数重置为 60');
  eq(st4.words[0].text, lib.raw[0].split('|')[0], '回绕后从词库第 1 词开始');
  eq(st4.words.length, 60, '回绕后恢复取 60 词');

  // 载入真实词库后，三重回顾机制照常工作
  press('btnContinue');
  ok(isActive('view-study'), '载入考研词库后可进入学习页');
  ok(getEl('cardWord').textContent.length > 0, '首词显示正常：' + getEl('cardWord').textContent);
  for (let i = 0; i < 6; i++) click(getEl('card'));
  ok(isActive('view-review'), '看满 6 词仍然触发 T1 回顾');
  eq(reviewTotal(), 6, 'T1 回顾 6 个词');
}

console.log('\n[12] 回顾模式：没有「认识 / 不认识」选项，看完即下一个');
{
  delete storageMap['fwords.state.v1'];      // 干净起步
  els = {};
  bootApp();
  press('btnContinue');
  for (let i = 0; i < 6; i++) click(getEl('card'));
  ok(isActive('view-review'), '看满 6 词进入 T1 回顾');
  eq(reviewTotal(), 6, 'T1 覆盖 6 个词');

  ok(!(getEl('btnHard').__listeners.click || []).length, '「不认识」按钮已移除');
  ok(!(getEl('btnEasy').__listeners.click || []).length, '「认识」按钮已移除');
  ok((getEl('btnNextR').__listeners.click || []).length === 1, '只剩一个「下一个」按钮');
  eq(getEl('btnNextR').textContent, '下一个', '非最后一张时按钮文案为「下一个」');

  // 未翻面：点击只翻释义，不推进
  ok(getEl('rcardMeaning').classList.contains('hidden'), '释义默认隐藏');
  click(getEl('rcard'));
  ok(getEl('rcardMeaning').textContent.indexOf('放弃') >= 0, '点击卡片翻出释义');
  eq(getEl('rcardCounter').textContent, '1 / 6', '翻面不推进进度');

  // 已翻面：点击前进
  click(getEl('rcard'));
  eq(getEl('rcardCounter').textContent, '2 / 6', '已翻面后再点击 → 前进到第 2 个词');

  // 右滑回到上一个，左滑前进
  swipe(getEl('rcard'), 1);
  eq(getEl('rcardCounter').textContent, '1 / 6', '右滑 → 上一个');
  swipe(getEl('rcard'), -1);
  eq(getEl('rcardCounter').textContent, '2 / 6', '左滑 → 下一个');

  // 走到最后一张，按钮变「完成回顾」
  for (let i = 0; i < 4; i++) press('btnNextR');
  eq(getEl('rcardCounter').textContent, '6 / 6', '到最后一个词');
  eq(getEl('btnNextR').textContent, '完成回顾', '最后一张按钮文案为「完成回顾」');

  press('btnNextR');
  ok(isActive('view-result'), '回顾完成进入结果页');
  eq(Number(getEl('resValue').textContent), 6, '结果页显示回顾的 6 个词');
  ok(!/不认识/.test(getEl('resDesc').textContent), '结果文案不再出现「不认识」');
  ok(!/[^没]认识/.test(getEl('resDesc').textContent), '结果文案不再统计「认识」');
  eq(getEl('resHardChips').children.length, 6, '结果页列出本次回顾过的 6 个词');
  eq(getEl('resHardChips').children[0].className, 'chip neutral', '回顾词条用中性样式（不再是「不熟」红标）');

  press('btnResContinue');
  ok(isActive('view-study'), '继续回到学习页');
}

console.log('\n[13] 词表自带分节 → 按「天」分成十组（十天搞定考研词汇的形态）');
{
  els = {};
  bootApp();
  confirmAnswer = true;
  getEl('importText').value = tenDays(10, 30);
  press('btnImport');
  ok(isActive('view-home'), '导入后回到首页');

  const grid = getEl('groupGrid');
  eq(grid.children.length, 10, '分成 10 组（= 10 天）');
  eq(grid.children[0].children[1].textContent, '0/30', '每组 30 词');
  eq(grid.children[0].children[0].textContent, 'Day 1', '第 1 组标签取自词表的节名');
  eq(grid.children[9].children[0].textContent, 'Day 10', '第 10 组标签 = Day 10');
  eq(getEl('groupSub').textContent, '分组进度 · 共 10 组（10 个分节）', '标题反映分节数');

  const st = saved();
  eq(st.groupMode, 'sections', '分组模式 = sections');
  eq(st.sections.length, 10, '记录 10 个分节');
  eq(st.words.length, 300, '300 词全部进入本次学习范围');
  eq(st.rawText, '', '整份词表不写进 rawText');
  eq(st.libKey, '__user__', '导入的词表成为可复用的词库');

  press('btnContinue');
  eq(getEl('study-group-label').textContent, 'Day 1 · 第 1 / 10 组', '学习页显示分节名');
  eq(getEl('cardWord').textContent, 'd1w1', '从该节第 1 个词开始');

  for (let i = 0; i < 6; i++) click(getEl('card'));
  ok(isActive('view-review'), '按天分组后 T1 照常触发');
  eq(reviewTotal(), 6, 'T1 仍是 6 个词');
  eq(getEl('rcardWord').textContent, 'd1w1', 'T1 从该节最早的词开始');
  settle();
  ok(!isActive('view-review'), '回顾可正常结算');
}

console.log('\n[14] 单独载入某一节（例如只背「第 3 天」）');
{
  press('btnGoWords');
  ok(isActive('view-words'), '进入词库页');
  const chips = getEl('libSecChips').children;
  eq(chips.length, 10, '词库页列出 10 个分节 chip');
  eq(chips[2].textContent, 'Day 3 · 30词', '第 3 个 chip 是 Day 3');
  eq(getEl('libCountRow').style.display, 'none', '有分节时隐藏「本次学习词数」');

  fire(chips[2], 'click');
  ok(isActive('view-home'), '载入某一节后回首页');
  const grid = getEl('groupGrid');
  eq(grid.children.length, 1, '该节作为单独一个学习单元（1 组）');
  eq(grid.children[0].children[0].textContent, 'Day 3', '组标签 = Day 3');
  eq(grid.children[0].children[1].textContent, '0/30', '30 词');

  const st = saved();
  eq(st.words[0].text, 'd3w1', '只载入该节的词');
  eq(st.groupMode, 'single', '分组模式 = single');
  ok(st.listName.indexOf('Day 3') >= 0, '词库名标注了节名：' + st.listName);
}

console.log('\n[15] JSON 词表导入');
{
  const arr = [];
  for (let i = 1; i <= 24; i++) arr.push({ word: 'json' + i, usphone: '/dʒ/', translation: '释义' + i });
  getEl('importText').value = JSON.stringify(arr);
  confirmAnswer = true;
  press('btnImport');

  const st = saved();
  eq(st.words.length, 24, 'JSON 数组解析出 24 词');
  eq(st.words[0].text, 'json1', '首词正确');
  eq(st.words[0].phonetic, '/dʒ/', 'usphone 映射为音标');
  eq(st.words[0].meaning, '释义1', 'translation 映射为释义');
  eq(getEl('groupGrid').children.length, 10, '无分节 → 均分 10 组');
  eq(st.sections.length, 0, 'state.sections 为空');
}

console.log('\n[16] 分节不成体系时不乱分组');
{
  const lines = ['# Day 1'];
  for (let i = 1; i <= 20; i++) lines.push('solo' + i + '|/t/|释义' + i);
  getEl('importText').value = lines.join('\n');
  confirmAnswer = true;
  press('btnImport');
  eq(saved().sections.length, 0, '只有 1 个分节 → 不算分节');
  eq(getEl('groupGrid').children.length, 10, '退回均分 10 组');
  eq(getEl('groupGrid').children[0].children[0].textContent, '1', '无分节时格子里显示组号');

  // 分节计数与词数不符（残缺分节）也要退回
  const bad = ['# Day 1'];
  for (let i = 1; i <= 10; i++) bad.push('a' + i + '|/t/|释义' + i);
  bad.push('# Day 2');
  for (let i = 1; i <= 10; i++) bad.push('b' + i + '|/t/|释义' + i);
  getEl('importText').value = bad.join('\n');
  press('btnImport');
  eq(saved().sections.length, 2, '两个分节被识别');
  eq(getEl('groupGrid').children.length, 2, '识别到分节后按节分组（2 节 → 2 组，不强行凑十份）');

  // 上面这种情况节数是 2，App 就分 2 组 —— 这是「按词表原分节」的正常结果
  const grid2 = getEl('groupGrid');
  eq(grid2.children[0].children[1].textContent, '0/10', '第 1 节 10 词');
}

console.log('\n[17] 大词表（10 天 × 200 词 = 2000 词）的存档体积');
{
  getEl('importText').value = tenDays(10, 200);
  confirmAnswer = true;
  press('btnImport');
  const st = saved();
  eq(st.words.length, 2000, '2000 词进入学习范围');
  eq(getEl('groupGrid').children.length, 10, '按天分成 10 组');
  eq(getEl('groupGrid').children[0].children[1].textContent, '0/200', '每组 200 词');

  const stSize = (storageMap['fwords.state.v1'] || '').length;
  const libSize = (storageMap['fwords.lib.user.v1'] || '').length;
  ok(stSize < 400 * 1024,
    '学习状态存档 ' + Math.round(stSize / 1024) + ' KB（词库本体不计入）', '应 < 400 KB');
  ok(libSize < 600 * 1024,
    '词库本体单独存放 ' + Math.round(libSize / 1024) + ' KB', '应 < 600 KB');

  press('btnContinue');
  ok(isActive('view-study'), '2000 词也能正常进入学习页');
  for (let i = 0; i < 6; i++) click(getEl('card'));
  ok(isActive('view-review'), '大词表下 T1 照常触发');
}

console.log('\n[18] 内置「十年计划」形态：kaoyan10 按 Day 分组');
{
  const d = window.WORD_LISTS && window.WORD_LISTS.kaoyan10;
  ok(!!d, 'wordlists.js 提供了 WORD_LISTS.kaoyan10');
  eq(d.sections.length, 10, '自带 10 个分节（Day 1 ~ Day 10）');
  eq(d.sections[0].label, 'Day 1', '第 1 节标签 = Day 1');
  eq(d.sections[9].label, 'Day 10', '第 10 节标签 = Day 10');

  let sum = 0;
  d.sections.forEach(function (s) { sum += s.count; });
  eq(sum, d.raw.length, '10 天完整覆盖全部 ' + sum + ' 词，不丢不重');
  ok(Math.max.apply(null, d.sections.map(s => s.count)) -
     Math.min.apply(null, d.sections.map(s => s.count)) <= 1,
    '每天词数最多相差 1（' + d.sections.map(s => s.count).join('/') + '）');

  // 与扁平入口共用同一份 raw，不该重复占用体积
  ok(window.WORD_LISTS.kaoyan.raw === d.raw, 'kaoyan 与 kaoyan10 共用同一份 raw（不重复存储）');
  const jsSize = fs.statSync(path.join(DEMO, 'wordlists.js')).size;
  ok(jsSize < 600 * 1024, 'wordlists.js ' + Math.round(jsSize / 1024) + ' KB（共用 raw 后未翻倍）', '应 < 600 KB');

  delete storageMap['fwords.state.v1'];
  els = {};
  bootApp();
  press('btnGoWords');
  selectLib('kaoyan10');
  ok(getEl('libSecChips').children.length === 10, '词库页列出 10 个「天」');
  eq(getEl('libCountRow').style.display, 'none', '按天载入时隐藏「本次学习词数」');

  press('btnLoadLib');
  const st = saved();
  eq(st.groupMode, 'sections', '整份载入 → 分组模式 sections');
  eq(getEl('groupGrid').children.length, 10, '首页切成 10 组 = 10 天');
  eq(getEl('groupGrid').children[9].children[0].textContent, 'Day 10', '第 10 格标签 = Day 10');
  eq(st.words.length, window.WORD_LISTS.kaoyan10.raw.length, '整份词库进入学习范围');

  // 这是本方案唯一一处「为功能牺牲体积」的取舍，把数字固化下来，避免以后悄悄变差。
  // 注意口径：storageMap 里是 JS 字符串，.length 数的是 UTF-16 码元（中文按 1 计），
  // 而 localStorage 配额也按这个口径算（Chrome 约 5,000 K）。UTF-8 字节数约为其 1.4 倍。
  const fullSize = (storageMap['fwords.state.v1'] || '').length;
  ok(fullSize > 250 * 1024 && fullSize < 900 * 1024,
    '十天计划整份载入 → 存档 ' + Math.round(fullSize / 1024) +
    ' K 字符（' + Math.round(fullSize / 1024 / 1024 * 100) / 100 + ' MB 口径，配额 5 MB）',
    '应在 250~900 K 字符之间');

  press('btnContinue');
  eq(getEl('study-group-label').textContent, 'Day 1 · 第 1 / 10 组', '学习页顶部显示「Day 1」');

  // 对比：分批模式（kaoyan）只把 60 词放进状态，体积小两个数量级
  press('btnGoWords');
  selectLib('kaoyan');
  getEl('libCount').value = '60';
  press('btnLoadLib');
  const batchSize = (storageMap['fwords.state.v1'] || '').length;
  ok(batchSize < 40 * 1024,
    '切到分批模式 → 存档降到 ' + Math.round(batchSize / 1024) + ' K 字符（是整份模式的 1/' +
    Math.round(fullSize / batchSize) + '）', '应 < 40 K 字符');
  ok(saved().words.length === 60, '分批模式只把 60 词放进状态');
}

console.log('\n[14] T2 复习完不会紧接着弹一次重复的 T1（回归）');
{
  /* 背景：老板报「刚复习完 5 分钟模式，又进了复习」。
   * 根因：T1 取最近 6 个词、T2 取最近 5 分钟的词，慢节奏下两者覆盖同一批词。
   * 60 秒/词时 T2 在第 5 个词到点，复习掉 1~5；T1 的「满 6 词」只差 1 个 →
   * 切下一个词立刻弹 T1，复习的还是刚看过的那批。
   * 修复：T2 / T3 完成后把 t1Baseline 推到当前进度（settleReview）。 */
  for (const k in storageMap) delete storageMap[k];   // 干净起步
  els = {};
  bootApp();
  press('btnContinue');

  // 60 秒/词，学到 T2 到点
  let guard = 0;
  while (!isActive('view-review') && guard++ < 20) { flowTime(60000); click(getEl('card')); }
  eq(getEl('reviewTag').textContent, 'T2 · 每 5 分钟', '60 秒/词 → 5 分钟节点触发 T2');
  const n = reviewTotal();

  // 做完这次 T2
  for (let i = 0; i < n; i++) press('btnNextR');
  ok(isActive('view-result'), 'T2 做完进入结果页');
  press('btnResContinue');
  ok(isActive('view-study'), '回到学习页');

  // 关键断言：再切一个词，不应立刻又进回顾
  flowTime(60000);
  click(getEl('card'));
  ok(!isActive('view-review'),
    'T2 之后切下一个词不会立刻弹回顾（' + (isActive('view-review') ? '实际弹了 ' + getEl('reviewTag').textContent : '未弹') + '）');

  // 而且要保证 T1 没被废掉：继续学，累计再满 6 个词时必须触发 T1
  let hitT1 = false;
  for (let i = 0; i < 20 && !hitT1; i++) {
    if (isActive('view-review')) {
      if (/^T1/.test(getEl('reviewTag').textContent)) { hitT1 = true; break; }
      const m = reviewTotal();
      for (let k2 = 0; k2 < m; k2++) press('btnNextR');
      if (isActive('view-result')) press('btnResContinue');
      continue;
    }
    if (isActive('view-result')) { press('btnResContinue'); continue; }
    if (!isActive('view-study')) break;
    flowTime(20000);              // 换快到 20 秒/词，让 T1 先于 T2 到点
    click(getEl('card'));
  }
  ok(hitT1, 'T1 没有被废掉：攒够 6 个词仍会触发 T1');
}

console.log('\n[15] 只有停在学习页才计时（回归）');
{
  /* 老板报「在调试面板时还会计时」。
   * 根因：showView 不管 phase —— 从学习页点齿轮进调试面板，phase 还留在 'learning'。
   * 同一个洞还有两处：退回首页照走；刷新页面后 loadState 把 reviewing 恢复成
   * learning，人落在首页也在白记账。 */
  for (const k in storageMap) delete storageMap[k];   // 干净起步
  els = {};
  bootApp();
  const snap = () => { saveFn(); return JSON.parse(storageMap['fwords.state.v1']); };

  press('btnContinue');
  ok(isActive('view-study'), '进入学习页');

  const t0 = snap().netStudyMs;
  flowTime(60000);
  ok(snap().netStudyMs > t0,
    '学习页：60 秒正常计入（+' + Math.round((snap().netStudyMs - t0) / 1000) + ' 秒）');

  press('btnDebug');
  ok(isActive('view-debug'), '从学习页进入调试面板');
  const t1 = snap().netStudyMs;
  flowTime(120000);
  eq(snap().netStudyMs, t1, '调试面板：待 120 秒不计入');

  press('btnBackHome2');
  ok(isActive('view-home'), '从调试面板退回首页');
  const t2 = snap().netStudyMs;
  flowTime(120000);
  eq(snap().netStudyMs, t2, '首页：待 120 秒不计入');

  press('btnContinue');
  const t3 = snap().netStudyMs;
  flowTime(60000);
  ok(snap().netStudyMs > t3, '点「继续背诵」回到学习页 → 计时恢复');

  // 刷新页面：落在首页，不该接着走表
  const beforeReload = snap().netStudyMs;
  els = {};
  bootApp();
  eq(snap().netStudyMs, beforeReload, '刷新后净学习时长原样恢复');
  const t4 = snap().netStudyMs;
  flowTime(120000);
  eq(snap().netStudyMs, t4, '刷新后落在首页：待 120 秒不计入');

  press('btnContinue');
  const t5 = snap().netStudyMs;
  flowTime(60000);
  ok(snap().netStudyMs > t5, '刷新后点「继续背诵」→ 计时恢复');
}

console.log('\n----------------------------------------');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('----------------------------------------\n');
process.exit(fail ? 1 : 0);
