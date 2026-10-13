# -*- coding: utf-8 -*-
"""从电子书（MOBI）里抠出词表，专治「音标独立成行」的排版。

为什么单独写一个：这类电子版词书的 HTML 会被拆成一堆 <span>，转成纯文本后
每个「语义块」独占一行，形如

    intellect
    / ˈintəlekt /
    n.
    智力，智慧
    ，理解力；［总称］知识分子

    助记
    intel=inter- 在…之间，lect- 词根，选择；收集→…
    例句
    （2004年阅读Text 4）Hofstadter says …

    +intellectual
    / ˌintəˈlektʃuəl /
    adj.
    智力的
    ；理智的；聪明的

跟 `import_words.py` 里那套「书版自然排版」的规则不同（那是纸书扫描/排版的
形态），所以这边单独解析，产出一份**带 LIST 分节的标准词表**，再交给
`import_words.py` 折成 10 天。

核心策略：**用音标行当锚点**。
    音标行（`/ … /`）辨识度最高，且每个真词条必有一个；考点搭配之类的短语
    行没有音标，自然被排除。

命令：
    python tools/mobi_words.py 书.mobi --stats            # 只统计
    python tools/mobi_words.py 书.mobi --review 120       # 打印前 120 条供核对
    python tools/mobi_words.py 书.mobi --dump out.txt     # 输出规范化词表
    python tools/mobi_words.py 书.mobi --dump out.txt --import --name "十天搞定考研词汇"
                                                          # 输出并直接生成 JS 词库
"""

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import mobi_extract                                    # noqa: E402

# 词条之间的「标注行」（独占一行）—— 释义块遇到它就结束
#
# 这个集合**刻意收得很紧**：只放「一眼就是版式标签、不可能当释义」的词。
# 踩过的坑：一开始图省事把 `补充` `记忆` `扩展` `用法` `翻译` 这些也塞进来，
# 结果 `supplement /ˈsʌplimənt/ n. 补充（品），增补（品）…` 的释义被从
# `补充` 处拦腰截断，只剩一个 `n.`。所以判据是「这本书里它只作标签出现」，
# 不是「它看起来像标签」。
LABELS = {
    '助记', '助记法', '记忆法',
    '派生词', '同根词',
    '考点搭配', '真题例句', '例句', '例句翻译', '译文', '参考译文',
    '近义词', '反义词', '同义词', '形近词', '相关词', '关联词',
    '词义辨析', '视频讲解',
}

# 音标独占一行：`/ ˈintəlekt /`
RE_PHON = re.compile(r'^/\s*(\S(?:.*?\S)?)\s*/\s*$')
# 行尾带音标：`（= analyze）/ ˈænəlaiz /`、`，散布 / diˈfjuːs /`
# 前缀非贪婪 + 音标段禁止含斜杠，保证不会把 `n./v. 零售adj./adv.` 这种切开
RE_PHON_TAIL = re.compile(r'^(.*?)(/\s*[^/]{1,60}\s*/)\s*$')
# 行内的音标（同上，但允许后面还有文字）
RE_PHON_INLINE = re.compile(r'/\s*([^/]{1,60}?)\s*/')
# 词条行：纯英文单词（最多 3 个词，允许 `+` 前缀标记派生词）
RE_WORD = re.compile(
    r"^\+?\s*([A-Za-z][A-Za-z\-'’\.]*(?:\s+[A-Za-z][A-Za-z\-'’\.]*){0,2})\s*$")
# 分节标题：`LIST 01`
RE_HEAD = re.compile(r'^LIST\s*0*(\d+)$', re.I)
# 分节标题（宽口径，用来在释义块里踩刹车）：`Day 1` / `第1天` / `LIST 01`
RE_BOUNDARY = re.compile(r'^(?:LIST\s*0*\d+|Day\s*\d+|第\s*\d+\s*天)\s*$', re.I)
# 长英文散文（例句）：不含中文、够长、词数够多
RE_PROSE = re.compile(r'^[^一-鿿\s][\x20-\x7e\u2018\u2019\u201c\u201d\u2014]{44,}$')
# 词性（可能一行塞好几个，也可能后面直接跟释义）
RE_POS = re.compile(
    r'^(?:(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl|a|ad)\.?\s*)+$',
    re.I)
# 光秃秃一个词性（带点）：`n.` `vt.` `adj.` —— 这不是单词
RE_POS_BARE = re.compile(
    r'^(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl|a|ad)\.$', re.I)

