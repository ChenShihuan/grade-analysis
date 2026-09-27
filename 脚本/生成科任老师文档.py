# -*- coding: utf-8 -*-
"""按科目生成给科任老师的单科分析文档（每科一份自包含 HTML）。

用法：
    python 生成科任老师文档.py <本次考试目录> <上次考试目录> [选项]

    python 生成科任老师文档.py 九年级9月考 八年级下 --班级 11

输出：<本次考试目录>/科任老师文档/<班级>班-<科目>.html，每份含三节——
  一、上次大考 · <科目> 跨班分数分布与等级构成
  二、本次大考 · <科目> 跨班分数分布与等级构成
  三、本次大考 · 本班该科目标生

数据层完全复用 生成分析页面.py（importlib 导入），**不复制它的函数**：
scan_subjects() 的扫描式识别、normalize_total() 的总分口径、_robust_range() 的稳健区间
都是踩坑换来的，复制一份迟早两边不一致。
"""
import argparse
import importlib
import json
import math
import os
import sys
from collections import OrderedDict
from html import escape as html_escape

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(HERE)                       # 成绩分析工作区/
sys.path.insert(0, HERE)
G = importlib.import_module("生成分析页面")       # 复用其数据层

TEMPLATE = os.path.join(HERE, "科任老师模板.html")

# 不出独立文档的科目：总分与加体育权重总分是聚合列（「总分排名落后于总排名」自相矛盾），
# 化学满分仅 10 分、62% 学生并列满分，排名不具筛选意义。体育只有一列，会被
# scan_subjects() 自动跳过，这里列出只是让意图显式。
EXCLUDE = ["化学", "总分", "加体育权重总分", "体育"]


# ------------------------------------------------------------------ 读取
def load_exam(arg):
    """读一场考试：配置 + 分班数据。结构与 生成分析页面.py 的 main() 保持一致。"""
    d = G.resolve_exam_dir(arg)
    name = os.path.basename(d)
    data_dir = os.path.join(d, "分班数据")
    if not os.path.isdir(data_dir):
        raise SystemExit(f"没有 分班数据 目录：{data_dir}\n请先运行 按班拆分成绩.py。")

    cfg = dict(G.DEFAULT_CONFIG)
    cfg_path = os.path.join(d, "配置.json")
    if os.path.exists(cfg_path):
        # utf-8-sig：记事本 / PowerShell 保存的配置会带 BOM，普通 utf-8 读取会抛错
        try:
            with open(cfg_path, encoding="utf-8-sig") as f:
                user = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"配置.json 不是合法的 JSON：{cfg_path}\n  {e}")
        cfg.update({k: v for k, v in user.items() if k in G.DEFAULT_CONFIG})

    classes, header, groups = G.read_split_data(data_dir)
    all_rows = [r for c in classes for r in groups[c]]
    return {"dir": d, "name": name, "cfg": cfg, "classes": classes,
            "header": header, "groups": groups, "all": all_rows}


def find_col(header, want):
    """按表头文字找列号；找不到返回 None。"""
    for i, h in enumerate(header):
        if (h or "").strip() == want:
            return i
    return None


def level_rank_range(rows, base_tuple, levels):
    """各等级的年级排名区间，从「排名基准科目」的排名列实际推导，不假定每档人数。

    （生成分析页面.py 把这段写在 main() 里没有独立成函数，故在此复刻同样的几行。）
    """
    _, _, rk_i, lv_i = base_tuple
    rr = {}
    for r in rows:
        lv = r[lv_i].strip() if lv_i < len(r) else ""
        rk = G.num(r[rk_i]) if rk_i < len(r) else None
        if lv in levels and rk is not None:
            lo, hi = rr.get(lv, (rk, rk))
            rr[lv] = (min(lo, rk), max(hi, rk))
    miss = [lv for lv in levels if lv not in rr]
    return {lv: [int(round(v[0])), int(round(v[1]))] for lv, v in rr.items()}, miss


