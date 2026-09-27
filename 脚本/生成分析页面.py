# -*- coding: utf-8 -*-
"""
由分班数据 CSV 生成 HTML 分析报告。

用法：
    python 生成分析页面.py <考试目录>

    <考试目录> 例如 九年级9月考，也可以给完整路径。
    读取该目录下 分班数据/*.csv，输出 <考试目录>/<考试目录名>.html。

科目、等级、排名区间均从数据自动推导，不写死；换科目或换等级体系无需改代码。
"""
import csv
import json
import os
import sys
from collections import Counter, OrderedDict, defaultdict
from html import escape as html_escape

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)                       # 成绩分析工作区/
TEMPLATE = os.path.join(HERE, "页面模板.html")

# ==================== 默认分析配置 ====================
DEFAULT_CONFIG = {
    # 重点分析的班级；空列表 = 分析全部班级（配色最多支持 8 个，超出会只保留前 8 个）
    "重点班级": ["01", "02", "11"],
    # 本班：图上的主角。固定用橙红色并加粗，便于一眼找到（见 assign_colors）
    "本班": "11",
    # 等级顺序；None = 按字母排序。若等级不是字母（如 优秀/良好/中等），请在此写明顺序
    "等级顺序": None,
    # 年级排名取自哪个科目（一般是总分）
    "排名基准科目": "总分",
}

# 分类配色（来自 dataviz 参考调色板，浅色/深色各自成套，顺序即 CVD 安全顺序）
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                 "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
                "#d55181", "#008300", "#9085e9", "#e66767"]

# 橙红（调色板第 2 位）。预留给「本班」：曲线上要一眼找到自己班，
# 这个色在明暗两套里都最跳，且与其余系列色的 CVD 区分度已由原调色板保证。
# 注意：只改「哪个班用哪个色」，不动配色集合本身，所以无需重跑配色校验。
FEATURED_IDX = 1


def assign_colors(focus, featured):
    """把调色板下标分配给各班：本班固定取 FEATURED_IDX，其余按原顺序取剩下的色。

    返回 OrderedDict{class: 调色板下标}。本班不在 focus 里时退化为纯顺序分配。
    """
    if featured not in focus:
        return OrderedDict((c, i % len(PALETTE_LIGHT)) for i, c in enumerate(focus))
    pool = [i for i in range(len(PALETTE_LIGHT)) if i != FEATURED_IDX]
    out = OrderedDict({featured: FEATURED_IDX})
    for k, c in enumerate([c for c in focus if c != featured]):
        out[c] = pool[k % len(pool)]
    return out

# 序数色阶锚点；两端硬约束是「靠近画布的那端仍须达 2:1 对比度」
ANCHORS_LIGHT = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf",
                 "#1c5cab", "#184f95", "#104281", "#0d366b"]   # 250..700
ANCHORS_DARK = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
                "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95"]   # 100..600


# ---------------------------------------------------------------- OKLab 插值
def _s2l(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_oklab(h):
    r, g, b = (_s2l(int(h[i:i + 2], 16)) for i in (1, 3, 5))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab_to_hex(L, a, b):
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def enc(c):
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)
    return "#%02x%02x%02x" % (enc(r), enc(g), enc(bb))


def make_ramp(n, anchors):
    """在锚点色阶上等距采样 n 档（OKLab 空间插值）。返回 浅->深 列表。"""
    if n <= 1:
        return [anchors[-1]] * n
    labs = [hex_to_oklab(h) for h in anchors]
    out = []
    for i in range(n):
        t = i / (n - 1) * (len(labs) - 1)
        j = min(int(t), len(labs) - 2)
        f = t - j
        out.append(oklab_to_hex(*[labs[j][k] + (labs[j + 1][k] - labs[j][k]) * f for k in range(3)]))
    return out


