# -*- coding: utf-8 -*-
"""把 demo/app.js 一次性拆成 MVC 分层（零构建、多文件普通 script）。

【这是一次性迁移工具】运行后 demo/app.js 会变成只剩引导代码，本脚本不再可重复运行。
分层映射与理由见 .workbuddy/memory/ 当日日记。

分层（加载顺序即依赖顺序）：
    fw-core.js       常量、存储、状态容器、通用工具（不依赖任何人）
    fw-model.js      词表解析与分组、词库注册表、持久化、回顾调度（不碰 DOM）
    fw-speech.js     发音（在线真人音源 + 系统 TTS 兜底）
    fw-view.js       全部渲染与视图切换（不改业务状态）
    fw-controller.js 事件绑定、手势、流程推进、计时循环
    app.js           引导：组装并启动

关键手法：**依赖别名前导**。每个文件顶部把用到的别层成员 `var x = FW.层.x;`，
这样函数体里的调用一个字都不用改 —— 重构风险压到最低。
共享变量（state/groups/ui）保持**引用恒定**（原地替换而非整个换对象），
否则别层加载时抓到的别名会指向旧对象。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "demo", "app.js")
OUT = os.path.join(ROOT, "demo")

BLOCKS = [
    (12, "consts"), (24, "store"), (41, "defaultState"), (73, "state"), (74, "groups"),
    (75, "ui"), (78, "dollar"), (80, "fmt"), (87, "fmtReviewSpan"), (93, "toastTimer"),
    (94, "toast"), (106, "unquote"), (115, "isPhonetic"), (121, "parseLine"),
    (154, "sectionLabel"), (168, "parseList"), (194, "parseWords"), (198, "groupLabels"),
    (200, "buildGroups"), (239, "groupTitle"), (245, "studyGroupLabel"), (253, "applyWordList"),
    (291, "LIB_KEY"), (292, "USER_LIB_KEY"), (294, "loadUserLib"), (304, "saveUserLib"),
    (315, "libRegistry"), (340, "libData"), (352, "libOffset"), (357, "jsonToList"),
    (385, "importFromText"), (419, "validSections"), (427, "libCount"), (433, "loadLibChunk"),
    (468, "loadLibSection"), (487, "renderWords"), (574, "curGroup"), (575, "curCursor"),
    (576, "setCursor"), (578, "learnedInGroup"), (588, "savePending"), (589, "saveSoon"),
    (591, "saveNow"), (605, "loadState"), (639, "lastTick"), (641, "tick"), (656, "pickTrigger"),
    (664, "reviewEntries"), (671, "byId"), (677, "VIEWS"), (679, "showView"), (697, "renderHome"),
    (743, "openGroup"), (753, "continueStudy"), (772, "renderStudy"), (796, "markLearned"),
    (808, "advance"), (844, "settleReview"), (857, "startReview"), (877, "reviewTypeLabel"),
    (883, "renderReview"), (917, "flipCard"), (932, "reviewNext"), (946, "reviewPrev"),
    (953, "reviewTap"), (959, "finishReview"), (996, "resumeAfterReview"), (1008, "finishGroup"),
    (1022, "resultContinue"), (1037, "updateStatusBar"), (1077, "voices"), (1078, "loadVoices"),
    (1081, "speechIf"), (1087, "YD_TYPE"), (1088, "AUDIO_MAX"), (1089, "PREFETCH_AHEAD"),
    (1090, "audioCache"), (1091, "currentAudio"), (1092, "humanDownUntil"), (1093, "humanWarned"),
    (1095, "accentType"), (1096, "humanOn"), (1099, "dictUrl"), (1104, "audioFor"),
    (1135, "playHuman"), (1152, "prefetchList"), (1172, "speakSeq"), (1173, "speakTimer"),
    (1174, "keepAliveTimer"), (1175, "ttsUnlocked"), (1176, "ttsWarned"), (1178, "pickVoice"),
    (1186, "stopKeepAlive"), (1191, "stopSpeak"), (1199, "startKeepAlive"), (1210, "systemSpeak"),
    (1236, "unlockTTS"), (1248, "speakAuto"), (1268, "speak"), (1289, "attachSwipe"),
    (1336, "detectBrowser"), (1384, "diagLine"), (1388, "renderDiag"), (1393, "runDiag"),
    (1470, "diagTest"), (1555, "renderAuto"), (1574, "setAuto"), (1583, "renderDebug"),
    (1607, "buildStamp"), (1619, "on"), (1628, "eachOf"), (1635, "warnMissing"), (1639, "bind"),
    (1806, "initWords"), (1818, "boot"), (1852, "bootCall"), (1856, "testHook"), (1859, "iifeClose"),
]

LAYER_OF = {
    "consts": "core", "store": "core", "defaultState": "core", "state": "core",
    # $() 是 DOM 取元素，归 view —— 这样 core 里一个 DOM API 都没有
    "dollar": "view",
    "groups": "core", "ui": "core", "fmt": "core",
    "fmtReviewSpan": "core", "toastTimer": "core", "toast": "core",

    "unquote": "model", "isPhonetic": "model", "parseLine": "model",
    "sectionLabel": "model", "parseList": "model", "parseWords": "model",
    "groupLabels": "model", "buildGroups": "model", "applyWordList": "model",
    "LIB_KEY": "model", "USER_LIB_KEY": "model", "loadUserLib": "model",
    "saveUserLib": "model", "libRegistry": "model", "libData": "model",
    "libOffset": "model", "jsonToList": "model", "importFromText": "model",
    "validSections": "model", "curGroup": "model", "curCursor": "model",
    "setCursor": "model", "learnedInGroup": "model", "savePending": "model",
    "saveSoon": "model", "saveNow": "model", "loadState": "model",
    "pickTrigger": "model", "reviewEntries": "model", "byId": "model",
    "markLearned": "model", "settleReview": "model",
    # initWords 归 model：它装的是「默认词库 → state.words + groups」，纯数据装配
    "initWords": "model",

    "voices": "speech", "loadVoices": "speech", "speechIf": "speech",
    "YD_TYPE": "speech", "AUDIO_MAX": "speech", "PREFETCH_AHEAD": "speech",
    "audioCache": "speech", "currentAudio": "speech", "humanDownUntil": "speech",
    "humanWarned": "speech", "accentType": "speech", "humanOn": "speech",
    "dictUrl": "speech", "audioFor": "speech", "playHuman": "speech",
    "prefetchList": "speech", "speakSeq": "speech", "speakTimer": "speech",
    "keepAliveTimer": "speech", "ttsUnlocked": "speech", "ttsWarned": "speech",
    "pickVoice": "speech", "stopKeepAlive": "speech", "stopSpeak": "speech",
    "startKeepAlive": "speech", "systemSpeak": "speech", "unlockTTS": "speech",
    "speakAuto": "speech", "speak": "speech", "detectBrowser": "speech",

    "groupTitle": "view", "studyGroupLabel": "view", "libCount": "view",
    "renderWords": "view", "VIEWS": "view", "showView": "view", "renderHome": "view",
    "renderStudy": "view", "reviewTypeLabel": "view", "renderReview": "view",
    "updateStatusBar": "view", "diagLine": "view", "renderDiag": "view",
    "runDiag": "view", "diagTest": "view", "renderAuto": "view", "setAuto": "view",
    "renderDebug": "view", "buildStamp": "view",

    "loadLibChunk": "controller", "loadLibSection": "controller", "lastTick": "controller",
    "tick": "controller", "openGroup": "controller", "continueStudy": "controller",
    "advance": "controller", "startReview": "controller", "flipCard": "controller",
    "reviewNext": "controller", "reviewPrev": "controller", "reviewTap": "controller",
    "finishReview": "controller", "resumeAfterReview": "controller", "finishGroup": "controller",
    "resultContinue": "controller", "attachSwipe": "controller", "on": "controller",
    "eachOf": "controller", "warnMissing": "controller", "bind": "controller",

    "boot": "app", "bootCall": "app", "testHook": "app", "iifeClose": "app",
}

ORDER = ["core", "model", "speech", "view", "controller", "app"]
FILE_OF = {"core": "fw-core.js", "model": "fw-model.js", "speech": "fw-speech.js",
           "view": "fw-view.js", "controller": "fw-controller.js", "app": "app.js"}

HEADER = {
    "core": "Fwords · core —— 常量、存储、状态容器、通用工具。\n"
            "最底层，不依赖任何其它层。",
    "model": "Fwords · model —— 词表解析与分组、词库注册表、持久化、回顾调度。\n"
             "**业务规则都在这里，不碰 DOM**（要提示用户就调 toast → core 的通知通道）。\n"
             "这一层是最容易原样搬去 Android 端的（Kotlin 只需要重写存储与 UI 绑定）。",
    "speech": "Fwords · speech —— 发音。\n"
              "主路径是「在线真人录音」（<audio>，不依赖 speechSynthesis），\n"
              "兜底是系统 TTS。两条路的跨平台约束见文件内注释，别合并。",
    "view": "Fwords · view —— 渲染与视图切换。\n"
            "只读 model 的数据、只写 DOM，**不改业务状态**（改状态的动作都交给 controller）。",
    "controller": "Fwords · controller —— 事件绑定、手势、流程推进、计时循环。\n"
                  "唯一同时接触 model 与 view 的层。",
    "app": "Fwords · 引导 —— 组装各层并启动。\n"
           "加载顺序由 index.html 保证：core → model → speech → view → controller → app。",
}

# 别名黑名单：这些名字**不能**用别名引入
#  - 原始值（bool/number）别名会「快照」，跨层读写必须走访问器
#  - 属主层加载在后面的，只能运行期 `FW.层.x(...)` 调用
NO_ALIAS = {
    "view": {"finishGroup", "finishReview", "openGroup", "loadLibSection",
             "humanDownUntil", "ttsWarned", "on"},
    "controller": {"humanDownUntil", "initWords"},
    "app": {"lastTick"},
    "model": set(), "speech": set(), "core": set(),
}

# 逐层补丁：(老串, 新串, 期望出现次数)
PATCHES = {
    "view": [
        # 「离开学习页就停表」原来是写在 showView 里的，但那是**改业务状态**，
        # 属于 controller 的职责。这里摘掉，挪到 controller.goTo。
        ("""function showView(name) {
  // 只有「停在学习页」才算学习时间。
  // 调试面板 / 首页 / 词库页都不该走表 —— 以前从学习页点齿轮进调试面板，phase 还留在
  // 'learning'，人没在学时长却一直在涨。回来时由各入口自己置回 'learning'。
  if (name !== 'view-study' && state.phase === 'learning') {
    state.phase = 'idle';
    saveSoon();
  }
  // 离开学习/回顾页就闭嘴，否则喇叭会对着别的页面继续念""",
         """function showView(name) {
  // 只管把界面切过去。**别在这里改业务状态** ——
  // 「离开学习页就停表」那种判断属于 controller（见 controller.goTo）。
  // 离开学习/回顾页就闭嘴，否则喇叭会对着别的页面继续念""", 1),
        # 下面几条：view 里既有的跨层调用，改成显式的运行期调用
        ("      b.addEventListener('click', function () { loadLibSection(i); });",
         "      b.addEventListener('click', function () { FW.controller.loadLibSection(i); });", 1),
        ("    cell.addEventListener('click', function () { openGroup(i); });",
         "    cell.addEventListener('click', function () { FW.controller.openGroup(i); });", 1),
        ("  if (!len || c >= len) { finishGroup(); return; }",
         "  if (!len || c >= len) { FW.controller.finishGroup(); return; }", 1),
        ("  if (!e) { finishReview(); return; }",
         "  if (!e) { FW.controller.finishReview(); return; }", 1),
        ("  ttsWarned = false;\n", "  resetTtsWarned();\n", 1),
        ("    if (Date.now() < humanDownUntil) {", "    if (humanDownLeftMs() > 0) {", 1),
    ],
    "model": [
        # applyWordList 与 initWords 各一处（initWords 已划归 model）
        ("  groups = buildGroups();\n", "  replaceGroups(buildGroups());\n", 2),
        ("    state = Object.assign({}, base, obj);\n",
         "    replaceState(Object.assign({}, base, obj));\n", 1),
        ("  groupLabels = [];\n", "  groupLabels.length = 0;   // 原地清空：别层抓到的是同一个数组\n", 1),
    ],
    "speech": [
        ("  try { voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : []; }"
         " catch (e) { voices = []; }",
         "  /* 原地更新：view 层在加载时抓的是同一个数组的引用 */\n"
         "  try {\n"
         "    const list = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];\n"
         "    voices.length = 0;\n"
         "    Array.prototype.push.apply(voices, list || []);\n"
         "  } catch (e) { voices.length = 0; }", 1),
    ],
    "controller": [
        ("    state = defaultState();\n", "    replaceState(defaultState());\n", 2),
        ("    groups = buildGroups();\n", "    replaceGroups(buildGroups());\n", 1),
        ("    savePending = true;\n", "    saveSoon();\n", 1),
        ("      humanDownUntil = 0;                 // 手动切换时清掉临时降级",
         "      resetHumanDown();                   // 手动切换时清掉临时降级", 1),
    ],
    "app": [
        ("  lastTick = performance.now();\n", "  resetClock();\n", 1),
        # 每秒落盘交给 model 自己管 —— savePending 是它的内部状态，
        # 在这里读会读到别名快照（永远 false），落盘就再也不发生了。
        ("  setInterval(function () { if (savePending) saveNow(); }, 1000);\n",
         "  startAutoSave();\n", 1),
    ],
    "core": [],
}

