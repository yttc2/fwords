# -*- coding: utf-8 -*-
"""从 MOBI / PalmDOC 电子书里提取正文文字（纯标准库，零依赖）。

为什么自己写：本机 pip 装不了包（kindleunpack / mobi 都拉不下来），
而 MOBI 的容器格式是公开的老式 Palm 数据库，用标准库足够。

格式要点（[MOBI 是 PalmDOC 的超集]）：

  1. 文件头是 PalmDB（PDB）数据库：
       0x00  32B  库名
       0x3C   4B  type     （电子书一般是 "BOOK"）
       0x40   4B  creator  （"MOBI"）
       0x4C   2B  记录条数 numRecords
       0x4E      记录信息表，每条 8 字节：offset(u32) + attributes(u8) + uniqueID(3B)
     **记录偏移是「从文件头起算」的绝对偏移，偏移表末尾再接 2 字节的 gap** ——
     所以第 i 条记录的数据范围是 [offs[i], offs[i+1])。

  2. 记录 0 不是正文，是两级头：
       先 16 字节 PalmDOC 头：compression(u16) / unused(u16) / textLength(u32)
                             / recordCount(u16) / recordSize(u16) / encryption(u16)
       紧跟 MOBI 头：magic "MOBI"(4B) + headerLength(u32) + type/code/uid/version …
       再跟 EXTH（书元信息，可选）

  3. 正文 = 记录 1 .. recordCount。compression 决定怎么解：
        1     = 无压缩，直接把字节拼起来
        2     = PalmDOC LZ77
        17480 = HUFF/CDIC（需要额外的 huff 记录）—— 本模块不支持，会明确报错

  4. **每条正文记录末尾带一个「尾部冗余条目」（trailing entry）**，长度编码在
     该记录最后 1 字节的低 2 位：`n = (rec[-1] & 3) + 1`。
     它存的是被切在记录边界上的那个多字节字符的剩余字节（相当于和下一条记录
     开头重复的部分），不去掉的话会在拼接处产生乱码 —— 本书里有 121 处。
     注意：**必须先剥尾部再解压**（尾部是接在压缩流后面的裸字节）。

  5. 正文是 HTML（不是纯文本），带 filepos 锚点和 <mbp:pagebreak/>。

命令：
    python tools/mobi_extract.py 书.mobi                 # 打印前若干行 + 统计
    python tools/mobi_extract.py 书.mobi --txt out.txt   # 导出纯文本
    python tools/mobi_extract.py 书.mobi --html out.html # 导出原始 HTML
    python tools/mobi_extract.py 书.mobi --toc           # 只打印书内目录
"""

import html as _html
import os
import re
import struct
import sys

# ---------------------------------------------------------------- 容器层


class MobiError(Exception):
    pass


