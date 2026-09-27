# -*- coding: utf-8 -*-
"""验证生成的科任老师文档：用无头浏览器真实渲染后检查 DOM。

用法：
    python 验证科任文档.py [文档目录]

**为什么必须真渲染而不是看源码**：SVG 全部由 JS 生成，源码里一个图形都没有。
JS 抛错时页面会静默剩下空白——语法正确 ≠ 运行时正确。这里导出的是渲染后的 DOM（文本），
不是截图：不出图片，但渲染必须验。

检查项：
  · 每份文档两个 SVG 图表都画出来了
  · 等级分带字母一个不少（有数据的档位都必须在图上出现）——少一个就是分带宽度为负被静默跳过
  · 两次「等级构成」表的行数与列数（含该科与总分两块）
  · 章节顺序：本次大考 → 本班目标生 → 上次大考
  · 本班曲线为橙红 #eb6834 且加粗
  · 本次考试情况总结存在、非空、不超 140 字
  · 目标生表的行数与姓名
"""
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from 工作区路径 import WS, 数据根目录   # noqa: E402  考试目录在 <工作区>/数据/ 下
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 文档是按「宽版长图」设计的，渲染宽度必须给它。若用无头浏览器的默认窗口（800×600），
# 图会被压窄，档位分带窄于 13px 时档位字母按设计不标——那是窄屏下的正常行为，
# 却会被误判成「缺档」。
WINDOW = "1400,1000"
SUMMARY_LIMIT = 140


def find_browser():
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("没找到 Chrome 或 Edge，无法验证渲染结果。")


def dump_dom(browser, path):
    """渲染并导出 DOM（文本，不是截图）。

    用管道而不是临时文件接输出：Windows 上 Chrome 进程若还握着临时文件句柄，
    TemporaryDirectory 清理会抛 PermissionError。
    """
    uri = "file:///" + os.path.abspath(path).replace("\\", "/").replace(" ", "%20")
    p = subprocess.run([browser, "--headless", "--disable-gpu", "--no-sandbox",
                        f"--window-size={WINDOW}",
                        "--dump-dom", "--virtual-time-budget=5000", uri],
                       capture_output=True, timeout=180)
    return p.stdout.decode("utf-8", errors="replace")


def extract_data(html):
    """取出注入的 JSON（模板里写作 `const DATA = {...};`）。"""
    m = re.search(r"const DATA = (\{.*?\});\s*\n", html, re.S)
    if not m:
        raise SystemExit("文档里找不到 const DATA = {...}; —— 模板或注入被改动过。")
    return json.loads(m.group(1))


def nice_step(maxv):
    """与模板 niceStep() 同一算法，用来复算横轴窗口。"""
    raw = maxv / 4
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    n = raw / mag
    m = 1 if n <= 1 else 2 if n <= 2 else 5 if n <= 5 else 10
    return m * mag


def window_of(ex, subj):
    """复算模板里的横轴窗口 [x0, x1]（按重点班级分数区间定，各留 5% 余量）。"""
    vals = [v for c in ex["focus"] for v in ex["classes"][c]["subjects"][subj]["s"]]
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    bin_w = max(0.5, nice_step(span / 14))
    x0 = math.floor((lo - span * 0.05) / bin_w) * bin_w
    x1 = math.ceil((hi + span * 0.05) / bin_w) * bin_w
    return x0, x1


def band_geometry(ex, subj, W):
    """按模板的算法复算每个档位分带在屏幕上的宽度。

    返回 (列表, 整档在图外的档位名)。列表每项 = (档位名, 像素宽)。
    模板的规则：宽度 < 0.6px 整条跳过；≥ 13px 才标档位字母（否则会叠字）。
    """
    win = window_of(ex, subj)
    if not win:
        return [], []
    x0, x1 = win
    IW = max(80, W - 54 - 18)                      # M.l=54, M.r=18
    gb = ex["classes"]["全年级"]["subjects"][subj]["b"]
    present = [(i, r) for i, r in enumerate(gb) if r]
    bands = [{"lv": ex["levels"][i], "lo": r[0], "hi": r[1]} for i, r in present]
    for k, b in enumerate(bands):
        b["left"] = -1e9 if k == len(bands) - 1 else (b["lo"] + bands[k + 1]["hi"]) / 2
        b["right"] = 1e9 if k == 0 else (b["hi"] + bands[k - 1]["lo"]) / 2

    def x(v):
        return 54 + (x1 - v) / (x1 - x0) * IW

    out, off = [], []
    for b in bands:
        a, bb = x(b["right"]), x(b["left"])          # 反向轴：right 是高分侧
        xa, xb = max(min(a, bb), 54), min(max(a, bb), 54 + IW)
        w = xb - xa
        out.append((b["lv"], w))
        if w < 0.6:
            off.append(b["lv"])
    return out, off