# 正则补丁：(模式, 替换, 最少命中次数)。用于「出现很多次、数量不该写死」的批量改名。
REGEX_PATCHES = {
    # controller 里所有 showView(...) 调用改走 goTo(...)：goTo 里做「离开学习页就停表」
    "controller": [(r"\bshowView\(", "goTo(", 20)],
}

EXTRA = {
    "core": """
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
""",
    "speech": """
/* ---- 给别层用的访问器 ----
 * humanDownUntil / ttsWarned 是原始值，别名会「快照」住旧值，
 * 跨层读写只能走函数，不能直接暴露变量。 */
function resetHumanDown() { humanDownUntil = 0; }
function humanDownLeftMs() { return Math.max(0, humanDownUntil - Date.now()); }
function resetTtsWarned() { ttsWarned = false; }

/* 供 app 层的调试钩子使用 */
function getVoicesList() { return voices; }
""",
    "controller": """
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
""",
    "view": """
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
""",
    "model": """
/* 每秒落一次盘，只在有改动（savePending）时真正写。
 * 放在 model 内部：savePending 是本层状态，别层读到的只是别名快照。 */
function startAutoSave() {
  setInterval(function () { if (savePending) saveNow(); }, 1000);
}
""",
    "app": "",
}

# core 的 toast 换成通知通道（实现搬去 view）
CORE_TOAST_OLD = """let toastTimer = null;
function toast(msg) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.classList.remove('show'); }, 1800);
}"""
CORE_TOAST_NEW = """/* 应用级提示 —— 这里只是个「通知通道」。
 * core 负责把消息送出去，具体怎么显示由 view 注册（setNotifier）。
 * 这样 model 层调 toast 时完全不碰 DOM，分层才是真的。 */
let notifier = null;
function setNotifier(fn) { notifier = fn; }
function toast(msg) { if (notifier) notifier(msg); }"""