class MobiBook(object):
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()
        d = self.raw
        if len(d) < 78:
            raise MobiError('文件太小，不像 MOBI')
        self.db_name = d[0:32].rstrip(b'\x00').decode('latin-1', 'replace')
        self.db_type = d[60:64].decode('latin-1', 'replace')
        self.db_creator = d[64:68].decode('latin-1', 'replace')
        if self.db_type not in ('BOOK', 'TEXt', 'DATA'):
            raise MobiError('不是电子书数据库（type=%r，应该是 BOOK）' % self.db_type)

        self.num_records = struct.unpack('>H', d[76:78])[0]
        self.offsets = []
        for i in range(self.num_records):
            o = 78 + i * 8
            if o + 8 > len(d):
                break
            off = struct.unpack('>I', d[o:o + 4])[0]
            self.offsets.append(off)
        if not self.offsets:
            raise MobiError('记录偏移表为空')
        # 末尾补一条「文件尾」，方便切片
        self.offsets.append(len(d))

        self._read_headers()
        self._read_text()

    # -- 记录 0 的两级头
    def _read_headers(self):
        r0 = self.offsets[0]
        if r0 + 16 > len(self.raw):
            raise MobiError('记录 0 越界')
        (self.compression, _u, self.text_length, self.text_records,
         self.record_size, self.encryption, _u2) = struct.unpack('>HHIHHHH', self.raw[r0:r0 + 16])
        if self.encryption:
            raise MobiError('这本书带 DRM（encryption=%d），解不了' % self.encryption)
        self.mobi_version = None
        self.encoding = 65001
        self.extra_flags = 1          # 默认「有跨记录重叠字节」
        mo = r0 + 16
        if self.raw[mo:mo + 4] == b'MOBI':
            hlen = struct.unpack('>I', self.raw[mo + 4:mo + 8])[0]
            self.mobi_version = struct.unpack('>I', self.raw[mo + 20:mo + 24])[0]
            enc = struct.unpack('>I', self.raw[mo + 28:mo + 32])[0]
            if enc in (1252, 65001):
                self.encoding = enc
            self.header_length = hlen
            # extraRecordDataFlags 在 MOBI 头偏移 0xEC（u32）
            if hlen >= 0xF0:
                f = struct.unpack('>I', self.raw[mo + 0xEC:mo + 0xF0])[0]
                if 0 <= f <= 0xFF:      # 明显越界就不信它（有些转换工具会写 0xFFFFFFFF）
                    self.extra_flags = f

    # -- PalmDOC LZ77 解压
    @staticmethod
    def _unpack_palmdoc(data):
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            c = data[i]
            i += 1
            if c == 0x00 or (0x09 <= c <= 0x7F):
                # 原样字节（0x00 和可打印 ASCII）
                out.append(c)
            elif 0x01 <= c <= 0x08:
                # 把后面 c 个字节原样拷贝
                out += data[i:i + c]
                i += c
            elif 0x80 <= c <= 0xBF:
                # 两字节，回引
                if i >= n:
                    break
                c2 = data[i]
                i += 1
                v = ((c << 8) | c2) & 0x3FFF
                dist = (v >> 3) & 0x07FF
                length = (v & 0x07) + 3
                if dist == 0 or dist > len(out):
                    break
                for _ in range(length):
                    out.append(out[-dist])
            else:  # 0xC0..0xFF
                out.append(0x20)
                out.append(c ^ 0x80)
        return bytes(out)

    @staticmethod
    def _varint_from_end(rec, size):
        """从 rec 的第 size 字节往前读一个「变长整数」（MOBI 尾部条目的长度编码）"""
        bitpos, result = 0, 0
        while size > 0:
            v = rec[size - 1]
            result |= (v & 0x7F) << bitpos
            bitpos += 7
            size -= 1
            if (v & 0x80) != 0 or bitpos >= 28:
                break
        return result

    def trailing_size(self, rec):
        """这条记录末尾有多少字节是「尾部冗余条目」（要丢掉）"""
        size = len(rec)
        if size <= 0:
            return 0
        flags = self.extra_flags
        num = 0
        testflags = flags >> 1
        while testflags:
            if testflags & 1:
                num += self._varint_from_end(rec, size - num)
            testflags >>= 1
        if flags & 1:
            i = size - num - 1
            if i < 0:
                return size
            num += (rec[i] & 0x03) + 1
        # 防御：剥出来的长度不能把整条记录吃光
        return num if 0 < num < size else 0

    def _read_text(self):
        if self.compression == 17480:
            raise MobiError('这本书用 HUFF/CDIC 压缩（compression=17480），'
                            '需要额外的 huff 解压表，本模块没实现。'
                            '用 Calibre 转成 EPUB/TXT 再导入即可。')
        if self.compression not in (1, 2):
            raise MobiError('未知压缩方式 compression=%d' % self.compression)

        end = 1 + self.text_records
        if end > len(self.offsets) - 1:
            end = len(self.offsets) - 1
        chunks = []
        for i in range(1, end):
            rec = self.raw[self.offsets[i]:self.offsets[i + 1]]
            # 尾部冗余条目是接在（压缩或未压缩的）数据后面的裸字节，**先剥再解压**
            if self.extra_flags:
                n = self.trailing_size(rec)
                if n:
                    rec = rec[:len(rec) - n]
            if self.compression == 2:
                rec = self._unpack_palmdoc(rec)
            chunks.append(rec)

        data = b''.join(chunks)
        # 头里声明的正文长度是最权威的截断点（个别文件末尾会多带填充字节）
        if 0 < self.text_length <= len(data):
            data = data[:self.text_length]
        self.text_bytes = data
        self.html_text = data.decode(
            'utf-8' if self.encoding == 65001 else 'cp1252', 'replace')

    # -- 目录：<guide><reference .../> 与正文里的 <a filepos=NNN>标题</a>
    def toc(self):
        out = []
        for m in re.finditer(r'<a\s+filepos=(\d+)\s*>(.*?)</a>', self.html_text,
                             re.I | re.S):
            label = self._plain(m.group(2)).strip()
            if label:
                out.append((int(m.group(1)), label))
        return out

    @staticmethod
    def _plain(s):
        s = re.sub(r'<[^>]+>', '', s)
        return _html.unescape(s)

    def plain_text(self):
        """HTML → 纯文本：块级标签转换行，其余标签去掉"""
        s = self.html_text
        s = re.sub(r'<\?xml[^>]*\?>', '', s, flags=re.I)
        s = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', s, flags=re.I | re.S)
        # 换行类
        s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
        s = re.sub(r'<mbp:pagebreak[^>]*>', '\n', s, flags=re.I)
        s = re.sub(r'</(p|div|h[1-6]|li|tr|blockquote)>', '\n', s, flags=re.I)
        s = re.sub(r'<(p|div|h[1-6]|li|tr|blockquote)\b[^>]*>', '\n', s, flags=re.I)
        s = re.sub(r'<[^>]+>', '', s)
        s = _html.unescape(s)
        # 少数转换工具会在正文里留下控制字节（尾部条目没剥干净时尤其明显），清掉
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        # 归一化空白：行尾空格去掉、连续空行压成一个
        s = s.replace('\u3000', ' ').replace('\xa0', ' ')
        lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in s.split('\n')]
        out = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return '\n'.join(out).strip()


