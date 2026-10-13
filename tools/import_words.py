#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把电子词表转成 Fwords 能直接用的词库文件。

支持格式：.txt / .csv / .tsv / .json / .md / .xlsx / .docx / .ppt
（xlsx 与 docx 用标准库解析——它们本质是带 XML 的 zip；.ppt 是 OLE2 复合文档，
  由同目录的 ppt_extract.py 解析。全程不需要装任何第三方包）

用法：
    python tools/import_words.py 十天搞定考研词汇.xlsx --name "十天搞定考研词汇"

    # 一次给多个文件：每个文件自动成为一个分节
    python tools/import_words.py "正课(1).ppt" "正课(2).ppt" --name "王江涛正课"

    # 一次给一整个目录（按文件名排序）
    python tools/import_words.py --dir D:/ppt --name "王江涛正课"

产物：
    demo/wordlists.user.js      ← 页面直接加载（含分节，按天分组就靠它）
    data/<名称>-normalized.txt  ← 规范化后的词表，可复制粘贴到网页的「自定义词表」

能吃的三种排版：

  1. 结构化表格（xlsx / docx / csv）—— 每行「单词,音标,释义」，最好处理。
     xlsx 里每个 sheet 名自动成为一个分节。

  2. 书版自然排版（txt / docx 正文）—— 一个词条跨多行：
         abandon /əˈbændən/ vt. 抛弃，放弃；遗弃
         【考点搭配】abandon oneself to 沉溺于
         account
         n. 账户；描述
     解析规则：单词只取行首 → 音标从行尾抠 /…/ → 【…】标注行整行丢弃 →
     释义续行（n. 账户；描述）接回上一个单词。

  3. 课件排版（.ppt / .pptx 导出为 ppt）—— 一页一页往上加内容的讲稿：
         1、intellect ['intəlekt](n.)智力，理解力；(总称)知识分子
         ●intellectual [,intəl'ektʃuəl]（adj.）智力的；理智的，聪明的
         考点搭配：intellectual enquiry 知识探索（2003年-翻译）
     这类排版的行首序号、项目符号、考点搭配、真题出处、母版占位符都是噪声，
     由 ppt_extract.py 先洗一遍再交给本脚本。

分节与「十天」：
    单独一行  Day 1 / List 3 / 第1天 / # Day 1 / [Day 1] / === Day 1 ===  都算分节。
    考频分级标题（五星词汇 / 四星词汇 …）不计入，只作版面噪声丢弃。
    王江涛《十天搞定考研词汇》是「20 个 List = 10 天」，所以用默认的 --per-day 2
    就能折成 10 天，App 首页直接显示 Day 1~Day 10 十个格子。
    若文件本身已经按 Day 分节，则原样使用，不再折叠。
    .ppt 默认「一个文件 = 一个分节」（正课1.ppt ~ 正课10.ppt → 正好十节）；
    想按讲稿的每一页分节，加 --section-mode slide。
"""

import argparse
import glob
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, 'demo')
DATA = os.path.join(ROOT, 'data')

# ppt_extract.py 就在同一个目录里。用脚本方式跑时 sys.path[0] 已经是 tools/，
# 但被 tests/ 当模块 import 时不是，所以这里补一次。
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PPT_EXT = ('.ppt', '.pps', '.pot')       # 旧版二进制（OLE2），走 ppt_extract
PPTX_EXT = ('.pptx', '.ppsx', '.potx')   # 新版 zip+XML

try:
    import ppt_extract                   # 同目录，.ppt 解析器
except ImportError:                      # 缺了它只是不支持 .ppt，其它格式照常
    ppt_extract = None

# ---------------------------------------------------------------- 分节识别

PHONETIC_CHARS = set("ˈˌəɪʊæɑɔɛɜθðʃʒŋɒʌ")

# 「五星词汇 / 四星词汇 / 三星词汇」这类考频分级标题
TIER_PAT = re.compile(r'^(?:[一二三四五六七八九十\d]+星|高频|低频|核心|基础|常考|超纲|必考|重点|拓展)词汇$')
# 分节单位：List 3 / Day 2 / Unit 5 / 第3个List
UNIT_PAT = re.compile(r'^(day|unit|list|part|week|lesson|chapter)\s*0*(\d+)$', re.I)
UNIT_CN_PAT = re.compile(r'^第\s*0*(\d+)\s*个?\s*(天|单元|组|课|list)$', re.I)


def section_kind(label):
    """分节类型：list / day / unit / tier / other —— 决定怎么折叠成 10 组"""
    s = label.strip()
    if UNIT_PAT.match(s):
        return UNIT_PAT.match(s).group(1).lower() if UNIT_PAT.match(s).group(1).lower() != 'chapter' else 'unit'
    if re.match(r'^第\s*0*\d+\s*个?\s*list$', s, re.I):
        return 'list'
    if UNIT_CN_PAT.match(s):
        return 'day'
    if TIER_PAT.match(s):
        return 'tier'
    return 'other'


def unit_number(label):
    """从分节标题里取编号：'List 7' → 7，'第3天' → 3；取不到返回 None"""
    s = label.strip()
    m = UNIT_PAT.match(s)
    if m:
        return int(m.group(2))
    m = re.match(r'^第\s*0*(\d+)\s*个?\s*list$', s, re.I)
    if m:
        return int(m.group(1))
    m = UNIT_CN_PAT.match(s)
    if m:
        return int(m.group(1))
    return None


def section_label(line):
    """一行是否分节标题；是则返回标题，否则 None（与 fw-model.js 的规则保持一致）"""
    s = line.strip()
    for pat in (r'^#{1,6}\s*(.+?)\s*$', r'^\[(.+?)\]$',
                r'^={2,}\s*(.+?)\s*={2,}$', r'^-{2,}\s*(.+?)\s*-{2,}$'):
        m = re.match(pat, s)
        if m:
            return m.group(1)
    if re.match(r'^(?:day|unit|list|part|week|lesson|chapter)\s*0*\d+$', s, re.I):
        return s
    if re.match(r'^第\s*0*\d+\s*[天单元组课]$', s):
        return s
    if TIER_PAT.match(s):
        return s
    return None


def is_phonetic(t):
    if not t:
        return False
    if t[0] in '/[':
        return True
    return any(c in PHONETIC_CHARS for c in t) and not re.search(r'[\u4e00-\u9fa5]', t)


# ---------------------------------------------------------------- 书版排版解析
#
# 结构化表格（xlsx/docx/csv）每一行都是「单词 | 音标 | 释义」，好解析。
# 但纸质书的电子版往往是**自然排版**，一个词条会跨好几行：
#
#     abandon /əˈbændən/ vt. 抛弃，放弃；遗弃
#     【考点搭配】abandon oneself to 沉溺于
#     【例句】He abandoned his wife and children.
#     account
#     n. 账户；描述
#
# 这种排版必须解决三个问题，否则会把释义行、例句行都当成新单词：
#   1. 单词只在一行的开头 —— 要从行首抠出单词，剩下的是「音标 + 词性 + 释义」
#   2. 【…】标注行（考点搭配/例句/记忆/派生）必须整行丢掉
#   3. 释义换行（n. 账户；描述）要接回上一个单词，而不是当新词条

POS_WORD = re.compile(r'^(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl|a|ad|pref|suf)\.$', re.I)
POS_PREFIX = re.compile(r'^(?:(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl)\.\s*)+', re.I)
# 标注行：【考点搭配】/ [记忆] / （派生）/ 〔…〕/ 《…》
ANNOT_RE = re.compile(r'^[【\[（(〔〈《<]')
# 行首的单词（最多 3 个词，允许连字符/撇号/点/括号，如 well-being、don't、tactic(s)）
# 括号要放行 —— 课件里 `tactic(s) [ˈtæktik(s)] n. …` 这种「可数/不可数」写法很常见，
# 不放行的话这行会被当成「上一条的释义续行」接上去，把上一个词的释义弄脏。
WORD_HEAD_RE = re.compile(
    r"^([A-Za-z][A-Za-z\-'’\.()]*(?:\s+[A-Za-z][A-Za-z\-'’\.()]*){0,2})(?=\s|$)")


def has_cjk(t):
    return bool(re.search(r'[\u4e00-\u9fa5]', t or ''))


# 音标括号里**允许空格**：课件里「英美双读音」写成 `[su:ˈpə:b, sju:-]`、
# `[ˈ mɪnɪməm]`，早先的 `[^\]\s]` 会在空格处截断，把后半截音标漏进释义里去。
# 代价是要挡住 `[ pl.]` 这类「形态标记」——它们也是方括号，但根本不是音标。
BRACKET_BODY_RE = re.compile(r'\[([^\]\n]{2,80})\]')
SLASH_BODY_RE = re.compile(r'/([^/\n]{2,80})/')
ANNOTATION_RE = re.compile(
    r'^\s*(?:pl|sing|fig|esp|abbr|usu|etc|sb|sth|pp|pt|inf|comp|sup|'
    r'BrE|AmE|compar|superl)\s*\.?\s*$', re.I)


def extract_phonetic(tail):
    """从行尾里抠出音标，返回 (音标, 去掉音标后的剩余)"""
    m = SLASH_BODY_RE.search(tail)
    if m and not has_cjk(m.group(1)):
        return m.group(0), (tail[:m.start()] + ' ' + tail[m.end():])
    m = BRACKET_BODY_RE.search(tail)
    if m:
        body = m.group(1)
        if not has_cjk(body) and re.search(r'[A-Za-z]', body) \
                and not ANNOTATION_RE.match(body):
            return m.group(0), (tail[:m.start()] + ' ' + tail[m.end():])
    toks = tail.split()
    for i, t in enumerate(toks):
        # 光秃秃的 `[` / `/` 不是音标 —— `[ pl.]` 被前面的规则排除后会只剩一个 `[`，
        # is_phonetic('[') 因为「首字符是 [」会误判成音标
        if len(t.strip('[]/()')) < 2:
            continue
        if is_phonetic(t):
            return t, ' '.join(toks[:i] + toks[i + 1:])
    return '', tail


def clean_meaning(t):
    s = re.sub(r'[\s\u3000]+', ' ', t or '').strip()
    return s.strip('，,；;、.·-—:：')


def parse_book_line(s):
    """按书版排版解析一行，返回 (word, phonetic, meaning) 或 None"""
    m = WORD_HEAD_RE.match(s)
    if not m:
        return None
    head = m.group(1).strip()
    tail = s[m.end():].strip()

    if POS_WORD.match(head) or has_cjk(head):
        return None
    # 单词最多 3 个词，超了说明这行是英文句子
    if len(head.split()) > 3:
        return None

    ph, rest = extract_phonetic(tail)
    # 释义保留「vt. / n. / adj.」这类词性前缀 —— 书里就是这么标的，
    # 一个词有多个词性时这几位字符是唯一的区分，丢掉反而损失信息
    ma = clean_meaning(rest)

    if tail:
        # 有「尾巴」但既没音标也没中文 → 是例句/英文短语/考点，不是词条
        if not ph and not has_cjk(ma):
            return None
        # 多词短语又没有音标和词性标记 → 大概率是【考点搭配】的裸行
        if len(head.split()) >= 2 and not ph and not POS_PREFIX.match(rest.strip()):
            return None
    return head, ph, ma


def looks_like_meaning(s):
    """这行像不像「上一条单词的释义续行」（如 n. 账户；描述 / 摘要，梗概）"""
    t = POS_PREFIX.sub('', s).strip()
    if not t or not has_cjk(t):
        return False
    if re.match(r"^[A-Za-z][A-Za-z\-'’\.]*[\s，,]", t):
        return False
    return True


def unquote(t):
    if len(t) >= 2 and t[0] == t[-1] and t[0] in '"\'':
        return t[1:-1].strip()
    return t


# ---------------------------------------------------------------- 各格式读取

def read_text(path):
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16'):
        try:
            with io.open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def read_json(path):
    d = json.loads(read_text(path))
    arr = d if isinstance(d, list) else (d.get('words') if isinstance(d, dict) else None)
    if not isinstance(arr, list):
        raise ValueError('JSON 顶层需要是数组，或 {words: [...]}')
    out = []
    for it in arr:
        if isinstance(it, str):
            out.append(it)
            continue
        if not isinstance(it, dict):
            continue
        w = it.get('word') or it.get('text') or it.get('en') or it.get('spelling') or ''
        if not w:
            continue
        ph = it.get('phonetic') or it.get('usphone') or it.get('phone') or it.get('us') or it.get('uk') or ''
        mn = it.get('meaning') or it.get('translation') or it.get('cn') or it.get('def') or ''
        if not mn and isinstance(it.get('translations'), list):
            mn = ' / '.join(
                (t.get('pos', '') + ' ' + (t.get('translation') or '')).strip() if isinstance(t, dict) else str(t)
                for t in it['translations'])
        out.append('|'.join([str(w), str(ph), str(mn)]).rstrip('|'))
    return '\n'.join(out)


SS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _xlsx_shared(z):
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root.iter(SS + 'si'):
            shared.append(''.join(t.text or '' for t in si.iter(SS + 't')))
    return shared


def _xlsx_sheets(z):
    """返回 [(sheet 名, 工作表 xml 路径)]，顺序与 Excel 中一致"""
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels_path = 'xl/_rels/workbook.xml.rels'
    rels = {}
    if rels_path in z.namelist():
        rr = ET.fromstring(z.read(rels_path))
        for r in rr:
            rid = r.get('Id')
            target = r.get('Target') or ''
            if not target.startswith('/'):
                target = 'xl/' + target.lstrip('./')
            rels[rid] = target
    rid_attr = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    out = []
    for sh in wb.iter(SS + 'sheet'):
        name = sh.get('name') or ''
        rid = sh.get(rid_attr)
        path = rels.get(rid)
        if path and path in z.namelist():
            out.append((name, path))
    if not out:
        names = sorted(n for n in z.namelist()
                       if re.match(r'xl/worksheets/sheet\d+\.xml$', n))
        out = [('', n) for n in names]
    return out


def _col_index(ref):
    """'C12' → 2"""
    m = re.match(r'([A-Z]+)', ref or '')
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path):
    """读全部工作表；每张表作为一个分节（表名即分节名）"""
    z = zipfile.ZipFile(path)
    shared = _xlsx_shared(z)
    sheets = _xlsx_sheets(z)
    blocks = []

    for sheet_name, sheet_path in sheets:
        rows = []
        root = ET.fromstring(z.read(sheet_path))
        for row in root.iter(SS + 'row'):
            cells = {}
            for c in row.iter(SS + 'c'):
                idx = _col_index(c.get('r'))
                t = c.get('t')
                if t == 'inlineStr':
                    val = ''.join(x.text or '' for x in c.iter(SS + 't'))
                else:
                    v = c.find(SS + 'v')
                    raw = v.text if v is not None and v.text is not None else ''
                    if t == 's':
                        try:
                            val = shared[int(raw)]
                        except (ValueError, IndexError):
                            val = ''
                    else:
                        val = raw
                val = (val or '').strip()
                if val:
                    cells[idx] = val
            if not cells:
                continue
            width = max(cells) + 1
            rows.append([cells.get(i, '') for i in range(width)])

        if not rows:
            continue
        # 丢掉表头行（第一行是 word/单词/phonetic/释义 之类的字段名）
        if len(rows) > 1 and _looks_like_header(rows[0]):
            rows = rows[1:]

        cols = _guess_columns(rows)
        body = [_row_line(r, cols) for r in rows]
        body = [b for b in body if b]
        if body:
            blocks.append((sheet_name, body))

    if not blocks:
        return ''

    parts = []
    named = len(blocks) > 1 and any(n for n, _ in blocks)
    for i, (name, body) in enumerate(blocks):
        if named:
            parts.append('# ' + (name or ('Sheet %d' % (i + 1))))
        parts.extend(body)
    return '\n'.join(parts)


HEADER_WORDS = {'word', '单词', '英文', '词条', 'spelling', 'phonetic', '音标',
                'meaning', 'translation', '释义', '中文', '词义', '解释', 'pos', '序号', 'index'}


def _looks_like_header(row):
    joined = ' '.join(row).lower()
    hits = sum(1 for h in HEADER_WORDS if h in joined)
    return hits >= 2


def _guess_columns(rows):
    """在第 1~3 行里找“哪几列是 单词 / 音标 / 释义”"""
    best = (0, 1, 2)
    best_score = -1
    for r in rows[:3]:
        for i, cell in enumerate(r):
            if not re.fullmatch(r'[A-Za-z][A-Za-z\-\'\. ]{0,24}', cell or ''):
                continue
            if (cell or '').strip().lower() in HEADER_WORDS:
                continue
            ph = ma = None
            for j, c2 in enumerate(r):
                if i == j or not c2:
                    continue
                if ph is None and is_phonetic(c2):
                    ph = j
                elif ma is None and re.search(r'[\u4e00-\u9fa5]', c2):
                    ma = j
            score = (1 if ph is not None else 0) + (1 if ma is not None else 0)
            if score > best_score:
                best_score = score
                best = (i, ph, ma)
    return best


def _row_line(row, cols):
    wi = cols[0]
    if wi >= len(row):
        return ''
    w = (row[wi] or '').strip()
    if not w or not re.search(r'[A-Za-z]', w):
        return ''
    if ' ' in w and not re.match(r'^[A-Za-z][A-Za-z\-\' ]*$', w):
        return ''
    ph = row[cols[1]].strip() if cols[1] is not None and cols[1] < len(row) else ''
    ma = row[cols[2]].strip() if cols[2] is not None and cols[2] < len(row) else ''
    if not ma:
        rest = [row[k].strip() for k in range(len(row))
                if k != wi and k != cols[1] and (row[k] or '').strip()]
        ma = ' '.join(rest)
    return '|'.join([w, ph, ma])


WS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def read_docx(path):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read('word/document.xml'))
    body = root.find(WS + 'body')
    lines = []
    for el in list(body):
        if el.tag == WS + 'p':
            txt = ''.join(t.text or '' for t in el.iter(WS + 't')).strip()
            if txt:
                lines.append(txt)
        elif el.tag == WS + 'tbl':
            for tr in el.findall(WS + 'tr'):
                cells = []
                for tc in tr.findall(WS + 'tc'):
                    cells.append(''.join(t.text or '' for t in tc.iter(WS + 't')).strip())
                if any(cells):
                    lines.append('\t'.join(cells))
    return '\n'.join(lines)


def read_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.txt', '.csv', '.tsv', '.md', '.text'):
        return read_text(path)
    if ext == '.json':
        return read_json(path)
    if ext == '.xlsx':
        return read_xlsx(path)
    if ext == '.docx':
        return read_docx(path)
    if ext in PPT_EXT or ext in PPTX_EXT:
        return read_deck(path, 'file')[0]
    if ext == '.xls':
        raise SystemExit('旧版 .xls 读不了，请在 Excel 里另存为 .xlsx 或 .csv')
    if ext == '.pdf':
        raise SystemExit('PDF 没有可直接解析的文本层，请「复制粘贴到网页的词表框」，'
                         '或用 Adobe/Word 打开后另存为 .docx / .csv')
    raise SystemExit('不支持的格式：%s' % ext)


# ---- 讲稿类（.ppt / .pptx）：一页一页往上加内容的课件 --------------------

def read_ppt_slides(path, raw=False, drop_phrases=True):
    """旧版 .ppt（OLE2 二进制）→ 每页的文字行

    解析细节全在 ppt_extract.py 里（纯标准库，见那个文件的说明）。
    这里只负责调用 + 把「清洗前的原始行数」带回去做报告。
    """
    if ppt_extract is None:
        raise SystemExit('找不到同目录的 ppt_extract.py，没法读 .ppt')
    try:
        slides, obj = ppt_extract.read_ppt_text(path, raw=raw, drop_phrases=drop_phrases)
    except ValueError as e:
        raise SystemExit('读 %s 失败：%s' % (path, e))
    for w in obj.warnings:
        print('！%s' % w)
    obj.raw_lines = sum(len(s) for s in obj.slides)
    return slides, obj


AS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def read_pptx_slides(path):
    """新版 .pptx（zip + XML）→ 每页的文字行。按页码排序，不是按 zip 里的顺序"""
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist()
             if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    names.sort(key=lambda n: int(re.search(r'(\d+)', os.path.basename(n)).group(1)))
    out = []
    for n in names:
        root = ET.fromstring(z.read(n))
        lines = []
        for para in root.iter(AS + 'p'):
            txt = ''.join(t.text or '' for t in para.iter(AS + 't')).strip()
            if txt:
                lines.append(txt)
        if lines:
            out.append(lines)
    return out


# 课件文件名末尾的序号：`…正课（7）.ppt` → 7、`…正课 2.ppt` → 2
# 只在「括号包裹」或「分隔符 + 数字」时才算，避免把 `词汇2024.ppt` 认成第 2024 讲
DECK_TAIL_RE = re.compile(
    r'(?:[（(\[]\s*(\d{1,3})\s*[)）\]]|[ _\-](\d{1,3}))\s*$')


def deck_index(name):
    """从课件文件名里取讲次编号；取不到返回 None"""
    stem = os.path.splitext(os.path.basename(name))[0]
    m = DECK_TAIL_RE.search(stem)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def slides_to_text(slides, label, mode, sec_label=None):
    """讲稿的每一页 → 带分节的文本

    mode='file'  —— 整个文件一节（正课1.ppt ~ 正课20.ppt 正好二十节）
    mode='slide' —— 每页一节（讲得细，但一节词很少）

    sec_label 是分节标题（默认用文件名）。课件文件名带讲次编号时会被改成
    `List N` —— 这样 `--per-day 2` 就能把「20 个正课」折成「10 天」，
    和书里「20 个 List = 10 天」的编排对上；否则分节类型是 other，不参与折叠。
    """
    title = sec_label or label
    if mode == 'file':
        lines = []
        for s in slides:
            lines.extend(s)
        return '# %s\n' % title + '\n'.join(lines) if lines else ''
    parts = []
    for i, s in enumerate(slides):
        if s:
            parts.append('# %s 第%d页' % (label, i + 1))
            parts.extend(s)
    return '\n'.join(parts)


def sniff_deck(path, ext):
    """按文件头判断讲稿的真实格式，别信扩展名。

    WPS 另存的时候经常出现「文件叫 .ppt，里面装的是 pptx」；反过来也有
    （.pptx 其实是旧的二进制）。扩展名只是线索，文件头才是事实：
        旧版 .ppt → OLE2 复合文档，开头 D0 CF 11 E0
        新版 .pptx → zip，开头 PK
    """
    try:
        with io.open(path, 'rb') as f:
            head = f.read(8)
    except IOError:
        return 'pptx' if ext in PPTX_EXT else 'ppt'
    if head[:2] == b'PK':
        return 'pptx'
    if head[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return 'ppt'
    return 'pptx' if ext in PPTX_EXT else 'ppt'


def read_deck(path, mode='file', raw=False, drop_phrases=True):
    """讲稿文件 → (带分节的文本, 统计信息 dict)"""
    ext = os.path.splitext(path)[1].lower()
    label = os.path.splitext(os.path.basename(path))[0]
    real_fmt = sniff_deck(path, ext)
    if real_fmt == 'pptx':
        if ext in PPT_EXT:
            print('            （文件头是 zip：扩展名写着 %s，实际是 pptx，按 pptx 处理）' % ext)
        slides = read_pptx_slides(path)
        raw_lines = sum(len(s) for s in slides)
        slides = [c for c in (ppt_extract.clean_lines(s, drop_phrases) for s in slides) if c]
        warnings = []
    else:
        if ext in PPTX_EXT:
            print('            （文件头是 OLE2：扩展名写着 %s，实际是旧版 ppt，按 ppt 处理）' % ext)
        slides, obj = read_ppt_slides(path, raw=raw, drop_phrases=drop_phrases)
        raw_lines = obj.raw_lines
        warnings = obj.warnings

    stat = {
        'label': label,
        'slides': len(slides),
        'kept': sum(len(s) for s in slides),
        'raw': raw_lines,
        'warn': warnings,
    }
    sec_label = None
    if mode == 'file':
        n = deck_index(path)
        if n is not None:
            sec_label = 'List %d' % n
            stat['sec'] = sec_label
    return slides_to_text(slides, label, mode, sec_label), stat


# ---------------------------------------------------------------- 规范化

def parse_explicit(s):
    """结构化行：`单词|音标|释义` / 制表符分隔 / ASCII 逗号分隔（xlsx、docx、csv 走这条）"""
    if '|' in s:
        parts = [unquote(x.strip()) for x in s.split('|', 2)]
    elif '\t' in s:
        parts = [unquote(x.strip()) for x in s.split('\t', 2)]
    elif ',' in s:
        parts = [unquote(x.strip()) for x in s.split(',', 2)]
    else:
        return None

    w = (parts[0] or '').strip()
    if not w or has_cjk(w) or not re.search(r'[A-Za-z]', w):
        return None
    # 第一个字段必须是「一个词」，不能是整行书版文本
    if len(w.split()) > 3 or not re.fullmatch(r"[A-Za-z][A-Za-z\-'’\.]*(?:\s+[A-Za-z\-'’\.]+)*", w):
        return None

    ph = ma = ''
    if len(parts) >= 3:
        ph = (parts[1] or '').strip()
        ma = clean_meaning(parts[2])
    elif len(parts) == 2:
        if is_phonetic(parts[1]):
            ph = (parts[1] or '').strip()
        else:
            ma = clean_meaning(parts[1])
    # 第二列装的其实是释义（有些表没有音标列）→ 纠正过来，别把中文当音标
    if ph and has_cjk(ph):
        ma = clean_meaning(ph + ' ' + ma)
        ph = ''
    return w, ph, ma


def normalize(text):
    """→ (entries, sections, dropped)

    entries:  [[word, phonetic, meaning, section_index], ...]
    sections: [{'label', 'kind', 'count'}] —— 折叠成 10 组之前的原始分节
    dropped:  被丢掉的行，用于核对「没把有用内容当垃圾扔了」
    """
    entries = []
    sections = []
    cur = -1          # 当前分节下标；-1 = 还没进入任何分节
    last = -1         # 最近一个词条，供「释义续行」回接
    cont = 0          # 最近这个词条已经接了几行续行
    dropped = []

    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith('//'):
            continue

        lab = section_label(s)
        if lab:
            cur = len(sections)
            sections.append({'label': lab, 'kind': section_kind(lab), 'count': 0})
            last, cont = -1, 0
            continue

        # 【考点搭配】【例句】【记忆】【派生】整行丢掉。
        # 这些是书里版权最重、也最不该被搬运的部分，而且本 App 用不上
        #（渲染与回顾只需要 单词 + 音标 + 释义）。
        if ANNOT_RE.match(s):
            dropped.append(s)
            continue

        e = parse_explicit(s) or parse_book_line(s)
        if e:
            entries.append([e[0], e[1], e[2], cur])
            last, cont = len(entries) - 1, 0
            continue

        # 不是词条 → 试作上一条单词的释义续行（如 `n. 账户；描述`）
        if last >= 0 and cont < 3 and looks_like_meaning(s):
            add = clean_meaning(s)
            if add and add not in entries[last][2]:
                cur_ma = entries[last][2]
                entries[last][2] = (cur_ma + '；' + add).strip('；') if cur_ma else add
            cont += 1
            continue

        dropped.append(s)

    # 分节计数以「词条实际归属」为准，这样去重之后计数依然自洽
    for e in entries:
        if 0 <= e[3] < len(sections):
            sections[e[3]]['count'] += 1

    return entries, sections, dropped


def collapse_sections(sections, per_day):
    """把「20 个 List」折叠成「10 个 Day」。

    规则：
      - 分节是 List（List 1~20）→ 每 per_day 个连续 List 合并成一天
      - 分节本身已经是 Day / Unit / 其他 → **原样使用**，不再折叠
        （Day 就是 App 要的单位；再折一次会把 10 天压成 5 天）
      - 不能整除时**不折叠** —— 宁可不折，不要错折
      - 非 List 分节不会因为折叠而消失：一次导入多个文件时，有的文件按 List
        分节、有的文件按文件名分节，这些「别的分节」按原顺序留在结果里
    """
    real = [s for s in sections if s['count'] > 0]
    units = [s for s in real if s['kind'] == 'list']
    if not units:
        return real

    if per_day <= 1 or len(units) < per_day or len(units) % per_day:
        return real

    def num_of(s):
        n = unit_number(s['label'])
        return str(n) if n is not None else s['label']

    # 每个 List 分节属于第几组（i // per_day），并先把每组的词数与 List 区间拼出来
    group_of = {id(s): i // per_day for i, s in enumerate(units)}
    span = {}
    for i, s in enumerate(units):
        g = i // per_day
        span.setdefault(g, [num_of(s), num_of(s)])[1] = num_of(s)

    out, emitted = [], set()
    for s in real:
        g = group_of.get(id(s))
        if g is None:
            out.append(s)                    # 非 List 分节：原样保留在原位置
        elif g not in emitted:
            emitted.add(g)
            out.append({
                'label': 'Day %d (List %s-%s)' % (g + 1, span[g][0], span[g][1]),
                'kind': 'day',
                'count': sum(x['count'] for x in units[g * per_day:(g + 1) * per_day]),
            })
    return out


# 释义里按词性切段：`vt. 收集 vi. 积聚` → ['vt. 收集 ', 'vi. 积聚']
POS_SPLIT_RE = re.compile(
    r'(?=(?:n|vt|vi|v|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl|a|ad)\.)')


def _meaning_parts(ma):
    """把释义按词性切成片段并抹掉空白，用来判断「这条释义是不是已经收过了」。

    课件是逐页叠加的，同一个词会在好几页里重复出现，而且换个行、多一个空格
    就算是「不一样」了。不归一化的话，collect 的释义会变成
    「vt. 收集 vi. 积聚 a. /ad. 对方付费的(地) / vt. 收集 vi. 积聚 a. /ad. 对方付费的(地)」
    —— 一句话说两遍。
    """
    out = set()
    for seg in POS_SPLIT_RE.split(ma or ''):
        seg = re.sub(r'[\s\u3000]+', '', seg).strip('；;/、,，')
        if seg:
            out.add(seg)
    return out


def dedup(entries):
    """同一单词的多条释义合并成一条；保留首次出现的位置（含分节归属）"""
    seen = {}
    out = []
    dup = 0
    for e in entries:
        w, ma, ph = e[0], e[2], e[1]
        k = w.lower()
        if k in seen:
            dup += 1
            i = seen[k]
            if ma and not _meaning_parts(ma) <= _meaning_parts(out[i][2]):
                out[i][2] = (out[i][2] + ' / ' + ma).strip(' /')
            if ph and not out[i][1]:
                out[i][1] = ph
            continue
        seen[k] = len(out)
        out.append(list(e))
    return out, dup


# ---------------------------------------------------------------- 从已有词库补音标

def load_kaoyan_index():
    p = os.path.join(DEMO, 'wordlists.js')
    if not os.path.exists(p):
        return {}
    text = read_text(p)
    idx = {}
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', text):
        line = m.group(1)
        if '|' not in line:
            continue
        w, _, rest = line.partition('|')
        ph, _, ma = rest.partition('|')
        w = w.strip()
        if w and w.lower() not in idx:
            idx[w.lower()] = (ph.strip(), ma.strip())
    return idx


def fill_missing(entries, idx):
    filled_ph = filled_ma = 0
    for e in entries:
        hit = idx.get(e[0].lower())
        if not hit:
            continue
        if not e[1] and hit[0]:
            e[1] = hit[0]
            filled_ph += 1
        if not e[2] and hit[1]:
            e[2] = hit[1]
            filled_ma += 1
    return filled_ph, filled_ma


# ---------------------------------------------------------------- 输出

def slug(name):
    s = re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5]+', '-', name).strip('-')
    return s or 'user'


REGISTRY = os.path.join(DATA, 'user-libs.json')


def load_registry(path=REGISTRY):
    """已导入的词库注册表 {key: {name, count, sections, raw}}。
    单独存 JSON 而不是去回读 JS —— 回读 JS 要自己写解析器，容易出错。"""
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (ValueError, IOError):
        print('！注册表 %s 读取失败，将重建' % path)
        return {}


def save_registry(reg, path=REGISTRY):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(reg, ensure_ascii=False))


def render_user_js(reg):
    """把注册表渲染成 demo/wordlists.user.js 的完整内容"""
    buf = ['// 由 tools/import_words.py 生成，请勿手改。',
           '// 数据源：data/user-libs.json（每导入一次词表就追加/更新一条）',
           '// 共 %d 个词库' % len(reg),
           'window.WORD_LISTS = window.WORD_LISTS || {};',
           '']
    for key in sorted(reg.keys()):
        lib = reg[key]
        sec = ''
        if lib.get('sections'):
            sec = '    sections: [%s],\n' % ', '.join(
                '{ label: %s, count: %d }' % (json.dumps(s['label'], ensure_ascii=False), s['count'])
                for s in lib['sections'])
        body = ',\n'.join('      ' + json.dumps(l, ensure_ascii=False) for l in lib['raw'])
        buf.append('// %s —— %d 词%s' % (
            lib['name'], len(lib['raw']),
            '，分 %d 节' % len(lib['sections']) if lib.get('sections') else '，未分节'))
        buf.append('window.WORD_LISTS[%s] = {' % json.dumps(key, ensure_ascii=False))
        buf.append('    name: %s,' % json.dumps(lib['name'], ensure_ascii=False))
        buf.append('    count: %d,' % len(lib['raw']))
        # 打上「导入」标记：fw-view.js 据此把来源显示成「导入」而不是「内置」。
        # 产物同样注册进 window.WORD_LISTS，光看这个对象跟内置词库长得一模一样，
        # 不给标记的话下拉里认不出来（曾经踩过：书版词库顶着「内置」标签，和开源词库混在一起）。
        buf.append('    imported: true,')
        if sec:
            buf.append(sec.rstrip('\n'))
        buf.append('    raw: [')
        buf.append(body)
        buf.append('    ]')
        buf.append('};')
        buf.append('')
    return '\n'.join(buf)


def write_user_js(name, lines, sections, out_path):
    key = 'u_' + slug(name)
    reg = load_registry()
    replaced = key in reg
    reg[key] = {'name': name, 'sections': sections or [], 'raw': list(lines)}
    save_registry(reg)

    content = render_user_js(reg)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    return key, len(content.encode('utf-8')), len(reg), replaced


def natural_key(s):
    """文件名排序：正课(2) 排在 正课(10) 前面，不能按字符串比。

    只按整名做自然序是不够的 —— 同一批课件的前缀经常不统一
    （`1-okay-考研词汇-正课（1）`、`1-okay考研词汇-正课（6）`、`1-考研词汇-正课（9）`），
    按整名压会先比出「okay」和「考研」的先后，把 （6）挤到 （8）后面。
    真正有意义的是文件名**末尾的序号**，所以拿它当主键，整名只用来排同号的次序。

    返回 (末尾序号, 分段元组)：分段里用 (0, 字符串) / (1, 整数) 打标签，
    避免 Python 3 在 int 和 str 之间比较时抛 TypeError。
    """
    name = os.path.basename(s)
    nums = re.findall(r'\d+', name)
    tail = int(nums[-1]) if nums else -1
    parts = tuple((1, int(x)) if x.isdigit() else (0, x.lower())
                  for x in re.split(r'(\d+)', name))
    return (tail, parts)


def collect_sources(args):
    """把「位置参数」和 --dir 目录里的文件合成一份待导入清单"""
    out = []
    for p in args.source:
        if os.path.isdir(p):
            p = os.path.join(p, '*')
        if any(ch in os.path.basename(p) for ch in '*?['):
            out.extend(sorted(glob.glob(p), key=natural_key))
        else:
            out.append(p)
    if args.dir:
        for d in args.dir:
            if not os.path.isdir(d):
                raise SystemExit('不是目录：%s' % d)
            for f in sorted(os.listdir(d), key=natural_key):
                fp = os.path.join(d, f)
                ext = os.path.splitext(f)[1].lower()
                if os.path.isfile(fp) and ext in (
                        '.txt', '.csv', '.tsv', '.md', '.json', '.xlsx', '.docx') + PPT_EXT + PPTX_EXT:
                    out.append(fp)
    # 去重但保序
    seen, uniq = set(), []
    for p in out:
        k = os.path.abspath(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def tidy_meaning(ma):
    """释义去冗余：同一单词在讲稿里被反复讲，去重后会拼成 `A / B / C`。

    只留信息量最大的片段 —— 被别的片段整段包含的直接去掉
    （`n. 和睦，和谐；融洽，一致` 已被 `n. 和睦，和谐；融洽，一致；a.爱好音乐的` 覆盖）。
    片段顺序保持原样，方便对照讲稿顺序。
    """
    parts = [p.strip() for p in (ma or '').split(' / ')]
    uniq = []
    for p in parts:
        if p and p not in uniq:
            uniq.append(p)
    keep = [p for p in uniq if not any(p != q and p in q for q in uniq)]
    return ' / '.join(keep)


def _clean_field(x):
    """词条字段消毒：`|` 是词条的分隔符，正文里出现会把格式串位。

    课件音标里真的有 —— `unclear [ʌn|kliә]`、`suspect [ˈsʌspekt,|səˈspekt]`
    （`|` 用来分开英式 / 美式读音）。统一换成 `/`，App 按 split('|') 解析就不会错位。
    """
    return (x or '').replace('|', '/')


def main():
    ap = argparse.ArgumentParser(description='把电子词表转成 Fwords 词库')
    ap.add_argument('source', nargs='*',
                    help='词表文件或目录，可以给多个（txt/csv/tsv/json/md/xlsx/docx/ppt/pptx）')
    ap.add_argument('--dir', action='append', default=[], metavar='DIR',
                    help='扫描整个目录里的词表文件（可多次）。文件名按自然序排，'
                         '正课(1).ppt ~ 正课(10).ppt 会按 1,2,…,10 排好')
    ap.add_argument('--name', default='', help='词库显示名，默认取第一个文件名')
    ap.add_argument('--out', default=os.path.join(DEMO, 'wordlists.user.js'),
                    help='输出的 JS 词库路径')
    ap.add_argument('--no-fill', action='store_true',
                    help='不从内置考研词库补音标/释义')
    ap.add_argument('--dump', default='', help='同时导出一份规范化 txt 到该路径')
    ap.add_argument('--section-mode', choices=('file', 'slide'), default='file',
                    help='讲稿（ppt/pptx）的分节方式：file=一个文件一节（默认，'
                         'N 个正课文件就得到 N 节），slide=每一页一节')
    ap.add_argument('--raw-deck', action='store_true',
                    help='讲稿不做清洗，直接吃原始文字（调试用，通常结果更脏）')
    ap.add_argument('--keep-phrases', action='store_true',
                    help='保留短语/搭配行（`holiday camp` 这类）。默认丢 —— 它们是讲解举例，'
                         '拼写配不上内置词库，留着只会变成没有音标没有释义的空卡片')
    ap.add_argument('--keep-empty', action='store_true',
                    help='保留「既没音标也没释义」的空词条。默认丢，它们是解析残留')
    ap.add_argument('--per-day', type=int, default=2, metavar='N',
                    help='每 N 个 List 合并成一天。王江涛那本书是「20 个 List = 10 天」，'
                         '用默认值 2 即可；不想折叠就填 1。'
                         '分节本身已经是 Day 时该参数不生效（不会二次折叠）')
    args = ap.parse_args()

    srcs = collect_sources(args)
    if not srcs:
        raise SystemExit('没有找到任何待导入的文件')
    for p in srcs:
        if not os.path.exists(p):
            raise SystemExit('找不到文件：%s' % p)

    name = args.name or os.path.splitext(os.path.basename(srcs[0]))[0]
    if len(srcs) > 1 and not args.name:
        name = '%s等%d个' % (name, len(srcs))

    chunks, stats = [], []
    for i, p in enumerate(srcs, 1):
        ext = os.path.splitext(p)[1].lower()
        if ext in PPT_EXT or ext in PPTX_EXT:
            print('读取 %d/%d  %s' % (i, len(srcs), p))
            text, st = read_deck(p, args.section_mode, raw=args.raw_deck,
                                 drop_phrases=not args.keep_phrases)
            stats.append(st)
            print('           %d 页，保留 %d 行（原始 %d 行，洗掉 %d 行版式噪声）%s'
                  % (st['slides'], st['kept'], st['raw'], st['raw'] - st['kept'],
                     '，分节 → %s' % st['sec'] if st.get('sec') else ''))
        else:
            print('读取 %d/%d  %s' % (i, len(srcs), p))
            text = read_any(p)
            if len(srcs) > 1:
                # 多文件时每个文件也当一节，免得分不清词从哪来
                text = '# %s\n' % os.path.splitext(os.path.basename(p))[0] + text
        chunks.append(text)
    raw_text = '\n\n'.join(chunks)

    entries, sections, dropped = normalize(raw_text)
    if len(entries) < 2:
        raise SystemExit('只解析出 %d 个词条，检查一下文件格式' % len(entries))

    entries, dup = dedup(entries)

    # 先补全、后清空 —— 顺序不能反。
    # 「同义词：depict」这类行只有单词没有释义，先补全的话内置词库能把缺的音标
    # 释义补上，它就变成一张正常卡片；反过来先清掉就永远补不回来了。
    filled_ph = filled_ma = 0
    if not args.no_fill:
        filled_ph, filled_ma = fill_missing(entries, load_kaoyan_index())

    # 补完还是「既没音标也没释义」的 = 解析残留（孤零零的 `the`、没跟释义的
    # 短语行…）。留着在 App 里就是一张正面有字、翻过来空白的卡片，没有意义。
    empty = []
    if not args.keep_empty:
        keep = []
        for e in entries:
            if e[1] or e[2]:
                keep.append(e)
            else:
                empty.append(e[0])
        entries = keep
        if len(entries) < 2:
            raise SystemExit('有效词条只剩 %d 个，检查一下文件格式' % len(entries))

    # 释义去冗余（讲稿反复讲同一个词，去重后会拼出很多包含关系的片段）
    for e in entries:
        e[2] = tidy_meaning(e[2])

    # 去重 + 清理会改变各节的词数 → 按实际归属重新计数，否则分节校验必然失败
    pre = sum(1 for e in entries if e[3] < 0)
    for s in sections:
        s['count'] = 0
    for e in entries:
        if 0 <= e[3] < len(sections):
            sections[e[3]]['count'] += 1
    if pre:
        sections.insert(0, {'label': '（开头）', 'kind': 'other', 'count': pre})

    lines = ['|'.join([_clean_field(e[0]), _clean_field(e[1]),
                       _clean_field(e[2]) or '（无释义）']).rstrip('|') for e in entries]
    if not entries[0][1]:
        lines = [(_clean_field(e[0]) + '|' + (_clean_field(e[2]) or '（无释义）'))
                 for e in entries]

    raw_sections = sections
    sections = collapse_sections(sections, args.per_day) if sections else []
    if sections:
        total = sum(s['count'] for s in sections)
        if total != len(lines):
            print('！分节计数(%d) != 词数(%d) → 交回 App 均分十组' % (total, len(lines)))
            sections = []

    out = os.path.abspath(args.out)
    key, size, lib_total, replaced = write_user_js(name, lines, sections, out)

    miss_ph = sum(1 for e in entries if not e[1])
    miss_ma = sum(1 for e in entries if not e[2])

    print('')
    print('词条            %d 个（原始 %d，去重合并 %d）' % (len(entries), len(entries) + dup, dup))
    print('音标覆盖        %d/%d（%.0f%%）%s' % (len(entries) - miss_ph, len(entries),
                                           (len(entries) - miss_ph) * 100.0 / len(entries),
                                           '，本次补入 %d 个' % filled_ph if filled_ph else ''))
    print('释义覆盖        %d/%d（%.0f%%）%s' % (len(entries) - miss_ma, len(entries),
                                           (len(entries) - miss_ma) * 100.0 / len(entries),
                                           '，本次补入 %d 个' % filled_ma if filled_ma else ''))
    if dropped:
        print('跳过的行        %d 行（例句 / 考点搭配 / 记忆法等标注行）'
              % len(dropped))
        print('                抽样：%s' % (dropped[0][:60] if dropped else ''))
    if empty:
        print('丢弃空词条      %d 个（既没音标也没释义，通常是解析残留）' % len(empty))
        print('                %s%s' % ('、'.join(empty[:8]), '…' if len(empty) > 8 else ''))

    if stats:
        print('讲稿            %d 个文件' % len(stats))
        for st in stats[:12]:
            print('                %-22s %3d 页 / 保留 %4d 行'
                  % (st['label'][:22], st['slides'], st['kept']))
        if len(stats) > 12:
            print('                …… 另有 %d 个' % (len(stats) - 12))

    # 折叠前后的分节对比：让「20 个 List → 10 天」这件事在输出里是可见的。
    # 注意判据是「节数变少了」而不是「和原始节数不等」—— 后者在有零词分节
    # （讲稿里只有例句、没有词条的页面）时也会成立，那样会误报成折叠。
    live = [s for s in raw_sections if s['count'] > 0]
    if sections and len(sections) < len(live):
        print('分节折叠        %d 个「%s」→ %d 天（每 %d 个一节合一天）' % (
            len([s for s in live if s['kind'] == 'list']) or len(live),
            'List' if any(s['kind'] == 'list' for s in live) else '节',
            len(sections), args.per_day))
    elif len(live) != len(raw_sections):
        print('零词分节        %d 节里没有词条（讲稿中只有例句/引文的页面），已跳过'
              % (len(raw_sections) - len(live)))

    if sections:
        show = '、'.join('%s(%d)' % (s['label'], s['count']) for s in sections[:10])
        if len(sections) > 10:
            show += '、…共 %d 节' % len(sections)
        print('分节            %d 节：%s' % (len(sections), show))
        print('                App 会按这些分节分组，也就是每组 = 一节')
    else:
        print('分节            无 → App 会均分成 10 组')
    print('输出            %s（%.0f KB，键名 %s）' % (out, size / 1024.0, key))
    if lib_total > 1:
        print('词库文件        已累积 %d 个词库%s' % (
            lib_total, '（本次覆盖了同名旧词库）' if replaced else ''))

    if args.dump:
        d = os.path.abspath(args.dump)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with io.open(d, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print('规范化文本      %s' % d)
    else:
        d = os.path.join(DATA, slug(name) + '-normalized.txt')
        os.makedirs(DATA, exist_ok=True)
        with io.open(d, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print('规范化文本      %s（可粘贴到网页「自定义词表」）' % d)

    print('')
    print('下一步：重新部署（tools/pack.py）或直接刷新页面 —— 词库下拉里会出现「%s」' % name)
    return 0


if __name__ == '__main__':
    sys.exit(main())