# 书末独立章节的标题 —— 只用来给**最后一个 LIST 封口**（它后面没有下一个
# LIST 标题了）。同样是「这本书里它只作标题出现」才收进来：
# 一开始贪心地把 `版权` `结论` `前言` 都塞进来，结果 `copyright` 的释义
# （`n. 版权`）被从 `版权` 处截断，只剩一个 `n.`。
STOP_HEADS = {'索引', '附录', '前言', '后记', '参考文献', '版权页'}

CJK = re.compile(r'[\u3400-\u9fff]')

# 行首行尾是这些符号时，拼接不补空格
PUNCT = '，。；：、）（【】《》「」‘’“”…—－·～!?,;:()[]{}<>"\'/\\|~'


def is_cjk(ch):
    return bool(CJK.match(ch))


def clean_field(x):
    """`|` 是词条分隔符，字段里出现会把格式串位 → 换成 `/`"""
    x = (x or '').replace('|', '/')
    return re.sub(r'\s+', ' ', x).strip()


def smart_join(block):
    """把释义的多个断行拼回一句。

    HTML 的 <span> 切行几乎总落在标点或中文之间，所以「两侧任一侧是中文/标点」
    就不补空格，否则补一个空格（英文短语被切断的情形）。
    """
    block = [b for b in block if b]
    if not block:
        return ''
    out = block[0]
    for nxt in block[1:]:
        a, b = out[-1], nxt[0]
        if a in PUNCT or b in PUNCT or is_cjk(a) or is_cjk(b):
            out += nxt
        else:
            out += ' ' + nxt
    # `n.智力` → `n. 智力`（词性后面补空格，只针对中文）
    out = re.sub(r'(?<=\.)(?=[\u3400-\u9fff])', ' ', out)
    return re.sub(r'\s+', ' ', out).strip()


def split_phonetic_parts(s):
    """把一行拆成 [('t', 文本), ('p', 音标), …]

    这本书给「多音多义」词会并列读音：`vt. 扩散；传播，散布 / diˈfjuːs /`，
    第二个读音就内嵌在释义行里。拆开后：读音归音标，文本归释义。
    """
    res, pos = [], 0
    for mo in RE_PHON_INLINE.finditer(s):
        if mo.start() > pos:
            res.append(('t', s[pos:mo.start()]))
        res.append(('p', mo.group(1)))
        pos = mo.end()
    if pos < len(s):
        res.append(('t', s[pos:]))
    return res


def phon_in_line(line):
    """这一行里的音标 → (前缀文字, 音标)；没有则 None

    两种形态：
      * 整行就是音标 —— `/ ˈintəlekt /`
      * 行尾带音标 —— `（= analyze）/ ˈænəlaiz /`、`，散布 / diˈfjuːs /`

    第二种是这本书的坑：`analyse` `inquiry` `sceptical` `by-product` 这些词的
    音标全藏在行尾（行首还有个异体词注），只认「整行音标」会把它们整条漏掉。
    """
    s = line.strip()
    m = RE_PHON.match(s)
    if m:
        return '', m.group(1)
    m = RE_PHON_TAIL.match(s)
    if not m:
        return None
    ph = m.group(2)[1:-1].strip()
    if not ph or CJK.search(ph):
        return None
    return m.group(1).strip(), ph


def _is_equals_note(t):
    """`（= clergy` / `）` 这类「等义词提示」残片 —— 只留音标，文字丢掉"""
    t = t.strip()
    if not t:
        return True
    if re.match(r'^[（(【\[]\s*[=＝]', t):
        return True
    return bool(re.fullmatch(r'[）)】\]」》。，、；:：]+', t))


def is_word_line(t):
    """是不是「单词行」。

    坑：`n.` `vt.` `adj.` 这些**词性标注也能被 RE_WORD 匹配上**（字母 + 点），
    必须排除。否则多音词的第二条音标会被当成新词条 —— 词头变成 `n.`，
    而本来的词条释义变空（`implement` `conflict` `object` 这一批全中招）。
    """
    t = (t or '').strip()
    m = RE_WORD.match(t)
    if not m:
        return None
    if RE_POS_BARE.match(m.group(1)):
        return None
    return t


def _word_line_above(lines, j, start):
    """音标行上方第一个非空行，如果是「单词行」就返回它（含 `+` 前缀）"""
    for x in range(j - 1, max(start - 1, j - 5), -1):
        t = lines[x].strip()
        if not t:
            continue
        if t in LABELS:
            return None
        return is_word_line(t)
    return None