# ------------------------------------------------------------------ 单场考试的单科数据
def build_exam_payload(ex, subject, class_id):
    """构造模板要的一份考试数据（该科 + 总分）。返回 (payload, 自检信息) 或 (None, 原因)。

    总分一并算进来：等级构成一节要在该科之外再给一份总分的等级构成，
    用来判断「这科掉队是不是整体掉队的缩影」。
    """
    cols = G.normalize_total(G.scan_subjects(ex["header"]))
    if not cols:
        return None, "表头里识别不出任何科目组"
    levels = ex["cfg"]["等级顺序"] or G.derive_levels(ex["groups"], cols)

    sub_tuple = next((c for c in cols if c[0] == subject), None)
    if sub_tuple is None:
        have = "、".join(c[0] for c in cols)
        return None, f"这场考试没有「{subject}」（它有：{have}）"
    total_tuple = next((c for c in cols if c[0] == "总分"), None)
    want = [sub_tuple] + ([total_tuple] if total_tuple and total_tuple is not sub_tuple else [])

    # 重点班级沿用该考试的 配置.json，但本班必须在内——文档是给本班科任老师看的
    focus = [c for c in ex["cfg"]["重点班级"] if c in ex["classes"]]
    if class_id in ex["classes"] and class_id not in focus:
        focus.append(class_id)
    if not focus:
        focus = list(ex["classes"])
    if len(focus) > 8:
        print(f"  ⚠ 重点班级有 {len(focus)} 个，配色上限 8，只保留前 8：{focus[:8]}")
        focus = focus[:8]

    base_tuple = next((c for c in cols if c[0] == ex["cfg"]["排名基准科目"]), None)
    rank, miss = ({}, [])
    if base_tuple:
        rank, miss = level_rank_range(ex["all"], base_tuple, levels)

    # 本班固定橙红 + 加粗（与主报告同一套分配逻辑）
    featured = class_id if class_id in focus else None
    cmap = G.assign_colors(focus, featured)
    payload = {
        "exam": ex["name"],
        "levels": levels,
        "bands": G.make_bands(levels),
        "focus": focus,
        "featured": featured,
        "classNames": {c: (c if c == "未分班" else c + "班") for c in ex["classes"]},
        "classColorsLight": {c: G.PALETTE_LIGHT[i] for c, i in cmap.items()},
        "classColorsDark": {c: G.PALETTE_DARK[i] for c, i in cmap.items()},
        "rampLight": G.make_ramp(len(levels), G.ANCHORS_LIGHT)[::-1],   # A 最深 -> 末档最浅
        "rampDark": G.make_ramp(len(levels), G.ANCHORS_DARK),           # A 最浅 -> 末档最深
        "rank": rank,
        "classes": OrderedDict(),
    }
    for c in ex["classes"]:
        # summarize() 只传这两科的列定义，但 levels 用的是全场推导的完整集合，
        # 否则「等级集合外的值」检查会因本科档位不全而误报
        payload["classes"][c] = G.summarize(ex["groups"][c], want, levels)
    payload["classes"]["全年级"] = G.summarize(ex["all"], want, levels)
    payload["hasTotal"] = total_tuple is not None

    # 自检：各档在分数轴上的分带宽度必须为正，否则前端会跳过该带、图上少一个档位字母。
    # 判据与前端一致：相邻档取中点，首尾向外延伸。
    grade_b = payload["classes"]["全年级"]["subjects"][subject]["b"]
    bs = [(i, r) for i, r in enumerate(grade_b) if r]
    bad = []
    for k, (i, (lo, hi)) in enumerate(bs):
        left = -1e9 if k == len(bs) - 1 else (lo + bs[k + 1][1][1]) / 2
        right = 1e9 if k == 0 else (hi + bs[k - 1][1][0]) / 2
        if right - left <= 0:
            bad.append((levels[i], round(left, 1), round(right, 1)))

    tk = payload["classes"]["全年级"]["subjects"][subject]
    info = {"levels": len(levels), "focus": focus, "grade_n": payload["classes"]["全年级"]["n"],
            "有档": tk["n"], "计数和": sum(tk["c"]), "均分": tk["mean"],
            "分带坏": bad, "缺排名区间": miss,
            "班人数": {c: payload["classes"][c]["n"] for c in ex["classes"]}}
    return payload, info


