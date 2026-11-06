"""把数据库里的行渲染成一个自包含的静态页面（无外部依赖、无 CDN）。

页面是纯 HTML + 内联 CSS/JS，可以直接丢到 GitHub Pages、对象存储、
或者双击本地打开。筛选和搜索都在前端做，不需要后端。
"""

from __future__ import annotations

import html
import json
from datetime import datetime

from . import clock
from .config import Config
from .score import TIER_LABEL, study_type

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f5f4f1;--card:#fff;--ink:#1c1917;--muted:#78716c;--line:#e7e5e4;
  --accent:#0f766e;--t5:#b45309;--t4:#0f766e;--t3:#57534e;--t2:#a8a29e;
  --shadow:0 1px 2px rgba(28,25,23,.04),0 10px 30px rgba(28,25,23,.06);
}
@media (prefers-color-scheme:dark){
  :root{--bg:#121211;--card:#1c1b19;--ink:#e7e5e4;--muted:#a8a29e;--line:#2d2b28;
  --accent:#2dd4bf;--t5:#f59e0b;--t4:#2dd4bf;--t3:#a8a29e;--t2:#78716c;--shadow:none}
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans SC",sans-serif;
  font-size:15px;line-height:1.65;letter-spacing:.01em;
}
.wrap{max-width:940px;margin:0 auto;padding:0 20px 80px}
a{color:inherit;text-decoration:none}
a:hover{color:var(--accent)}

/* ---------- 头部 ---------- */
header{padding:56px 0 26px;border-bottom:1px solid var(--line)}
.kicker{margin:0 0 10px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted)}
h1{margin:0;font-size:clamp(26px,5vw,34px);font-weight:650;letter-spacing:-.01em}
.sub{margin:8px 0 0;color:var(--muted);font-size:14px}
.stats{display:flex;flex-wrap:wrap;gap:8px 22px;margin-top:20px;font-size:13px;color:var(--muted)}
.stats b{color:var(--ink);font-variant-numeric:tabular-nums}

/* ---------- 工具条 ---------- */
.controls{position:sticky;top:0;z-index:20;padding:14px 0;background:var(--bg);border-bottom:1px solid var(--line)}
.controls .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input[type=search]{
  flex:1 1 240px;min-width:0;padding:9px 13px;font:inherit;font-size:14px;color:var(--ink);
  background:var(--card);border:1px solid var(--line);border-radius:9px;outline:none;
}
input[type=search]:focus{border-color:var(--accent)}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chips button{
  padding:6px 12px;font:inherit;font-size:13px;color:var(--muted);cursor:pointer;
  background:transparent;border:1px solid var(--line);border-radius:999px;
}
.chips button.on{color:#fff;background:var(--accent);border-color:var(--accent)}
.controls .count{margin-left:auto;font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}

/* ---------- 本周必读 ---------- */
.picks{padding:34px 0 8px}
h2{margin:0 0 16px;font-size:13px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.picks ol{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.picks li{display:grid;grid-template-columns:26px 1fr;gap:12px;padding:10px 0;border-bottom:1px dashed var(--line)}
.picks li:last-child{border-bottom:0}
.picks .n{font:600 13px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--t5)}
.picks .t{font-weight:550}
.picks .m{display:block;margin-top:2px;font-size:12px;color:var(--muted)}

/* ---------- 列表 ---------- */
.list{display:grid;gap:14px;padding-top:26px}
.card{
  display:grid;grid-template-columns:46px 1fr;gap:14px;
  padding:20px 22px 18px;background:var(--card);border:1px solid var(--line);
  border-left:3px solid var(--t3);border-radius:12px;box-shadow:var(--shadow);
}
.card.t5{border-left-color:var(--t5)}
.card.t4{border-left-color:var(--t4)}
.card.t3{border-left-color:var(--t3)}
.card.t2{border-left-color:var(--t2)}
.rk{font:600 15px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted)}
.meta{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;color:var(--muted)}
.j{font-weight:650;font-size:12px;letter-spacing:.04em;color:var(--t4);text-transform:uppercase}
.t5 .j{color:var(--t5)}
.tp{padding:1px 7px;border:1px solid var(--line);border-radius:5px;font-size:11px}
.tp.new{color:#fff;background:var(--t5);border-color:var(--t5)}
.tp.zh{color:var(--accent);border-color:var(--accent)}
h3{margin:8px 0 0;font-size:16.5px;font-weight:600;line-height:1.45;letter-spacing:-.005em}
.zht{margin:5px 0 0;font-size:14px;color:var(--accent);font-weight:500}
.digest{margin:12px 0 0;padding:12px 14px;background:var(--bg);border-radius:9px;display:grid;gap:5px;font-size:13.5px}
.digest div{display:grid;grid-template-columns:34px 1fr;gap:8px}
.lab{color:var(--muted);font-size:12px;padding-top:1px}
details{margin-top:10px;font-size:13.5px}
summary{cursor:pointer;color:var(--muted);font-size:12.5px;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"＋ ";color:var(--accent)}
details[open] summary::before{content:"－ "}
details p{margin:8px 0 0;color:var(--muted);white-space:pre-line;padding-left:14px;border-left:2px solid var(--line)}
.foot{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:baseline;margin-top:14px;font-size:12px;color:var(--muted)}
.sc{font:600 12px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--ink);padding:3px 7px;background:var(--bg);border-radius:5px}
.why{flex:1 1 260px;min-width:0}
.links{display:flex;gap:12px}
.links a{border-bottom:1px solid var(--line)}

footer{margin-top:56px;padding-top:22px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);line-height:1.8}
.empty{padding:40px 0;color:var(--muted)}
.hide{display:none!important}

