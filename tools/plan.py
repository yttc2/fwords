"""背单词计划排布引擎 —— 早/晚双队列 + 标记池。

对应的那张 20 天表的语义（逐格核对得出，不是猜的）：

    上行 = 早上学的 =  当天新单元  +  学后第 7、14、21… 天的单元
    下行 = 晚上复习的 =  当天新单元  +  学后第 1 天 + 第 3 天的单元

早上放长间隔（7/14 天），晚上放短间隔（1/3 天）—— 隔得越远掉得越厉害，
早上离新内容还有一整天，先救这些。晚上只处理短期的 + 手动标记的。

D14 起固定队列排干（10 个单元全学完，没有新单元可进队列），
这正是图上那七格留白的位置 —— 交给标记池。所以「十三天后开标记模式」
不是硬规定，而是固定队列自然枯竭后的唯一内容来源。

内容载荷是抽象的：排布算法只认 unit 序号和「这个单元有多少条目」，
不关心条目是单词、句子还是古诗。换内容源 = 换喂进来的 items，
排布代码一行都不用动。

用法：
    python tools/plan.py                              # 打印 20 天计划
    python tools/plan.py --html plan.html             # 可打印单页
    python tools/plan.py --day 8                      # 第 8 天早/晚明细
    python tools/plan.py --mark-day 8 --mark 12 40    # 第 8 天标记了这几条
    python tools/plan.py --from-txt mylist.txt        # 换内容源（任何文本行）

间隔的来龙去脉见下面 MORNING_OFFSETS / EVENING_OFFSETS 的注释，改之前先读。
"""

import argparse
import html as htmllib
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBS = os.path.join(ROOT, "data", "user-libs.json")

# 十个单元的字母标号，和图上一致
LETTERS = "ABCDEFGHIJ"


# ==================== 间隔 ====================

# 早上：学后第 N 天回顾（7 的倍数天 = 7、14、21…）
MORNING_OFFSETS = [7]
# 晚上：学后第 N 天回顾。0 = 当天新单元晚上过一遍
EVENING_OFFSETS = [0, 1, 3]

# 为什么晚上是 0/1/3 而不是标准艾宾浩斯 1/2/4/7/15：
# 逐格核对过图上 D1-D13 的晚上行，A 组在 D1/D2/D4 出现 → +1、+3；
# D3 只有 BC（没有 A）→ +2 不存在；D5 是 BDE（没有 A）→ +4 不存在。
# 13 格按 0/1/3 零误差，+4 一次都没出现过。**别凭「艾宾浩斯」的印象改这里。**
#
# 为什么早上是 7 的倍数而不是 1/2/4/7：
# 早上行的 D8=AH、D15=AH 两次出现同一组合，只有 7/14 能同时对上；
# D18-D20 = D/E/F 对应 D11-D13 的 +7，也是 7 的倍数。20 格零误差。


# ==================== 内容源（抽象载荷） ====================

class Unit(object):
    """一个学习单元。排布算法只用到 id / label / items 三样。

    items 里放什么都行：单词、整句、古诗、公式、问答题。
    引擎不读 items 的内容，只数个数决定词量统计 —— 这是「可换内容」的关键。
    """

    def __init__(self, uid, label, items):
        self.id = uid              # 1 起
        self.label = label        # 展示名，如 "Day 1 (List 1-2)"
        self.items = items or []  # 载荷本体，结构不约束

    def __len__(self):
        return len(self.items)

    def kind(self):
        """内容的类型标签，只用于统计和展示。"""
        if not self.items:
            return "空"
        first = self.items[0]
        if isinstance(first, dict):
            return first.get("kind") or "条目"
        return "条目"


def split_line(line):
    parts = line.split("|", 2)
    w = parts[0].strip()
    m = parts[2].strip() if len(parts) >= 3 else ""
    return w, m