def check_sort(browser, path):
    """真点一次列头，验证交互排序（结构检查验不了这个）。

    做法：往文档副本里追加一段脚本，程序化点击「落后名次」列头两次
    （一次升序、一次反向），把两次的表格内容与表头文字写进一个 div，再读出来核对。
    追加的脚本在文档自带脚本之后执行，所以点击时表已经画好。
    """
    with open(path, encoding="utf-8") as f:
        html = f.read()
    probe = """
<script>
(function () {
  const card = document.getElementById('tgt-card');
  if (!card) { document.title = 'SORTTEST:NO_CARD'; return; }
  const ths = [...card.querySelectorAll('thead th')];
  const rows = () => [...card.querySelectorAll('tbody tr')]
      .map(tr => [...tr.children].map(td => td.textContent.trim()));
  const click = i => { ths[i].dispatchEvent(new MouseEvent('click', { bubbles: true })); };
  const gap = ths.findIndex(t => t.textContent.indexOf('落后名次') >= 0);
  const before = rows();
  click(gap);
  const asc = rows();
  click(gap);
  const desc = rows();
  const d = document.createElement('div');
  d.id = 'sorttest';
  d.textContent = JSON.stringify({
    header: ths.map(t => t.textContent.trim()),
    ncol: ths.length,
    gapCol: gap,
    before: before.map(r => r[4]),
    asc: asc.map(r => r[4]),
    desc: desc.map(r => r[4]),
    eliteBefore: [...card.querySelectorAll('tbody tr')].map(tr => tr.className),
  });
  document.body.appendChild(d);
})();
</script>
"""
    tmp = os.path.join(os.environ.get("TEMP", "."), "_sorttest.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html.replace("</body>", probe + "</body>"))
    try:
        dom = dump_dom(browser, tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    m = re.search(r'<div id="sorttest">(.*?)</div>', dom, re.S)
    if not m:
        return ["点击列头排序的测试没能取到结果（脚本没跑或表结构变了）"]
    import html as _h
    info = json.loads(_h.unescape(m.group(1)))
    probs = []
    if info["gapCol"] < 0:
        return ["表头里找不到「落后名次」列"]
    asc = [float(x) for x in info["asc"]]
    desc = [float(x) for x in info["desc"]]
    base = [float(x) for x in info["before"]]
    if asc != sorted(asc):
        probs.append(f"点「落后名次」后不是升序：{asc}")
    if desc != sorted(desc, reverse=True):
        probs.append(f"再点一次没有反向：{desc}")
    if sorted(base) != sorted(asc):
        probs.append("排序改变了行内容（只是换个顺序，不该丢行或多行）")
    if len(base) < 2:
        probs.append("测试样本不足两行，排序没验到")
    return probs


def check_one(browser, path):
    name = os.path.basename(path)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    data = extract_data(html)
    dom = dump_dom(browser, path)
    subj = data["subject"]
    problems = []

    if len(dom) < 5000:
        problems.append(f"DOM 只有 {len(dom)} 字符，脚本多半抛错了（页面空白）")
        return name, problems, {}

    # ① 两个图表都画出来了
    n_svg = len(re.findall(r'role="img"', dom))
    if n_svg != 2:
        problems.append(f"SVG 图表应有 2 个，实际 {n_svg} 个")

    # ② 等级分带字母。
    # 必须**分节统计**：两节的档位集合不同（六档 vs 二十档），全局取集合会互相顶替；
    # 也**不能假设两节在 DOM 里的先后**——章节顺序调过（上次大考现在在最后），
    # 按「prev 到 curr」切片会切出空串，于是「所有档位字母都缺」这种假警报。
    # 每节取「自己的图表容器 → 自己的构成表」这一段。
    chunks = {}
    for tag, pre in (("本次", "curr"), ("上次", "prev")):
        a = dom.find(f'id="{pre}-host"')
        if a < 0:
            problems.append(f"DOM 里找不到「{tag}」的图表容器，页面多半没渲染出来")
            return name, problems, {}
        b = dom.find(f'id="{pre}-comp"', a)
        chunks[tag] = dom[a:b if b > a else len(dom)]

    expect, offchart = {}, {}
    for tag, ex in (("上次", data["prev"]), ("本次", data["curr"])):
        drawn = set(re.findall(r'<text[^>]*font-weight="600"[^>]*>([^<]+)</text>', chunks[tag]))
        m = re.search(r'viewBox="0 0 ([\d.]+) ', chunks[tag])
        W = float(m.group(1)) if m else 1240
        geo, off = band_geometry(ex, subj, W)
        expect[tag] = len(geo)
        offchart[tag] = off
        # 只有「屏幕上够宽、应当标字母」的档位才要求出现字母：
        #   · 整档在图外的（w < 0.6）按设计不画，改由图注披露，见第 ④ 项
        #   · 窄于 13px 的按设计不标字母（会叠字）
        missing = [lv for lv, w in geo if w >= 13 and lv not in drawn]
        if missing:
            problems.append(f"「{tag}·{ex['exam']}」图上缺档位字母：{''.join(missing)}"
                            f"（该档分带够宽却没画，是渲染问题）")
        extra = [lv for lv in drawn if lv not in [g[0] for g in geo] and lv in ex["levels"]]
        if extra:
            problems.append(f"「{tag}」图上画出了不该有的档位字母：{''.join(extra)}")

    # ③ 等级构成表：每节一张，含「该科」与「总分」两块
    tables = re.findall(r'<div id="(prev|curr)-comp">(.*?)</div>', dom, re.S)
    by_id = {k: v for k, v in tables}
    if len(by_id) != 2:
        problems.append(f"等级构成表应有 2 张，实际 {len(by_id)} 张")
    else:
        for tag, pre, ex in (("本次", "curr", data["curr"]), ("上次", "prev", data["prev"])):
            seg = by_id[pre]
            blocks = 2 if ex.get("hasTotal") else 1
            want_rows = 2 + (len(ex["focus"]) + 1) * blocks   # thead 两行 + 每块的各班与全年级
            ntr = len(re.findall(r"<tr>", seg))
            if ntr != want_rows:
                problems.append(f"「{tag}」等级构成表 {ntr} 行，应为 {want_rows} 行（{blocks} 块）")
            body = re.search(r"<tbody>.*?</tbody>", seg, re.S)
            if body:
                first_row = re.search(r"<tr>.*?</tr>", body.group(0), re.S)
                got_cols = first_row.group(0).count("<td") if first_row else 0
                if got_cols != len(ex["levels"]):
                    problems.append(f"「{tag}」等级构成表列数 {got_cols}，应为 {len(ex['levels'])}")
            else:
                problems.append(f"「{tag}」等级构成表没有表体")
            if blocks == 2 and "总分" not in seg:
                problems.append(f"「{tag}」等级构成表缺少总分成块")

    # ④ 章节顺序：本次大考 → 本班目标生 → 上次大考
    order = [dom.find(f'id="{i}"') for i in ("curr-host", "tgt-card", "prev-host")]
    if min(order) < 0 or order != sorted(order):
        problems.append("章节顺序不是「本次大考 → 本班目标生 → 上次大考」")

    # ⑤ 本班曲线：橙红 + 加粗（与主报告同一套规则）
    featured = data["curr"].get("featured")
    if featured:
        want_color = data["curr"]["classColorsLight"][featured]
        i_curr = dom.find('id="curr-host"')
        seg = dom[i_curr:dom.find('id="curr-legend"', i_curr)]
        if f'stroke="{want_color}"' not in seg:
            problems.append(f"本次图表里没有本班（{featured}）的颜色 {want_color}")
        elif 'stroke-width="3.2"' not in seg:
            problems.append("本班曲线没有加粗（应 stroke-width=3.2）")
        if want_color != "#eb6834":
            problems.append(f"本班颜色应为橙红 #eb6834，实际 {want_color}")

    # ⑥ 读图说明：要讲清统计方式与怎么读，且整档出窗的档位必须被点名（否则读者以为缺档）。
    # 按 id 取，不按 DOM 顺序配对（章节顺序变过，配对会整体错位）。
    # 容器必须是 div 不能是 p——里面要放多个段落，<p> 不能嵌套 <p>，浏览器会把内层提出来。
    for tag, pre in (("本次", "curr"), ("上次", "prev")):
        m = re.search(rf'<div class="axis-note" id="{pre}-note">(.*?)</div>\s*</div>', dom, re.S)
        note = m.group(1) if m else ""
        if not note:
            problems.append(f"「{tag}」读图说明没有渲染出来（容器标签或 id 变了？）")
            continue
        if "核密度" not in note:
            problems.append(f"「{tag}」读图说明没有讲统计方式（应说明这是核密度估计）")
        if "看三件事" not in note:
            problems.append(f"「{tag}」读图说明没有讲怎么读图")
        # 不应再出现被要求去掉的数轴细节
        for w in ("分箱宽", "横轴范围", "落在该范围之外"):
            if w in note:
                problems.append(f"「{tag}」读图说明里还残留数轴细节「{w}」")
        for lv in offchart.get(tag, []):
            if lv not in note:
                problems.append(f"「{tag}」{lv} 档整档在图外，但读图说明没点名")

    # ⑦ 考试情况总结：必须存在、非空、不超字数
    summ = re.search(r'<div class="summary-card" id="curr-summary">(.*?)</div>', dom, re.S)
    if not summ:
        problems.append("本次大考的「考试情况总结」没有渲染出来")
    else:
        # 只取正文那个 <p>：整块里还有标题，连标题一起数字数会平白多出十几个字
        mp = re.search(r"<p>(.*?)</p>", summ.group(1), re.S)
        body_txt = re.sub(r"<[^>]+>", "", mp.group(1)).strip() if mp else ""
        if not body_txt:
            problems.append("「考试情况总结」是空的")
        elif len(body_txt) > SUMMARY_LIMIT:
            problems.append(f"「考试情况总结」{len(body_txt)} 字，超过 {SUMMARY_LIMIT}")
        for w in ("undefined", "NaN", "None"):
            if w in body_txt:
                problems.append(f"「考试情况总结」里出现 {w}")
        # 若该考试目录提供了人工总结，文档里必须是那一版——防止静默退回脚本初稿
        mp2 = os.path.join(os.path.dirname(os.path.dirname(path)), "总结.json")
        if os.path.exists(mp2):
            with open(mp2, encoding="utf-8-sig") as f:
                manual = json.load(f)
            want = manual.get(subj)
            if isinstance(want, str) and want.strip() and want.strip() != body_txt:
                problems.append(f"「考试情况总结」与 总结.json 里的人工版本不一致"
                                f"（文档里是：{body_txt[:30]}…）")

    # ⑧ 目标生表：行数、姓名、默认按总排名由前到后、拔尖组单独标注、表头可点排序
    n_tgt = len(data["targets"])
    card = re.search(r'<div class="table-card" id="tgt-card">(.*?)</div>', dom, re.S)
    if not card:
        problems.append("目标生卡片没渲染出来")
    else:
        # 数 <tr 而不是 <tr>：拔尖行带 class 属性，写死 "<tr>" 会漏数它们
        got = len(re.findall(r"<tr[ >]", card.group(1))) - 1      # 减表头
        if got != n_tgt:
            problems.append(f"目标生表 {got} 行，应为 {n_tgt} 行")
        for t in data["targets"]:
            if t["name"] not in card.group(1):
                problems.append(f"目标生 {t['name']} 没出现在表里")

        elite_n = data["rule"].get("eliteN") or 10
        rows = re.findall(r'<tr( [^>]*)?>(.*?)</tr>', card.group(1), re.S)[1:]   # 去掉表头
        ranks, flags = [], []
        for attr, cells in rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", cells, re.S)
            if len(tds) < 3:
                continue
            mm = re.search(r"\d+", tds[2])          # 第 3 列 = 总排名
            ranks.append(int(mm.group()) if mm else None)
            flags.append('class="elite"' in (attr or ""))
        if None not in ranks and ranks != sorted(ranks):
            problems.append(f"目标生表默认不是按总排名由前到后：{ranks[:6]}…")
        want = [t["totalRank"] <= elite_n
                for t in sorted(data["targets"], key=lambda t: t["totalRank"])]
        if flags != want:
            problems.append(f"拔尖组（前 {elite_n}）标注与数据不符：标了 {sum(flags)} 行，应为 {sum(want)} 行")
        if f"年级总排名前 {elite_n}" not in card.group(1):
            problems.append(f"目标生表没有说明拔尖组的口径（前 {elite_n}）")
        n_btn = card.group(1).count('role="button"')
        if n_btn < 8:
            problems.append(f"目标生表可点排序的表头只有 {n_btn} 个，应覆盖 8 列")

    # ⑨ 交互排序：真点一次列头（结构检查验不了交互）
    problems.extend(check_sort(browser, path))

    stat = {"档位": f"{expect['上次']}/{expect['本次']}", "目标生": n_tgt, "DOM": len(dom)}
    return name, problems, stat


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        数据根目录(), "九年级9月考", "科任老师文档")
    if not os.path.isdir(d):
        cand = os.path.join(os.getcwd(), d)
        if os.path.isdir(cand):
            d = cand
        else:
            raise SystemExit(f"找不到文档目录：{d}")
    files = sorted(f for f in os.listdir(d) if f.lower().endswith(".html"))
    if not files:
        raise SystemExit(f"目录里没有 HTML：{d}\n请先运行 生成科任老师文档.py。")

    browser = find_browser()
    print(f"渲染器：{browser}")
    print(f"文档目录：{d}（{len(files)} 份）\n")

    bad = 0
    for fn in files:
        name, problems, stat = check_one(browser, os.path.join(d, fn))
        if problems:
            bad += 1
            print(f"✗ {name}")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"✓ {name}　图上档位 {stat['档位']}，目标生 {stat['目标生']} 人，DOM {stat['DOM']} 字符")
    print(f"\n有问题的文档：{bad} / {len(files)}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