@media (max-width:620px){
  header{padding-top:36px}
  .card{grid-template-columns:30px 1fr;gap:10px;padding:16px}
  .controls .count{display:none}
}
"""

JS = """
(function(){
  var q = document.getElementById('q');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var buttons = Array.prototype.slice.call(document.querySelectorAll('.chips button'));
  var count = document.getElementById('count');
  var filter = 'all';

  function match(card){
    if(filter === 'top' && card.dataset.tier !== '5') return false;
    if(filter === 'zh' && card.dataset.zh !== '1') return false;
    if(filter !== 'all' && filter !== 'top' && filter !== 'zh' && card.dataset.type !== filter) return false;
    var needle = (q.value || '').trim().toLowerCase();
    if(!needle) return true;
    return (card.dataset.text || '').indexOf(needle) !== -1;
  }

  function apply(){
    var shown = 0;
    cards.forEach(function(card){
      var ok = match(card);
      card.classList.toggle('hide', !ok);
      if(ok) shown++;
    });
    count.textContent = shown + ' / ' + cards.length + ' 篇';
  }

  buttons.forEach(function(btn){
    btn.addEventListener('click', function(){
      buttons.forEach(function(b){ b.classList.remove('on'); });
      btn.classList.add('on');
      filter = btn.dataset.f;
      apply();
    });
  });
  q.addEventListener('input', apply);
  apply();
})();
"""


def _e(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _authors(item: dict) -> str:
    names = item.get("authors") or []
    if not names:
        return ""
    if len(names) <= 2:
        return "、".join(names)
    return "、".join(names[:2]) + " 等"


def _link(item: dict) -> str:
    if item.get("doi"):
        return "https://doi.org/" + item["doi"]
    if item.get("url"):
        return item["url"]
    if item.get("pmid"):
        return "https://pubmed.ncbi.nlm.nih.gov/" + item["pmid"] + "/"
    return ""


def _card(item: dict, rank: int) -> str:
    tier = int(item.get("tier") or 0)
    stype = study_type(item.get("pubtypes") or [], item.get("title") or "")
    is_new = item.get("first_seen") == clock.today().isoformat()
    has_zh = bool(item.get("zh_title"))

    chips = []
    if is_new:
        chips.append('<span class="tp new">新</span>')
    label = _label(stype)
    if label:
        chips.append(f'<span class="tp">{label}</span>')
    if tier >= 5:
        chips.append('<span class="tp">旗舰</span>')
    if has_zh:
        chips.append('<span class="tp zh">中文</span>')
    chips_html = "".join(chips)

    meta_bits = []
    published = item.get("published") or ""
    if published:
        if published[:4] == str(clock.today().year):
            meta_bits.append(published[5:])
        else:
            meta_bits.append(published)
    authors = _authors(item)
    if authors:
        meta_bits.append(authors)
    meta_html = " · ".join(_e(bit) for bit in meta_bits)

    zh_block = ""
    if has_zh:
        rows = []
        for label, key in (("设计", "zh_design"), ("结果", "zh_finding"), ("意义", "zh_practice")):
            text = item.get(key) or ""
            if text:
                rows.append(f'<div><span class="lab">{label}</span><span>{_e(text)}</span></div>')
        if rows:
            zh_block = '<div class="digest">' + "".join(rows) + "</div>"

    abstract = (item.get("abstract") or "").strip()
    abs_block = (
        '<details><summary>英文摘要</summary><p>%s</p></details>' % _e(abstract) if abstract else ""
    )

    link = _link(item)
    links = []
    if item.get("pmid"):
        links.append('<a href="https://pubmed.ncbi.nlm.nih.gov/%s/">PubMed</a>' % _e(item["pmid"]))
    if link:
        links.append('<a href="%s">DOI</a>' % _e(link))

    why = " · ".join(item.get("reasons") or [])
    links_html = "".join(links)
    blob = " ".join(
        [
            item.get("title") or "",
            item.get("journal") or "",
            item.get("zh_title") or "",
            (item.get("abstract") or "")[:1200],
            authors,
        ]
    ).lower()

    title_html = '<a href="%s">%s</a>' % (_e(link), _e(item.get("title")))
    zh_title_html = (
        '<p class="zht">%s</p>' % _e(item.get("zh_title")) if has_zh else ""
    )

    return f"""
