# -*- coding: utf-8 -*-
"""tools/plan.py 的断言测试：早晚双队列 + 标记池 + 抽象载荷。

跑法：
    python tests/plan-test.py

为什么这个文件可以放心 import plan.py：
    plan.py 是纯计算，读 user-libs.json 但**不写**。不像 import-test.py
    会动真数据。这里即使断言失败也不会碰老板的词库。
"""

import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import plan as P  # noqa: E402

FAILS = []
COUNT = [0]


def ok(cond, label):
    COUNT[0] += 1
    if not cond:
        FAILS.append(label)
        print("  FAIL  %s" % label)


def eq(a, b, label):
    COUNT[0] += 1
    if a != b:
        FAILS.append(label)
        print("  FAIL  %s\n        期望 %r\n        实际 %r" % (label, b, a))


# ============ 1. 与参考图 20 格逐格对齐（核心） ============
print("[1] 早晚两段与参考图 D1-D20 逐格对齐")

# 图上手抄：上行=早上，下行=晚上，D14 起晚上是留白
IMG_MORN = ["A", "B", "C", "D", "E", "F", "G", "AH", "BI", "CJ",
            "D", "E", "F", "G", "AH", "BI", "CJ", "D", "E", "F"]
IMG_EVE = ["A", "AB", "BC", "ACD", "BDE", "CEF", "DFG", "EGH", "FHI", "GIJ",
           "HJ", "I", "J", "", "", "", "", "", "", ""]


def letters_str(us):
    return "".join(P.letter(u["unit"]) for u in us)


# 10 个单元，每个 1 条 —— 词量不影响排布
fake = [P.Unit(i + 1, "第 %s 段" % P.letter(i + 1), [{}]) for i in range(10)]
pl = P.build_plan(fake, 20, {})

for i, p in enumerate(pl):
    eq(letters_str(p["morning"]), IMG_MORN[i],
       "D%d 早上与图一致（图=%s）" % (p["day"], IMG_MORN[i] or "-"))
    eq(letters_str(p["evening"]), IMG_EVE[i],
       "D%d 晚上与图一致（图=%s）" % (p["day"], IMG_EVE[i] or "-"))

eq(P.first_dry_day(pl), 14, "固定队列从 D14 起排干")
eq([p["day"] for p in pl if p["eveningEmpty"]], list(range(14, 21)),
   "D14-D20 晚上都排干（图上那七格留白）")

# 早上 +7 的证据：D8 和 D15 都是 A+H，D18-D20 = D/E/F
eq(letters_str(pl[7]["morning"]), "AH", "D8 早上 = A(D1+7) + H(新)")
eq(letters_str(pl[14]["morning"]), "AH", "D15 早上 = A(D1+14) + H(D8+7)")
eq([letters_str(pl[d]["morning"]) for d in (17, 18, 19)], ["D", "E", "F"],
   "D18-D20 早上 = D11-D13 的 +7")

# 晚上 0/1/3 的证据：+2 不存在、+4 不存在
eq(letters_str(pl[2]["evening"]), "BC", "D3 晚上没有 A（+2 不存在）")
eq(letters_str(pl[4]["evening"]), "BDE", "D5 晚上没有 A（+4 不存在）")

# ============ 2. 早晚不重复 ============
print("[2] 早晚两段各自独立")
for p in pl:
    m = {u["unit"] for u in p["morning"]}
    e = {u["unit"] for u in p["evening"]}
    overlap = m & e
    if p["isNewUnit"]:
        eq(overlap, {p["day"]}, "D%d 早晚重叠的只有当天新单元" % p["day"])
    else:
        eq(overlap, set(), "D%d 早晚无重叠" % p["day"])
eq(P.morning_units(8, 10), [1, 8], "D8 早上 = U1(+7) U8(新)")
eq(P.evening_units(8, 10), [5, 7, 8],
   "D8 晚上 = U7(+1) U5(+3) U8(当天新单元)；U1 归早上那段不重复")

# ============ 3. 单元数 / 天数变化 ============
print("[3] 单元数 / 天数变化")
u6 = [P.Unit(i + 1, "U%d" % i, [{}]) for i in range(6)]
p6 = P.build_plan(u6, 20, {})
# D8 = D1+7，U1 存在 → 早上仍回顾 A
eq(letters_str(p6[7]["morning"]), "A",
   "6 单元时 D8 早上仍回顾 A（D1+7），只是没有新单元可学")
eq(letters_str(p6[6]["morning"]), "",
   "6 单元时 D7 早上是空的：单元学完了，且没有单元的 +7 落在 D7")
