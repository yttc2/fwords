"""打包静态站点：demo/ → dist/，产出可直接解压到站点根目录的 zip。

用法：
    python tools/pack.py            # 完整包（含导入的私有词库），本地自用
    python tools/pack.py --clean    # 线上精简包：剔除私有词库，只留开源内容

产出：
    dist/fwords-demo/            完整站点目录（demo/ 的干净副本）
    dist/fwords-demo.zip         完整包
    dist/fwords-demo-clean/      精简站点目录（仅 --clean）
    dist/fwords-demo-clean.zip   精简包（仅 --clean）

为什么用扁平结构：宝塔/命令行部署时最容易犯的错就是「解压后多套了一层文件夹」，
导致访问到 404。让 zip 里直接是 index.html 等文件，这一步就消失了。

为什么需要 --clean：demo/wordlists.user.js 装的是你导入的词库。如果导入的是
商业出版物（如《十天搞定考研词汇》）的完整内容，传到公网等于向公众分发，性质
与本地自用不同。--clean 把它**替换成空壳**而不是删掉 —— 删掉会让 index.html 里
的 <script src="wordlists.user.js"> 变成 404，虽然功能不受影响但控制台会报错，
不干净。空壳只声明一个空的 window.WORD_LISTS，引用和功能都正常。

每次改了 demo/ 下的代码，重新跑一次本脚本，再上传覆盖即可。
"""

import os
import re
import shutil
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "demo")
DIST = os.path.join(ROOT, "dist")

STAGE = os.path.join(DIST, "fwords-demo")
ZIP_PATH = os.path.join(DIST, "fwords-demo.zip")
STAGE_CLEAN = os.path.join(DIST, "fwords-demo-clean")
ZIP_CLEAN = os.path.join(DIST, "fwords-demo-clean.zip")

SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

# 含用户导入内容的文件：精简包里替换成空壳
PRIVATE_FILES = ["wordlists.user.js"]
EMPTY_USER_JS = (
    "// 线上精简包：不含任何导入的私有 / 受版权保护的词库。\n"
    "// 由 tools/pack.py --clean 生成。本地自用请用完整包。\n"
    "window.WORD_LISTS = window.WORD_LISTS || {};\n"
)


def collect(stage_dir):
    """返回 [(绝对路径, zip内相对路径)]，按相对路径排序，保证打包结果稳定。"""
    items = []
    for base, dirs, files in os.walk(stage_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name in SKIP_NAMES:
                continue
            full = os.path.join(base, name)
            rel = os.path.relpath(full, stage_dir).replace(os.sep, "/")
            items.append((full, rel))
    items.sort(key=lambda x: x[1])
    return items


def stamp_build(stage_dir):
    """把 index.html 里的 dev 版本戳换成打包时间，并给 js/css 打上 ?v= 指纹。

    为什么必须做：手机上常见「新 index.html + 旧 app.js 缓存」的混搭 ——
    新按钮画出来了，但旧 JS 没给它绑事件，于是**按下去毫无反应**，
    而电脑上因为强刷过所以正常。给资源加指纹后，HTML 一更新，浏览器就必须
    重新拉 JS，这类问题不会再出现。

    返回版本串（形如 202610122014）。
    """
    build = time.strftime("%Y%m%d%H%M")
    path = os.path.join(stage_dir, "index.html")
    if not os.path.isfile(path):
        return build
    with open(path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace('name="fwords-build" content="dev"',
                        'name="fwords-build" content="%s"' % build)
    html = re.sub(r'\?v=dev\b', "?v=" + build, html)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return build


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    clean = "--clean" in argv

    stage = STAGE_CLEAN if clean else STAGE
    zip_path = ZIP_CLEAN if clean else ZIP_PATH
    label = "精简包（线上）" if clean else "完整包（本地自用）"

    if not os.path.isdir(SRC):
        print("找不到源目录: %s" % SRC)
        return 1

    # 1. 重建本次要产出的那一个（只清自己，不动另一个包 —— 完整包和精简包可以并存）
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    if os.path.isfile(zip_path):
        os.remove(zip_path)
    if not os.path.isdir(DIST):
        os.makedirs(DIST)
    shutil.copytree(SRC, stage, ignore=shutil.ignore_patterns(*SKIP_DIRS, *SKIP_NAMES))

    # 2. 打版本戳 + 资源指纹（必须在精简替换之前做完，两者互不影响）
    build = stamp_build(stage)

    # 3. 精简包：把私有词库替换成空壳
    stripped = []
    if clean:
        for name in PRIVATE_FILES:
            target = os.path.join(stage, name)
            had = os.path.isfile(target)
            size = os.path.getsize(target) if had else 0
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(EMPTY_USER_JS)
            stripped.append((name, size if had else 0))

    # 防呆：源目录里本来就没有私有词库（干净 clone 的仓库就是这样）。
    # 这种情况下 --clean 是「无中生有」一个空壳，覆盖到站点根会把线上已有的词库冲掉。
    if clean and not os.path.isfile(os.path.join(SRC, PRIVATE_FILES[0])):
        print("")
        print("  !! 源目录里没有 %s（干净 clone 的仓库本来就没有）。" % PRIVATE_FILES[0])
        print("     此时 --clean 是凭空造一个空壳。直接 cp 到站点根，会覆盖掉线上已有的词库！")
        print("     线上部署请去掉 --clean：dist 里没有这个文件，cp 不会动站点根那一份。")
        print("")

    items = collect(stage)
    if not items:
        print("源目录为空: %s" % SRC)
        return 1

    # 4. 生成 zip（arcname 用相对路径 → 扁平结构）
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in items:
            z.write(full, rel)

    print("打包完成 —— %s" % label)
    print("  源目录: %s" % SRC)
    print("  站点目录: %s" % stage)
    print("  ZIP: %s" % zip_path)
    print("  版本戳: %s（debug 面板会显示；js/css 已加 ?v=%s）" % (build, build))
    if clean:
        for name, size in stripped:
            if size:
                print("  已剔除内容: %s（原 %.1f KB → 空壳）" % (name, size / 1024))
            else:
                print("  已置空: %s（源文件本就不存在）" % name)
    print()
    print("  包含 %d 个文件（zip 内为扁平结构，解压到站点根目录即用）：" % len(items))
    total = 0
    for full, rel in items:
        size = os.path.getsize(full)
        total += size
        print("    %-16s %8.1f KB" % (rel, size / 1024))
    print("    %-16s %8.1f KB" % ("(压缩包)", os.path.getsize(zip_path) / 1024))
    print("    %-16s %8.1f KB" % ("(原始合计)", total / 1024))
    print()
    print("下一步：上传 %s 到服务器站点根目录并解压。" % os.path.relpath(zip_path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