DECL_RE = re.compile(r"^(function\s+([\w$]+)|(?:let|const|var)\s+([\w$]+))")
# 只认列 0 的声明：缩进的是函数体内的局部变量，不算顶层名字
# 标识符要允许 $ —— 本项目的 $() 就是 document.getElementById 的简写
DECL_RE_COL0 = re.compile(r"^(?:function\s+([\w$]+)|(?:let|const|var)\s+([\w$]+))")
COMMENT_OR_BLANK = re.compile(r"^\s*($|//|\*|/\*)")

# 补丁里新引入、扫描阶段还不存在的跨层名字，手工补上
MANUAL_ALIAS = {
    "app": [("controller", "resetClock")],
}

# 补丁 / EXTRA 里新增的成员：它们不在原文件的顶层声明里，扫描阶段看不到，
# 必须在这里登记，否则不会被导出、别层引用时直接 ReferenceError。
EXTRA_NAMES = {
    "core": ["setNotifier", "replaceState", "replaceGroups"],
    "model": ["startAutoSave"],
    "controller": ["resetClock", "goTo"],
    "speech": ["resetHumanDown", "humanDownLeftMs", "resetTtsWarned", "getVoicesList"],
}

# 从属主层里摘掉的名字（实现已搬去别层，不能再当作本层成员导出）
EXCLUDE_NAMES = {
    "core": {"toastTimer"},   # 渲染用的计时器随 toast 实现一起搬到了 view
}

