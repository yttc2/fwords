"""部署前自检：把 dist/fwords-demo 用 HTTP 起起来，验证资源都能正常返回。

用法：
    python tests/serve-check.py                 # 默认检查 dist/fwords-demo
    python tests/serve-check.py <目录>           # 检查指定目录（如 dist/fwords-demo-clean）

意义：确认这套文件作为「静态站点」能正常工作（而不是只在本地 file:// 下能开）。
部署到服务器后如果页面打不开，先在本机跑通这个脚本，就能排除「文件本身有问题」。
"""

import http.server
import os
import re
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request

_DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dist", "fwords-demo")
# 必须是绝对路径：下面会 os.chdir(ROOT)，之后拿相对 ROOT 拼引用会解析错。
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_ROOT)
PORT = 8899

REQUIRED = ["/", "/index.html", "/style.css", "/app.js", "/words.js", "/wordlists.js"]
FAKE = "/__not_exist__.js"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    if not os.path.isdir(ROOT):
        print("找不到目录: %s" % ROOT)
        print("先运行打包（把 demo/ 复制到 dist/fwords-demo/）再执行本脚本。")
        return 1

    print("站点目录: %s" % ROOT)
    files = sorted(f for f in os.listdir(ROOT))
    print("包含文件: %s" % ", ".join(files))

    os.chdir(ROOT)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", PORT), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.2)

    base = "http://127.0.0.1:%d" % PORT
    failed = []

    print("\n== 必需资源 ==")
    for path in REQUIRED:
        try:
            r = urllib.request.urlopen(base + path, timeout=5)
            body = r.read()
            status = r.status
        except urllib.error.HTTPError as e:
            status, body = e.code, b""
        ok = status == 200 and len(body) > 0
        print("  [%s] %-14s %s  %d bytes" % ("OK " if ok else "FAIL", path, status, len(body)))
        if not ok:
            failed.append(path)

    print("\n== 不存在的路径应返回 404 ==")
    try:
        r = urllib.request.urlopen(base + FAKE, timeout=5)
        status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    ok = status == 404
    print("  [%s] %-14s %s" % ("OK " if ok else "FAIL", FAKE, status))
    if not ok:
        failed.append(FAKE)

    print("\n== 首页引用检查 ==")
    html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
    title = re.search(r"<title>(.*?)</title>", html)
    print("  <title> = %s" % (title.group(1) if title else "(缺失)"))
    build = re.search(r'name="fwords-build" content="([^"]*)"', html)
    print("  版本戳 = %s" % (build.group(1) if build else "(缺失)"))
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    for ref in refs:
        is_local = not ref.startswith(("http://", "https://", "//"))
        # 资源指纹 ?v=xxx 是查询串，不是文件名的一部分，比对存在性前要剥掉
        path_only = ref.split("?")[0].split("#")[0]
        exists = os.path.exists(os.path.join(ROOT, path_only)) if is_local else True
        flag = "OK " if exists else "FAIL"
        print("  [%s] 引用 %s" % (flag, ref))
        if not exists:
            failed.append(ref)
    if not refs:
        print("  (没有发现任何 src/href 引用)")

    srv.shutdown()

    print()
    if failed:
        print("自检失败，问题项: %s" % ", ".join(failed))
        return 1
    print("自检通过：所有资源可正常访问，引用路径全部可解析。")
    print("可以把这个目录的内容（或对应的 zip）上传到服务器了：%s" % ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
