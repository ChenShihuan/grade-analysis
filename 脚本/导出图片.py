# -*- coding: utf-8 -*-
"""把分析文档（HTML）整页导出为 PNG，存到 HTML 同目录、同名。

用法：
    python 导出图片.py <考试目录 | 文档目录 | 单个 html> [--宽 1400] [--缩放 1]

    python 导出图片.py 九年级9月考\科任老师文档      # 该目录下每个 html 各出一张
    python 导出图片.py 九年级9月考                   # 只处理该目录下的 html，不进子目录

**为什么要先量高度**：Chrome 的 --screenshot 只截「窗口大小」那一块，
页面比窗口高就会被裁掉，比窗口矮则留下大片空白。所以先注入一段脚本把
document.documentElement.scrollHeight 写进 DOM、读回来，再用这个高度去截图。

**为什么不用 shell 重定向**：PowerShell 里给原生命令加 `2>$null` 会把 stdout 一起吞掉
（本项目踩过，DOM 长度变成 0）。一律用 Python 的 subprocess 管道接输出。
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# Chrome 对单张位图的高度有上限（约 16384px），超过会静默截断，必须提前拦下
MAX_H = 16000

# 注入的探针：把页面真实尺寸写进一个 div，事后从导出的 DOM 里读回来。
# 必须在 appendChild 之前取 scrollHeight——探针自己也会把页面撑高。
PROBE = """
<script>
(function () {
  const de = document.documentElement, b = document.body;
  const info = {
    h: Math.ceil(Math.max(de.scrollHeight, b.scrollHeight)),
    w: Math.ceil(Math.max(de.scrollWidth, b.scrollWidth)),
    svg: document.querySelectorAll('svg').length,
    tables: document.querySelectorAll('table').length,
    title: document.title
  };
  const d = document.createElement('div');
  d.id = 'pagebox';
  d.textContent = JSON.stringify(info);
  b.appendChild(d);
})();
</script>
"""


def find_browser():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("没找到 Chrome 或 Edge，无法导出图片。")


def file_uri(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/").replace(" ", "%20")


def run(browser, args, timeout=300):
    p = subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox"] + args,
                       capture_output=True, timeout=timeout)
    return p.stdout.decode("utf-8", errors="replace")


def measure(browser, path, width):
    """量页面渲染后的真实尺寸，顺带回报画了几个 svg、几张表（用来判断有没有渲染出来）。"""
    with open(path, encoding="utf-8") as f:
        html = f.read()
    tmp = os.path.join(os.environ.get("TEMP", "."), "_measure.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html.replace("</body>", PROBE + "</body>"))
    try:
        dom = run(browser, ["--window-size=%d,1200" % width, "--hide-scrollbars",
                            "--virtual-time-budget=5000", "--dump-dom", file_uri(tmp)])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    m = re.search(r'<div id="pagebox">(.*?)</div>', dom, re.S)
    if not m:
        return None
    import html as _h
    try:
        return json.loads(_h.unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def shoot(browser, path, out, width, height, scale):
    args = [f"--window-size={width},{height}", "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--screenshot={out}", "--virtual-time-budget=5000", file_uri(path)]
    run(browser, args)
    return os.path.exists(out) and os.path.getsize(out) > 0


def png_size(path):
    """从 PNG 头里读宽高（IHDR 紧跟在 8 字节签名 + 4 字节长度 + 4 字节类型之后）。"""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    import struct
    return struct.unpack(">II", head[16:24])


def collect(target):
    """把参数解析成待转换的 html 列表。"""
    p = target if os.path.isabs(target) else os.path.join(WS, target)
    if not os.path.exists(p):
        p2 = os.path.join(os.getcwd(), target)
        if os.path.exists(p2):
            p = p2
        else:
            raise SystemExit(f"找不到：{target}\n  已尝试：{p}")
    if os.path.isfile(p):
        return [p]
    return [os.path.join(p, f) for f in sorted(os.listdir(p))
            if f.lower().endswith(".html")]


def main():
    ap = argparse.ArgumentParser(description="把分析文档整页导出为 PNG")
    ap.add_argument("目标", help="考试目录 / 文档目录 / 单个 html")
    ap.add_argument("--宽", type=int, default=1400,
                    help="渲染宽度（CSS 像素）。文档按 1400 宽设计，默认即可")
    ap.add_argument("--缩放", type=int, default=1,
                    help="像素密度倍数：2 更清晰（适合打印），文件约大一倍")
    ap.add_argument("--只量不截", action="store_true", help="只报页面尺寸，不生成图片")
    args = ap.parse_args()

    files = collect(args.目标)
    if not files:
        raise SystemExit(f"目录里没有 html：{args.目标}")

    browser = find_browser()
    print(f"渲染器：{browser}")
    print(f"宽度 {args.宽} px　缩放 {args.缩放}x　共 {len(files)} 个 html\n")

    bad = 0
    for src in files:
        name = os.path.basename(src)
        info = measure(browser, src, args.宽)
        if not info:
            print(f"✗ {name}：量不到页面尺寸（渲染失败？先用 验证科任文档.py 查）")
            bad += 1
            continue
        if not info["svg"] and not info["tables"]:
            print(f"✗ {name}：页面上没有 svg 也没有表格，多半是 JS 抛错了")
            bad += 1
            continue
        h = info["h"] + 2                     # +2 免得把最后一行像素切掉
        if h > MAX_H:
            print(f"✗ {name}：页面高 {h}px 超过 Chrome 上限 {MAX_H}px，会把图截断。"
                  f"请把文档分节或降低 --宽")
            bad += 1
            continue

        out = os.path.splitext(src)[0] + ".png"
        if args.只量不截:
            print(f"· {name}：渲染尺寸 {info['w']}×{info['h']}"
                  f"（{info['svg']} 个图 / {info['tables']} 张表）")
            continue

        ok = shoot(browser, src, out, args.宽, h, args.缩放)
        if not ok:
            print(f"✗ {name}：截图命令没有产出文件")
            bad += 1
            continue
        size = os.path.getsize(out)
        wh = png_size(out)
        # 纯空白页也能生成 PNG，但会小得离谱——用体积兜一道
        flag = "" if size > 20000 else "  ⚠ 文件异常小，图可能是空白"
        print(f"✓ {name}　{wh[0]}×{wh[1]} px　{size / 1024:.0f} KB"
              + f"　（页面上 {info['svg']} 个图 / {info['tables']} 张表）{flag}")
        if flag:
            bad += 1

    if not args.只量不截:
        print(f"\n有问题的：{bad} / {len(files)}　→ 与 html 同目录、同名，扩展名为 .png")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
