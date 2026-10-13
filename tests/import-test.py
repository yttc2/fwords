#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/import_words.py 的回归测试。

重点覆盖「书版自然排版」——一个词条跨多行、夹着考点搭配与例句的排法。
这是最容易把释义行 / 例句行误当成新单词的地方，也是之前版本真的错了的地方。

运行：
    python tests/import-test.py
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
DATA = os.path.join(ROOT, 'data')
TMP = os.path.join(DATA, '_t')
IMPORT_PY = os.path.join(TOOLS, 'import_words.py')
REGISTRY = os.path.join(DATA, 'user-libs.json')

# 注册表存的是「老板导入过的全部词库」，是真实数据，测试绝不能碰坏它。
# 本测试里的端到端导入会真的往注册表里写，所以开跑前先整份备份，收尾时原样还原。
# 教训：早先收尾是无条件 os.remove(REGISTRY)，一跑测试就把老板已导入的词库清空了。
_REG_BACKUP = None
if os.path.isfile(REGISTRY):
    with open(REGISTRY, 'rb') as _f:
        _REG_BACKUP = _f.read()

PASS = 0
FAIL = 0


def ok(cond, label, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('PASS  ' + label)
    else:
        FAIL += 1
        print('FAIL  ' + label + ('    <- ' + extra if extra else ''))


def eq(actual, expect, label):
    ok(actual == expect, label, '期望 %r，实得 %r' % (expect, actual))


def load_module():
    spec = importlib.util.spec_from_file_location('import_words', IMPORT_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


IW = load_module()


def run_import(src, name, extra=(), fill=False):
    """跑一次导入，返回 (stdout, normalized_lines)。fill=False 时禁用词库补全。"""
    out = os.path.join(TMP, name + '.user.js')
    dump = os.path.join(TMP, name + '.txt')
    cmd = [sys.executable, IMPORT_PY, src, '--name', name, '--out', out,
           '--dump', dump] + list(extra)
    if not fill:
        cmd.append('--no-fill')
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    lines = []
    if os.path.isfile(dump):
        lines = io.open(dump, encoding='utf-8').read().splitlines()
    return (r.stdout or '') + (r.stderr or ''), lines


# ============================================================ 单行解析

print('=' * 60)
print('[1] parse_book_line —— 从行首抠出单词')
e = IW.parse_book_line('abandon /əˈbændən/ vt. 抛弃，放弃；遗弃')
ok(e is not None, '整行可解析')
eq(e[0], 'abandon', '单词 = abandon（不含音标与词性）')
eq(e[1], '/əˈbændən/', '音标被正确抽出')
ok(e[2].startswith('vt.') and '遗弃' in e[2], '释义保留词性前缀：' + repr(e[2]))

eq(IW.parse_book_line('abstract /ˈæbstrækt/ adj. 抽象的')[0], 'abstract', '第二个词同样正确')
eq(IW.parse_book_line('well-being /ˌwel ˈbiːɪŋ/ n. 幸福')[0], 'well-being', '连字符词条不被截断')
eq(IW.parse_book_line('acquire /əˈkwaɪə/  vt. 获得，取得；学到')[0], 'acquire', '音标前多余空格不影响')
eq(IW.parse_book_line('account')[0], 'account', '单独的单词行可解析（释义在下一行）')

print('')
print('[2] 非词条行必须被拒绝')
eq(IW.parse_book_line('n. 摘要，梗概'), None, '词性行 n. 摘要 -> 不是词条')
eq(IW.parse_book_line('vt. 加速，促进'), None, '词性行 vt. 加速 -> 不是词条')
eq(IW.parse_book_line('【考点搭配】abandon oneself to 沉溺于'), None, '【考点搭配】行 -> 不是词条')
eq(IW.parse_book_line('【例句】He abandoned his wife and children.'), None, '【例句】行 -> 不是词条')
eq(IW.parse_book_line('He abandoned his wife and children.'), None, '裸英文句子 -> 不是词条')
eq(IW.parse_book_line('abandon oneself to 沉溺于'), None, '裸考点搭配 -> 不是词条')
eq(IW.parse_book_line('五一劳动节'), None, '纯中文行 -> 不是词条')

print('')
print('[3] looks_like_meaning / section_kind')
ok(IW.looks_like_meaning('n. 账户；描述'), 'n. 账户；描述 -> 是释义续行')
ok(IW.looks_like_meaning('摘要，梗概'), '纯中文 -> 是释义续行')
ok(not IW.looks_like_meaning('abandon /əˈbændən/ vt. 抛弃'), '完整词条行 -> 不是续行')
ok(not IW.looks_like_meaning('List 3'), '分节标题 -> 不是续行')
eq(IW.section_kind('List 7'), 'list', "section_kind('List 7')")
eq(IW.section_kind('Day 2'), 'day', "section_kind('Day 2')")
eq(IW.section_kind('五星词汇'), 'tier', "section_kind('五星词汇')")
eq(IW.unit_number('List 7'), 7, "unit_number('List 7')")
eq(IW.unit_number('第3天'), 3, "unit_number('第3天')")

# ============================================================ 多行归并

print('')
print('[4] normalize —— 释义换行回接 + 标注丢弃')
text4 = '\n'.join([
    'List 1',
    'abandon /əˈbændən/ vt. 抛弃，放弃；遗弃',
    '【考点搭配】abandon oneself to 沉溺于',
    '【例句】He abandoned his wife and children.',
    'account',
    'n. 账户；描述',
    'vi. 解释',
    'access /ˈækses/ n. 通道，入口；接近',
    'vt. 存取（计算机文件）',
])
entries4, sections4, dropped4 = IW.normalize(text4)
words4 = [e[0] for e in entries4]
eq(words4, ['abandon', 'account', 'access'], '只解析出 3 个词条')
eq(entries4[0][2], 'vt. 抛弃，放弃；遗弃', '第 1 条释义纯净')
eq(entries4[1][2], 'n. 账户；描述；vi. 解释', 'account 的两行续行都接了回来')
eq(entries4[2][2], 'n. 通道，入口；接近；vt. 存取（计算机文件）', 'access 续行同样接回')
eq(len(dropped4), 2, '两条【…】标注行被丢弃')
eq(sections4[0]['count'], 3, 'List 1 计到 3 个词')

print('')
print('[5] 考频分级标题不计入分节（避免 20 个 List 被拆成 30 节）')
text5 = '\n'.join([
    '五星词汇', 'Day 1', 'List 1', 'abandon /əˈbændən/ vt. 抛弃',
    'List 2', 'access /ˈækses/ n. 入口',
    '四星词汇', 'Day 2', 'List 3', 'acquire /əˈkwaɪə/ vt. 获得',
    'List 4', 'adapt /əˈdæpt/ vi. 适应',
])
entries5, sections5, _ = IW.normalize(text5)
eq([s['label'] for s in sections5 if s['count'] > 0],
   ['List 1', 'List 2', 'List 3', 'List 4'],
   '只剩 4 个 List 分节（五星词汇 / Day 这类零词分节被过滤）')
eq(sum(s['count'] for s in sections5), 4, '分节计数合计 = 词条数')
eq(len(entries5), 4, '词条数与分节合计一致')

# ============================================================ List -> Day

print('')
print('[6] collapse_sections —— 20 个 List 折成 10 天')
secs = [{'label': 'List %d' % i, 'kind': 'list', 'count': 5} for i in range(1, 21)]
out6 = IW.collapse_sections(secs, 2)
eq(len(out6), 10, '20 个 List -> 10 天')
eq(out6[0]['label'], 'Day 1 (List 1-2)', '第 1 天标签含 List 区间')
eq(out6[9]['label'], 'Day 10 (List 19-20)', '第 10 天标签含 List 区间')
eq(sum(s['count'] for s in out6), 100, '折叠后词数守恒（10 x 10）')
eq(len(IW.collapse_sections(secs, 1)), 20, 'per_day=1 -> 不折叠')
eq(len(IW.collapse_sections(secs, 3)), 20, '20 不能被 3 整除 -> 宁可不折，不要错折')

days6 = [{'label': 'Day %d' % i, 'kind': 'day', 'count': 4} for i in range(1, 11)]
eq(len(IW.collapse_sections(days6, 2)), 10,
   '分节已经是 Day -> 原样使用，不再二次折叠（否则 10 天会被压成 5 天）')
eq(IW.collapse_sections(days6, 2)[0]['label'], 'Day 1', 'Day 分节的标签保持原样')

mixed6 = secs[:4] + [{'label': 'Day 9', 'kind': 'day', 'count': 3}]
eq(IW.collapse_sections(mixed6, 2)[0]['label'], 'Day 1 (List 1-2)',
   '同时存在 List 与 Day 时优先按 List 折叠')

# ============================================================ 端到端

print('')
print('[7] 端到端：书版排版文件')
src7 = os.path.join(DATA, '样例-书格式.txt')
ok(os.path.isfile(src7), '样例文件存在', src7)
if os.path.isfile(src7):
    out7, lines7 = run_import(src7, 'e2e-book')
    words7 = [l.split('|')[0] for l in lines7]
    ok('abandon' in words7 and 'account' in words7, '解析出预期的词条')
    bad = [w for w in words7 if '.' in w or ' ' in w]
    ok(not bad, '没有把词性行 / 短语当成新单词', '混入：' + str(bad))
    ok(not any(l.startswith('【') for l in lines7), '输出里没有【…】标注行')
    eq(len(lines7), 13, '共 13 个词条')
    ok('Day 1 (List 1-2)' in out7, '输出报告里有 List->Day 折叠记录')
    ok('跳过的行' in out7, '输出报告里如实报告跳过的行数')
    ab7 = [l for l in lines7 if l.startswith('abstract')]
    eq(ab7[0] if ab7 else '(缺)',
       'abstract|/ˈæbstrækt/|adj. 抽象的；n. 摘要，梗概',
       'abstract 的续行释义正确合并')

print('')
print('[8] 端到端：三种结构化格式（回归）')
for fmt in ('xlsx', 'csv', 'docx'):
    src8 = os.path.join(DATA, '样例-十天词表.' + fmt)
    if not os.path.isfile(src8):
        subprocess.run([sys.executable, os.path.join(ROOT, 'tests', 'make-sample.py')],
                       capture_output=True)
    out8, lines8 = run_import(src8, 'e2e-' + fmt)
    eq(len(lines8), 16, '%s 解析出 16 词' % fmt)
    eq(lines8[0] if lines8 else '(空)',
       "abandon|/ə'bændən/|v. 放弃，抛弃；遗弃",
       '%s 首个词条正确（全角逗号未被吃掉）' % fmt)
    ok('Day 1' in out8 and 'Day 2' in out8, '%s 识别到 Day 1 / Day 2 分节' % fmt)

print('')
print('[9] --per-day 1 关闭折叠')
out9, lines9 = run_import(os.path.join(DATA, '样例-书格式.txt'), 'e2e-nofold',
                          extra=('--per-day', '1'))
ok('Day 1 (List' not in out9, 'per_day=1 时不做 List->Day 折叠')
eq(len(lines9), 13, '词条数不受折叠开关影响')

print('')
print('[10] 端到端：只给「词的顺序」，释义由开源考研词库配对补全')
src10 = os.path.join(DATA, '样例-纯词表.txt')
if not os.path.isfile(src10):
    io.open(src10, 'w', encoding='utf-8').write(
        'List 1\nabandon\nabstract\nacademic\naccelerate\naccess\naccommodate\n'
        'List 2\nacquire\nadapt\nadequate\nadjacent\n')
out10, lines10 = run_import(src10, 'e2e-wordsonly', fill=True)
eq(len(lines10), 10, '解析出 10 个词条')
missing_ph = [l.split('|')[0] for l in lines10 if len(l.split('|')) < 2 or not l.split('|')[1]]
missing_ma = [l.split('|')[0] for l in lines10 if len(l.split('|')) < 3 or not l.split('|')[2]]
ok(not missing_ph, '音标 100% 由词库补全', '缺音标：' + str(missing_ph))
ok(not missing_ma, '释义 100% 由词库补全', '缺释义：' + str(missing_ma))
ok('abandon' in lines10[0] and '|' in lines10[0], '首条已补成 单词|音标|释义：' + lines10[0])
ok('Day 1 (List 1-2)' in out10, '只给 List 也能折成 Day')
ok('释义覆盖        10/10（100%）' in out10, '报告注明释义全部为补入')

# ============================================================ 讲稿（.ppt / .pptx）

print('')
print('[11] 课件文字清洗 —— 版式噪声与词条识别')

import struct as _struct                          # noqa: E402  只在下面造测试样本用
import zipfile as _zipfile                        # noqa: E402

sys.path.insert(0, TOOLS)
import ppt_extract as PE                          # noqa: E402

STRIP_CASES = [
    ("1、intellect ['intəlekt](n.)智力，理解力；",
     "intellect ['intəlekt](n.)智力，理解力；", '行首序号「1、」剥掉'),
    ('1）elect [iˈlekt]vt.选举，推举',
     'elect [iˈlekt]vt.选举，推举', '行首序号「1）」剥掉'),
    ("●intellectual [,intəl'ektʃuəl]（adj.）智力的",
     "intellectual [,intəl'ektʃuəl]（adj.）智力的", '行首圆点剥掉'),
    ('* collection [kәˈlekʃәn] n. 群体；收藏品',
     'collection [kәˈlekʃәn] n. 群体；收藏品', '行首星号剥掉（曾是漏网的真实 bug）'),
    ("派生词：election [ɪ'lekʃ(ə)n] n. 选举，推选；当选【2015-1-1】",
     "election [ɪ'lekʃ(ə)n] n. 选举，推选；当选", '「派生词：」剥掉、行内真题出处抠掉'),
    ("claimant['kleɪmənt]n.要求者；主张者",
     "claimant ['kleɪmənt]n.要求者；主张者", '词与音标黏连处补空格'),
    ('transmissible [træns\'misəbl] adj.能传送的；可传达的',
     "transmissible [træns'misəbl] adj.能传送的；可传达的", '正常词条原样通过'),
]
for _src, _want, _label in STRIP_CASES:
    eq(PE.clean_line(_src), _want, _label)

DROP_CASES = [
    ('考点搭配：infuse sb. with the new idea 向某人灌输新思想', '考点搭配整行丢'),
    ('译文：Hofstadter 说，我们国家的教育制度掌握在这些人手中', '译文整行丢'),
    ('lect-表示“选择；收集”', '词根释义行丢掉'),
    ('fore-表示“向前；在前；提前”', '词根释义行丢掉（词根名带连字符）'),
    ('【2015-1-1、2014-1-1】', '纯真题出处行丢掉'),
    ('2个派生词：', '计数标签行丢掉'),
    ('List 1-1（page   2  ）', '讲稿页眉丢掉'),
    ('___PPT9', '母版占位符丢掉'),
    ('单击此处编辑母版标题样式', '母版提示文字丢掉'),
    ('主讲教师：刘文涛', '讲师信息行丢掉'),
    ('微信 liuwentaoteacher', '讲师信息行丢掉（冒号位置是空格）'),
    ('哈工大航天学院38岁教授高会军',
     '纯中文花絮丢掉（留着会被当成上一条单词的释义续行接上去）'),
    ('在文中可引申理解为“才学、知识”', '纯中文引申语丢掉'),
    ('Hofstadter says our country\'s educational system is in the grips of people '
     'who joyfully and militantly proclaim their hostility to intellect.',
     '真题长例句丢掉（文件里的版本尾部带中文出处，也要能认出来）'),
    ('presidential election总统选举', '「英文紧贴中文」的搭配行丢掉'),
]
for _src, _label in DROP_CASES:
    eq(PE.clean_line(_src), '', _label)

eq(PE.clean_lines(['abandon /əˈbændən/', 'abandon /əˈbændən/', 'access /ˈækses/']),
   ['abandon /əˈbændən/', 'access /ˈækses/'],
   '页内重复行只留一次，且 /…/ 音标的收尾斜杠不会被切开')
ok(IW.parse_book_line('not a word line at all') is None,
   '清洗层没挡掉的英文行，到了导入器照样被拒（两道防线）')


# ---- 自造一个结构真实的最小 .ppt（OLE2 复合文档 + 记录流）------------------
#
# 解析器最容易错的地方是 OLE2 的扇区链：FAT / DIFAT / 目录项 / mini stream。
# 网上也没有小的 .ppt 样本可用（随便一个课件都几十 MB），所以这里按 [MS-CFB]
# 和 [MS-PPT] 手工拼一个十几 KB 的文件出来做往返验证。

def _ppt_rec(ver, inst, rt, payload=b''):
    """一条 PPT 记录：recVer(低4位)+recInstance(高12位) | recType | recLen"""
    return _struct.pack('<HHI', (inst << 4) | ver, rt, len(payload)) + payload


def _make_ppt_stream(slides, pad_to=4096):
    parts = [_ppt_rec(0xF, 0, 0x03E8, _ppt_rec(0, 0, 0x03E9, b'\x00' * 40))]  # Document 容器
    for lines in slides:
        body = b''.join(_ppt_rec(0, 0, 0x0FA0, t.encode('utf-16-le')) for t in lines)
        parts.append(_ppt_rec(0xF, 0, 0x03EE, body))                          # Slide 容器
    data = b''.join(parts)
    # 补一条填充记录，把流凑到 >= 4096 —— 故意走主 FAT 而不是 mini stream，
    # 这样测试能覆盖到扇区链，也少一层。
    target = pad_to if len(data) + 8 <= pad_to else pad_to * (len(data) // pad_to + 1)
    data += _ppt_rec(0, 0, 0x0FFF, b'\x00' * (target - len(data) - 8))
    return data


def _make_ole(stream_name, payload):
    """造一个最小的 OLE2 文件：扇区0=FAT，扇区1=目录，扇区2起=流数据"""
    sec = 512
    nsec = (len(payload) + sec - 1) // sec
    if 2 + nsec > 128:
        raise ValueError('测试样本太大')

    hdr = bytearray(sec)
    hdr[0:8] = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    _struct.pack_into('<HH', hdr, 0x18, 0x003E, 0x0003)      # 版本
    _struct.pack_into('<H', hdr, 0x1C, 0xFFFE)               # 字节序
    _struct.pack_into('<HH', hdr, 0x1E, 9, 6)                # 扇区 512 / mini 扇区 64
    _struct.pack_into('<I', hdr, 0x2C, 1)                    # 1 个 FAT 扇区
    _struct.pack_into('<I', hdr, 0x30, 1)                    # 目录起始扇区
    _struct.pack_into('<I', hdr, 0x38, 4096)                 # mini stream 阈值
    _struct.pack_into('<I', hdr, 0x3C, 0xFFFFFFFE)           # 没有 mini FAT
    _struct.pack_into('<I', hdr, 0x44, 0xFFFFFFFE)           # 没有 DIFAT 扇区
    for i in range(109):
        _struct.pack_into('<I', hdr, 0x4C + 4 * i, 0 if i == 0 else 0xFFFFFFFF)

    fat = [0xFFFFFFFF] * 128
    fat[0] = 0xFFFFFFFD                                      # 扇区0 是 FAT 自己
    fat[1] = 0xFFFFFFFE                                      # 目录链到此为止
    for k in range(nsec):
        fat[2 + k] = (3 + k) if k < nsec - 1 else 0xFFFFFFFE

    d = bytearray(sec)

    def entry(idx, name, etype, start, size, child=0xFFFFFFFF):
        o = idx * 128
        raw = name.encode('utf-16-le') + b'\x00\x00'
        d[o:o + len(raw)] = raw
        _struct.pack_into('<H', d, o + 0x40, len(raw))       # 名字长度（含结尾 0）
        d[o + 0x42] = etype
        d[o + 0x43] = 1                                      # 颜色：黑
        _struct.pack_into('<III', d, o + 0x44, 0xFFFFFFFF, 0xFFFFFFFF, child)
        _struct.pack_into('<I', d, o + 0x74, start)
        _struct.pack_into('<Q', d, o + 0x78, size)

    entry(0, 'Root Entry', 5, 0xFFFFFFFE, 0, child=1)
    entry(1, stream_name, 2, 2, len(payload))

    return (bytes(hdr) + _struct.pack('<128I', *fat) + bytes(d)
            + payload + b'\x00' * (nsec * sec - len(payload)))


print('')
print('[12] .ppt 解析 —— OLE2 扇区链 + 记录遍历（自造样本往返验证）')
SAMPLE_SLIDES = [
    ["List 1-1（page   2  ）",
     "1、intellect ['intəlekt](n.)智力，理解力；(总称)知识分子",
     "哈工大航天学院38岁教授高会军"],
    ["intellect ['intəlekt](n.)智力，理解力；(总称)知识分子",
     "考点搭配：intellectual enquiry 知识探索（2003年-翻译）",
     "●intellectual [,intəl'ektʃuəl]（adj.）智力的；理智的，聪明的"],
    ["lect-表示“选择；收集”",
     "1）elect [iˈlekt]vt.选举，推举 vi.进行选举，作出选择",
     "* collection [kәˈlekʃәn] n. 群体；收藏品"],
]
STREAM_BYTES = _make_ppt_stream(SAMPLE_SLIDES)
OLE_BYTES = _make_ole('PowerPoint Document', STREAM_BYTES)
os.makedirs(TMP, exist_ok=True)
SYNTH_PPT = os.path.join(TMP, 'synthetic.ppt')
with open(SYNTH_PPT, 'wb') as _f:
    _f.write(OLE_BYTES)

cfb = PE.CompoundFile(SYNTH_PPT)
eq(cfb.sector_size, 512, 'OLE2 头部：扇区大小 512')
eq(cfb.names(), ['PowerPoint Document'], 'OLE2 目录：只列出这一个流')
eq(cfb.stream('PowerPoint Document'), STREAM_BYTES,
   'OLE2 扇区链：取回的字节与写入的完全一致')
eq(cfb.stream('不存在的流'), None, '取不存在的流返回 None')

tops = PE.top_records(STREAM_BYTES)
eq(tops[-1][0] + 8 + tops[-1][5], len(STREAM_BYTES),
   'PPT 顶层记录正好铺满整个流（结构自检通过）')
eq([rt for _p, _v, _i, rt, _b, _l in tops], [0x03E8, 0x03EE, 0x03EE, 0x03EE, 0x0FFF],
   '顶层记录类型：Document 容器 + 3 张幻灯片 + 填充记录')

ppt = PE.PptText(SYNTH_PPT)
eq(len(ppt.warnings), 0, '解析自检无告警')
eq([len(s) for s in ppt.slides], [3, 3, 3], '原始文字：按页取到 3/3/3 行')
eq(ppt.clean_slides,
   [["intellect ['intəlekt](n.)智力，理解力；(总称)知识分子"],
    ["intellect ['intəlekt](n.)智力，理解力；(总称)知识分子",
     "intellectual [,intəl'ektʃuəl]（adj.）智力的；理智的，聪明的"],
    ['elect [iˈlekt]vt.选举，推举 vi.进行选举，作出选择',
     'collection [kәˈlekʃәn] n. 群体；收藏品']],
   '清洗后：页眉/花絮/词根行/考点搭配被剔除，词条保留')

entries12, sections12, _ = IW.normalize('\n'.join(
    '\n'.join(s) for s in ppt.clean_slides))
entries12, _dup12 = IW.dedup(entries12)
eq([e[0] for e in entries12], ['intellect', 'intellectual', 'elect', 'collection'],
   '自造 .ppt 能被解析成 4 个词条（跨页重复的 intellect 合并成一条）')
eq(entries12[2][1], '[iˈlekt]', '音标正确抽出')
eq(entries12[0][2], '(n.)智力，理解力；(总称)知识分子', '释义正确抽出')


# ---- .pptx（zip + XML）：端到端走一遍命令行 --------------------------------

def _make_pptx(path, slides):
    with _zipfile.ZipFile(path, 'w') as z:
        for num, lines in slides:
            paras = ''.join(
                '<a:p><a:r><a:t>%s</a:t></a:r></a:p>'
                % ln.replace('&', '&amp;').replace('<', '&lt;')
                for ln in lines)
            z.writestr('ppt/slides/slide%d.xml' % num,
                       '<?xml version="1.0" encoding="UTF-8"?>'
                       '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
                       ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                       '<p:cSld><p:spTree><p:sp><p:txBody>' + paras +
                       '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')


print('')
print('[13] .pptx 端到端 —— 页序按页码排，版式噪声同样被清洗')
SRC13 = os.path.join(TMP, 'inline.pptx')
_make_pptx(SRC13, [
    (10, ['List 1-1（page   2  ）', '3、diffuse [di‘fju:z]（v.）扩散；传播，散布']),
    (2, ['2、transmit [træns\'mit]（v.）传输；传导；发射(信号)',
         '考点搭配：transmit ... to ... 把…传给…']),
    (1, ["1、intellect ['intəlekt](n.)智力，理解力；(总称)知识分子"]),
])
out13, lines13 = run_import(SRC13, 'e2e-pptx')
words13 = [l.split('|')[0] for l in lines13]
eq(words13, ['intellect', 'transmit', 'diffuse'],
   'slide1 → slide2 → slide10（按页码数值排，不是按字符串「slide10 < slide2」）')
eq(lines13[2], "diffuse|[di‘fju:z]|（v.）扩散；传播，散布", '行首序号剥掉、音标释义齐全')
ok('考点搭配' not in out13, '考点搭配行没被当成词条')

# 扩展名和真实格式不一致（WPS 另存经常这样）→ 必须按文件头判断
print('')
print('[13b] 扩展名骗人时的兜底 —— 按文件头识别真实格式')
MISLABELED = os.path.join(TMP, 'inline-as-ppt.ppt')
with open(MISLABELED, 'wb') as _f:
    _f.write(open(SRC13, 'rb').read())
eq(IW.sniff_deck(MISLABELED, '.ppt'), 'pptx',
   '「叫 .ppt 其实是 pptx」被识破（文件头是 PK）')
eq(IW.sniff_deck(SYNTH_PPT, '.pptx'), 'ppt',
   '「叫 .pptx 其实是旧版 ppt」被识破（文件头是 D0CF11E0）')
_mis_slides, _mis_stat = IW.read_deck(MISLABELED)
eq(_mis_stat['kept'], 3, '误命名成 .ppt 的 pptx 照样正常读出 3 行文字')


# ============================================================ 课件清洗（第二批）

print('')
print('[15] 课件清洗补充规则 —— 短语行 / 词根词缀行 / 讲解行')

# --- 短语 / 搭配行：整行丢（它们是讲解举例，不是要背的词）
for _p in ('holiday camp',
           'live off campus',
           'modern democratic states 现代的民主国家',
           'the Department of Justice 司法部',
           'in spite of 尽管'):
    eq(PE.clean_line(_p), '', '短语行整行丢：%s' % _p)

# --- 反例保护：连字符合成词只有一个英文片段，不能当短语丢掉
eq(PE.clean_line('Anti-immigrant 反对移民'), 'Anti-immigrant 反对移民',
   '连字符合成词保留（`Anti-immigrant` 不算两个词）')
eq(PE.clean_line('mega-project 特大项目'), 'mega-project 特大项目', '连字符合成词保留')
eq(PE.clean_line('collect vt. 收集'), 'collect vt. 收集', '`vt.` 是词性，不是第二个单词')
eq(PE.clean_line("over-the-counter ['əuvəðə'kauntə] a. 不需处方也可出售的"),
   "over-the-counter ['əuvəðə'kauntə] a. 不需处方也可出售的",
   '带音标的一律是词条，不当短语丢')

# --- 词根 / 词缀 / 构词法标注行：整行丢
for _r in ('liber-词根，自由，随意',
           'ject-词根，投掷，扔',
           'ment-名词后缀',
           'y-名词后缀',
           'er-后缀',
           'clar=clear-表示“清楚；清晰”',
           'pel=puls-表示“拖，拉，拽；驱动”',
           'sembl-=simil-=simul-均表示“相同；相像”',
           'capt-侧重表示“抓”',
           'benefi = beneficial-有益的，有利的',
           'tern = turn-转变',
           't = ment-名词后缀',
           'x-由“mass-大量”简写',
           '在文中表示“生僻”含义：记录器；寄存器；计数器'):
    eq(PE.clean_line(_r), '', '构词法标注行整行丢：%s' % _r)

# --- 反例：释义里出现「表示」二字的正常词条不能被误杀
eq(PE.clean_line('resent [riˈzent]vt.对…表示忿恨，怨恨'),
   'resent [riˈzent]vt.对…表示忿恨，怨恨', '释义里的「表示」不触发词根规则')

# --- 讲解型中文行
eq(PE.clean_line('与touch有关的单词'), '', '「与X有关的单词」整行丢')

# --- 以 `=` 开头的讲解式搭配
eq(PE.clean_line('= rules of conduct 行为准则'), '', '「= …」是讲解搭配，整行丢')

# --- 剥序号要在认标签之前：`（2）相关词：` 剥掉 `（2）` 才认得出是标签行
eq(PE.clean_line('（2）相关词：'), '', '`（2）相关词：` 剥序号后当标签行丢掉')
eq(PE.clean_line('区别4词：'), '', '`区别4词：` 标签后没有内容，整行丢')
# --- 但「相关词 / 反 / 区别N词」后面跟的是**真词条**，内容要留下
eq(PE.clean_line('相关词：modest [ˈmɔdist] a. 谦虚的；适度的；羞怯的'),
   'modest [ˈmɔdist] a. 谦虚的；适度的；羞怯的', '`相关词：` 后面的真词条保留')
eq(PE.clean_line("区别1词：attack [ə'tæk]（v./n.）进攻，攻击；袭击"),
   "attack [ə'tæk]（v./n.）进攻，攻击；袭击", '`区别1词：` 后面的真词条保留')
eq(PE.clean_line('1）反：subject [ˈsʌbdʒikt] n. 对象，主体；主题；学科；主语'),
   'subject [ˈsʌbdʒikt] n. 对象，主体；主题；学科；主语', '`反：` 后面的真词条保留')
# --- 序号区间（`49-50、`）也要剥掉
eq(PE.clean_line('49-50、commission [kə‘miʃən] n. /v. 委任，委托'),
   'commission [kə‘miʃən] n. /v. 委任，委托', '`49-50、` 区间序号被剥掉')


print('')
print('[16] 音标提取 —— 英美双读音里的空格 / 竖线 / 形态标记')
# 英美双读音写成 `[英, 美]` 或 `[英|美]`，中间有空格或竖线。
# 早先的 `\[([^\]\s]{2,60})\]` 遇到空格就截断，把后半截音标漏进释义里。
eq(IW.extract_phonetic('[su:ˈpə:b, sju:-] adj. 华丽的')[0], '[su:ˈpə:b, sju:-]',
   '双读音（逗号+空格）整段抠出来')
eq(IW.extract_phonetic('[ˈ mɪnɪməm] n. 最小值')[0], '[ˈ mɪnɪməm]',
   '音标里的空格不再截断')
eq(IW.extract_phonetic('[ʌn|kliә] a. 不清楚的')[0], '[ʌn|kliә]',
   '音标里的竖线整段抠出来')
eq(IW.parse_book_line('unclear [ʌn kliә] a. 不清楚的'),
   ('unclear', '[ʌn kliә]', 'a. 不清楚的'), '整行解析：音标与释义各就各位')
# 反例：`[ pl.]` 是形态标记，不是音标
eq(IW.extract_phonetic('[ pl.]会议记录 a.微小的')[0], '',
   '`[ pl.]` 形态标记不被当成音标')
eq(IW.parse_book_line('minute [ˈminit, maiˈnju:t]n.分钟；片刻；[ pl.]会议记录 a.微小的')[1],
   '[ˈminit, maiˈnju:t]', '第一个方括号才是音标，后面的 `[ pl.]` 不影响')

print('')
print('[17] 词条字段消毒 —— `|` 是分隔符，正文里出现会串位')
eq(IW._clean_field('[ʌn|kliә]'), '[ʌn/kliә]', '音标里的 `|` 换成 `/`')
eq(IW._clean_field('n. 甲|乙'), 'n. 甲/乙', '释义里的 `|` 同样处理')
SRC17 = os.path.join(TMP, 'pipe.txt')
io.open(SRC17, 'w', encoding='utf-8', newline='\n').write(
    'unclear [ʌn|kliә] a. 不清楚的\nsuperb [su:ˈpə:b, sju:-] adj. 华丽的\n')
_out17, lines17 = run_import(SRC17, 'e2e-pipe')
ok(all(l.count('|') == 2 for l in lines17),
   '带竖线音标的词条，输出仍是「单词|音标|释义」三段',
   str([l for l in lines17 if l.count('|') != 2]))

print('')
print('[18] 课件文件名排序 / 讲次编号 / 折成 10 天')
# 同一批课件的前缀经常不统一（`1-okay-`、`1-okay`、`1-`），
# 只按整名做自然序会先比「okay」和「考研」，把 （6） 挤到 （8） 后面
_MIXED = ['1-okay-考研词汇-正课（%d）.ppt' % i for i in (1, 2, 3, 4, 5, 7, 8)]
_MIXED += ['1-okay考研词汇-正课（6）.ppt']
_MIXED += ['1-考研词汇-正课（%d）.ppt' % i for i in range(9, 21)]
eq([IW.deck_index(f) for f in sorted(_MIXED, key=IW.natural_key)], list(range(1, 21)),
   '混合前缀的 20 个课件按文件名末尾序号排成 1..20')
eq(IW.deck_index('1-okay考研词汇-正课（6）.ppt'), 6, '`（6）` 取到 6')
eq(IW.deck_index('正课 2.ppt'), 2, '`正课 2`（分隔符形式）取到 2')
eq(IW.deck_index('1-考研词汇-正课.ppt'), None, '没有序号时返回 None')
eq(IW.deck_index('考研词汇2024.ppt'), None, '`2024` 是年份不是讲次，不算序号')
# 带讲次编号的课件分节会被命名成 `List N`，于是能被 --per-day 2 折成 10 天
_DECK6 = os.path.join(TMP, '正课（6）.ppt')
shutil.copyfile(SYNTH_PPT, _DECK6)
_text6, _stat6 = IW.read_deck(_DECK6)
eq(_stat6.get('sec'), 'List 6', '「一个文件一节」时分节命名成 `List 6`')
_slides_sec = [{'label': 'List %d' % i, 'kind': 'list', 'count': 3} for i in range(1, 21)]
_folded = IW.collapse_sections(_slides_sec, 2)
eq(len(_folded), 10, '20 个 List 折成 10 天')
eq(_folded[0]['label'], 'Day 1 (List 1-2)', '第 1 天 = List 1-2')
eq(_folded[9]['label'], 'Day 10 (List 19-20)', '第 10 天 = List 19-20')
eq(sum(s['count'] for s in _folded), 60, '折叠不丢词')

print('')
print('[19] 释义去冗余 —— 讲稿反复讲同一个词，去重后只剩被包含的那些')
eq(IW.tidy_meaning('n. 和睦，和谐；融洽，一致 / n. 和睦，和谐；融洽，一致；a.爱好音乐的'),
   'n. 和睦，和谐；融洽，一致；a.爱好音乐的',
   '被整段包含的释义去掉，只留信息量最大的那条')
eq(IW.tidy_meaning('n. 甲 / n. 甲 / n. 乙'), 'n. 甲 / n. 乙', '完全相同的片段只留一次')
eq(IW.tidy_meaning('a. /ad. 对方付费的(地)'), 'a. /ad. 对方付费的(地)',
   '`a. /ad.` 里的斜杠不分段（分隔符是「空格+斜杠+空格」）')

print('')
print('[20] 空词条清理必须排在词库补全之后')
# `同义词：depict` 这类行只有单词没有释义，先补全就能救回来；顺序反了就永远丢了
SRC20 = os.path.join(TMP, 'empty-order.txt')
io.open(SRC20, 'w', encoding='utf-8', newline='\n').write(
    'depict\n\naccount /əˈkaunt/ n. 账户；描述\n')
_out20, lines20 = run_import(SRC20, 'e2e-emptyfill', fill=True)
_ph20 = [l for l in lines20 if l.split('|')[0] == 'depict']
ok(_ph20 and _ph20[0].count('|') == 2 and _ph20[0].split('|')[1],
   '无释义的单词先被内置词库补全，再决定去留', str(_ph20))
ok('补入' in _out20, '报告里能看到补入数量')
# 补全也救不回来的（既没音标也没释义）才丢
_out20b, lines20b = run_import(SRC20, 'e2e-emptyfill2', fill=True,
                               extra=('--no-fill',))
ok(not [l for l in lines20b if l.split('|')[0] == 'depict'],
   '--no-fill 下救不回来，空词条被丢掉')


# ---- 真实课件（可选）：设 FWORDS_PPT=<路径> 启用 --------------------------
#
# 仓库里不适合塞一个 25 MB 的课件当固定样本，所以做成可选的。

print('')
print('[14] 真实 .ppt 端到端（可选）')
_real = os.environ.get('FWORDS_PPT') or r'C:\Users\ytc\Desktop\1-okay-考研词汇-正课（1）.ppt'
_CANDIDATES = [_real,
               r'C:\Users\ytc\Desktop\十天-乱序-PPT\1-okay-考研词汇-正课（1）.ppt']
_real = next((p for p in _CANDIDATES if os.path.isfile(p)), _real)
if os.path.isfile(_real):
    out14, lines14 = run_import(_real, 'e2e-realppt')
    words14 = [l.split('|')[0] for l in lines14]
    ok(len(words14) >= 30, '真实课件解析出 >= 30 个词条', '实得 %d' % len(words14))
    ok(all(w.isascii() for w in words14), '词条里没有混进中文/版式噪声',
       '异常：%s' % [w for w in words14 if not w.isascii()][:3])
    ok(all(l.count('|') == 2 for l in lines14), '每条都是「单词|音标|释义」三段')
    print('      （%s → %d 词）' % (os.path.basename(_real), len(words14)))
else:
    print('SKIP  没找到真实课件；设环境变量 FWORDS_PPT=<路径> 可启用这一节')


# ============================================================ MOBI 电子书

print('')
print('[21] MOBI 电子书抽取（自造最小文件，往返验证）')

import struct as _st                             # noqa: E402
import mobi_extract as ME                        # noqa: E402
import mobi_words as MW                          # noqa: E402


def _make_mobi(path, html, header_len=0x100, extra_flags=0, record_size=4096):
    """按 PalmDB 结构手工拼一个最小 MOBI —— 正文无压缩（compression=1）+ UTF-8。

    结构：78 字节库头 | 每条记录 8 字节偏移表 | 2 字节填充 | 各记录数据
    记录 0 是 PalmDOC 头（16B）+ MOBI 头（header_len 字节）。
    extra_flags 写在 MOBI 头偏移 0xEC（0 = 记录不带尾部冗余条目）。
    """
    body = html.encode('utf-8') if isinstance(html, str) else html
    chunks = [body[i:i + record_size] for i in range(0, len(body), record_size)] or [b'']
    palm = _st.pack('>HHIHHHH', 1, 0, len(body), len(chunks), record_size, 0, 0)
    mobi = bytearray(header_len)
    mobi[0:4] = b'MOBI'
    _st.pack_into('>I', mobi, 4, header_len)
    _st.pack_into('>I', mobi, 0xEC, extra_flags)
    recs = [bytes(palm) + bytes(mobi)] + chunks

    hdr = bytearray(78)
    hdr[0:32] = b'mobitest'.ljust(32, b'\x00')
    hdr[60:64] = b'BOOK'
    hdr[64:68] = b'MOBI'
    _st.pack_into('>H', hdr, 76, len(recs))
    off = 78 + 8 * len(recs) + 2
    infos = bytearray()
    for r in recs:
        infos += _st.pack('>IB', off, 0) + b'\x00\x00\x00'
        off += len(r)
    with open(path, 'wb') as f:
        f.write(bytes(hdr) + bytes(infos) + b'\x00\x00' + b''.join(recs))


MOBI_HTML = (
    '<html><head><guide><reference title="目录" filepos=0000000100 /></guide>'
    '<body>'
    '<div>目录</div>'
    '<div><a filepos=0000000100  >前言一</a></div>'
    # 封面页：`LIST 1` / `LIST 2` 是讲次封面，不是真分节，必须跳过
    '<div>Day 1</div><div>第1天</div><div>LIST 1</div><div>LIST 2</div>'
    '<div>LIST 01</div>'
    '<div>intellect</div><div>/ ˈintəlekt /</div><div>n.</div>'
    '<div>智力，智慧</div><div>，理解力；［总称］知识分子</div>'
    '<div>助记</div><div>intel=inter- 在…之间，lect- 词根，选择；收集</div>'
    '<div>派生词</div>'
    '<div>+intellectual</div><div>/ ˌintəˈlektʃuəl /</div><div>adj.</div><div>智力的</div>'
    '<div>考点搭配</div><div>intellectual enquiry</div><div>知识探索（2003年-翻译）</div>'
    # 多音多义：第二条音标前面那行是中文释义，不能被当成新词条
    '<div>transplant</div><div>/ trænsˈplɑːnt /</div><div>vt.</div><div>移植</div>'
    '<div>/ ˈtrænsplɑːnt /</div><div>n.</div><div>移植</div>'
    # 音标藏在行尾（行首是异体词注）
    '<div>analyse</div><div>（= analyze）/ ˈænəlaiz /</div><div>vt.</div><div>分解；分析</div>'
    # 变体注里的音标属于异体词，不能算进本词
    '<div>analytic</div><div>/ ˌænəˈlitik /</div><div>（= analytical / ˌænəˈlitikl /</div>'
    '<div>）adj.</div><div>分解的；分析的</div>'
    '<div>officer</div><div>/ ˈɔːfisə /</div><div>n.</div><div>警官</div>'
    '<div>LIST 02</div>'
    # 行内换读音：`，散布 / diˈfjuːs /`
    '<div>diffuse</div><div>/ diˈfjuːz /</div><div>vt.</div><div>扩散；传播</div>'
    '<div>，散布 / diˈfjuːs /</div><div>adj.</div><div>四散的</div>'
    '<div>索引</div><div>analyse</div><div>diffuse</div><div>intellect</div>'
    '<div>officer</div><div>transplant</div>'
    '<div>附录</div><div>后记</div>'
    '</body></html>'
)

SYNTH_MOBI = os.path.join(TMP, 'synthetic.mobi')
os.makedirs(TMP, exist_ok=True)
_make_mobi(SYNTH_MOBI, MOBI_HTML)

_bk = ME.MobiBook(SYNTH_MOBI)
eq(_bk.compression, 1, '自造 MOBI 被认出「无压缩」')
eq(_bk.db_type, 'BOOK', '库头 type 读对')
eq(_bk.encoding, 65001, '文本编码认成 UTF-8')
ok('intellect' in _bk.html_text, '正文 HTML 拼接成功')
ok(_bk.toc()[0][1] == '前言一', '目录锚点（`<a filepos=…>`）能读出来')

# 尾部冗余条目：长度编码在最后 1 字节的低 2 位
_bk.extra_flags = 1
eq(_bk.trailing_size(b'abc\xb8\x88\x02'), 3, '尾部条目长度 = (末字节 & 3) + 1')
eq(_bk.trailing_size(b'abc\x00'), 1, '末字节 0x00 → 只剥 1 字节')
_bk.extra_flags = 0
eq(_bk.trailing_size(b'abc\x02'), 0, 'flags=0 时不动正文')

_secs, _stat = MW.extract(SYNTH_MOBI)
eq([n for n, _ in _secs], [1, 2], '封面页 LIST 1/2 不算分节，只认出 LIST 01 / 02')
_map = {}
for _n, _es in _secs:
    for _w, _ph, _ma, _dv in _es:
        _map[_w] = (_n, _ph, _ma, _dv)

eq(sorted(_map), ['analyse', 'analytic', 'diffuse', 'intellect',
                  'intellectual', 'officer', 'transplant'],
   '词条集合正确（考点搭配、助记、例句都没混进来）')
ok('n.' not in _map and 'adj.' not in _map,
   '`n.` / `adj.` 这类词性标注不会被当成单词行', str(sorted(_map)))
eq(_map['intellect'][1], '/ˈintəlekt/', '音标里的多余空格抹掉')
eq(_map['intellect'][2], 'n. 智力，智慧，理解力；［总称］知识分子',
   '断行释义拼回一句（含 `n.` 后补空格）')
eq(_map['intellect'][3], False, '普通词条 derived=False')
eq(_map['intellectual'][3], True, '`+intellectual` 认成派生词')
eq(_map['transplant'][1], '/trænsˈplɑːnt/ /ˈtrænsplɑːnt/',
   '多音词的两个音标都收下')
eq(_map['transplant'][2], 'vt. 移植 / n. 移植',
   '多音词的释义按读音分组（曾只剩一个 `vt.`）')
eq(_map['analyse'][1], '/ˈænəlaiz/', '音标藏在行尾也能抽到（`（= analyze）/ … /`）')
eq(_map['analyse'][2], 'vt. 分解；分析', '变体注不污染释义')
eq(_map['analytic'][1], '/ˌænəˈlitik/',
   '变体注里的音标不算本词读音（曾把 analytical 的读音也收进来）')
eq(_map['diffuse'][1], '/diˈfjuːz/ /diˈfjuːs/', '行内换读音也能切开')
eq(_map['diffuse'][2], 'vt. 扩散；传播，散布 / adj. 四散的', '行内换读音的释义分组正确')

eq(sorted(MW.index_words(_bk.plain_text())),
   ['analyse', 'diffuse', 'intellect', 'officer', 'transplant'],
   '书末「索引」能单独抽出来（开头的目录页不算）')

# 产出的词表要能被 import_words 原样吃下，并折成「一天」
_dump = MW.dump_text(_secs, 'synth')
_entries, _sections, _dropped = IW.normalize(_dump)
eq(len(_entries), 7, '词表 dump 能被 import_words 解析出 7 个词条')
eq([s['kind'] for s in _sections], ['list', 'list'], 'dump 里的 LIST 分节被认成 list')
_folded = IW.collapse_sections(_sections, 2)
eq(len(_folded), 1, '两个 LIST 折成 1 天')
ok('Day 1' in _folded[0]['label'], '折出来的标签带 Day 1', _folded[0]['label'])


# ============================================================ 收尾

print('')
# 测试前就存在的键（老板已导入的词库）不算意外，只有新增的非测试键才是问题。
_preexisting = set()
if _REG_BACKUP:
    try:
        _preexisting = set(json.loads(_REG_BACKUP.decode('utf-8')))
    except (ValueError, UnicodeDecodeError):
        _preexisting = set()
reg = IW.load_registry()
strangers = [k for k in reg
             if k not in _preexisting
             and not (k.startswith('u_e2e') or k.startswith('u_回归'))]
ok(not strangers, '测试只往注册表写了测试词库', '意外键：' + str(strangers))

# 还原成测试前的样子；测试前不存在才删掉。
if _REG_BACKUP is not None:
    with open(REGISTRY, 'wb') as _f:
        _f.write(_REG_BACKUP)
elif os.path.isfile(REGISTRY):
    os.remove(REGISTRY)
if os.path.isdir(TMP):
    shutil.rmtree(TMP)
for f in os.listdir(DATA):
    if f.startswith('e2e-') or f.startswith('回归-') or f.startswith('书格式-') or f.startswith('纯词表-'):
        p = os.path.join(DATA, f)
        os.remove(p) if os.path.isfile(p) else shutil.rmtree(p, ignore_errors=True)
print('已清理测试产物（注册表已还原、data/_t/、e2e-*、回归-*）')

print('')
print('-' * 60)
print('通过 %d，失败 %d' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