# 早上允许为空（排干日只剩晚上有内容，或早晚都空）
ok(any(not p["morning"] for p in p6), "允许某天早上没内容")
# 排干日 = 最后一个单元(6) 的第 4 天 = D10
eq(P.first_dry_day(p6), 10, "6 单元 → D10 起排干（6+1+3=10）")
eq(p6[9]["eveningEmpty"], True, "D10 晚上固定队列为空")
eq(p6[8]["eveningEmpty"], False, "D9 晚上还有 U6(当天) 不算排干")
u20 = [P.Unit(i + 1, "U%d" % i, [{}]) for i in range(20)]
p20 = P.build_plan(u20, 20, {})
eq(P.first_dry_day(p20), 0, "20 单元 20 天 → 一直有内容，不存在排干日")
eq(p20[19]["isNewUnit"], True, "D20 仍是新单元")

# ============ 4. 单元粒度：默认按分节，不重切 ============
print("[4] 单元粒度 —— 老板定了按图走，不自作主张切小")

# 默认（--units 0 / 不传）必须原样保留词库分节
big = [P.Unit(1, "A", [{"word": str(i)} for i in range(300)]),
       P.Unit(2, "B", [{"word": str(i)} for i in range(300)])]
eq(P.resize_units(big, 0), big, "n=0 → 原样返回，不重切")
eq(len(P.resize_units(big, 2)), 2, "n 等于原单元数 → 不重切")
# 真实词库：默认就是 10 个单元（对应图上 A-J），不能被偷偷改成 20
reg0 = os.path.join(ROOT, "data", "user-libs.json")
if os.path.exists(reg0):
    with io.open(reg0, encoding="utf-8") as f:
        libs0 = json.load(f)
    _, lib0 = P.pick_lib(libs0, "")
    u10 = P.build_units_from_lib(lib0)
    eq(len(u10), 10, "默认按词库分节切出 10 个单元 = 图上 A-J")
    eq(P.resize_units(u10, 0), u10, "不传 --units 时不重切，10 个单元原样")
    # 排干日必须是 D14（图上留白的位置）
    eq(P.first_dry_day(P.build_plan(u10, 20, {})), 14,
       "10 单元 → D14 排干，正是图上留白的那天")

# resize_units 本身仍要正确（换内容源时可能用得上）
eq([len(u) for u in P.resize_units(big, 4)], [150, 150, 150, 150],
   "显式要求时才重切：600 条切 4 单元")
eq(sum(len(u) for u in P.resize_units(big, 4)), 600, "重切后不丢词")
u4 = P.resize_units(big, 4)
eq(u4[0].items[0]["word"], "0", "重切保持原顺序")
eq(u4[1].items[0]["word"], "150", "第 2 单元从 150 开始")
odd = [P.Unit(1, "A", [{"word": str(i)} for i in range(25)])]
eq([len(x) for x in P.resize_units(odd, 10)], [3, 3, 3, 3, 3, 2, 2, 2, 2, 2],
   "25 条切 10 单元 → 3×5 + 2×5（floor + 余数补前）")
eq(len(P.resize_units(odd, 100)), 25, "请求 100 单元但只有 25 条 → 降到 25")
eq(len(P.resize_units([P.Unit(1, "A", [])], 5)), 1, "空单元重切不炸")

# ============ 5. 标记池 ============
print("[5] 标记池")
pm = P.build_plan(fake, 20, {15: {1, 2, 3}, 16: {5}})
eq(pm[14]["markedCount"], 3, "D15 有 3 条标记")
eq(pm[14]["eveningCount"], 0, "D15 固定队列仍为空")
eq(pm[14]["eveningTotal"], 3, "D15 晚上总量 = 标记 3 条")
eq(pm[15]["eveningTotal"], 1, "D16 晚上总量 = 标记 1 条")
eq(pm[13]["markedCount"], 0, "没标记的那天就是 0")

pm2 = P.build_plan(fake, 20, {4: {1, 2}})
eq(pm2[3]["eveningCount"], 3, "D4 固定队列 3 个单元")
eq(pm2[3]["eveningTotal"], 5, "D4 晚上 = 固定 3 单元 + 标记 2 条")

eq(pm[14]["eveningEmpty"], True, "D15 固定队列标记为排干")
ok(pm[14]["eveningTotal"] > 0,
   "D15 有标记后晚上不为空 —— 标记模式填上了留白")
eq(pm[13]["eveningTotal"], 0, "D14 既没固定队列也没标记 → 真的是空")

# ============ 5. 抽象载荷：排布对内容类型无感知 ============
print("[6] 内容载荷可换")
w = [P.Unit(1, "A", [{"word": "abandon", "meaning": "v. 放弃"}] * 5)]
s = [P.Unit(1, "第 1 句段", [{"text": "苟利国家生死以"}] * 3)]
t = [P.Unit(1, "公式", ["E=mc^2", "a^2+b^2=c^2"])]
for name, u in (("单词", w), ("句子", s), ("纯字符串", t)):
    plx = P.build_plan(u, 20, {})
    eq(len(plx), 20, "%s 载荷也能排 20 天" % name)
    eq(plx[0]["morningCount"], len(u[0]), "%s 载荷的词量统计正确" % name)
    eq(plx[0]["morning"],
       [{"unit": 1, "count": len(u[0]), "label": u[0].label}],
       "%s 载荷的排布结构一致" % name)