def is_new_entry(lines, j, end):
    """第 j 行是不是「下一个词条的单词行」（后面紧跟音标）

    必须**先满足「这一行本身是单词行」**：多音词的第二条音标前面那行是中文释义
    （`移植` 后面跟 `/ ˈtrænsplɑːnt /`），只看「下一行是不是音标」会把它误判成
    新词条，于是释义被截成光秃秃的 `vt.`。
    """
    if not is_word_line(lines[j]):
        return False
    k = j + 1
    while k < end and not lines[k].strip():
        k += 1
    return k < end and phon_in_line(lines[k]) is not None


def parse_section(lines, start, end):
    """一个 LIST 里 → [(word, phonetic, meaning, derived)]

    一个词条可能横跨多个读音 / 多个词性：

        transplant
        / trænsˈplɑːnt /
        vt.
        移植
        / ˈtrænsplɑːnt /     ← 第二个读音，长得和「下一个词条」一样，靠上一行是不是单词区分
        n.
        移植

    所以遇到「上方不是单词」的音标行要**当成同一词条的另一读音**继续收，
    而不是断在这里（否则释义只剩一个 `vt.`）。

    例外是 `（= 异体词）/ 音标 /` 这种**变体注**：那个音标属于异体词
    （`（= production / prəˈdʌkʃən /` 里的是 production 的读音），整行丢掉。
    """
    out = []
    k = start
    while k < end:
        pf = phon_in_line(lines[k])
        if not pf:
            k += 1
            continue
        pre, first_phon = pf

        # 词头：优先用同行音标前面那段（若它本身是个单词），否则往上找非空行
        wline = None
        pre_clean = re.sub(r'[（(]\s*[=＝][^）)]*[）)]?', '', pre).strip()
        if pre_clean and is_word_line(pre_clean):
            wline = pre_clean
        if not wline:
            wline = _word_line_above(lines, k, start)
        if not wline:
            k += 1
            continue
        derived = wline.lstrip().startswith('+')

        phones = [first_phon]
        groups = [[]]                      # 每个读音对应一组释义
        j = k + 1
        while j < end:
            s = lines[j].strip()
            if not s:
                # 空行**不**结束释义 —— 电子书的 `<p>` 断行位置各家不同，
                # 拿空行当边界会把「n. / 智力，智慧 / ，理解力」这种断行截断。
                # 真正的边界交给下面几条判据。
                j += 1
                continue
            if s in LABELS or RE_BOUNDARY.match(s):
                break
            if RE_PROSE.match(s):
                break              # 长英文例句（本来该有 `例句` 标注，这里兜底）
            npf = phon_in_line(s)
            if npf:
                npre, nphon = npf
                if _word_line_above(lines, j, start):
                    break              # 这是下一个词条的读音
                # 空前缀说明这行**就是**一条独立音标（多音词的第二读音），
                # 不是变体注 —— `_is_equals_note('')` 返回 True，不挡住的话
                # 会把 `transplant` 的第二个读音一起丢掉
                if npre and _is_equals_note(npre):
                    j += 1             # `（= 异体词）/ 音标 /`：整行是变体注，丢掉
                    continue
                # 同一个词的另一个读音。音标前面的文字仍属**上一组**，
                # 之后才开新组（`，散布 / diˈfjuːs /` 里的「，散布」接的是前半句）
                v = npre.strip().lstrip('）)】」》').strip()
                if v and not _is_equals_note(v):
                    groups[-1].append(v)
                phones.append(nphon)
                groups.append([])
                j += 1
                continue
            if is_new_entry(lines, j, end):
                break
            for kind, val in split_phonetic_parts(s):
                if kind == 'p':
                    phones.append(val)
                    groups.append([])
                else:
                    v = val.strip().lstrip('）)】」》').strip()
                    if v and not _is_equals_note(v):
                        groups[-1].append(v)
            j += 1

        word = RE_WORD.match(wline).group(1)
        phonetic = ' '.join('/%s/' % re.sub(r'\s+', ' ', p).strip() for p in phones)
        meaning = ' / '.join(x for x in (smart_join(g) for g in groups) if x)
        out.append((word, phonetic, meaning, derived))
        k = max(j, k + 1)
    return out


def find_sections(lines):
    """找出「真」的 LIST 分节 → [(起始行, 编号, 结束行)]

    书里每个 Day 前面有一页封面，写着 `LIST 1` / `LIST 2`（不补零），紧接着
    才是补零的正文标题 `LIST 01`。判据：**标题后面到下一个标题之间要有音标行**。
    补零后 `LIST 10` 和封面页会重名，靠这个判据才能区分。
    """
    heads = []
    for k, ln in enumerate(lines):
        m = RE_HEAD.match(ln.strip())
        if m:
            heads.append((k, int(m.group(1))))
    out = []
    for i, (k, num) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(lines)
        if any(phon_in_line(lines[x]) for x in range(k + 1, end)):
            out.append((k, num, end))
    # 最后一个 LIST 后面没有下一个标题了，得自己封口：书末的「索引 / 附录」等
    # 章节标题就是边界。不封口的话，末条释义会把整份索引吞进去。
    if out:
        k, num, _ = out[-1]
        stop = len(lines)
        for x in range(k + 1, len(lines)):
            if lines[x].strip() in STOP_HEADS:
                stop = x
                break
        out[-1] = (k, num, stop)
    return out