# 被别层赋值的原始值（bool/number）：**绝对不能跨层用别名**。
# 别名是「加载时快照」，写了一边另一边看不到 —— 踩过一次：app 里
# `var savePending = FW.model.savePending` 永远是 false，每秒落盘的 interval 就再没触发过。
# 这类状态只能由属主层提供函数访问（saveSoon / resetClock / resetHumanDown …）。
REASSIGNED_PRIMITIVES = {
    "savePending", "lastTick", "humanDownUntil", "ttsWarned", "speakTimer",
    "keepAliveTimer", "speakSeq", "ttsUnlocked", "humanWarned", "currentAudio", "toastTimer",
}


def split_chunks(lines):
    """切块。注意一个块里可能有**多条声明**（比如常量区 10 条 const、
    speakSeq 那一串 let），不能只登记第一条 —— 否则那些名字不会被导出，
    别层引用时直接 ReferenceError。"""
    starts = [b[0] for b in BLOCKS]
    out = []
    for i, (ln, key) in enumerate(BLOCKS):
        s = ln - 1
        j = s - 1
        while j >= 0 and COMMENT_OR_BLANK.match(lines[j]):
            j -= 1
        prev_end = (starts[i - 1] - 1) if i > 0 else 0
        out.append((key, max(j + 1, prev_end), ln - 1))
    items = []
    for i, (key, lead, decl) in enumerate(out):
        nxt = out[i + 1][1] if i + 1 < len(out) else len(lines)
        text = "\n".join(lines[lead:nxt])
        names = [m.group(1) or m.group(2)
                 for m in (DECL_RE_COL0.match(l) for l in lines[lead:nxt]) if m]
        items.append({"key": key, "layer": LAYER_OF[key], "names": names, "text": text})
    return items


