"""打分：为什么这篇排前面。

只按影响因子排是没法用的 —— 顶刊一天也能发十条 case report 和社论。
所以分三层：
  期刊层  tier 5..1        —— 你信这本刊的整体水准
  证据层  PublicationType  —— RCT / Meta / Guideline 才是真正值得读的
  个人层  兴趣词命中        —— 你正在做的方向（数据库研究、结石、BPH…）
最后加一点“新鲜度”，让昨天上线的往前排。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .config import Config
from .sources import Paper

TIER_LABEL = {5: "旗舰", 4: "一线", 3: "主流", 2: "补充", 1: "其他"}

# 按 PubMed PublicationType 原值（小写后）匹配
PUBTYPE_BONUS = {
    "randomized controlled trial": 16,
    "practice guideline": 16,
    "guideline": 14,
    "meta-analysis": 14,
    "systematic review": 12,
    "clinical trial, phase iii": 14,
    "clinical trial, phase ii": 6,
    "clinical trial": 5,
    "consensus development conference": 10,
    "multicenter study": 8,
    "observational study": 4,
    "comparative study": 3,
    "review": -2,
    "case reports": -14,
    "editorial": -16,
    "comment": -16,
    "letter": -12,
    "news": -16,
    "published erratum": -40,
    "retraction of publication": -40,
}

TYPE_LABEL = {
    "rct": "RCT",
    "meta": "Meta/系统评价",
    "guideline": "指南",
    "multi": "多中心",
    "review": "综述",
    "case": "病例报告",
    "other": "论著",
}

# Crossref 来的条目没有 PublicationType（PubMed 要晚几天才收录），
# 先用标题兜一下，否则新上线的那批会全按期刊层级排。
TITLE_HINTS = (
    (re.compile(r"\bmeta-analysis\b", re.I), "Meta-Analysis"),
    (re.compile(r"\bsystematic review\b", re.I), "Systematic Review"),
    (re.compile(r"\brandomi[sz]ed\b", re.I), "Randomized Controlled Trial"),
    (re.compile(r"\bguideline", re.I), "Practice Guideline"),
    (re.compile(r"\bcase report", re.I), "Case Reports"),
    (re.compile(r"\beditorial\b|\bcommentary\b|\bperspective\b", re.I), "Editorial"),
)

PUBTYPE_TO_STYPE = {
    "Randomized Controlled Trial": "rct",
    "Meta-Analysis": "meta",
    "Systematic Review": "meta",
    "Practice Guideline": "guideline",
    "Case Reports": "case",
    "Editorial": "other",
}


def _title_hint(title: str) -> str | None:
    for pattern, ptype in TITLE_HINTS:
        if pattern.search(title or ""):
            return ptype
    return None


def study_type(pubtypes: list[str], title: str = "") -> str:
    low = [p.lower() for p in pubtypes]
    if any(p.startswith("randomized controlled trial") or p.startswith("clinical trial") for p in low):
        return "rct"
    if any("meta-analysis" in p or "systematic review" in p for p in low):
        return "meta"
    if any("guideline" in p or "consensus" in p for p in low):
        return "guideline"
    if "multicenter study" in low:
        return "multi"
    if "case reports" in low:
        return "case"
    if "review" in low:
        return "review"
    hint = _title_hint(title)
    if hint:
        return PUBTYPE_TO_STYPE.get(hint, "other")
    return "other"


def _days_old(published: str) -> int | None:
    if not published:
        return None
    try:
        d = datetime.strptime(published, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - d).days


def score_paper(p: Paper, cfg: Config) -> tuple[float, list[str]]:
    total = 0.0
    why: list[str] = []

    if p.tier:
        bonus = cfg.tier_weight * p.tier
        total += bonus
        why.append(f"{TIER_LABEL.get(p.tier, '其他')}期刊 · {p.abbr} +{bonus:.0f}")

    pt_hits = []
    for raw in p.pubtypes:
        bonus = PUBTYPE_BONUS.get(raw.lower())
        if bonus is not None:
            total += bonus
            pt_hits.append((raw, bonus))
    if pt_hits:
        for raw, bonus in pt_hits[:3]:
            why.append(f"{raw} {'+' if bonus >= 0 else ''}{bonus:.0f}")
    else:
        hint = _title_hint(p.title)
        bonus = PUBTYPE_BONUS.get((hint or "").lower())
        if hint and bonus is not None:
            total += bonus
            why.append(f"标题判断 · {hint} {'+' if bonus >= 0 else ''}{bonus:.0f}")

    haystack = f"{p.title} {p.abstract}".lower()
    hits = [kw for kw in cfg.interests if kw in haystack]
    if hits:
        bonus = min(len(hits) * cfg.interest_weight, cfg.interest_cap)
        total += bonus
        shown = "、".join(hits[:4])
        why.append(f"兴趣词 {shown} +{bonus:.0f}")

    age = _days_old(p.published)
    if age is not None and 0 <= age <= cfg.fresh_days:
        total += cfg.fresh_bonus
        why.append(f"{age} 天内发表 +{cfg.fresh_bonus:.0f}")

    if p.cited_by >= 5:
        bonus = min(p.cited_by / 5.0, 8.0)
        total += bonus
        why.append(f"已被引 {p.cited_by} 次 +{bonus:.0f}")

    if not p.abstract:
        total -= 12
        why.append("无摘要 -12")

    return round(total, 1), why