def extract(path):
    """→ (sections, stats)；sections = [(编号, [词条...])]"""
    book = mobi_extract.MobiBook(path)
    text = book.plain_text()
    lines = text.split('\n')
    secs = find_sections(lines)
    if not secs:
        raise SystemExit('在这本书里没找到 `LIST nn` 分节标题，可能不是同一本书')
    result = []
    for k, num, end in secs:
        result.append((num, parse_section(lines, k + 1, end)))
    stat = {
        'chars': len(text),
        'lines': len(lines),
        'sections': len(result),
        'entries': sum(len(e) for _, e in result),
    }
    return result, stat


# ---------------------------------------------------------------- 输出

def dump_text(sections, title):
    """生成 import_words.py 能直接吃的规范词表（`LIST nn` 分节 + `词|音标|释义`）"""
    buf = ['// %s' % title,
           '// 由 tools/mobi_words.py 从电子书抽取，格式：单词|音标|释义']
    for num, entries in sections:
        buf.append('')
        buf.append('LIST %02d' % num)
        for w, ph, ma, _ in entries:
            buf.append('%s|%s|%s' % (clean_field(w), clean_field(ph),
                                     clean_field(ma) or '（无释义）'))
    return '\n'.join(buf) + '\n'


def _plain_word(s):
    return re.sub(r"[\s\-'’\.]", '', s).lower()


def index_words(text, title='索引', stop='附录'):
    """从书末「索引」里抽出全部词 → set

    索引是现成的「权威词表」，拿来交叉校验抽取结果：差集里如果全是
    英/美拼写变体，说明抽得干净。
    """
    lines = text.split('\n')
    # 「索引」在开头的目录页里也会出现一次，要取**最后**那处（正文里的真节），
    # 再往后找「附录」当结束边界 —— 顺序扫会一头撞进目录页，读到 0 个词
    starts = [k for k, l in enumerate(lines) if l.strip() == title]
    if not starts:
        return set()
    a = starts[-1]
    b = len(lines)
    for k in range(a + 1, len(lines)):
        if lines[k].strip() == stop:
            b = k
            break
    out = set()
    for l in lines[a + 1:b]:
        s = l.strip()
        if not s or re.match(r'^[A-Z]$', s):
            continue
        if RE_WORD.match(s) and not RE_POS_BARE.match(s):
            out.add(s.lower())
    return out