def strip_comments(t):
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    return re.sub(r"//[^\n]*", " ", t)


def name_re(n):
    """名字的词边界正则。
    - `$` 不是字字符，\b 匹配不到它，得换前瞻/后顾断言；
      而且必须要求后面跟 `(`，否则会把正则字面量结尾的 `$`（如 `/...$/`）误判成调用。
    - 其余普通标识符用 \b 就够。"""
    if n == "$":
        return r"(?<![\w$])\$\s*\("
    if re.match(r"^\w+$", n):
        return r"\b" + re.escape(n) + r"\b"
    return r"(?<![\w$])" + re.escape(n) + r"(?![\w$])"


def main():
    lines = io.open(SRC, encoding="utf-8").read().split("\n")
    items = split_chunks(lines)
    if "iifeClose" not in [it["key"] for it in items]:
        raise SystemExit("已经拆过了？demo/app.js 里找不到收尾的 })();")

    # 去掉 iifeClose（生成器自己收尾）
    items = [it for it in items if it["key"] != "iifeClose"]

    # 每层的成员 = 原文件里划给它的顶层声明（去掉已搬走的）+ 补丁/EXTRA 新增的
    layer_names = {}
    owner = {}
    for layer in ORDER:
        ns = []
        for it in items:
            if it["layer"] == layer:
                ns.extend(n for n in it["names"] if n not in EXCLUDE_NAMES.get(layer, ()))
        ns.extend(EXTRA_NAMES.get(layer, []))
        layer_names[layer] = sorted(set(ns))
        for n in layer_names[layer]:
            owner[n] = layer

    # ---- 逐层拼装 ----
    files = {}
    for layer in ORDER:
        mine = [it for it in items if it["layer"] == layer]
        text = "\n".join(it["text"] for it in mine)

        # 补丁
        for old, new, want in PATCHES[layer]:
            got = text.count(old)
            if got != want:
                raise SystemExit("补丁未命中：[%s] 期望 %d 次、实际 %d 次\n%r" % (layer, want, got, old[:70]))
            text = text.replace(old, new)

        for pat, rep, least in REGEX_PATCHES.get(layer, []):
            n = len(re.findall(pat, text))
            if n < least:
                raise SystemExit("正则补丁命中过少：[%s] %r 期望≥%d 实际 %d" % (layer, pat, least, n))
            text = re.sub(pat, rep, text)

        if layer == "core":
            if text.count(CORE_TOAST_OLD) != 1:
                raise SystemExit("core 的 toast 实现没找到")
            text = text.replace(CORE_TOAST_OLD, CORE_TOAST_NEW)
            text = text.replace("let toastTimer = null;\n", "", 1)

        # 依赖别名（一个名字一行，读起来能直接看出这个文件依赖了谁）
        # 扫描范围要包含 EXTRA —— 里面也会有跨层调用（比如 view 的 setNotifier）
        code = strip_comments(text + "\n" + EXTRA[layer])
        deps = {}
        for n, ol in owner.items():
            if ol == layer or n in NO_ALIAS[layer]:
                continue
            if re.search(name_re(n), code):
                deps.setdefault(ol, []).append(n)

        # 补丁里新引入、扫描阶段还不存在的跨层名字
        for ol, n in MANUAL_ALIAS.get(layer, []):
            deps.setdefault(ol, []).append(n)

        # 硬断言：原始值不许跨层别名（见 REASSIGNED_PRIMITIVES 的说明）
        bad = sorted(n for names in deps.values() for n in names if n in REASSIGNED_PRIMITIVES)
        if bad:
            raise SystemExit(
                "[%s] 跨层引用了被赋值的原始值：%s\n"
                "→ 这类状态要由属主层提供函数访问，或改用对象包裹。" % (layer, ", ".join(bad)))

        alias_lines = []
        for ol in ORDER:
            if ol in deps:
                alias_lines.append("  /* —— 依赖 %s 层 —— */" % ol)
                for n in sorted(deps[ol]):
                    alias_lines.append("  var %s = FW.%s.%s;" % (n, ol, n))
                alias_lines.append("")

        exports = layer_names[layer]
        export_block = ["  FW.%s = {" % layer]
        for n in exports:
            export_block.append("    %s: %s," % (n, n))
        export_block.append("  };")

        parts = [
            "/* " + HEADER[layer].replace("\n", "\n * ") + "\n *\n"
            " * 本文件由 tools/split_mvc.py 从旧的单文件 app.js 拆分而来。\n"
            " * 依赖通过文件顶部的别名显式引入；导出统一挂在 window.FW 上。\n */",
            ";(function (FW) {",
            "  'use strict';",
            "",
        ]
        if alias_lines:
            parts.append("  /* ========== 依赖注入（别名）========== */")
            parts.extend(alias_lines)
        parts.append(text.rstrip("\n"))
        if EXTRA[layer].strip():
            parts.append("")
            parts.append(EXTRA[layer].strip("\n"))
        parts.append("")
        parts.extend(export_block)
        parts.append("})(window.FW = window.FW || {});")
        files[FILE_OF[layer]] = "\n".join(parts) + "\n"

    for name, content in files.items():
        with io.open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        print("  写入 %-18s %6d 字符  %4d 行" % (name, len(content), content.count("\n")))


if __name__ == "__main__":
    main()
