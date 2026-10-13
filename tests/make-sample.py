#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成词表样例文件，用来验证 tools/import_words.py 的解析能力。

只用标准库手写 xlsx / docx（它们本质是带 XML 的 zip），
所以不需要装 openpyxl / python-docx。

产物（都在 data/ 下）：
    样例-十天词表.xlsx   两个 sheet：Day 1 / Day 2
    样例-十天词表.csv    # Day 1 / # Day 2 分节
    样例-十天词表.docx   Word 表格
"""

import io
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')

DAYS = {
    'Day 1': [
        ('abandon', "/ə'bændən/", 'v. 放弃，抛弃；遗弃'),
        ('absolute', "/'æbsəluːt/", 'adj. 绝对的；完全的'),
        ('abstract', "/'æbstrækt/", 'adj. 抽象的 / n. 摘要'),
        ('academic', "/ˌækə'demɪk/", 'adj. 学院的；学术的'),
        ('accelerate', "/ək'seləreɪt/", 'v. 加速，促进'),
        ('accommodate', "/ə'kɒmədeɪt/", 'v. 容纳；供应住宿'),
        ('accompany', "/ə'kʌmpəni/", 'v. 陪伴，陪同'),
        ('accumulate', "/ə'kjuːmjəleɪt/", 'v. 积累，积聚'),
    ],
    'Day 2': [
        ('accurate', "/'ækjərət/", 'adj. 准确的，精确的'),
        ('acknowledge', "/ək'nɒlɪdʒ/", 'v. 承认；答谢'),
        ('acquire', "/ə'kwaɪə/", 'v. 获得，取得；学到'),
        ('adapt', "/ə'dæpt/", 'v. 适应；改编'),
        ('adequate', "/'ædɪkwət/", 'adj. 足够的；适当的'),
        ('adjacent', "/ə'dʒeɪsnt/", 'adj. 邻近的，毗连的'),
        ('adjust', "/ə'dʒʌst/", 'v. 调整，调节；适应'),
        ('administer', "/əd'mɪnɪstə/", 'v. 管理；执行；给予'),
    ],
}


# ------------------------------------------------------------ xlsx

def _sheet_xml(rows):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetData>']
    for i, row in enumerate(rows, start=1):
        out.append('<row r="%d">' % i)
        for j, val in enumerate(row):
            col = chr(ord('A') + j)
            out.append('<c r="%s%d" t="s"><v>%d</v></c>' % (col, i, val))
        out.append('</row>')
    out.append('</sheetData></worksheet>')
    return ''.join(out)


def write_xlsx(path):
    shared = ['单词', '音标', '释义']
    sheet_rows = {}
    for name, items in DAYS.items():
        rows = []
        for w, ph, ma in items:
            idxs = []
            for v in (w, ph, ma):
                if v not in shared:
                    shared.append(v)
                idxs.append(shared.index(v))
            rows.append(idxs)
        sheet_rows[name] = rows

    names = list(DAYS.keys())
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>']
    for i in range(len(names)):
        ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1))
    ct.append('</Types>')

    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')

    sheets_xml = ''.join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (n, i + 1, i + 1)
                         for i, n in enumerate(names))
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets>%s</sheets></workbook>') % sheets_xml

    wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(len(names)):
        wb_rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1))
    wb_rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>' % (len(names) + 1))
    wb_rels.append('</Relationships>')

    sst = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="%d" uniqueCount="%d">'
           % (len(shared), len(shared))]
    for s in shared:
        sst.append('<si><t xml:space="preserve">%s</t></si>' % s.replace('&', '&amp;').replace('<', '&lt;'))
    sst.append('</sst>')

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ''.join(ct))
        z.writestr('_rels/.rels', rels)
        z.writestr('xl/workbook.xml', workbook)
        z.writestr('xl/_rels/workbook.xml.rels', ''.join(wb_rels))
        z.writestr('xl/sharedStrings.xml', ''.join(sst))
        for i, n in enumerate(names):
            header = [0, 1, 2]  # 单词 / 音标 / 释义
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1),
                       _sheet_xml([header] + sheet_rows[n]))


# ------------------------------------------------------------ docx

def write_docx(path):
    def esc(s):
        return s.replace('&', '&amp;').replace('<', '&lt;')

    body = []
    for name, items in DAYS.items():
        body.append('<w:p><w:r><w:t xml:space="preserve"># %s</w:t></w:r></w:p>' % esc(name))
        rows = ['<w:tr><w:tc><w:p><w:r><w:t>单词</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>音标</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>释义</w:t></w:r></w:p></w:tc></w:tr>']
        for w, ph, ma in items:
            cells = ''.join('<w:tc><w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p></w:tc>' % esc(v)
                            for v in (w, ph, ma))
            rows.append('<w:tr>%s</w:tr>' % cells)
        body.append('<w:tbl>%s</w:tbl>' % ''.join(rows))

    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>') % ''.join(body)

    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')

    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', doc)


# ------------------------------------------------------------ csv / txt

def write_csv(path):
    lines = []
    for name, items in DAYS.items():
        lines.append('# ' + name)
        lines.append('单词,音标,释义')
        for w, ph, ma in items:
            lines.append('%s,%s,%s' % (w, ph, ma))
    io.open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')


def main():
    os.makedirs(DATA, exist_ok=True)
    tasks = [
        (write_xlsx, '样例-十天词表.xlsx'),
        (write_csv, '样例-十天词表.csv'),
        (write_docx, '样例-十天词表.docx'),
    ]
    for fn, name in tasks:
        p = os.path.join(DATA, name)
        fn(p)
        print('%-28s %7.1f KB' % (name, os.path.getsize(p) / 1024.0))
    total = sum(s['count'] if isinstance(s, dict) else len(s) for s in [DAYS[k] for k in DAYS])
    print('\n共 %d 天 / %d 词' % (len(DAYS), sum(len(v) for v in DAYS.values())))


if __name__ == '__main__':
    main()
