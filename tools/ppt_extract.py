#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从旧版 .ppt（PowerPoint 97-2003）里把文字抠出来 —— 纯标准库，不装任何东西。

为什么自己写：
    .pptx 是 zip+XML，标准库能直接读（import_words.py 里的 read_docx 就是这个套路）。
    但 .ppt 是 OLE2 复合文档（文件头 D0CF11E0），文字以「记录」形式塞在
    PowerPoint Document 流里，得先解析 FAT 扇区链才能拿到。本机 pip 连不上源，
    索性按公开规范自己撸一个。

用到的两份规范：
    [MS-CFB]  Compound File Binary File Format    —— 怎么从扇区里拼出「流」
    [MS-PPT]  PowerPoint (.ppt) Binary File Format —— 怎么从「流」里切出「记录」

已实测的样本：《十天搞定考研词汇》真题核心词汇精讲 .ppt
    26 MB，其中 25 MB 是图片，正文流只有 553 KB；58 张幻灯片，文字全在
    幻灯片容器的文本记录里（大纲文本为空）。提取结果与讲义内容逐条一致。

命令行：
    python tools/ppt_extract.py 词表.ppt                  # 按页打印，人眼看
    python tools/ppt_extract.py 词表.ppt --txt out.txt    # 落成 txt（--raw 保留原始行）
    python tools/ppt_extract.py 词表.ppt --json out.json
    python tools/ppt_extract.py 词表.ppt --probe          # 体检：流列表 / 记录自检