# ------------------------------------------------------------------ 分布曲线的形状特征
def _quantile(sv, q):
    """sv 必须已排序。线性插值分位数。"""
    if not sv:
        return None
    k = (len(sv) - 1) * q
    f = int(k)
    c = min(f + 1, len(sv) - 1)
    return sv[f] + (sv[c] - sv[f]) * (k - f)


def _kde_density(vals, xs):
    """在 xs 上算高斯核密度；带宽用与前端**完全同一套** Silverman 规则，
    保证「总结里说的峰」就是图上看到的那个峰。"""
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    if sd <= 0:
        return [0.0] * len(xs)
    h = max(1.06 * sd * n ** (-0.2), 1e-9)
    out = []
    for x in xs:
        s = sum(math.exp(-0.5 * ((x - v) / h) ** 2) for v in vals)
        out.append(s / (n * h * math.sqrt(2 * math.pi)))
    return out


def _kde_peaks(vals, grid=160):
    """局部极大 [(分数, 密度)]，丢弃低于主峰 18% 的。"""
    n = len(vals)
    if n < 8:
        return []
    lo, hi = min(vals), max(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n)
    if sd <= 0:
        return []
    h = max(1.06 * sd * n ** (-0.2), 1e-9)
    pad = 3 * h
    xs = [lo - pad + (hi - lo + 2 * pad) * i / (grid - 1) for i in range(grid)]
    dens = _kde_density(vals, xs)
    peaks = [(xs[i], dens[i]) for i in range(1, grid - 1)
             if dens[i] > dens[i - 1] and dens[i] >= dens[i + 1]]
    if not peaks:
        return []
    top = max(d for _, d in peaks)
    return [(x, d) for x, d in peaks if d >= 0.18 * top]


def _is_bimodal(vals, peaks, sd):
    """两峰之间是否真有一个凹陷——用来判定「双峰」。

    阈值是实测定的，不是拍脑袋：九年级本次 11 班 6 科里，峰检测只在历史与思政
    检出两个峰（其余 4 科单峰），这两科正是用户看图认定「双峰」的那两科：
      历史  峰 54.2 / 62.6，谷 59.1，谷/矮峰 = 0.964，峰距 1.18σ
      思政  峰 29.5 / 37.7，谷 34.8，谷/矮峰 = 0.982，峰距 1.27σ
    最初写「谷 < 矮峰的 0.75」把这些全滤掉了——Silverman 带宽对 n=40 有 3~4 分宽，
    真实的簇间凹陷被平滑得很浅。所以判据改为「峰数 + 峰距 + 两峰可比 + 确实有凹陷」。
    """
    if len(peaks) < 2:
        return None
    (x1, d1), (x2, d2) = sorted(peaks, key=lambda t: -t[1])[:2]
    lo, hi = sorted((x1, x2))
    if hi - lo < 0.7 * sd:
        return None                      # 两峰离得太近，是同一条鼓包上的毛刺
    if min(d1, d2) < 0.6 * max(d1, d2):
        return None                      # 次峰太矮，算不上独立的一簇
    mid = [lo + (hi - lo) * i / 20 for i in range(21)]
    valley = min(_kde_density(vals, mid))
    if valley >= 0.99 * min(d1, d2):
        return None                      # 两峰之间不下凹，只是平顶
    return round(lo), round(hi)


def _peak_at(sh):
    """主峰位置（分数）。没有明显峰时退回中位数。

    **不要拿它判断「整体重心」**：主峰位置被偏度带偏，数学 11 班主峰比 02 班高 1.9 分
    而均分低 1.4 分，照主峰说「重心在上」是反的。判断整体位置用中位数。
    """
    if sh["peaks"]:
        return sorted(sh["peaks"], key=lambda t: -t[1])[0][0]
    return sh["median"]