<article class="card t{tier}" data-type="{stype}" data-tier="{tier}" data-zh="{1 if has_zh else 0}" data-text="{_e(blob)}">
  <div class="rk">{rank:02d}</div>
  <div>
    <div class="meta">
      {chips_html}
      <span class="j">{_e(item.get("abbr"))}</span>
      <span>{meta_html}</span>
    </div>
    <h3>{title_html}</h3>
    {zh_title_html}
    {zh_block}
    {abs_block}
    <div class="foot">
      <span class="sc">{float(item.get("score") or 0):.0f}</span>
      <span class="why">{_e(why)}</span>
      <span class="links">{links_html}</span>
    </div>
  </div>
</article>"""


def _label(stype: str) -> str:
    return {
        "rct": "RCT",
        "meta": "Meta",
        "guideline": "指南",
        "multi": "多中心",
        "review": "综述",
        "case": "病例",
        "other": "",
    }.get(stype, "")


def build_html(items: list[dict], cfg: Config, generated_at: datetime, stats: dict | None = None) -> str:
    week = clock.today().isoformat()
    stats = stats or {}
    new_count = stats.get("new_today", sum(1 for i in items if i.get("first_seen") == week))
    db_total = stats.get("total", len(items))
    zh_count = sum(1 for i in items if i.get("zh_title"))

    picks = []
    for rank, item in enumerate(items[: cfg.top_n], start=1):
        label = item.get("zh_title") or item.get("title") or ""
        picks.append(
            f'<li><span class="n">{rank}</span><span><a class="t" href="{_e(_link(item))}">'
            f'{_e(label)}</a><span class="m">{_e(item.get("abbr"))} · '
            f'{_e(item.get("published"))} · 评分 {float(item.get("score") or 0):.0f}</span></span></li>'
        )
    picks_block = (
        f'<section class="picks"><h2>本周必读</h2><ol>{"".join(picks)}</ol></section>' if picks else ""
    )

    cards = "".join(_card(item, n) for n, item in enumerate(items, start=1))
    if not cards:
        cards = '<p class="empty">这个窗口里没有抓到新文献。可能是期刊池里的 ISSN 不对，先跑 <code>python main.py check</code>。</p>'

    stamp = generated_at.strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(cfg.title)} · {week}</title>
<meta name="description" content="{_e(cfg.subtitle)}">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="kicker">Urology Literature Radar</p>
  <h1>{_e(cfg.title)}</h1>
  <p class="sub">{_e(cfg.subtitle)} · {week}</p>
  <div class="stats">
    <span>本期 <b>{len(items)}</b> 篇</span>
    <span>今日入库 <b>{new_count}</b></span>
    <span>中文速读 <b>{zh_count}</b></span>
    <span>更新于 <b>{stamp}</b></span>
  </div>
</header>

<div class="controls">
  <div class="row">
    <input id="q" type="search" placeholder="搜标题、期刊、摘要…" autocomplete="off">
    <div class="chips">
      <button data-f="all" class="on">全部</button>
      <button data-f="rct">RCT</button>
      <button data-f="meta">Meta</button>
      <button data-f="guideline">指南</button>
      <button data-f="top">旗舰</button>
      <button data-f="zh">有中文</button>
    </div>
    <span class="count" id="count"></span>
  </div>
</div>

{picks_block}

<div class="list">
{cards}
</div>

<footer>
  数据来源：Crossref（上线当天即可见）+ PubMed（摘要、文献类型）。两源按 DOI 合并去重。<br>
  评分 = 期刊层级 + 文献类型（RCT / Meta / 指南优先，病例报告和社论降权）+ 兴趣词命中 + 新鲜度，明细见每篇底部。<br>
  本页为静态文件，不追踪任何访问。库内累计 {db_total} 篇，生成时间 {stamp}。
</footer>
</div>
<script>{JS}</script>
</body>
</html>
"""


def write_page(
    items: list[dict],
    cfg: Config,
    generated_at: datetime | None = None,
    stats: dict | None = None,
) -> str:
    generated_at = generated_at or clock.now()
    cfg.output.parent.mkdir(parents=True, exist_ok=True)
    html_text = build_html(items, cfg, generated_at, stats)
    cfg.output.write_text(html_text, encoding="utf-8")

    (cfg.output.parent / "papers.json").write_text(
        json.dumps(
            {"generated_at": generated_at.isoformat(timespec="seconds"), "items": items},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return str(cfg.output)