def build_units_from_lib(lib):
    """从词库注册表建单元：有分节就按分节，没有就均分十份。

    优先用分节（词库自带 Day 1~Day 10），理由和 App 里一样：
    分节能对上「这是第几天」，均分出来的单元跨节，学习时没有天数概念。
    """
    raw = lib.get("raw") or []
    sections = lib.get("sections") or []

    def to_items(chunk):
        out = []
        for x in chunk:
            w, m = split_line(x)
            out.append({"word": w, "meaning": m, "kind": "word"})
        return out

    # 分节必须完整覆盖全部词才可信，否则退回均分
    if sections and len(sections) >= 2 \
            and sum(s.get("count") or 0 for s in sections) == len(raw):
        units, pos = [], 0
        for s in sections:
            cnt = s.get("count") or 0
            units.append(Unit(len(units) + 1,
                              s.get("label") or "第 %d 单元" % (len(units) + 1),
                              to_items(raw[pos:pos + cnt])))
            pos += cnt
        return units

    # 均分十份：floor + 余数补前（不用 ceil）
    n = min(10, len(raw)) or 1
    base, rem = divmod(len(raw), n)
    units, pos = [], 0
    for i in range(n):
        size = base + (1 if i < rem else 0)
        if size == 0:
            continue
        units.append(Unit(len(units) + 1, "第 %d 单元" % (len(units) + 1),
                          to_items(raw[pos:pos + size])))
        pos += size
    return units


def build_units_from_txt(path):
    """换内容源：任何「一行一条目」的文本。

    这是「后期导入别的东西来背」的入口 —— 句子、古诗、公式都能直接喂。
    行首 # 分节（# Day 1 / # 第一天 / ## 第1单元）；没分节就均分十份。
    """
    with io.open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    buckets, cur = [], []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("#"):
            if cur:
                buckets.append(cur)
            cur = []
            continue
        cur.append(s)
    if cur:
        buckets.append(cur)
    if not buckets:
        sys.exit("%s 里没读到内容" % path)

    units = []
    if len(buckets) == 1:
        items = buckets[0]
        n = min(10, len(items)) or 1
        base, rem = divmod(len(items), n)
        pos = 0
        for i in range(n):
            size = base + (1 if i < rem else 0)
            if size == 0:
                continue
            units.append(Unit(len(units) + 1,
                              "第 %s 段" % letter(len(units) + 1),
                              [guess_item(x) for x in items[pos:pos + size]]))
            pos += size
    else:
        for b in buckets:
            units.append(Unit(len(units) + 1,
                              "第 %s 段" % letter(len(units) + 1),
                              [guess_item(x) for x in b]))
    return units


def guess_item(line):
    """从一行猜这是什么东西。猜不准不要紧 —— 引擎不读内容。"""
    parts = line.split("|", 2)
    if len(parts) >= 2:
        return {"word": parts[0].strip(), "meaning": parts[-1].strip(),
                "kind": "word"}
    parts = line.split(None, 1)
    if len(parts) == 2:
        return {"word": parts[0], "meaning": parts[1].strip(), "kind": "word"}
    return {"word": line, "meaning": "", "kind": "line"}


def letter(n):
    """1-10 → A-J；超过 10 退化成 U11、U12…（两段表格宽度有限，不做 AA/AJ）。"""
    return LETTERS[n - 1] if 1 <= n <= len(LETTERS) else "U%d" % n


# ==================== 排布 ====================

def morning_units(day, n_units, offsets=MORNING_OFFSETS):
    """早上学哪些单元：新单元 + 学后第 7/14/21… 天的单元。"""
    out = set()
    if 1 <= day <= n_units:
        out.add(day)                                   # 当天新单元
    for u in range(1, min(day, n_units) + 1):
        k = day - u
        if k > 0 and any(k % o == 0 for o in offsets):
            out.add(u)
    return sorted(out)


def evening_units(day, n_units, offsets=EVENING_OFFSETS):
    """晚上复习哪些单元：新单元 + 学后第 1 天 + 第 3 天。

    早上排的「7 天前回顾单元」不进晚上 —— 早晚是两段，不是同一份内容过两遍。
    但**当天新单元例外**：图上 D1 早上 A、晚上也 A，D2 早上 B、晚上 A+B，
    新单元晚上一定要过一遍（当天不回顾，后面所有间隔整体后移）。
    """
    out = set()
    for u in range(1, min(day, n_units) + 1):
        if (day - u) in offsets:
            out.add(u)
    # 只剔除「早上因长间隔被回顾」的单元，当天新单元保留
    for u in morning_units(day, n_units):
        if not (1 <= day <= n_units and u == day):
            out.discard(u)
    return sorted(out)