eq(P.Unit(1, "x", [{}]).kind(), "条目", "纯 dict 载荷的 kind 兜底")
eq(P.Unit(1, "x", []).kind(), "空", "空单元的 kind")
eq(len(P.Unit(1, "x", [1, 2, 3])), 3, "Unit 支持 __len__")

# ============ 6. 真实词库：分节 = 单元 ============
print("[7] 真实词库")
reg = os.path.join(ROOT, "data", "user-libs.json")
if os.path.exists(reg):
    with io.open(reg, encoding="utf-8") as f:
        libs = json.load(f)
    key, lib = P.pick_lib(libs, "")
    units = P.build_units_from_lib(lib)
    raw = lib.get("raw") or []
    total = sum(len(u) for u in units)
    eq(total, len(raw), "单元覆盖全部 %d 词，不丢不重" % len(raw))
    eq(len(units), 10, "当前词库切出 10 个单元")
    eq(P.letter(1) + ".." + P.letter(10), "A..J", "标号是 A-J")
    for u in units:
        for it in u.items[:3]:
            ok(bool(it.get("word")), "%s 的条目有词" % u.label)
    plr = P.build_plan(units, 20, {})
    eq(len(plr), 20, "真实词库能排 20 天")
    eq(P.first_dry_day(plr), 14, "10 单元 → D14 起排干")
    pk = max(p["morningCount"] for p in plr)
    ok(pk < 900, "早上峰值 %d 条在合理范围" % pk)
    plm = P.build_plan(units, 20, {14: {1, 2, 3}})
    eq(plm[13]["eveningTotal"], 3, "D14 排干日 + 3 条标记 → 晚上 3 条")
    # 分节标签要真的带出来（老板选的是「用词库的 10 个 Day 分节」）
    ok(all(u.label for u in units), "每个单元都有标签")
    ok("Day 1" in units[0].label, "单元 1 的标签是词库分节名（%s）" % units[0].label)
else:
    print("  跳过：没有 data/user-libs.json")

# ============ 7. 换内容源（txt） ============
print("[8] --from-txt 换内容源")
tmp = os.path.join(ROOT, "data", "_t", "plan-src.txt")
os.makedirs(os.path.dirname(tmp), exist_ok=True)
try:
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("# 第一天\n床前明月光\n疑是地上霜\n# 第二天\n举头望明月\n低头思故乡\n")
    u = P.build_units_from_txt(tmp)
    eq(len(u), 2, "按 # 分节 → 2 个单元")
    eq(len(u[0]), 2, "第一节 2 条")
    eq(u[0].label, "第 A 段", "单元标号用字母")
    eq(len(P.build_plan(u, 10, {})), 10, "诗句也能排 10 天")

    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join("第%d句" % i for i in range(1, 26)))
    u2 = P.build_units_from_txt(tmp)
    eq(len(u2), 10, "无分节 → 均分 10 个单元")
    eq(sum(len(x) for x in u2), 25, "均分后总数不变")
    eq([len(x) for x in u2], [3, 3, 3, 3, 3, 2, 2, 2, 2, 2],
       "25 条均分 10 份 = 3×5 + 2×5（floor + 余数补前）")
finally:
    if os.path.exists(tmp):
        os.remove(tmp)

# ============ 8. 渲染不崩 ============
print("[9] 渲染")
plan = P.build_plan(fake, 20, {15: {1, 2}})
txt = P.render_text(plan, fake, "2026-10-03")
ok("D1" in txt, "文本渲染含 D1")
ok("固定队列" in txt, "文本渲染说明排干语义")
htm = P.render_html(plan, fake, "测试", "2026-10-03")
ok(htm.startswith("<!DOCTYPE html>"), "HTML 有 doctype")
ok("</html>" in htm, "HTML 闭合")
md = P.render_md(plan, fake, "测试", "2026-10-03")
ok(md.count("\n|") >= 20, "Markdown 至少 20 行表格")
ok("dry" in htm, "HTML 里排干日带 dry 样式")
htm2 = P.render_html(plan, fake, "<script>alert(1)</script>", "")
ok("<script>" not in htm2, "词库名里的标签被转义")
ok("标记" in htm, "HTML 标出标记条目数")

# ============ 9. 日期 ============
print("[10] 日期")
eq(P.date_str("2026-10-03", 1), "2026-10-03", "D1 = 起始日")
eq(P.date_str("2026-10-03", 14), "2026-10-16", "D14 = 起始日 +13 天")
eq(P.date_str("", 5), "", "没给起始日 → 空串")
eq(P.date_str("瞎写的", 5), "", "非法日期 → 空串")

# ============ 结果 ============
print()
if FAILS:
    print("失败 %d / %d" % (len(FAILS), COUNT[0]))
    sys.exit(1)
print("全部通过：%d 项" % COUNT[0])