def read_mobi(path):
    return MobiBook(path).plain_text()


# ---------------------------------------------------------------- CLI

def main():
    import argparse
    ap = argparse.ArgumentParser(description='从 MOBI 电子书提取文字（零依赖）')
    ap.add_argument('source')
    ap.add_argument('--txt', default='', help='导出纯文本到该路径')
    ap.add_argument('--html', default='', help='导出原始 HTML 到该路径')
    ap.add_argument('--toc', action='store_true', help='只打印书内目录')
    ap.add_argument('--head', type=int, default=60, help='打印前 N 行（默认 60）')
    ap.add_argument('--probe', action='store_true', help='只体检容器结构')
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit('找不到文件：%s' % args.source)
    b = MobiBook(args.source)
    print('库名     %s' % b.db_name)
    print('压缩     %d (%s)' % (b.compression,
          {1: '无压缩', 2: 'PalmDOC LZ77', 17480: 'HUFF/CDIC'}.get(b.compression, '?')))
    print('编码     %s' % ('UTF-8' if b.encoding == 65001 else 'CP1252'))
    print('正文     %d 字节 / %d 条记录 / 声明长度 %d'
          % (len(b.text_bytes), b.text_records, b.text_length))
    print('MOBI 版本 %s' % b.mobi_version)
    if args.probe:
        return

    if args.toc:
        for pos, label in b.toc():
            print('%-10d %s' % (pos, label))
        return

    if args.html:
        with open(args.html, 'wb') as f:
            f.write(b.text_bytes)
        print('HTML 已写入 %s' % args.html)
    if args.txt:
        text = b.plain_text()
        with open(args.txt, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        print('纯文本已写入 %s（%d 字符 / %d 行）'
              % (args.txt, len(text), text.count('\n') + 1))
        return
    text = b.plain_text()
    print('--- 前 %d 行 ---' % args.head)
    for ln in text.split('\n')[:args.head]:
        print(ln)


if __name__ == '__main__':
    main()