# ---------------------------------------------------------------- 读取与推导
def resolve_exam_dir(arg):
    cand = arg if os.path.isabs(arg) else os.path.join(WS, arg)
    if not os.path.isdir(cand):
        cand2 = os.path.join(os.getcwd(), arg)
        if os.path.isdir(cand2):
            cand = cand2
        else:
            raise SystemExit(f"找不到考试目录：{arg}\n  已尝试：{cand}")
    return os.path.abspath(cand)


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_split_data(data_dir):
    """读取分班数据目录，返回 (班级编号列表, 表头, {班级: 数据行列表})。"""
    files = sorted(f for f in os.listdir(data_dir) if f.endswith(".csv"))
    if not files:
        raise SystemExit(f"分班数据目录里没有 CSV：{data_dir}\n请先运行 按班拆分成绩.py。")
    header, groups = None, OrderedDict()
    for fn in files:
        cls = fn[:-4].replace("班", "").strip()
        with open(os.path.join(data_dir, fn), encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        if header is None:
            header = rows[0]
        groups[cls] = rows[1:]
    return list(groups.keys()), header, groups


def scan_subjects(header):
    """扫描表头，找出所有科目组，返回 [(科目名, 分数列, 排名列, 等第列)]。

    判定规则：某列有表头文字，且**紧随其后的两列恰为「排名」「等第」**，则这三列构成一个科目组。
    不按等距（每三列一组）取，因为各年级表的列结构并不一致——例如八年级的「体育」
    只有一个分数列、不带排名与等第，会打破等距，让其后所有科目整体错位。
    """
    groups, i, n = [], 2, len(header)

    def cell(k):
        return (header[k] or "").strip() if 0 <= k < n else ""

    while i + 2 < n:
        if cell(i) and cell(i + 1) == "排名" and cell(i + 2) == "等第":
            groups.append((cell(i), i, i + 1, i + 2))
            i += 3
        else:
            i += 1          # 单列（如八年级的「体育」）或空列，跳过继续找
    return groups


def normalize_total(groups):
    """统一总分口径：一律用「不含体育的总分」。

    各表的总分列名不一致：九年级叫「总分」，七八年级另有「加体育权重总分」「不加体育权重总分」，
    且七年级上根本没有「总分」。这里统一取「不加体育权重总分」并改名为「总分」，
    使各年级报告的「总分」含义一致；该列存在时，同名的「总分」列作废（七年级下两者相差 93.5 分）。
    """
    preferred = next((g for g in groups if g[0] == "不加体育权重总分"), None)
    if preferred is None:
        return groups          # 只有「总分」的表（如九年级）：它本身就不含体育
    out = []
    for g in groups:
        if g[0] == "总分":
            continue           # 被「不加体育权重总分」取代
        out.append(("总分", g[1], g[2], g[3]) if g is preferred else g)
    return out


def derive_levels(groups, subjects):
    """所有等第列里实际出现过的值，排序后作为等级顺序。"""
    seen = set()
    for rows in groups.values():
        for r in rows:
            for _, _, _, lc in subjects:
                if lc < len(r) and r[lc].strip():
                    seen.add(r[lc].strip())
    return sorted(seen)


def _robust_range(vals, n_trim=1):
    """该档分数的稳健区间：两端各去掉 n_trim 个极端值。

    直接用 min/max 会被单条异常记录毁掉。九年级数学就有一例：某学生排名第 5（A 档）
    却只有 79 分，把 A 档的最低分从 91 拉到 79，于是 A 档区间 79~95 与 B 档 86~89 重叠，
    相邻档取中点时算出「左界 > 右界」的负宽度，B 档在图上整个消失（用户报过）。
    去掉各一个极端值即可恢复单调的档位区间。样本太小时不裁剪，避免把区间削没。
    """
    v = sorted(vals)
    if len(v) >= 2 * n_trim + 6:
        v = v[n_trim:len(v) - n_trim]
    return [round(v[0], 1), round(v[-1], 1)]


def summarize(rows, subjects, levels):
    out = OrderedDict()
    for name, sc_i, _, lc_i in subjects:
        cnt, scores = Counter(), []
        by_level = defaultdict(list)     # 各档的分数，用于在分数轴上定位等级分界
        for r in rows:
            lv = r[lc_i].strip() if lc_i < len(r) else ""
            sc = num(r[sc_i]) if sc_i < len(r) else None
            if lv:
                cnt[lv] += 1
            if sc is not None:
                scores.append(sc)
                if lv:
                    by_level[lv].append(sc)
        stray = {k: v for k, v in cnt.items() if k not in levels}
        if stray:
            raise SystemExit(f"{name} 出现等级集合外的值：{stray}（请检查 配置.json 的「等级顺序」）")
        out[name] = {
            "c": [cnt.get(lv, 0) for lv in levels],
            "mean": round(sum(scores) / len(scores), 1) if scores else None,
            "n": sum(cnt.values()),
            "s": [round(x, 1) for x in scores],      # 原始分数：供前端做分布曲线
            # 各档在该科的分数区间 [低, 高]；空档为 null。
            # 等级由排名决定，但在分数轴上表现为一段连续区间，前端据此画等级分界带。
            "b": [_robust_range(v) if (v := by_level.get(lv)) else None for lv in levels],
        }
    return {"n": len(rows), "subjects": out}


def make_bands(levels):
    """按等级数把档次分成几组，得到 优秀/良好/中等/待提高 的分界。

    等级多（如九年级 A~T 共 20 档，且每档人数接近）时四等分，约等于年级排名四分段；
    等级少（如七八年级 A+/A/B+/B/C+/C 共 6 档，档与档人数悬殊）时只分三组，
    否则「良好」和「中等」会各只剩一档，读不出意义。分组边界一律按等级顺序取，
    与人数无关，故不会把人数多的档误并进「优秀」。
    """
    n = len(levels)
    if n < 3:
        return [{"key": "全部", "from": 0, "to": n, "desc": f"{levels[0]}~{levels[-1]} 档"}]
    names = ["优秀", "良好", "中等", "待提高"] if n >= 8 else ["优秀", "中等", "待提高"]
    k = len(names)
    edges = [round(n * i / k) for i in range(k + 1)]
    return [{"key": names[i],
             "from": edges[i], "to": edges[i + 1],
             "desc": f"{levels[edges[i]]}~{levels[edges[i + 1] - 1]} 档"}
            for i in range(k) if edges[i + 1] > edges[i]]


def main():
    if len(sys.argv) < 2:
        raise SystemExit("用法：python 生成分析页面.py <考试目录>"
                         "\n例如：python 生成分析页面.py 九年级9月考")

    exam_dir = resolve_exam_dir(sys.argv[1])
    exam_name = os.path.basename(exam_dir)
    data_dir = os.path.join(exam_dir, "分班数据")
    if not os.path.isdir(data_dir):
        raise SystemExit(f"没有 分班数据 目录：{data_dir}\n请先运行 按班拆分成绩.py。")

    cfg = dict(DEFAULT_CONFIG)
    cfg_path = os.path.join(exam_dir, "配置.json")
    if os.path.exists(cfg_path):
        # utf-8-sig：记事本 / PowerShell 保存的配置会带 BOM，普通 utf-8 读取会抛错
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                user = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"配置.json 不是合法的 JSON：{cfg_path}\n  {e}")
        cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})

    classes, header, groups = read_split_data(data_dir)
    subject_cols = normalize_total(scan_subjects(header))
    if not subject_cols:
        raise SystemExit(
            "没能从表头识别出任何科目组。\n"
            "  识别规则：某列有表头文字，且其后两列恰为「排名」「等第」。\n"
            f"  表头前 8 列：{header[:8]}\n"
            "  若该表结构特殊，请先跑 探查工作簿.py 核对，再决定如何处理。")
    dropped_total = [n for n in ("总分", "加体育权重总分")
                     if n in [c[0] for c in scan_subjects(header)]
                     and n not in [c[0] for c in subject_cols]]
    subjects = [c[0] for c in subject_cols]
    levels = cfg["等级顺序"] or derive_levels(groups, subject_cols)

    # 重点班级：默认取配置里的；为空则取全部班级
    focus = [c for c in cfg["重点班级"] if c in groups] or classes
    if len(focus) > 8:
        print(f"⚠ 重点班级有 {len(focus)} 个，配色最多支持 8 个，只保留前 8 个：{focus[:8]}")
        focus = focus[:8]
    if len(focus) > 3:
        print(f"⚠ 本次有 {len(focus)} 个班级同时上色，请用 dataviz 的 validate_palette.js "
              f"确认这组配色仍通过相邻/全对检验。")
    dropped = [c for c in cfg["重点班级"] if c not in groups]
    if dropped:
        print(f"提示：配置里的重点班级 {dropped} 在本次数据中不存在，已跳过。")

    print(f"考试目录：{exam_dir}")
    print(f"班级　　：{len(classes)} 个（{'、'.join(classes)}）")
    print(f"重点班级：{'、'.join(focus)}")
    print(f"科目　　：{len(subjects)} 个（{'、'.join(subjects)}）")
    print(f"等级　　：{len(levels)} 档（{' '.join(levels)}）")
    if "不加体育权重总分" in [c[0] for c in scan_subjects(header)]:
        print("总分口径：已取「不加体育权重总分」作为总分（不含体育），"
              + ("同名的「总分」列已弃用" if dropped_total else "各年级统一"))

    ramp_light = make_ramp(len(levels), ANCHORS_LIGHT)[::-1]   # A 最深 -> 末档最浅
    ramp_dark = make_ramp(len(levels), ANCHORS_DARK)           # A 最浅 -> 末档最深

    # 本班固定用橙红色并加粗：曲线上要一眼找到自己班
    featured = cfg["本班"]
    if featured not in focus:
        print(f"提示：本班「{featured}」不在重点班级 {focus} 里，本图不会有橙红加粗曲线。")
        featured = None
    else:
        print(f"本班　　：{featured}（图上用橙红色加粗）")
    cmap = assign_colors(focus, featured) if featured else \
        OrderedDict((c, i % len(PALETTE_LIGHT)) for i, c in enumerate(focus))

    data = {
        "exam": exam_name,
        "levels": levels,
        "subjects": subjects,
        "focus": focus,
        "featured": featured,
        "bands": make_bands(levels),
        "classNames": {c: (c if c == "未分班" else c + "班") for c in classes},
        "classColorsLight": {c: PALETTE_LIGHT[i] for c, i in cmap.items()},
        "classColorsDark": {c: PALETTE_DARK[i] for c, i in cmap.items()},
        # 科目折线图上色用（分类色上限 8；超出时前端只画前 8 科并提示）
        "paletteLight": PALETTE_LIGHT,
        "paletteDark": PALETTE_DARK,
        "rampLight": ramp_light,
        "rampDark": ramp_dark,
        "classes": OrderedDict(),
    }
    for c in classes:
        data["classes"][c] = summarize(groups[c], subject_cols, levels)
    all_rows = [r for c in classes for r in groups[c]]
    data["classes"]["全年级"] = summarize(all_rows, subject_cols, levels)

    # 各等级的年级排名区间：从「排名基准科目」的排名列实际推导，不假定每档人数
    base_subj = cfg["排名基准科目"]
    rank_range = {}
    if base_subj in subjects:
        _, _, rk_i, lv_i = subject_cols[subjects.index(base_subj)]
        for r in all_rows:
            lv = r[lv_i].strip() if lv_i < len(r) else ""
            rk = num(r[rk_i]) if rk_i < len(r) else None
            if lv in levels and rk is not None:
                lo, hi = rank_range.get(lv, (rk, rk))
                rank_range[lv] = (min(lo, rk), max(hi, rk))
        miss = [lv for lv in levels if lv not in rank_range]
        if miss:
            print(f"⚠ 「{base_subj}」中未出现的等级：{''.join(miss)}（排名区间将留空）")
    else:
        print(f"⚠ 排名基准科目「{base_subj}」不在科目列表中，排名区间将留空")
    data["rank"] = {lv: [int(round(v[0])), int(round(v[1]))] for lv, v in rank_range.items()}

    # 自检：各档在分数轴上的分带必须宽度为正，否则前端会跳过该带、图上少一个档位字母。
    # 判据与前端 bandsOf() 一致：相邻档取中点，首尾向外延伸。
    print()
    print("=== 等级分带自检（宽度为负会导致该档字母在图上消失）===")
    gsubj = data["classes"]["全年级"]["subjects"]
    band_issues = []
    for subj in subjects:
        bs = [(i, r) for i, r in enumerate(gsubj[subj]["b"]) if r]
        for k, (i, (lo, hi)) in enumerate(bs):
            left = -1e9 if k == len(bs) - 1 else (lo + bs[k + 1][1][1]) / 2
            right = 1e9 if k == 0 else (hi + bs[k - 1][1][0]) / 2
            if right - left <= 0:
                band_issues.append((subj, levels[i], round(left, 1), round(right, 1)))
    if band_issues:
        for subj, lv, left, right in band_issues:
            print(f"  ⚠ {subj} {lv} 档：分带 {left}~{right} 宽度为负，该档不会显示")
    else:
        print("  全部科目各档分带宽度均为正  OK")

    print()
    print("=== 等级 → 年级排名区间（由数据推导）===")
    print("  " + "  ".join(f"{lv}:{data['rank'][lv][0]}~{data['rank'][lv][1]}"
                           for lv in levels if lv in data["rank"]))
    print()
    print(f"=== 班级概览自检（基准科目：{base_subj}）===")
    grade_n = data["classes"]["全年级"]["n"]
    for c in focus + ["全年级"]:
        cd = data["classes"][c]
        tk = cd["subjects"].get(base_subj, {})
        if not tk:
            continue
        ok = "OK" if sum(tk["c"]) == tk["n"] else "!! 计数与总数不符"
        print(f"  {c:>4s} 人数={cd['n']:>4d} 均分={tk['mean']} "
              f"有档={tk['n']:>4d} 计数和={sum(tk['c']):>4d}  {ok}")
    lvl_sum = sum(data["classes"]["全年级"]["subjects"][base_subj]["c"])
    print(f"  全年级等级计数合计 {lvl_sum} / 总人数 {grade_n}"
          f"{'  OK' if lvl_sum == grade_n else '  ⚠ 不一致，可能有学生没有等级'}")
    if grade_n != len(all_rows):
        print(f"  ⚠ 全年级行数 {len(all_rows)} 与统计人数 {grade_n} 不符")

    # 注入模板
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    for token in ("/*__DATA__*/null", "__EXAM__"):
        if token not in tpl:
            raise SystemExit(f"页面模板里找不到占位符 {token}，模板可能被改动过。")
    # 考试名写进 <title> 与页面标题，多份报告同时打开时才分得清
    tpl = tpl.replace("__EXAM__", html_escape(exam_name))
    page = tpl.replace("/*__DATA__*/null",
                       json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    out = os.path.join(exam_dir, f"{exam_name}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"\n已生成：{out}  ({len(page) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