def shape_of(vals):
    """一组分数的形状特征。"""
    sv = sorted(vals)
    n = len(sv)
    if n < 5:
        return None
    mean = sum(sv) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in sv) / n)
    low_cut, high_cut = mean - 1.5 * sd, mean + 1.5 * sd
    return {
        "n": n, "mean": mean, "sd": sd, "min": sv[0], "max": sv[-1],
        "median": _quantile(sv, .5), "p10": _quantile(sv, .10), "p25": _quantile(sv, .25),
        "p75": _quantile(sv, .75), "p90": _quantile(sv, .90),
        "peaks": _kde_peaks(sv),
        # 远低/远高于均值 1.5σ 的人数与分界：用来区分「一个孤点脱节」与「真的拖尾」
        "low_cut": low_cut, "high_cut": high_cut,
        "low_n": sum(1 for v in sv if v < low_cut),
        "high_n": sum(1 for v in sv if v > high_cut),
        # 两条尾巴的长度（均分到两端）：均分本身看不出分布偏哪边，尾巴长度才看得出
        "tail_low": mean - sv[0], "tail_high": sv[-1] - mean,
    }


def exam_summary(payload, subject, class_id, limit=140):
    """基于分布曲线写一段本次考试情况总结（≤ limit 字，默认 140）。

    重点写**表里读不出、曲线上才明显**的问题：双峰断裂、低分拖尾、高分段稀薄、
    过度集中（区分度低）、离散过大。均分相同而形状不同的两个班，真实问题完全不同，
    这正是只看均分/名次会漏掉的部分。按优先级取若干条，凑到字数上限为止。
    """
    cls = payload["classes"]
    me_tk = (cls.get(class_id) or {}).get("subjects", {}).get(subject)
    if not me_tk or not me_tk.get("s"):
        return ""
    me = shape_of(me_tk["s"])
    if me is None:
        return ""
    others = []
    for c in payload["focus"]:
        if c == class_id:
            continue
        tk = (cls.get(c) or {}).get("subjects", {}).get(subject)
        if tk and tk.get("s"):
            sh = shape_of(tk["s"])
            if sh:
                others.append((c, sh))
    def cname(c):
        return c + "班"                            # 班级显示名，与模板一致

    # ---- ① 均分与班内位次 -------------------------------------------------
    ranked = sorted([(me["mean"], class_id)] + [(s["mean"], c) for c, s in others], reverse=True)
    pos = [c for _, c in ranked].index(class_id) + 1
    segs = []
    if others:
        best_c, best = max(others, key=lambda t: t[1]["mean"])
        d = me["mean"] - best["mean"]
        cmp_txt = (f"较{cname(best_c)}低 {abs(d):.1f} 分" if d < -0.05
                   else f"较{cname(best_c)}高 {d:.1f} 分" if d > 0.05 else f"与{cname(best_c)}持平")
        segs.append(f"均分 {me['mean']:.1f}，在 {len(others)+1} 个对比班中排第 {pos}，{cmp_txt}")
    else:
        segs.append(f"均分 {me['mean']:.1f}")

    # ---- ② 形态解读（按优先级，越靠前越该说）------------------------------
    # 这一节是「表里看不出、曲线上才明显」的部分：均分相同而形状不同的两个班，
    # 真实问题完全不同，只看均分/名次会漏掉。写法上**重形态、轻数字**——
    # 用户明确要求「不要罗列太多具体数据，注重整体印象解读」。
    #
    # 判据一律用**两班直接对比**或**无分布假设的间隔**，不用「低于均分 1.5σ 算拖尾」那类
    # 隐含正态假设的阈值——报告本身声明「不做任何分布假设」，而且实测 4/40 低于 1.5σ
    # 与正态的理论 6.7% 差不多，据此说「拖尾长」是站不住的。
    feats = []
    best_c, best = (max(others, key=lambda t: t[1]["mean"]) if others else (None, None))
    sv = sorted(me_tk["s"])

    # ① 双峰：结构性问题，最该说。说清「两簇在哪、中间空在哪」，不空喊「两极分化」
    bim = _is_bimodal(me_tk["s"], me["peaks"], me["sd"])
    if bim:
        lo_pk, hi_pk = bim
        inner = [v for v in sv if lo_pk < v < hi_pk]
        edges = [lo_pk] + inner + [hi_pk]
        gw, ga, gb = max((b - a, a, b) for a, b in zip(edges, edges[1:]))
        hole = f"，{ga:.0f}~{gb:.0f} 分这一段无人" if gw >= 2 else ""
        feats.append(f"曲线是双峰：{lo_pk} 分上下与 {hi_pk} 分上下各聚着一簇人{hole}——"
                     f"班里实际是两拨人，不是一条连续的梯队，统一讲评对哪一拨都不解渴")

    # ② 整体位置：比中位数，**不比主峰位置**。
    # 主峰位置看着像是「重心」，其实被偏度带偏：数学 11 班主峰比 02 班高 1.9 分，
    # 均分却低 1.4 分（峰在均分之上、尾巴拖在下面），照主峰说「重心在上」是反的。
    # 而且 n=40 时带宽有 3~4 分宽，差 2 分的峰位本身就在噪声里。
    if others:
        d = me["median"] - best["median"]
        if d <= -2:
            feats.append(f"中位数比{cname(best_c)}低约 {abs(d):.0f} 分，整体位置偏低，"
                         f"不是个别分数段的问题")
        elif d >= 2:
            feats.append(f"中位数比{cname(best_c)}高约 {d:.0f} 分，整体位置在上")

    # ③ 下侧偏厚：均分被尾巴拽低，看图就是主峰右侧鼓出来一块
    if me["median"] - me["mean"] > 1.0:
        feats.append("均分被低分一侧拽在中位数之下，尾巴沉在下方")

    # ④ 孤点：最低的那批人里，相邻名次之间若拉开一个大口子，就是有人脱离了主体。
    # 用「间隔」判定不依赖任何分布假设；平缓下滑的低分长尾不会触发。
    k = max(2, int(len(sv) * 0.3))
    gaps = [(sv[i + 1] - sv[i], i) for i in range(k - 1)]
    if gaps:
        g, i = max(gaps)
        if g > 0.9 * me["sd"]:
            who = "一名学生" if i == 0 else f"最低的 {i + 1} 名学生"
            feats.append(f"{who}（最低 {sv[0]:.0f} 分）与上面一名相差 {g:.0f} 分，"
                         f"完全脱离主体，是孤悬的点而非拖尾")

    if others:
        # 高分端薄不薄，主要看「前 10% 的门槛」而不是最高分一个人——最高分可能是一枝独秀。
        # 门槛差 2 分以内当噪声，不说（差 1 分就断言「整体压不上去」是过度解读）。
        p90_low = me["p90"] < best["p90"] - 2
        if p90_low and me["max"] < best["max"] - 1:
            feats.append("高位段整体压不上去，尖子生也没顶出来")
        elif p90_low:
            feats.append("前 10% 的门槛明显低于对方，高分段厚度不够")
        elif me["max"] < best["max"] - 1:
            feats.append("最高分落后，是尖子生个人没顶出来，面上并不差")
        # 低分端比不比人低。门槛差 2 分以内同样当噪声（与高分端同一把尺子）
        if me["p10"] < best["p10"] - 2 or me["min"] < best["min"] - 1:
            feats.append("低分一侧也比对方沉")
        # 离散度：均分可能一样，但一个班挤成一团、另一个班摊得很开
        sd_max_c, sd_max_sh = max(others, key=lambda t: t[1]["sd"])
        sd_min_c, sd_min_sh = min(others, key=lambda t: t[1]["sd"])
        sd_max, sd_min = sd_max_sh["sd"], sd_min_sh["sd"]
        if me["sd"] < sd_min * 0.85:
            feats.append(f"成绩挤在 {me['median']:.0f} 分附近，比{cname(sd_min_c)}集中得多，区分度偏低")
        elif me["sd"] > sd_max * 1.15:
            feats.append(f"是三条曲线里最摊的一条，比{cname(sd_max_c)}散得多")

    # ---- ③ 组装：均分那段必留，形状问题按优先级塞到字数上限 ----------------
    out = segs[0]
    for f in feats:
        cand = out + "；" + f
        if len(cand) > limit:
            break
        out = cand
    return out if out.endswith("。") else out + "。"


