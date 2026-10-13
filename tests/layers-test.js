/* 分层边界静态自检：不跑业务，只检查「MVC 的规矩有没有被破坏」。
 *
 * 为什么需要它：拆分成 6 个文件之后，最大的风险不是功能坏掉（那有两套行为测试兜着），
 * 而是**分层慢慢烂掉** —— 有人图方便在 model 里写了 DOM、在 view 里改了状态、
 * 或者给某层导出了一个根本不存在的方法（加载即 ReferenceError）。
 * 这些在行为测试里全都看不出来。
 *
 * 运行：node tests/layers-test.js
 */
const fs = require('fs');
const path = require('path');

const DEMO = path.join(__dirname, '..', 'demo');
const FILES = require('./app-files.js');

let pass = 0, fail = 0;
const ok = (c, label) => { c ? pass++ : fail++; console.log((c ? '  PASS  ' : '  FAIL  ') + label); };
const eq = (a, b, label) => ok(a === b, label + (a === b ? '' : `（实际 ${JSON.stringify(a)} ≠ ${JSON.stringify(b)}）`));

const read = f => fs.readFileSync(path.join(DEMO, f), 'utf8');
const LAYER_OF = {
  'fw-core.js': 'core', 'fw-model.js': 'model', 'fw-speech.js': 'speech',
  'fw-view.js': 'view', 'fw-controller.js': 'controller', 'app.js': 'app'
};
// 允许的「反向引用」（引用加载在后面的层）：只在运行期调用，不参与加载期别名
const RUNTIME_LAYER_CALLS = { 'view': ['controller'], 'controller': [], 'model': [], 'speech': [],
                              'core': [], 'app': [] };

const srcs = {};
FILES.forEach(f => { srcs[f] = read(f); });

/* ============ 1. 每个导出的名字都必须真的存在 ============ */
console.log('\n========== 1. 导出完整性 ==========');
FILES.forEach(f => {
  const s = srcs[f];
  const layer = LAYER_OF[f];
  const m = s.match(/FW\.(\w+) = \{([\s\S]*?)\n  \};/);
  if (!m) { ok(false, f + ' 找不到导出块'); return; }
  eq(m[1], layer, f + ' 导出的层名正确');

  // 本文件里列 0 的声明（缩进的是函数体内的局部变量）
  const declared = new Set();
  s.split('\n').forEach(l => {
    const d = l.match(/^(?:function\s+([\w$]+)|(?:let|const|var)\s+([\w$]+))/);
    if (d) declared.add(d[1] || d[2]);
  });
  const exported = m[2].split('\n').map(l => (l.match(/^\s*([\w$]+):/) || [])[1]).filter(Boolean);
  const missing = exported.filter(n => !declared.has(n));
  ok(exported.length > 0, f + ' 导出了 ' + exported.length + ' 个成员');
  eq(missing.length, 0, f + ' 导出项都有对应的声明' + (missing.length ? '，缺失：' + missing.join(', ') : ''));
});

/* ============ 2. 分层职责：底层不许碰 DOM ============ */
console.log('\n========== 2. 分层职责 ==========');
const DOM_API = /document\.|\.innerHTML|\.classList|\.textContent|querySelector/;
const DOM_HELPER = /\$\(/;   // $() 就是 DOM 取元素

['fw-core.js', 'fw-model.js'].forEach(f => {
  // 注释里提到 DOM 词无所谓，剥掉注释再判
  const code = srcs[f].replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
  ok(!DOM_API.test(code), f + ' 不直接使用 DOM API');
  ok(!DOM_HELPER.test(code), f + ' 不调用 $() 取元素');
});
{
  // view 只读状态，不改业务状态
  const code = srcs['fw-view.js'].replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
  const writes = [...code.matchAll(/\bstate\.\w+\s*=(?!=)/g)].map(m => m[0]);
  eq(writes.length, 0, 'view 不直接修改 state' + (writes.length ? '，出现：' + writes.join(' ') : ''));
  const uiWrites = [...code.matchAll(/\bui\.\w+\s*=(?!=)/g)].map(m => m[0]);
  eq(uiWrites.length, 0, 'view 不直接修改 ui' + (uiWrites.length ? '，出现：' + uiWrites.join(' ') : ''));
}
{
  // speech 只管发声，不做渲染
  const code = srcs['fw-speech.js'].replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
  ok(!/\.innerHTML|\.classList/.test(code), 'speech 不做 DOM 渲染');
}

/* ============ 3. 依赖方向：别名只能引用先加载的层 ============ */
console.log('\n========== 3. 依赖方向 ==========');
FILES.forEach(f => {
  const layer = LAYER_OF[f];
  const myIdx = FILES.indexOf(f);
  const refs = [...srcs[f].matchAll(/FW\.(\w+)\./g)].map(m => m[1]);
  const backward = [...new Set(refs)].filter(l => {
    const idx = FILES.findIndex(x => LAYER_OF[x] === l);
    return idx >= 0 && idx > myIdx;
  });
  const allowed = RUNTIME_LAYER_CALLS[layer] || [];
  const bad = backward.filter(l => !allowed.includes(l));
  eq(bad.length, 0, f + ' 没有引用加载在后面的层' + (bad.length ? '，越界：' + bad.join(', ') : ''));
});
{
  // 反向引用必须是显式的运行期调用（形如 FW.controller.xxx( ）—— 加载期别名会拿到 undefined
  const code = srcs['fw-view.js'];
  const calls = [...code.matchAll(/FW\.controller\.(\w+)/g)].map(m => m[1]).sort();
  ok(calls.length > 0, 'view 对 controller 的调用是显式的运行期调用（' + calls.join(', ') + '）');
  const aliasStyle = /var \w+ = FW\.controller\./.test(code);
  ok(!aliasStyle, 'view 没有用加载期别名引用 controller（那会拿到 undefined）');
}

/* ============ 4. 加载顺序三处一致 ============ */
console.log('\n========== 4. 加载顺序一致性 ==========');
{
  const html = read('index.html');
  const inHtml = [...html.matchAll(/<script src="([\w.-]+)\?v=[^"]*"><\/script>/g)]
    .map(m => m[1])
    .filter(n => LAYER_OF[n]);          // 数据文件（words.js 等）不参与分层
  eq(inHtml.join(','), FILES.join(','), 'index.html 的脚本顺序与 app-files.js 一致');

  // 顺序本身就是契约：加载顺序 = 依赖顺序，改动即破坏别名解析
  const EXPECTED = ['core', 'model', 'speech', 'view', 'controller', 'app'];
  eq(FILES.map(f => LAYER_OF[f]).join(','), EXPECTED.join(','),
    '顺序契约：core → model → speech → view → controller → app');
}

console.log('\n----------------------------------------');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('----------------------------------------\n');
process.exit(fail ? 1 : 0);
