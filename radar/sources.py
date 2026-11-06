"""两个数据源 + 一个合并器。

为什么是两个：
  Crossref 快 —— 文章 online-first 当天就有 DOI/标题，但摘要经常是空的；
  PubMed   全 —— 有摘要、有 MeSH、有 PublicationType（RCT / Meta-Analysis / Guideline），
                这些正是「高分」排序要用的信号，但收录通常比上线晚几天到两周。
两边用 DOI 合并，摘要取长的那个。
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date

from .config import Config, Journal
from .net import Http

CROSSREF_JOURNAL = "https://api.crossref.org/journals/{issn}/works"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NORM_RE = re.compile(r"[^a-z0-9]+")


def clean_text(value: str) -> str:
    """去掉 Crossref 摘要里的 JATS 标签和多余空白。"""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def norm_title(value: str) -> str:
    return _NORM_RE.sub("", (value or "").lower())[:140]


@dataclass
class Paper:
    doi: str = ""
    pmid: str = ""
    title: str = ""
    abstract: str = ""
    journal: str = ""
    abbr: str = ""
    group: str = "综合"
    tier: int = 0
    published: str = ""
    authors: list[str] = field(default_factory=list)
    pubtypes: list[str] = field(default_factory=list)
    cited_by: int = 0
    url: str = ""
    sources: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower()
        if self.pmid:
            return "pmid:" + self.pmid
        return "t:" + norm_title(self.title)

    @property
    def link(self) -> str:
        if self.doi:
            return "https://doi.org/" + self.doi
        return self.url or (f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else "")


# --------------------------------------------------------------------- Crossref


def _crossref_date(item: dict) -> str:
    for name in ("published-online", "published", "issued", "created"):
        node = item.get(name) or {}
        parts = node.get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            p = parts[0]
            y = int(p[0])
            m = int(p[1]) if len(p) > 1 and p[1] else 1
            d = int(p[2]) if len(p) > 2 and p[2] else 1
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def _crossref_authors(item: dict) -> list[str]:
    out = []
    for a in item.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x)
        if not name:
            name = a.get("name") or ""
        if name:
            out.append(name)
    return out


def fetch_crossref(http: Http, cfg: Config, journal: Journal, since: date) -> list[Paper]:
    params = {
        "filter": f"from-created-date:{since.isoformat()},type:journal-article",
        "rows": str(cfg.crossref_rows),
        "sort": "created",
        "order": "desc",
        "mailto": cfg.mailto,
    }
    data = None
    for issn in (journal.issn, journal.issn_alt):
        if not issn:
            continue
        data = http.get_json(CROSSREF_JOURNAL.format(issn=issn), params, allow_404=True)
        if data and data.get("message", {}).get("items"):
            break
    if not data:
        return []

    papers: list[Paper] = []
    for item in data.get("message", {}).get("items", []):
        titles = item.get("title") or []
        if not titles or not titles[0]:
            continue
        papers.append(
            Paper(
                doi=(item.get("DOI") or "").strip(),
                title=clean_text(titles[0]),
                abstract=clean_text(item.get("abstract") or ""),
                journal=journal.name,
                abbr=journal.abbr,
                group=journal.group,
                tier=journal.tier,
                published=_crossref_date(item),
                authors=_crossref_authors(item),
                cited_by=int(item.get("is-referenced-by-count") or 0),
                url=item.get("URL") or "",
                sources={"crossref"},
            )
        )
    return papers


# --------------------------------------------------------------------- PubMed


def _txt(node) -> str:
    if node is None:
        return ""
    return _WS_RE.sub(" ", "".join(node.itertext())).strip()


def _pubmed_date(article) -> str:
    node = article.find(".//Article/ArticleDate")
    if node is None:
        node = article.find(".//Article/Journal/JournalIssue/PubDate")
    if node is None:
        return ""
    year = node.findtext("Year") or ""
    if not year:
        medline = node.findtext("MedlineDate") or ""
        m = re.match(r"(\d{4})", medline)
        if not m:
            return ""
        return f"{m.group(1)}-01-01"
    month_raw = (node.findtext("Month") or "").strip()
    day_raw = (node.findtext("Day") or "").strip()
    month = 1
    if month_raw.isdigit():
        month = int(month_raw)
    elif month_raw[:3].lower() in MONTHS:
        month = MONTHS[month_raw[:3].lower()]
    day = int(day_raw) if day_raw.isdigit() else 1
    return f"{int(year):04d}-{month:02d}-{day:02d}"


def _pubmed_abstract(article) -> str:
    parts = []
    for node in article.findall(".//Article/Abstract/AbstractText"):
        text = _txt(node)
        if not text:
            continue
        label = node.get("Label")
        parts.append(f"{label}: {text}" if label else text)
    return "\n".join(parts)


def _pubmed_authors(article) -> list[str]:
    out = []
    for node in article.findall(".//Article/AuthorList/Author"):
        name = " ".join(x for x in (node.findtext("ForeName"), node.findtext("LastName")) if x)
        if not name:
            name = node.findtext("CollectiveName") or ""
        if name:
            out.append(name)
    return out


def pubmed_query(journals: list[Journal]) -> str:
    clauses = " OR ".join(f'"{j.abbr}"[ta]' for j in journals)
    return f"({clauses}) AND English[lang]"


def fetch_pubmed(http: Http, cfg: Config, journals: list[Journal], days: int) -> list[Paper]:
    hits = http.get_json(
        ESEARCH,
        {
            "db": "pubmed",
            "term": pubmed_query(journals),
            "reldate": days,
            "datetype": "edat",
            "retmax": cfg.pubmed_retmax,
            "sort": "date",
            "retmode": "json",
            "tool": "uro-radar",
            "email": cfg.mailto,
        },
    )
    ids = (hits or {}).get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    papers: list[Paper] = []
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        raw = http.get(
            EFETCH,
            {
                "db": "pubmed",
                "id": ",".join(chunk),
                "retmode": "xml",
                "rettype": "abstract",
                "tool": "uro-radar",
                "email": cfg.mailto,
            },
        )
        root = ET.fromstring(raw)
        for art in root.findall(".//PubmedArticle"):
            pmid = _txt(art.find(".//MedlineCitation/PMID"))
            abbr = _txt(art.find(".//MedlineJournalInfo/MedlineTA"))
            journal = cfg.by_abbr.get(abbr)
            if journal is None:
                # 配置里没有的期刊（PubMed 的同名/改名期刊）直接跳过
                continue
            doi = ""
            for aid in art.findall(".//PubmedData/ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi" and aid.text:
                    doi = aid.text.strip()
                    break
            papers.append(
                Paper(
                    doi=doi,
                    pmid=pmid,
                    title=_txt(art.find(".//Article/ArticleTitle")),
                    abstract=_pubmed_abstract(art),
                    journal=journal.name,
                    abbr=journal.abbr,
                    group=journal.group,
                    tier=journal.tier,
                    published=_pubmed_date(art),
                    authors=_pubmed_authors(art),
                    pubtypes=[_txt(x) for x in art.findall(".//Article/PublicationTypeList/PublicationType") if _txt(x)],
                    sources={"pubmed"},
                )
            )
        time.sleep(0.4)
    return papers


def count_pubmed(http: Http, cfg: Config, journal: Journal, days: int) -> int:
    """只取命中数，用于 check 命令核对期刊缩写。"""
    data = http.get_json(
        ESEARCH,
        {
            "db": "pubmed",
            "term": f'"{journal.abbr}"[ta]',
            "reldate": days,
            "datetype": "edat",
            "retmax": 0,
            "retmode": "json",
            "tool": "uro-radar",
            "email": cfg.mailto,
        },
    )
    return int((data or {}).get("esearchresult", {}).get("count", 0))


# --------------------------------------------------------------------- 合并


def merge(papers: list[Paper]) -> list[Paper]:
    """同一篇在 Crossref 和 PubMed 各出现一次 —— 按 DOI，没有 DOI 就按标题归一化。"""
    by_doi: dict[str, Paper] = {}
    by_title: dict[str, Paper] = {}

    for p in papers:
        if not p.title:
            continue
        dkey = p.doi.lower()
        tkey = norm_title(p.title)
        existing = (dkey and by_doi.get(dkey)) or (tkey and by_title.get(tkey))
        if existing is None:
            existing = p
        else:
            if len(p.abstract) > len(existing.abstract):
                existing.abstract = p.abstract
            if not existing.pmid and p.pmid:
                existing.pmid = p.pmid
            if not existing.doi and p.doi:
                existing.doi = p.doi
            if not existing.published and p.published:
                existing.published = p.published
            if len(p.authors) > len(existing.authors):
                existing.authors = p.authors
            if len(p.pubtypes) > len(existing.pubtypes):
                existing.pubtypes = p.pubtypes
            existing.cited_by = max(existing.cited_by, p.cited_by)
            existing.sources |= p.sources
        if existing.doi:
            by_doi[existing.doi.lower()] = existing
        if tkey:
            by_title[tkey] = existing

    unique = {id(p): p for p in list(by_doi.values()) + list(by_title.values())}
    return list(unique.values())