def build_plan(units, days=20, marked=None):
    """排 N 天。marked = {天: {条目下标集合}}，当天晚上额外带上。"""
    n = len(units)
    marked = marked or {}
    plan = []
    for d in range(1, days + 1):
        m = morning_units(d, n)
        e = evening_units(d, n)
        mk = marked.get(d) or set()
        m_cnt = sum(len(units[u - 1]) for u in m)
        e_cnt = sum(len(units[u - 1]) for u in e)
        plan.append({
            "day": d,
            "morning": [{"unit": u, "count": len(units[u - 1]),
                         "label": units[u - 1].label} for u in m],
            "evening": [{"unit": u, "count": len(units[u - 1]),
                         "label": units[u - 1].label} for u in e],
            "morningCount": m_cnt,
            "eveningCount": e_cnt,
            "marked": sorted(mk),
            "markedCount": len(mk),
            "eveningTotal": e_cnt + len(mk),
            # 固定队列排干的那几天 → 全靠标记撑着
            "eveningEmpty": (not e),
            "isNewUnit": 1 <= d <= n,
        })
    return plan


def first_dry_day(plan):
    """第一个「固定队列排干」的天 = 所有单元学完 + 1 那天。

    10 个单元 → D14 起固定队列为空 —— 正是图上留白的位置，标记模式该上线的时机。
    从「连续排干」里取第一天，不能只看单天：D1 若因实现偏差排干会被误报。
    """
    run = []
    for p in plan:
        if p["eveningEmpty"]:
            run.append(p["day"])
            if len(run) == 2:          # 连续两天排干才算真的排干
                return run[0]
        else:
            run = []
    return run[0] if run else 0


# ==================== 渲染 ====================

def unit_str(us):
    if not us:
        return "—"
    return " ".join(letter(u["unit"]) for u in us)


def render_text(plan, units, start_date):
    L = ["共 %d 单元 · %d 条目（%s）" % (
        len(units), sum(len(u) for u in units),
        units[0].kind() if units else "空"),
         "早上 = 新单元 + 学后第 %s 天"
         % "、".join(str(o) for o in MORNING_OFFSETS),
         "晚上 = 新单元 + 学后第 %s 天 + 当天标记的"
         % "、".join(str(o) for o in EVENING_OFFSETS if o),
         ""]
    for p in plan:
        d = p["day"]
        date = " (%s)" % date_str(start_date, d) if start_date else ""
        head = "D%-3d%s " % (d, date)
        head += ("新单元 %s" % letter(d)) if p["isNewUnit"] else "第 %d 天" % d
        L.append(head)
        L.append("      早 %-16s %5d 条" % (unit_str(p["morning"]),
                                           p["morningCount"]))
        ev = unit_str(p["evening"]) if p["evening"] else "—"
        if p["markedCount"]:
            ev += "  + 标记 %d" % p["markedCount"]
        note = "   ← 固定队列已排干，晚上只剩标记" if p["eveningEmpty"] else ""
        L.append("      晚 %-16s %5d 条%s" % (ev, p["eveningTotal"], note))
    L.append("")
    L.append("单日最多：早上 %d 条 · 晚上 %d 条" % (
        max(p["morningCount"] for p in plan),
        max(p["eveningTotal"] for p in plan)))
    dry = first_dry_day(plan)
    if dry:
        L.append("D%d 起固定队列排干，晚上只剩标记的条目" % dry)
    else:
        L.append("固定队列全程有内容，标记模式作为额外补充")
    return "\n".join(L)


def date_str(start, d):
    import datetime
    try:
        base = datetime.date.fromisoformat(start)
    except Exception:
        return ""
    return (base + datetime.timedelta(days=d - 1)).isoformat()


HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>背单词计划 · __LIB__</title>
<style>
  *{box-sizing:border-box}
  body{margin:0;padding:26px 18px 56px;background:#f6f7f9;color:#1f2328;
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:#656d76;font-size:13px;margin-bottom:12px}
  .sub b{color:#1f2328;font-weight:600}
  .key{margin:0 0 16px;font-size:12.5px;color:#656d76;line-height:1.9}
  .key code{background:#eef1f4;padding:1px 6px;border-radius:3px;
       font-size:12px;color:#1f2328}
  .tbl{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;
       background:#fff;border:1px solid #d8dee4;border-radius:8px;overflow:hidden}
  .tbl th,.tbl td{border-bottom:1px solid #e6eaef;padding:7px 4px;text-align:center;
       font-size:12.5px;line-height:1.4;vertical-align:top;word-break:break-word}
  .tbl thead th{background:#e8384f;color:#fff;font-weight:600;font-size:12px;
       border-bottom-color:#c92c40;padding:8px 4px}
  .tbl thead th .d{font-size:10.5px;opacity:.85;font-weight:400;
       display:block;margin-top:1px}
  .side{width:24px;background:#fafbfc;color:#8b949e;font-size:11px;
       border-right:1px solid #e6eaef;padding:7px 2px}
  .row-m{background:#fceef0}
  .row-e{background:#fff}
  .row-e.dry{background:#eef1f4}
  .u{font-weight:600;font-size:15px}
  .u.mk{color:#e8384f}
  .c{display:block;color:#8b949e;font-size:10.5px;font-weight:400;margin-top:1px}
  .l{display:block;color:#656d76;font-size:10px;font-weight:400;margin-top:1px;
     overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .tot{background:#eef1f4;font-weight:600}
  .peak{margin-top:14px;color:#656d76;font-size:12.5px;line-height:1.8}
  .peak b{color:#e8384f}
  @media print{
    body{background:#fff;padding:0}
    .tbl th,.tbl td{padding:4px 2px;font-size:9px}
    .tbl thead th{font-size:9px;padding:4px 2px}
    .c,.l,.tbl thead th .d,.side{font-size:7.5px}
    .u{font-size:10px}
  }
  @page{landscape;size:A4 landscape;margin:10mm}
</style></head><body>
<h1>背单词计划</h1>
<div class="sub">__LIB__ · <b>__UNITS__</b> 单元 · <b>__TOTAL__</b> 条目 ·
  <b>__DAYS__</b> 天</div>
<div class="key">
  早上 = 当天新单元 + 学后第 <code>__MOFF__</code> 天<br>
  晚上 = 当天新单元 + 学后第 <code>__EOFF__</code> 天 + 当天标记的条目<br>
  __DRYNOTE__
</div>
__TABLE__
<div class="peak">单日最多：早上 <b>__PM__</b> 条 · 晚上 <b>__PE__</b> 条</div>
</body></html>
"""


def render_html(plan, units, libname, start_date):
    head, mor, eve = [], [], []
    for p in plan:
        d = p["day"]
        head.append("<th>D%d<span class='d'>%s</span></th>" % (
            d, date_str(start_date, d) if start_date else "&nbsp;"))
        tds = []
        for u in p["morning"]:
            tds.append("<td><span class='u'>%s</span>"
                       "<span class='c'>%d 条</span>"
                       "<span class='l'>%s</span></td>" % (
                           letter(u["unit"]), u["count"],
                           htmllib.escape(u["label"])))
        if not tds:
            tds.append("<td>—</td>")
        mor.append("<th class='side'>早</th>" + "".join(tds))

        cls = "row-e dry" if p["eveningEmpty"] else "row-e"
        tds = []
        for u in p["evening"]:
            tds.append("<td><span class='u'>%s</span>"
                       "<span class='c'>%d 条</span>"
                       "<span class='l'>%s</span></td>" % (
                           letter(u["unit"]), u["count"],
                           htmllib.escape(u["label"])))
        if not tds:
            tds.append("<td>—</td>")
        if p["markedCount"]:
            tds.append("<td><span class='u mk'>+%d</span>"
                       "<span class='c'>标记</span></td>" % p["markedCount"])
        eve.append("<tr class='%s'><th class='side'>晚</th>%s</tr>"
                   % (cls, "".join(tds)))
    head.append("<th>最多</th>")
    mor.insert(0, "<tr class='row-m'>")
    mor.append("<th class='side tot'>早</th><td class='tot'>%d</td></tr>"
               % max(p["morningCount"] for p in plan))
    eve.append("<tr><th class='side tot'>晚</th><td class='tot'>%d</td></tr>"
               % max(p["eveningTotal"] for p in plan))
    table = ("<table class='tbl'><thead><tr>%s</tr></thead><tbody>%s%s</tbody></table>"
             % ("".join(head), "".join(mor), "".join(eve)))
    dry = first_dry_day(plan)
    drynote = ("灰底 = 固定队列已排干，<b>D%d 起</b>晚上只剩标记的条目" % dry) \
        if dry else "固定队列全程有内容，标记作为额外补充"
    return (HTML_TMPL
            .replace("__LIB__", htmllib.escape(libname))
            .replace("__UNITS__", str(len(units)))
            .replace("__TOTAL__", str(sum(len(u) for u in units)))
            .replace("__DAYS__", str(len(plan)))
            .replace("__MOFF__", "、".join(str(o) for o in MORNING_OFFSETS))
            .replace("__EOFF__", "、".join(str(o) for o in EVENING_OFFSETS if o))
            .replace("__DRYNOTE__", drynote)
            .replace("__TABLE__", table)
            .replace("__PM__", str(max(p["morningCount"] for p in plan)))
            .replace("__PE__", str(max(p["eveningTotal"] for p in plan))))


def render_md(plan, units, libname, start_date):
    dry = first_dry_day(plan)
    L = ["# 背单词计划 · %s" % libname, "",
         "- 早上 = 当天新单元 + 学后第 %s 天"
         % "、".join(str(o) for o in MORNING_OFFSETS),
         "- 晚上 = 当天新单元 + 学后第 %s 天 + 当天标记的"
         % "、".join(str(o) for o in EVENING_OFFSETS if o),
         ("- 第 %d 天起固定队列排干，晚上只剩标记的条目" % dry) if dry
         else "- 固定队列全程有内容，标记模式作为额外补充",
         "", "| 天 | 日期 | 早上 | 条数 | 晚上 | 标记 | 晚合计 |",
         "|---|---|---|---|---|---|---|"]
    for p in plan:
        L.append("| D%d | %s | %s | %d | %s | %s | %d |" % (
            p["day"], date_str(start_date, p["day"]) or "—",
            unit_str(p["morning"]), p["morningCount"],
            unit_str(p["evening"]) if p["evening"] else "—",
            ("+%d" % p["markedCount"]) if p["markedCount"] else "—",
            p["eveningTotal"]))
    return "\n".join(L) + "\n"


# ==================== 入口 ====================

def load_libs():
    if not os.path.exists(LIBS):
        sys.exit("找不到 %s —— 先用 tools/import_words.py 导入词表" % LIBS)
    with io.open(LIBS, encoding="utf-8") as f:
        return json.load(f)


def pick_lib(libs, key):
    if not libs:
        sys.exit("词库注册表是空的")
    if not key:
        return list(libs.keys())[0], libs[list(libs.keys())[0]]
    if key in libs:
        return key, libs[key]
    for k, v in libs.items():
        if key in k or key in (v.get("name") or ""):
            return k, v
    sys.exit("没有词库匹配 %r" % key)


def resize_units(units, n):
    """把现有单元重新切分成 n 个（保持内容顺序，不丢不重）。

    **默认不要用这个**。单元数 = 词库自己的分节数（当前是 10 个 Day，正好对应
    图上的 A-J），这是老板定的排法。切小只是为了让早晚两段词量拉平，
    但老板明确说过「不用考虑背的多的问题，按照图片的就行」——
    计划表要跟图一致，不要自作主张改单元粒度。
    仅在换内容源且分节数明显不合理时才用（比如 txt 里只有 2 节）。
    """
    if n <= 0 or n == len(units):
        return units
    allitems = [it for u in units for it in u.items]
    total = len(allitems)
    if total == 0 or n <= 0:
        return units
    n = min(n, total)                    # 每个单元至少 1 条
    base, rem = divmod(total, n)
    out, pos = [], 0
    for i in range(n):
        size = base + (1 if i < rem else 0)
        out.append(Unit(len(out) + 1, units[0].label if len(units) == 1
                        else "第 %s 组" % letter(len(out) + 1),
                        allitems[pos:pos + size]))
        pos += size
    return out


def main():
    ap = argparse.ArgumentParser(description="排早/晚双队列的背单词计划")
    ap.add_argument("--lib", default="", help="词库 key 或名称片段")
    ap.add_argument("--from-txt", default="",
                    help="换内容源：任何一行一条目的 txt（# 开头可分节）")
    ap.add_argument("--days", type=int, default=20, help="计划天数，默认 20")
    ap.add_argument("--units", type=int, default=0,
                    help="重切单元数。**一般不用** —— 默认按词库分节（10 个 Day "
                         "对应图上 A-J），老板定的排法就是按图走。只有换内容源且"
                         "分节数不合理时才需要")
    ap.add_argument("--mark", type=int, nargs="*", default=None, metavar="IDX",
                    help="当天晚上要加进队列的条目下标（词库全局 1 起）")
    ap.add_argument("--mark-day", type=int, default=0,
                    help="--mark 属于第几天（默认跟 --day 走，没给 --day 则第 1 天）")
    ap.add_argument("--start-date", default="", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--json", dest="json_out", default="", help="输出 JSON")
    ap.add_argument("--html", dest="html_out", default="", help="输出 HTML")
    ap.add_argument("--md", dest="md_out", default="", help="输出 Markdown")
    ap.add_argument("--day", type=int, default=0, help="打印某天早晚明细")
    args = ap.parse_args()

    if args.from_txt:
        units = build_units_from_txt(args.from_txt)
        if args.units:
            units = resize_units(units, args.units)
        libname = os.path.basename(args.from_txt)
    else:
        key, lib = pick_lib(load_libs(), args.lib)
        units = build_units_from_lib(lib)
        if args.units:
            units = resize_units(units, args.units)
        libname = lib.get("name") or key

    marked = {}
    if args.mark is not None:
        # --mark 默认跟着 --day 走：查第 15 天就默认标记在第 15 天，
        # 否则标记悄悄落到第 1 天，输出看着「晚上 0 条」像没生效
        mday = args.mark_day or args.day or 1
        marked[mday] = set(args.mark)

    days = max(args.days, len(units) + 1)
    plan = build_plan(units, days, marked)

    # --mark 收的是「词库全局 1 起序号」，这里反查回 (单元, 单元内下标)
    # 为什么要反查：标记存的是条目标识，排布和展示都得知道它落在哪个单元
    mark_items = {}
    for d, idxs in marked.items():
        for gi in idxs:
            acc = 0
            for u in units:
                n = len(u)
                if acc < gi <= acc + n:
                    mark_items.setdefault(d, []).append(
                        (u, gi - acc - 1, gi))
                    break
                acc += n

    # —— 某天明细 ——
    if args.day:
        p = plan[args.day - 1]
        print("D%d  %s" % (p["day"], date_str(args.start_date, p["day"]) or ""))
        for tag, k in (("早上", "morning"), ("晚上", "evening")):
            print("\n【%s】%d 条" % (tag, p[k + "Count"]))
            if not p[k]:
                print("  固定队列为空 —— 这一段全靠当天标记的条目")
            for u in p[k]:
                unit = units[u["unit"] - 1]
                print("\n  %s · %s · %d 条" % (letter(u["unit"]),
                                               u["label"], u["count"]))
                for it in unit.items[:300]:
                    w = it.get("word", "") if isinstance(it, dict) else str(it)
                    m = it.get("meaning", "") if isinstance(it, dict) else ""
                    print("    %-26s %s" % (w, m[:60]))
                if len(unit.items) > 300:
                    print("    …… 另有 %d 条" % (len(unit.items) - 300))
        if p["marked"]:
            print("\n【标记】%d 条（当晚进队）" % len(p["marked"]))
            got = 0
            for unit, li, gi in mark_items.get(p["day"], []):
                if li >= len(unit.items):
                    continue
                it = unit.items[li]
                w = it.get("word", "") if isinstance(it, dict) else str(it)
                m = it.get("meaning", "") if isinstance(it, dict) else ""
                print("    #%-5d %s · %-22s %s"
                      % (gi, letter(unit.id), w[:22], m[:56]))
                got += 1
            if got < len(p["marked"]):
                print("    （有 %d 个下标超出词库范围，已忽略）"
                      % (len(p["marked"]) - got))
        return

    print(render_text(plan, units, args.start_date))

    if args.json_out:
        payload = {
            "name": libname, "kind": units[0].kind() if units else "空",
            "morningOffsets": MORNING_OFFSETS,
            "eveningOffsets": EVENING_OFFSETS,
            "dryDay": first_dry_day(plan),
            "days": len(plan), "startDate": args.start_date,
            "units": [{"unit": u.id, "label": u.label, "count": len(u)}
                      for u in units],
            "plan": [{"day": p["day"],
                      "date": date_str(args.start_date, p["day"]),
                      "morning": p["morning"], "evening": p["evening"],
                      "morningCount": p["morningCount"],
                      "eveningCount": p["eveningCount"],
                      "marked": p["marked"], "markedCount": p["markedCount"],
                      "eveningTotal": p["eveningTotal"],
                      "eveningEmpty": p["eveningEmpty"],
                      "isNewUnit": p["isNewUnit"]} for p in plan],
        }
        with io.open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("已写入 %s" % args.json_out)

    if args.html_out:
        with io.open(args.html_out, "w", encoding="utf-8") as f:
            f.write(render_html(plan, units, libname, args.start_date))
        print("已写入 %s" % args.html_out)

    if args.md_out:
        with io.open(args.md_out, "w", encoding="utf-8") as f:
            f.write(render_md(plan, units, libname, args.start_date))
        print("已写入 %s" % args.md_out)


if __name__ == "__main__":
    main()