"""

import argparse
import io
import json
import os
import re
import struct
import sys

# ============================================================ [MS-CFB] 复合文档

FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE

OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

STGTY_STREAM = 2

PPT_STREAM = 'PowerPoint Document'


def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def _u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


class DirEntry(object):
    __slots__ = ('name', 'etype', 'child', 'start', 'size')

    def __init__(self, name, etype, start, size):
        self.name = name
        self.etype = etype
        self.start = start
        self.size = size

    def __repr__(self):
        return '<DirEntry %r type=%d size=%d>' % (self.name, self.etype, self.size)


class CompoundFile(object):
    """OLE2 复合文档：列流、按名取流"""

    def __init__(self, path):
        with io.open(path, 'rb') as f:
            self.data = f.read()
        if self.data[:8] != OLE_MAGIC:
            raise ValueError('不是 OLE2 复合文档（旧版 .ppt 应为 D0CF11E0 开头）。'
                             '.pptx 是 zip，会以 PK 开头，请用别的方式处理')
        if len(self.data) < 512:
            raise ValueError('文件太小，不像 .ppt')

        d = self.data
        self.sector_size = 1 << _u16(d, 0x1E)
        self.mini_sector_size = 1 << _u16(d, 0x20)
        self.dir_start = _u32(d, 0x30)
        self.mini_cutoff = _u32(d, 0x38)
        self.mini_fat_start = _u32(d, 0x3C)
        self.difat_start = _u32(d, 0x44)
        self.num_fat = _u32(d, 0x2C)
        if self.sector_size not in (512, 4096):
            raise ValueError('扇区大小异常：%d' % self.sector_size)

        self.fat = self._read_fat()
        self.directory = self._read_directory()
        self._mini = None

    # -------------------------------------------------- 扇区 / 链

    def _sector(self, n):
        """第 n 号扇区（扇区 0 紧跟在 512 字节文件头之后）"""
        off = (n + 1) * self.sector_size
        return self.data[off:off + self.sector_size]

    def _read_fat(self):
        """DIFAT → FAT。头里自带 109 个 FAT 扇区号，不够就去 DIFAT 扇区接着拿"""
        secs = [_u32(self.data, 0x4C + 4 * i) for i in range(109)]
        secs = [x for x in secs if x != FREESECT]
        sec = self.difat_start
        per = self.sector_size // 4 - 1
        guard = 0
        while sec not in (ENDOFCHAIN, FREESECT) and guard < 1 << 20:
            guard += 1
            raw = self._sector(sec)
            secs.extend(_u32(raw, 4 * i) for i in range(per))
            sec = _u32(raw, 4 * per)
        fat = []
        for s in secs[:self.num_fat] if self.num_fat else secs:
            raw = self._sector(s)
            fat.extend(struct.unpack_from('<%dI' % (self.sector_size // 4), raw, 0))
        return fat

    def _read_chain(self, start, fat, size=None):
        buf = bytearray()
        sec, seen = start, set()
        while sec not in (ENDOFCHAIN, FREESECT) and sec < len(fat):
            if sec in seen:
                break
            seen.add(sec)
            buf += self._sector(sec)
            if size is not None and len(buf) >= size:
                break
            sec = fat[sec]
        return bytes(buf[:size]) if size is not None else bytes(buf)

    def _read_directory(self):
        raw = self._read_chain(self.dir_start, self.fat)
        out = []
        for off in range(0, len(raw) - 127, 128):
            nlen = _u16(raw, off + 0x40)
            name = raw[off:off + max(0, nlen - 2)].decode('utf-16-le', 'replace')
            out.append(DirEntry(name, raw[off + 0x42],
                                _u32(raw, off + 0x74), _u64(raw, off + 0x78)))
        return out

    def _mini_stream(self):
        """小于 4096 字节的流不走 FAT，统一挤在 Root 的 mini stream 里"""
        if self._mini is None:
            root = self.directory[0]
            ms = self._read_chain(root.start, self.fat)
            raw = self._read_chain(self.mini_fat_start, self.fat)
            mf = list(struct.unpack_from('<%dI' % (len(raw) // 4), raw, 0)) if raw else []
            self._mini = (ms, mf)
        return self._mini

    def stream(self, name):
        for e in self.directory:
            if e.etype == STGTY_STREAM and e.name == name:
                if e.size == 0:
                    return b''
                if e.size < self.mini_cutoff:
                    ms, mf = self._mini_stream()
                    buf, sec, seen = bytearray(), e.start, set()
                    while sec not in (ENDOFCHAIN, FREESECT) and sec < len(mf):
                        if sec in seen:
                            break
                        seen.add(sec)
                        o = sec * self.mini_sector_size
                        buf += ms[o:o + self.mini_sector_size]
                        sec = mf[sec]
                    return bytes(buf[:e.size])
                return self._read_chain(e.start, self.fat, e.size)
        return None

    def names(self):
        return [e.name for e in self.directory if e.etype == STGTY_STREAM]


# ============================================================ [MS-PPT] 记录流

RT_DOCUMENT = 0x03E8
RT_NOTES = 0x03F0
RT_MAIN_MASTER = 0x03F8
RT_SLIDE = 0x03EE
RT_SLIDE_PERSIST_ATOM = 0x03F3
RT_TEXT_CHARS_ATOM = 0x0FA0
RT_TEXT_BYTES_ATOM = 0x0FA8
RT_CSTRING = 0x0FBA
RT_SLIDE_LIST_WITH_TEXT = 0x0FF0
RT_USER_EDIT_ATOM = 0x0FF5
RT_PERSIST_DIRECTORY_ATOM = 0x6002

TEXT_ATOMS = (RT_TEXT_CHARS_ATOM, RT_TEXT_BYTES_ATOM, RT_CSTRING)
SLIDE_TYPES = (RT_SLIDE, RT_NOTES, RT_MAIN_MASTER)


def iter_records(data, start=0, end=None, depth=0):
    """深度优先遍历记录，产出 (偏移, recVer, recInstance, recType, 载荷起, 载荷长, 深度)

    记录头 8 字节：recVer(低4位)+recInstance(高12位) | recType(2B) | recLen(4B)
    recVer == 0xF 表示「容器」，载荷里还套着子记录。
    载荷长度越界就停 —— 那通常意味着这里已经跑出该记录的边界了。
    """
    if end is None:
        end = len(data)
    pos = start
    while pos + 8 <= end:
        vi, rt, rl = struct.unpack_from('<HHI', data, pos)
        ver, inst = vi & 0xF, vi >> 4
        body = pos + 8
        if body + rl > end:
            return
        yield pos, ver, inst, rt, body, rl, depth
        if ver == 0xF:
            for sub in iter_records(data, body, body + rl, depth + 1):
                yield sub
        pos = body + rl


def top_records(data):
    """只走第一层记录（不递归）。返回 [(偏移, recVer, recInstance, recType, 载荷起, 载荷长), ...]"""
    pos = 0
    out = []
    while pos + 8 <= len(data):
        vi, rt, rl = struct.unpack_from('<HHI', data, pos)
        if pos + 8 + rl > len(data):
            break
        out.append((pos, vi & 0xF, vi >> 4, rt, pos + 8, rl))
        pos += 8 + rl
    return out


def decode_text(rt, payload):
    """文字记录 → str。CharsAtom 是 UTF-16LE；BytesAtom 是单字节码页"""
    if rt in (RT_TEXT_CHARS_ATOM, RT_CSTRING):
        s = payload.decode('utf-16-le', 'replace')
    else:
        try:
            s = payload.decode('utf-8')
        except UnicodeDecodeError:
            s = payload.decode('cp1252', 'replace')
    # 记录里拿 \r 和 \v 当换行
    s = s.replace('\r', '\n').replace('\v', '\n')
    return s.replace('\x00', '')


def region_lines(data, start, end):
    """某个区域里所有文字记录，按出现顺序摊成行"""
    out = []
    for _p, _v, _i, rt, b, l, _d in iter_records(data, start, end):
        if rt in TEXT_ATOMS:
            for ln in decode_text(rt, data[b:b + l]).split('\n'):
                ln = ln.strip()
                if ln:
                    out.append(ln)
    return out


# ============================================================ 文字清洗
#
# 课件和纸质书不一样：书上排版还算整齐，课件是「一页一页往上加内容」的
# 讲稿，混着大量版式噪声。这些噪声如果原样交给 import_words.py，会把
# 例句、考频标注、模板占位符当成释义接到单词后面去，所以先在提取这层洗掉。
#
# 典型噪声（都是实测样本里真实出现的）：
#     1、intellect [...]        ← 行首序号「1、」
#     1）elect [...]            ← 行首序号「1）」
#     ●intellectual [...]       ← 行首项目符号
#     * collection [...]        ← 行首星号
#     2个派生词：                ← 纯标签行
#     考点搭配：infuse sb. ...   ← 考点搭配（书里例句，App 用不上）
#     译文：Hofstadter 说……      ← 译文
#     lect-表示「选择；收集」      ← 词根释义行
#     【2015-1-1、2014-1-1】      ← 真题出处
#     List 1-1（page 2）         ← 页眉
#     ___PPT9 / 单击此处编辑母版标题样式  ← 母版残留
#     claimant['kleɪmənt]n. ……   ← 词和音标黏在一起（课件里很常见）

# 行首序号 / 项目符号（含 `49-50、` 这种区间序号）
BULLET_RE = re.compile(
    r'^(?:\s*(?:[（(\[【]?\s*(?:\d{1,3}(?:\s*[-—–]\s*\d{1,3})?'
    r'|[一二三四五六七八九十]{1,3})\s*[)）\]】、.．:：,，]'
    r'|[●○◎◆◇■□▲△▶►★☆·•※→✓✔*✱✳❖✦✧§†‡])\s*)+')
# 标签行：`派生词：` `考点搭配：` `译文：` …
# 「相关词 / 反 / 近义 / 同义 / 区别N词」都是在列**真词条**（带音标带释义，如
# `相关词：modest [ˈmɔdist] a. 谦虚的`、`区别1词：attack [ə'tæk]（v./n.）进攻，攻击`），
# 所以按「保留内容」处理。
# 顺序要求：长标签排在短标签前面，否则 `反义词：` 会被 `反` 抢先匹配、
# `同义词：` 会被 `同义` 抢先匹配。
LABEL_RE = re.compile(
    r'^(派生词|近义词|反义词|同义词|形近词|相关词|关联词|区别\s*\d*\s*词|考点搭配|搭配|'
    r'记忆法|记忆|助记|例句|译文|词根|扩展|联想|辨析|注意|提示|出处|延伸|补充|'
    r'反|近义|同义)\s*[：:]\s*')
# 只保留标签后面的内容，标签本身连内容一起丢的
DROP_LABELS = {'考点搭配', '搭配', '译文', '词根', '例句', '联想', '记忆法', '记忆',
               '辨析', '助记', '提示', '注意', '出处'}
# `2个派生词：` 这种纯计数标签
COUNT_LABEL_RE = re.compile(r'^\d{1,3}\s*个?\s*(?:派生词|近义词|反义词|同义词|词)\s*[：:]?$')
# 词根释义行：`lect-表示「选择；收集」`
ROOT_RE = re.compile(r'^[A-Za-z][A-Za-z\-]{0,20}\s*[-－—]\s*表示\s*[「“"\'\u2018]')
# 构词法标注行 —— 整行丢弃，它们讲的是词根词缀，不是词条。实测形态：
#     liber-词根，自由，随意        tain-词根，拿住，保持      ject-词根，投掷，扔
#     ment-名词后缀   er-后缀   y-名词后缀
#     clar=clear-表示「清楚；清晰」  pel=puls-表示「拖，拉，拽；驱动」
#     capt-侧重表示「抓」           cogn-/gno(r)-表示「知道；了解；知晓」
#     gno=gnor-也表示「…」          sembl-=simil-=simul-均表示「相同；相像」
#     t = ment-名词后缀             cent-=sent-表示「感觉」
ROOT_TAG_RE = re.compile(
    r'^[A-Za-z\-/()\s]{1,32}[-—－–]\s*(?:侧重|也|均|又)?\s*'
    r'[\u4e00-\u9fa5]{0,3}?(?:词根|词缀|前缀|后缀|表示|由|即|源自|来自|简写)')
# 带等号、又带「词根/词缀/表示」字样的行 —— 构词式（形如 `A=B-表示…`）
ROOT_WORD_RE = re.compile(r'词根|词缀|前缀|后缀|表示')
# 构词式的另一种写法：等号右边接「派生词-中文」，没有「表示」二字
#     benefi = beneficial-有益的，有利的        tern = turn-转变
ROOT_EQ_RE = re.compile(
    r'^[A-Za-z\-/()]{1,24}\s*[=＝]\s*[A-Za-z\-/()]{1,24}\s*[-—－–]\s*\S')
# 中文起头的构词说明：`在文中表示“生僻”含义：记录器；寄存器；计数器`
ROOT_CN_RE = re.compile(
    r'^[\u4e00-\u9fa5]{1,12}(?:也|均|又)?(?:侧重)?表示\s*[「“"\'\u2018]')
# 讲解型中文行（整行丢）：
#     `与touch有关的单词`   `与“turn”有关的单词`   `相关词`   `词族`
EXPLAIN_CN_RE = re.compile(
    r'^(?:与|和|跟)?\s*[^\s]{0,40}?\s*'
    r'(?:有关的单词|相关词|同根词|同源词|词族|词群|词形变化)$')
# 词与音标黏连：`claimant['kleɪmənt]` → `claimant ['kleɪmənt]`
GLUE_BRACKET_RE = re.compile(r'(?<=[A-Za-z])\[')
# 行首是「英文词紧贴中文」→ 是短语/搭配行而不是词条，如 `presidential election总统选举`
GLUED_CN_RE = re.compile(r'^[A-Za-z][A-Za-z\-\'’\.]*[\u4e00-\u9fa5]')
# 短语 / 搭配行：`holiday camp`、`live off campus`、`modern democratic states 现代的民主国家`
# 判据是「行首有 ≥2 个空格分隔的英文单词」，而且整行没有真正的音标括号。
# 反例保护：
#   `Anti-immigrant 反对移民` / `mega-project 特大项目` —— 只有一个英文片段（连字符不算分隔）
#   `collect vt. 收集` —— 首个英文片段后面紧跟 `.`，说明 `vt.` 是词性不是第二个词
#   `over-the-counter ['əuvəðə'kauntə] a. …` —— 有 `[...]`，是词条
PHRASE_HEAD_RE = re.compile(
    r"^[A-Za-z][A-Za-z\-'’]*(?:\s+[A-Za-z][A-Za-z\-'’]*)+")
# 真正的音标括号：成对的 `[...]` 或 `/.../`（`serve as /on 担任…` 只有半个斜杠，不算）
PHONETIC_PAIR_RE = re.compile(r'\[[^\]]{1,60}\]|/[^/\s][^/]{0,58}/')
# 真题出处：`【2015-1-1、2014-1-1】`、`（2004年-阅读理解Text4）`、`( 1999年阅读Text 2)`
PROV_RE = re.compile(r'【[^】]{0,90}】|[（(]\s*[^（）()]{0,50}?(?:19|20)\d{2}[^（）()]{0,50}?[）)]')
# 页眉页脚：`List 1-1（page   2  ）`
PAGE_RE = re.compile(r'^List\s*\d+\s*[-—]\s*\d+.*$', re.I)
# 母版残留
MASTER_RE = re.compile(r'^_{2,}PPT\d*$|^单击此处编辑|^第[一二三四五六七八九十]级$|^Office\s*主题')
# 「主讲教师：刘文涛」「微信 liuwentaoteacher」这类页眉信息行
META_CN_RE = re.compile(r'^[\u4e00-\u9fa5]{1,8}[\s：:]')
# 释义行常见的词性前缀（判断「这行像不像释义」用）
POS_LIKE_RE = re.compile(
    r'^(?:(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|aux|abbr|pl|a|ad)\.)')

# 长句/花絮的判定阈值
SENTENCE_LEN = 60      # 不含中文、又超过这个长度 → 是例句，不是词条
NOISE_CN_LEN = 8       # 纯中文、又没有词性和分号、还超过这个长度 → 是花絮，不是释义

# 是否丢掉「短语 / 搭配」行（`holiday camp`）。默认丢 —— 它们是讲解举例，
# 而且拼写配不上内置词库，留着只会变成一堆没有音标、没有释义的空卡片。
# 想让短语也进词库就传 drop_phrases=False（CLI 是 import_words.py 的 --keep-phrases）。


def _looks_like_noise(t):
    """两类整行噪声：

    1. 纯中文、没有词性前缀也没有分号的长句 —— 课件里的花絮/故事，
       例如「哈工大航天学院38岁教授高会军」。留着会被当成上一条单词的
       释义续行接上去，把释义弄脏。
    2. 不含中文的超长行 —— 真题长例句。
    """
    if not re.search(r'[\u4e00-\u9fa5]', t):
        return len(t) > SENTENCE_LEN
    if re.search(r'[A-Za-z]', t) or POS_LIKE_RE.match(t):
        return False
    if '；' in t or ';' in t:
        return False
    return len(t) > NOISE_CN_LEN


def split_glued(t):
    """把「单词和音标黏在一起」的地方补上空格

        claimant['kleɪmənt]n. 要求者   → claimant ['kleɪmənt]n. 要求者
        claimant/ˈkleɪmənt/ n. 要求者   → claimant /ˈkleɪmənt/ n. 要求者
        abandon /əˈbændən/ vt. 抛弃     → 原样不动（收尾斜杠不能被当成开引斜杠）

    `*` 和 `[` 好判断，`/` 不行 —— 开引和收尾长得一模一样。所以数一下：
    第偶数个 `/` 是开引斜杠，奇数个是收尾。
    """
    out, seen_slash = [], 0
    for i, ch in enumerate(t):
        if ch == '[' and i and t[i - 1].isalpha():
            out.append(' ')
        elif ch == '/':
            if seen_slash % 2 == 0 and i and t[i - 1].isalpha():
                out.append(' ')
            seen_slash += 1
        out.append(ch)
    return ''.join(out)


def _looks_like_phrase(t):
    """短语 / 搭配行 —— 课件里老师用它们举例，不是要背的词。

    `holiday camp`、`live off campus`、`modern democratic states 现代的民主国家`、
    `the Department of Justice 司法部`、`in spite of 尽管`

    判据：行首有 ≥2 个空格分隔的英文单词，且整行没有成对的音标括号。
    有音标的一律放过（`over-the-counter ['əuvəðə'kauntə] a. …` 是词条）；
    首个英文片段后面紧跟 `.` 的也放过（`collect vt. 收集` 的 `vt.` 是词性）。
    """
    if PHONETIC_PAIR_RE.search(t):
        return False
    m = PHRASE_HEAD_RE.match(t)
    if not m:
        return False
    if t[m.end():m.end() + 1] == '.':
        return False
    return True


def clean_line(s, drop_phrases=True):
    """清洗一行课件文字；返回 '' 表示这行应整行丢弃

    检查顺序有讲究（每一条都是被真实文件坑出来的）：

      1. 剥行首序号/符号 —— **要早**。`（2）相关词：`、`1）反：subject […]` 这类行
         剥掉序号之后才认得出是标签行；不先剥就会当成释义续行接到上一个单词后面。
      2. 认标签（派生词/相关词/反…）—— 必须在「页眉信息行」之前。
         `派生词：election […]` 和 `主讲教师：刘文涛` 长得一样，都是「几个字＋冒号」，
         先认标签才不会把 `派生词：` 后面的真词一起丢掉。
      3. 摘真题出处 —— 必须在噪声判定之前，否则带中文出处的英文长句会因为
         「含中文」而漏网。
      4. 噪声判定放最后。
    """
    t = re.sub(r'[\s\u3000]+', ' ', s or '').strip()
    if not t:
        return ''

    if MASTER_RE.match(t) or PAGE_RE.match(t) or COUNT_LABEL_RE.match(t):
        return ''

    # ① 剥行首序号 / 项目符号
    t = BULLET_RE.sub('', t).strip()
    if not t:
        return ''

    # 以 `=` 开头的行是讲解式搭配（`= rules of conduct 行为准则`）
    if t[0] in '=＝':
        return ''
    if ROOT_RE.match(t) or ROOT_TAG_RE.match(t) or ROOT_CN_RE.match(t) or ROOT_EQ_RE.match(t):
        return ''
    if EXPLAIN_CN_RE.match(t):
        return ''
    # 构词式：`clar=clear-表示「清楚；清晰」`、`t = ment-名词后缀`
    # （要求同时出现等号和「词根/词缀/表示」字样，避免误伤释义里的字样）
    if ('=' in t or '＝' in t) and ROOT_WORD_RE.search(t):
        return ''

    # 真题出处标记整行丢；行内的抠掉（`n. 选举；当选【2015-1-1】` → `n. 选举；当选`）
    if PROV_RE.fullmatch(t):
        return ''
    t = PROV_RE.sub('', t).strip()

    # ② 认标签
    m = LABEL_RE.match(t)
    if m:
        if m.group(1) in DROP_LABELS:
            return ''
        t = t[m.end():].strip()
        if not t:
            return ''

    # ③ `主讲教师：刘文涛`「微信 liuwentaoteacher」这类页眉信息行
    if META_CN_RE.match(t):
        return ''

    # `presidential election总统选举`：抠出来的「单词」其实是短语的前半截，丢掉。
    # 但 `elect vt.选举`（词性紧贴释义）要放过 —— 那是个正经词条。
    head = t.split(' ', 1)
    if len(head) > 1 and GLUED_CN_RE.match(head[1]) and not POS_LIKE_RE.match(head[1]):
        return ''

    # 短语 / 搭配行（可用 drop_phrases=False 关掉）
    if drop_phrases and _looks_like_phrase(t):
        return ''

    # ④ 噪声判定放在最后
    if _looks_like_noise(t):
        return ''

    return split_glued(t).strip()


def clean_lines(lines, drop_phrases=True):
    """整页清洗：逐行洗 + 丢掉页内重复行（课件是逐页叠加的，同一行会反复出现）"""
    out, seen = [], set()
    for s in lines:
        c = clean_line(s, drop_phrases)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


# ============================================================ 提取

class PptText(object):
    """.ppt 的文字提取结果"""

    def __init__(self, path, kinds=(RT_SLIDE,), drop_phrases=True):
        self.path = path
        self.kinds = tuple(kinds)
        self.drop_phrases = drop_phrases
        self.warnings = []
        self.cfb = CompoundFile(path)
        raw = self.cfb.stream(PPT_STREAM)
        if raw is None:
            raise ValueError('这个文件里没有 %s 流，可能不是 PowerPoint 文件' % PPT_STREAM)
        self.stream = raw
        self.slides = self._extract()

    # ------------------------------------------------ 定位每一页

    def _extract(self):
        """按「顶层记录」顺序取每一页的文字。

        为什么不顺着 UserEditAtom → PersistDirectory → SlideListWithText 走官方路线：
          那套是给「反复保存过的大文件」准备的（旧版本记录不删，必须靠持久目录
          挑出当前生效的那一份）。但实测样本恰恰是个反例 —— 它没有持久目录记录，
          官方路线直接断在半路。而顶层记录天然是按页顺序平铺的：
          实测 78 条顶层记录正好铺满整个流，58 张幻灯片依序排开，
          提取顺序与讲义页码 List 1-1 → List 1-5 完全一致。
        所以：顶层平铺为主；万一某文件把幻灯片埋在容器里，再递归兜一遍。
        """
        found = []
        for pos, _v, _i, rt, b, l in top_records(self.stream):
            if rt in self.kinds:
                found.append((pos, b, l))
        if not found:
            self.warnings.append('顶层没有 %s 记录，改递归查找（顺序可能不准）'
                                 % '/'.join('0x%04X' % k for k in self.kinds))
            for pos, _v, _i, rt, b, l, _d in iter_records(self.stream):
                if rt in self.kinds:
                    found.append((pos, b, l))
        # 第一层记录没铺满整个流 → 说明结构有偏差，提醒一句
        tops = top_records(self.stream)
        if tops and tops[-1][0] + 8 + tops[-1][5] != len(self.stream):
            self.warnings.append('顶层记录没能铺满整个流，尾部 %d 字节未解析'
                                 % (len(self.stream) - tops[-1][0] - 8 - tops[-1][5]))

        out = []
        for _pos, b, l in found:
            lines = region_lines(self.stream, b, b + l)
            if lines:
                out.append(lines)
        return out

    # ------------------------------------------------ 对外

    @property
    def clean_slides(self):
        """清洗后的每页文字（导入用这个）"""
        return [c for c in (clean_lines(s, self.drop_phrases) for s in self.slides) if c]

    def as_text(self, raw=False):
        slides = [list(s) for s in self.slides] if raw else self.clean_slides
        return '\n\n'.join('\n'.join(s) for s in slides)

    def as_json(self, raw=False):
        slides = [list(s) for s in self.slides] if raw else self.clean_slides
        return [{'no': i + 1, 'lines': s} for i, s in enumerate(slides)]

    def stats(self, raw=False):
        slides = self.slides if raw else self.clean_slides
        return {'slides': len(slides), 'lines': sum(len(s) for s in slides)}


def read_ppt_text(path, raw=False, drop_phrases=True):
    """给 import_words.py 用：返回 (每页文字行的二维列表, PptText 对象)"""
    p = PptText(path, drop_phrases=drop_phrases)
    return ([list(s) for s in p.slides] if raw else p.clean_slides), p


# ============================================================ 命令行

def main():
    ap = argparse.ArgumentParser(description='从旧版 .ppt 里抠文字（纯标准库）')
    ap.add_argument('source')
    ap.add_argument('--txt', default='')
    ap.add_argument('--json', default='')
    ap.add_argument('--raw', action='store_true', help='不做清洗，输出原始行')
    ap.add_argument('--probe', action='store_true', help='体检：流列表 + 记录自检')
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit('找不到文件：%s' % args.source)

    size = os.path.getsize(args.source)
    print('文件            %s（%.1f MB）' % (args.source, size / 1048576.0))

    if args.probe:
        cfb = CompoundFile(args.source)
        print('扇区            %d 字节' % cfb.sector_size)
        print('流              共 %d 个' % len(cfb.names()))
        for n in cfb.names():
            s = cfb.stream(n)
            print('                %-36s %10d 字节' % (n, len(s) if s else 0))
        st = cfb.stream(PPT_STREAM)
        if st:
            tops = top_records(st)
            tail = len(st) - (tops[-1][0] + 8 + tops[-1][5]) if tops else len(st)
            print('')
            print('顶层记录        %d 条，铺设 %d/%d 字节%s' % (
                len(tops), len(st) - tail, len(st),
                '（尾部 %d 字节未解析）' % tail if tail else '（正好铺满，结构可信）'))
            from collections import Counter
            cnt = Counter(rt for _p, _v, _i, rt, _b, _l in tops)
            for rt, k in cnt.most_common(8):
                print('                type=0x%04X  %d 条' % (rt, k))
            print('')
            print('UserEditAtom    有（可为空）' if any(rt == RT_USER_EDIT_ATOM for _p, _v, _i, rt, _b, _l in tops) else 'UserEditAtom    无')
            print('PersistDirectory %s' % ('有' if any(rt == RT_PERSIST_DIRECTORY_ATOM for _p, _v, _i, rt, _b, _l in tops) else '无（走顶层平铺，正常）'))
        return 0

    p = PptText(args.source)
    for w in p.warnings:
        print('！%s' % w)
    s = p.stats(raw=args.raw)
    sr = p.stats(raw=True)
    print('提取            %d 页 / %d 行（原始 %d 行，清洗掉 %d 行噪声）'
          % (s['slides'], s['lines'], sr['lines'], sr['lines'] - s['lines']))
    for i, lines in enumerate(p.as_json(raw=args.raw)[:3]):
        print('--- 第 %d 页（%d 行）---' % (i + 1, len(lines['lines'])))
        for ln in lines['lines'][:8]:
            print('    %s' % ln[:74])

    if args.txt:
        with io.open(args.txt, 'w', encoding='utf-8', newline='\n') as f:
            f.write(p.as_text(raw=args.raw))
        print('写出 txt        %s' % args.txt)
    if args.json:
        with io.open(args.json, 'w', encoding='utf-8') as f:
            json.dump(p.as_json(raw=args.raw), f, ensure_ascii=False, indent=1)
        print('写出 json       %s' % args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
