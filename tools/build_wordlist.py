"""把开源词库（jsonl）转换成 Fwords Demo 用的 JS 词库文件。

数据源：https://github.com/KyleBing/english-vocabulary
        路径 full_line_jsonl/sentence/正序/考研.jsonl（考研 · 9602 词）

用法：
    python tools/build_wordlist.py

输入：data/kaoyan_zhengxu.jsonl
输出：demo/wordlists.js

输出两个词库入口：
    kaoyan     整份考研词库（5047 词），按词数分批取
    kaoyan10   同一批词切成 10 天（Day 1 ~ Day 10），对应「十天背完」的用法

输出格式与 demo/words.js 一致（`单词|音标|释义`），这样 fw-model.js 的 parseWords()
可以直接复用，不需要改解析逻辑。

体积控制：只保留 单词 / 音标 / 释义 三个字段。原始数据里的 phrases（词组）和
sentences（例句）在 Demo 阶段不参与机制验证，丢掉可以把文件从 8.2 MB 压到 1 MB 以内。
"""

import io
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "data", "kaoyan_zhengxu.jsonl")
OUT = os.path.join(ROOT, "demo", "wordlists.js")

LIST_KEY = "kaoyan"
LIST_NAME = "考研词汇"

DAYS = 10
DAY_KEY = "kaoyan10"
DAY_NAME = "考研词汇 · 十天计划"


def clean(s):
    """清掉会破坏 `单词|音标|释义` 格式的字符。"""
    for ch in ("|", "\n", "\r", "\t"):
        s = s.replace(ch, " ")
    return " ".join(s.split())


def build_line(entry):
    word = clean((entry.get("word") or "").strip())
    if not word:
        return None

    phonetic = clean((entry.get("us") or entry.get("uk") or "").strip())
    if phonetic:
        # 原始音标不带斜杠，统一包一层，并去掉可能已有的斜杠避免出现 //
        phonetic = "/" + phonetic.strip("/") + "/"

    parts = []
    for t in entry.get("translations") or []:
        txt = clean((t.get("translation") or "").strip())
        if not txt:
            continue
        kind = clean((t.get("type") or "").strip())
        parts.append("%s. %s" % (kind, txt) if kind else txt)
    meaning = " / ".join(parts) if parts else "（无释义）"

    return "%s|%s|%s" % (word, phonetic, meaning)


def main():
    if not os.path.isfile(SRC):
        print("找不到源文件: %s" % SRC)
        print("请先下载：full_line_jsonl/sentence/正序/考研.jsonl")
        return 1

    seen = set()
    lines = []
    dup = 0
    with io.open(SRC, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            line = build_line(entry)
            if not line:
                continue
            key = line.split("|", 1)[0].lower()
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            lines.append(line)

    if not lines:
        print("源文件解析后没有任何有效词条")
        return 1

    # 同一批词切成 10 天：前 rem 天各多 1 个词，保证 10 天完整覆盖、不丢不重
    base = len(lines) // DAYS
    rem = len(lines) % DAYS
    sections = []
    for d in range(DAYS):
        sections.append({"label": "Day %d" % (d + 1), "count": base + (1 if d < rem else 0)})

    # 生成 demo/wordlists.js
    # 两个入口共用同一份 raw（用 IIFE 里的局部变量持有），避免把 5000 条词条写两遍
    buf = []
    buf.append("// 由 tools/build_wordlist.py 自动生成，请勿手改。")
    buf.append("// 数据源：https://github.com/KyleBing/english-vocabulary")
    buf.append("// 格式：单词|音标|释义（与 words.js 一致，复用 fw-model.js 的 parseWords）")
    buf.append("(function () {")
    buf.append("  var raw = [")
    for line in lines:
        buf.append("    %s," % json.dumps(line, ensure_ascii=False))
    buf.append("  ];")
    buf.append("")
    buf.append("  window.WORD_LISTS = {")
    # 十天计划放在前面 —— 它是需求里「十天背完考研单词」的直接对应物，
    # 也是词库页的默认选中项（fw-model.js 的 libData 取注册表第一个）。
    buf.append("    %s: {" % DAY_KEY)
    buf.append("      name: '%s'," % DAY_NAME)
    buf.append("      count: %d," % len(lines))
    buf.append("      sections: [")
    for s in sections:
        buf.append("        { label: '%s', count: %d }," % (s["label"], s["count"]))
    buf.append("      ],")
    buf.append("      raw: raw")
    buf.append("    },")
    buf.append("    %s: {" % LIST_KEY)
    buf.append("      name: '%s'," % LIST_NAME)
    buf.append("      count: %d," % len(lines))
    buf.append("      raw: raw")
    buf.append("    }")
    buf.append("  };")
    buf.append("})();")

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(buf) + "\n")

    size = os.path.getsize(OUT)
    print("生成完成: %s" % OUT)
    print("  词条数: %d（跳过重复 %d）" % (len(lines), dup))
    print("  文件大小: %.2f MB" % (size / 1024.0 / 1024.0))
    print()
    print("  十天计划分节（共 %d 天）:" % DAYS)
    for s in sections:
        print("    %-8s %d 词" % (s["label"], s["count"]))
    print()
    print("  抽样前 3 条:")
    for line in lines[:3]:
        print("    " + line)
    print("  抽样末 3 条:")
    for line in lines[-3:]:
        print("    " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