def report_text(sections, title, per_day=2, index=None, source='', notes=()):
    """生成人工核对用的可读稿：按 Day / LIST 分组，一行一个词条"""
    total = sum(len(e) for _, e in sections)
    buf = []
    buf.append('%s' % ('=' * 76))
    buf.append('  %s · 词表核对稿' % title)
    if source:
        buf.append('  源文件：%s' % source)
    buf.append('  %d 个 LIST / %d 天，共 %d 个词条' % (
        len(sections), (len(sections) + per_day - 1) // per_day, total))
    buf.append('  每行格式：序号. 单词   音标   释义（`+` 表示书里的派生词）')
    buf.append('%s' % ('=' * 76))

    wmax = 0
    for _, es in sections:
        for w, _, _, _ in es:
            wmax = max(wmax, len(w))
    wmax = min(max(wmax, 10), 24)

    for i, (num, es) in enumerate(sections):
        if i % per_day == 0:
            day = i // per_day + 1
            span = sections[i:i + per_day]
            buf.append('')
            buf.append('┌' + '─' * 74)
            buf.append('│ Day %-2d 第%d天        含 %s' % (
                day, day, '、'.join('LIST %02d' % n for n, _ in span)))
            buf.append('└' + '─' * 74)
        buf.append('')
        buf.append('■ LIST %02d   （%d 词）' % (num, len(es)))
        for j, (w, ph, ma, dv) in enumerate(es, 1):
            mark = '+' if dv else ' '
            buf.append('%4d.%s%-*s  %-22s  %s' % (j, mark, wmax, w, ph, ma))

    if index is not None:
        ext = set()
        for _, es in sections:
            for w, _, _, _ in es:
                ext.add(w.lower())
        only_idx = sorted(index - ext)
        only_ext = sorted(ext - index)
        buf.append('')
        buf.append('=' * 76)
        buf.append('  校对：与书末「索引」比对')
        buf.append('=' * 76)
        buf.append('  索引 %d 词 / 抽取 %d 词' % (len(index), len(ext)))
        if not only_idx and not only_ext:
            buf.append('  完全一致 ✔')
        else:
            if only_idx:
                buf.append('  · 只在索引、正文没抽到（%d）：' % len(only_idx))
                for k in range(0, len(only_idx), 6):
                    buf.append('      ' + '  '.join(only_idx[k:k + 6]))
            if only_ext:
                buf.append('  · 正文抽到、索引里没有（%d）：' % len(only_ext))
                for k in range(0, len(only_ext), 6):
                    buf.append('      ' + '  '.join(only_ext[k:k + 6]))
            buf.append('  说明：本书正文用美式拼写、索引额外收了英式拼写，'
                       '差集里成对出现的（analyse/analyze）属正常。')

    if notes:
        buf.append('')
        buf.append('=' * 76)
        buf.append('  人工备注（--note 传入）')
        buf.append('=' * 76)
        for n in notes:
            for line in str(n).split('\\n'):
                buf.append('  ' + line)
    return '\n'.join(buf) + '\n'


def main():
    ap = argparse.ArgumentParser(description='从 MOBI 电子书抽词表（零依赖）')
    ap.add_argument('source')
    ap.add_argument('--dump', default='', help='输出规范化词表的路径（txt）')
    ap.add_argument('--report', default='',
                    help='输出人工核对稿（按 Day / LIST 分组，逐词列出）')
    ap.add_argument('--vs-index', action='store_true',
                    help='核对稿末尾附上「与书末索引比对」的结果')
    ap.add_argument('--note', action='append', default=[],
                    metavar='文本', help='写进核对稿末尾的人工备注，可重复')
    ap.add_argument('--review', type=int, default=0,
                    help='打印前 N 条词条供人工核对')
    ap.add_argument('--stats', action='store_true', help='只打印统计')
    ap.add_argument('--no-derived', action='store_true',
                    help='不要「派生词」块里的词（默认收）')
    ap.add_argument('--import', dest='do_import', action='store_true',
                    help='顺便调用 import_words.py 生成词库 JS')
    ap.add_argument('--name', default='', help='词库显示名（配合 --import）')
    ap.add_argument('--per-day', type=int, default=2,
                    help='每 N 个 LIST 折成一天（默认 2，对应 20 LIST = 10 天）')
    ap.add_argument('--out', default='', help='词库 JS 输出路径（配合 --import）')
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit('找不到文件：%s' % args.source)

    title = args.name or os.path.splitext(os.path.basename(args.source))[0]
    sections, stat = extract(args.source)
    if args.no_derived:
        sections = [(n, [e for e in es if not e[3]]) for n, es in sections]

    print('文件        %s' % title)
    print('正文        %d 字符 / %d 行' % (stat['chars'], stat['lines']))
    print('分节        %d 个 LIST' % len(sections))
    total = sum(len(e) for _, e in sections)
    print('词条        %d 条（含派生词）' % total)
    if args.stats:
        for num, es in sections:
            miss_ma = sum(1 for e in es if not e[2])
            print('  LIST %02d   %3d 条   无释义 %d' % (num, len(es), miss_ma))
        return

    if args.review:
        n = 0
        for num, es in sections:
            if n >= args.review:
                break
            print('\n=== LIST %02d ===' % num)
            for w, ph, ma, dv in es:
                if n >= args.review:
                    break
                print('%s%s | %s | %s' % ('+' if dv else ' ', w, ph, ma))
                n += 1
        return

    if args.dump:
        with io.open(args.dump, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dump_text(sections, title))
        print('词表        %s' % args.dump)

    if args.report:
        idx = None
        if args.vs_index:
            idx = index_words(mobi_extract.MobiBook(args.source).plain_text())
            print('索引        %d 词（书末「索引」节）' % len(idx))
        with io.open(args.report, 'w', encoding='utf-8', newline='\n') as f:
            f.write(report_text(sections, title, per_day=args.per_day,
                                index=idx, source=args.source, notes=args.note))
        print('核对稿      %s' % args.report)

    if args.do_import:
        import subprocess
        src = args.dump or args.source
        cmd = [sys.executable, os.path.join(HERE, 'import_words.py'), src,
               '--per-day', str(args.per_day)]
        if args.name:
            cmd += ['--name', args.name]
        if args.out:
            cmd += ['--out', args.out]
        print('\n调用 import_words.py …')
        raise SystemExit(subprocess.call(cmd))


if __name__ == '__main__':
    main()