# ------------------------------------------------------------------ 人工总结（可选）
def load_manual_summaries(exam_dir):
    """人工撰写的总结：<考试目录>/总结.json，形如 {"数学": "……"}。

    为什么要留这个口子：最好的总结来自「看图说话」——先把曲线渲染出来看一眼，
    再写下判断，而不是靠脚本硬套模板。脚本算出的那份是**兜底初稿**，
    这里有人工版本就优先用；两者都过同一个字数上限检查。
    """
    p = os.path.join(exam_dir, "总结.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8-sig") as f:      # 记事本/PowerShell 存的会带 BOM
            d = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"总结.json 不是合法的 JSON：{p}\n  {e}")
    if not isinstance(d, dict):
        raise SystemExit(f'总结.json 应该是一个对象：{{"科目": "文字"}}，实际是 {type(d).__name__}')
    # 下划线开头的键当注释（与 配置.json 的「_说明」同一惯例），不当科目
    return {k: v.strip() for k, v in d.items()
            if not k.startswith("_") and isinstance(v, str) and v.strip()}


# ------------------------------------------------------------------ 目标生
def find_targets(ex, subject, class_id, top_n, ratio):
    """本班该科目标生：总排名 ≤ top_n，且该科排名 > 总排名 × ratio。"""
    cols = G.normalize_total(G.scan_subjects(ex["header"]))
    sub_tuple = next((c for c in cols if c[0] == subject), None)
    base_tuple = next((c for c in cols if c[0] == ex["cfg"]["排名基准科目"]), None)
    if sub_tuple is None or base_tuple is None:
        return []
    sid_i = find_col(ex["header"], "学号")
    name_i = find_col(ex["header"], "姓名")
    _, sc_i, rk_i, lc_i = sub_tuple
    _, base_sc, base_rk, _ = base_tuple

    out = []
    for r in ex["groups"].get(class_id, []):
        tr = G.num(r[base_rk]) if base_rk < len(r) else None
        sr = G.num(r[rk_i]) if rk_i < len(r) else None
        if tr is None or sr is None or tr > top_n or sr <= tr * ratio:
            continue
        out.append({
            "sid": (r[sid_i].strip() if sid_i is not None and sid_i < len(r) else ""),
            "name": (r[name_i].strip() if name_i is not None and name_i < len(r) else ""),
            "totalRank": int(tr),
            "subjRank": int(sr),
            "gap": int(sr - tr),
            "ratio": round(sr / tr, 1) if tr else None,
            "score": G.num(r[sc_i]) if sc_i < len(r) else None,
            "band": (r[lc_i].strip() if lc_i < len(r) else ""),
        })
    out.sort(key=lambda t: -t["gap"])
    return out


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(
        description="按科目生成给科任老师的单科分析文档",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("本次考试", help="本次大考目录，如 九年级9月考")
    ap.add_argument("上次考试", help="上次大考目录，如 八年级下")
    ap.add_argument("--班级", default="11", help="本班编号（两位数，默认 11）")
    ap.add_argument("--目标生上限", type=int, default=100, help="总排名前多少名算候选（默认 100）")
    ap.add_argument("--倍数", type=float, default=1.3, help="单科排名 > 总排名 × 该倍数即入选（默认 1.3）")
    ap.add_argument("--科目", default=None, help="只生成指定科目（逗号分隔），默认取两场考试的交集")
    ap.add_argument("--总结字数", type=int, default=140,
                    help="本次考试情况总结的字数上限（默认 140）")
    ap.add_argument("--拔尖线", type=int, default=10,
                    help="目标生表里把年级总排名前多少名另标一档（默认 10）")
    args = ap.parse_args()

    class_id = str(args.班级).zfill(2)
    curr = load_exam(args.本次考试)
    prev = load_exam(args.上次考试)
    print(f"本次大考：{curr['name']}   {len(curr['classes'])} 个班 / {len(curr['all'])} 人")
    print(f"上次大考：{prev['name']}   {len(prev['classes'])} 个班 / {len(prev['all'])} 人")

    curr_cols = G.normalize_total(G.scan_subjects(curr["header"]))
    prev_cols = G.normalize_total(G.scan_subjects(prev["header"]))
    print(f"本次科目：{'、'.join(c[0] for c in curr_cols)}")
    print(f"上次科目：{'、'.join(c[0] for c in prev_cols)}")

    # 科目 = 两场考试的交集 − 排除项。只在一场出现的科目做不了对比，显式提示而不是静默产出半截文档
    if args.科目:
        subjects = [s.strip() for s in args.科目.split(",") if s.strip()]
    else:
        cur_names = {c[0] for c in curr_cols} - set(EXCLUDE)
        prv_names = {c[0] for c in prev_cols} - set(EXCLUDE)
        subjects = [c[0] for c in curr_cols if c[0] in cur_names & prv_names]
        only_curr = sorted(cur_names - prv_names)
        only_prev = sorted(prv_names - cur_names)
        if only_curr:
            print(f"提示：{curr['name']} 独有、无法对比，跳过：{'、'.join(only_curr)}")
        if only_prev:
            print(f"提示：{prev['name']} 独有、无法对比，跳过：{'、'.join(only_prev)}")
    if not subjects:
        raise SystemExit("两场考试没有可对比的科目，无事可做。")
    print(f"生成科目：{'、'.join(subjects)}（共 {len(subjects)} 份）")

    if class_id not in curr["classes"]:
        raise SystemExit(f"本次考试里没有 {class_id} 班（有：{'、'.join(curr['classes'])}）")
    if class_id not in prev["classes"]:
        print(f"⚠ 上次考试里没有 {class_id} 班，其「上次」一节将缺本班曲线。")

    manual = load_manual_summaries(curr["dir"])
    if manual:
        print(f"人工总结：{curr['name']}/总结.json 提供了 {len(manual)} 条")
        stray = [k for k in manual if k not in subjects]
        if stray:
            print(f"  ⚠ 这些科目本次不出文档，已忽略：{'、'.join(stray)}"
                  f"（科目名要和文档一致，别写错别字）")

    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    for token in ("/*__DATA__*/null", "__EXAM__"):
        if token not in tpl:
            raise SystemExit(f"科任老师模板里找不到占位符 {token}，模板可能被改动过。")

    out_dir = os.path.join(curr["dir"], "科任老师文档")
    os.makedirs(out_dir, exist_ok=True)

    print("\n=== 逐科生成 ===")
    ok_count = 0
    checks = []          # 每科两场的自检信息，最后统一核对，不能只看最后一科
    summaries = []       # 每科的考试情况总结，最后打印出来人工读一遍
    for subj in subjects:
        pp, pinfo = build_exam_payload(prev, subj, class_id)
        cp, cinfo = build_exam_payload(curr, subj, class_id)
        if cp is None:
            print(f"  ✗ {subj}：本次考试不可用——{cinfo}")
            continue
        if pp is None:
            print(f"  ✗ {subj}：上次考试不可用——{pinfo}，跳过（对比文档需要两场都有）")
            continue
        checks.append((subj, "上次", prev["name"], pinfo))
        checks.append((subj, "本次", curr["name"], cinfo))
        targets = find_targets(curr, subj, class_id, args.目标生上限, args.倍数)
        if subj in manual:
            summary, src = manual[subj], "人工"
        else:
            summary, src = exam_summary(cp, subj, class_id, limit=args.总结字数), "脚本初稿"
        if len(summary) > args.总结字数:
            print(f"  ⚠ {subj} 的{src}总结 {len(summary)} 字，超过上限 {args.总结字数}"
                  f"（不会截断，请自行精简）")
        summaries.append((subj, summary, src))

        data = {
            "subject": subj,
            "classId": class_id,
            "className": f"{class_id}班",
            "prevExam": prev["name"],
            "currExam": curr["name"],
            "prev": pp,
            "curr": cp,
            "targets": targets,
            "summary": summary,
            "rule": {"topN": args.目标生上限, "ratio": args.倍数,
                     "gradeN": cp["classes"]["全年级"]["n"],
                     "eliteN": args.拔尖线},
        }
        title = f"{class_id}班 · {subj} · 科任老师分析"
        page = tpl.replace("__EXAM__", html_escape(title))
        page = page.replace("/*__DATA__*/null",
                            json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        out = os.path.join(out_dir, f"{class_id}班-{subj}.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(page)
        ok_count += 1
        print(f"  ✓ {subj}：上次 {pinfo['levels']} 档 / 本次 {cinfo['levels']} 档，"
              f"本班 {cinfo['班人数'].get(class_id, '—')} 人，目标生 {len(targets)} 人"
              f"  ->  {os.path.basename(out)}")

    # 自检：与 生成分析页面.py 同款的核对数字，必须读，不能只看「已生成」。
    # 分带宽度若为负，该档字母会在图上静默消失——必须逐科逐场核，不能只看最后一科。
    print("\n=== 自检（逐科逐场）===")
    issues = 0
    for subj, tag, exam_name, info in checks:
        lv_sum = info["计数和"]
        ok = lv_sum == info["有档"]
        line = (f"  [{tag}] {exam_name} · {subj}：{info['levels']} 档，全年级 {info['grade_n']} 人，"
                f"该科有档 {info['有档']}，计数和 {lv_sum}  "
                f"该科均分 {info['均分']}  {'OK' if ok else '⚠ 计数与总数不符'}")
        if not ok:
            issues += 1
        print(line)
        if info["分带坏"]:
            issues += 1
            for lv, l, r in info["分带坏"]:
                print(f"    ⚠ {lv} 档分带 {l}~{r} 宽度为负，该档字母不会显示")
        if info["缺排名区间"]:
            issues += 1
            print(f"    ⚠ 等级未出现在基准科目排名里：{''.join(info['缺排名区间'])}——表头「排名基准科目」可能不对")
        bad_n = [c for c, n in info["班人数"].items() if n < 5]
        if bad_n:
            issues += 1
            print(f"    ⚠ 人数异常少的班：{bad_n}")
    print(f"  自检问题数：{issues}")

    # 总结必须逐条读一遍：字数、以及与曲线是否相符
    print(f"\n=== 本次考试情况总结（上限 {args.总结字数} 字）===")
    for subj, s, src in summaries:
        flag = "OK" if 0 < len(s) <= args.总结字数 else f"⚠ 超限 {len(s)} 字"
        print(f"\n  【{subj}】{len(s)} 字 · {src} · {flag}\n    {s}")

    # 目标生逐科点名，便于与既有名单人工核对
    print("\n=== 目标生点名 ===")
    for subj in subjects:
        rows = find_targets(curr, subj, class_id, args.目标生上限, args.倍数)
        print(f"  {subj}（{len(rows)} 人）："
              + ("、".join(f"{r['name']}({r['totalRank']}→{r['subjRank']})" for r in rows) or "无"))

    print(f"\n已生成 {ok_count} 份文档 -> {out_dir}")


if __name__ == "__main__":
    main()
