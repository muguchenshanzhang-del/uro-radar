"""命令行：build（抓取+打分+速读+出页面）与 check（核对期刊池）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta

from . import clock
from .config import Config
from .digest import run as run_digest
from .net import Http
from .render import write_page
from .score import score_paper
from .sources import Paper, count_pubmed, fetch_crossref, fetch_pubmed, merge
from .store import Store, row_to_dict


def _log(msg: str) -> None:
    print(msg, flush=True)


def _row(p: Paper, score: float, reasons: list[str], today: str) -> dict:
    return {
        "key": p.key,
        "doi": p.doi,
        "pmid": p.pmid,
        "title": p.title,
        "abstract": (p.abstract or "")[:6000],
        "journal": p.journal,
        "abbr": p.abbr,
        "grp": p.group,
        "tier": p.tier,
        "published": p.published,
        "authors": json.dumps(p.authors[:8], ensure_ascii=False),
        "pubtypes": json.dumps(p.pubtypes, ensure_ascii=False),
        "cited_by": p.cited_by,
        "url": p.url,
        "sources": json.dumps(sorted(p.sources)),
        "score": score,
        "reasons": json.dumps(reasons, ensure_ascii=False),
        "first_seen": today,
        "last_seen": today,
    }


def cmd_build(args) -> int:
    cfg = Config(args.config)
    clock.configure(cfg.timezone)
    if args.days:
        cfg.days = args.days
    if args.limit:
        cfg.limit = args.limit
    if args.no_llm:
        cfg.llm_enabled = False

    http = Http(cfg.user_agent)
    since = clock.today() - timedelta(days=cfg.days)
    _log(f"窗口：{since} 起 {cfg.days} 天 · 期刊 {len(cfg.journals)} 本")

    collected: list[Paper] = []

    _log("\n[1/5] Crossref")
    for journal in cfg.journals:
        try:
            got = fetch_crossref(http, cfg, journal, since)
        except Exception as exc:
            _log(f"  ! {journal.abbr}: {exc}")
            continue
        collected.extend(got)
        _log(f"  · {journal.abbr:<34}{len(got):>3}")
        time.sleep(0.15)

    _log("\n[2/5] PubMed")
    try:
        pubmed_papers = fetch_pubmed(http, cfg, cfg.journals, cfg.days)
        collected.extend(pubmed_papers)
        _log(f"  · 共 {len(pubmed_papers)} 条")
    except Exception as exc:
        _log(f"  ! PubMed 失败：{exc}")

    papers = merge(collected)
    _log(f"\n[3/5] 合并去重后 {len(papers)} 篇")

    today = clock.today().isoformat()
    rows = []
    for paper in papers:
        total, why = score_paper(paper, cfg)
        rows.append(_row(paper, total, why, today))

    store = Store(cfg.db)
    state_path = cfg.root / "data" / "state.json"
    if state_path.exists():
        try:
            store.import_state(json.loads(state_path.read_text("utf-8")))
            _log(f"  · 读入状态：{len(store.export_state()['seen'])} 条历史记录")
        except Exception as exc:
            _log(f"  ! 状态文件读不了，当第一次跑：{exc}")
    store.upsert(rows)
    _log(f"  · 入库完成，库里共 {store.stats()['total']} 篇")

    _log("\n[4/5] 中文速读")
    pending = store.pending_digest(since.isoformat(), cfg.llm_max_per_run)
    if pending:
        _log(f"  · 待生成 {len(pending)} 篇（上限 {cfg.llm_max_per_run}）")
        saved = run_digest(store, cfg, http, pending, log=lambda m: _log("  " + m.strip()))
        _log(f"  · 本次生成 {saved} 篇")
    else:
        _log("  · 没有需要生成的")

    _log("\n[5/5] 生成页面")
    items = [row_to_dict(r) for r in store.recent(cfg.days, cfg.limit)]
    stats = store.stats()
    path = write_page(items, cfg, stats=stats)
    pruned = store.prune(cfg.prune_days)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(store.export_state(), ensure_ascii=False), encoding="utf-8")
    _log(f"  · {len(items)} 篇 -> {path}")
    if pruned:
        _log(f"  · 清理旧记录 {pruned} 条")
    _log(f"  · 库内共 {stats['total']} 篇，其中 {stats['with_zh']} 篇有中文速读")
    store.close()
    return 0


def cmd_check(args) -> int:
    cfg = Config(args.config)
    clock.configure(cfg.timezone)
    http = Http(cfg.user_agent)
    since = clock.today() - timedelta(days=30)
    _log(f"{'期刊':<34}{'Crossref':>10}{'PubMed':>9}   tier/group  （近 30 天）")
    _log("-" * 82)
    broken = 0
    for journal in cfg.journals:
        try:
            cr = len(fetch_crossref(http, cfg, journal, since))
        except Exception:
            cr = -1
        try:
            pm = count_pubmed(http, cfg, journal, 30)
        except Exception:
            pm = -1
        flag = "" if (cr > 0 and pm > 0) else "   ← 两边都没抓到，多半是 ISSN 或缩写不对"
        if flag:
            broken += 1
        _log(f"{journal.abbr:<34}{cr:>10}{pm:>9}   {journal.tier}/{journal.group}{flag}")
        time.sleep(0.2)
    _log("-" * 82)
    if broken:
        _log(f"{broken} 本期刊 30 天内两边都是空的。月刊/双月刊偶尔正常，连续几天为 0 再改。")
    else:
        _log("全部期刊两边都能抓到。")
    return 0


def cmd_doctor(args) -> int:
    """自检模型接口：网关通不通、模型在不在、有没有余额。配完 key 先跑这个。"""
    cfg = Config(args.config)
    clock.configure(cfg.timezone)
    key = os.environ.get(cfg.llm_key_env, "").strip()
    if not key:
        _log(f"✗ 没有设置环境变量 {cfg.llm_key_env}")
        return 2

    _log(f"· 网关 {cfg.llm_base_url}")
    _log(f"· 模型 {cfg.llm_model}")
    http = Http(cfg.llm_user_agent, timeout=30)
    headers = {"Authorization": f"Bearer {key}", "User-Agent": cfg.llm_user_agent}

    try:
        data = http.get_json(cfg.llm_base_url + "/models", headers=headers)
    except Exception as exc:
        _log(f"✗ 连不上网关：{exc}")
        return 1

    ids = [m.get("id") for m in (data or {}).get("data", []) if isinstance(m, dict)]
    _log(f"✓ 网关可达，返回 {len(ids)} 个模型")
    if cfg.llm_model not in ids:
        near = [i for i in ids if "deepseek" in (i or "").lower()]
        _log(f"✗ 列表里没有 {cfg.llm_model}，相近的有：{near}")
        return 1
    _log(f"✓ {cfg.llm_model} 在列表里")

    body = {
        "model": cfg.llm_model,
        "messages": [{"role": "user", "content": "回复两个字：正常"}],
        "max_tokens": 8,
    }
    try:
        resp = http.post_json(cfg.llm_base_url + "/chat/completions", body, headers)
        text = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        _log(f"✓ 生成正常：{str(text).strip()[:40]}")
        return 0
    except Exception as exc:
        _log(f"✗ 生成失败：{exc}")
        return 1


def cmd_apply(args) -> int:
    """把外部生成的中文速读写回库里（没有 API key 时，用任意模型/人手动生成都能走这条路）。"""
    from pathlib import Path

    cfg = Config(args.config)
    clock.configure(cfg.timezone)
    raw = json.loads(Path(args.file).read_text("utf-8"))
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        _log("JSON 格式不对：需要 {\"items\": [...]} 或直接一个数组")
        return 2

    store = Store(cfg.db)
    written = 0
    for item in items:
        key = (item.get("key") or "").strip()
        if not key:
            continue
        store.save_digest(key, item, item.get("model") or "manual")
        written += 1
    store.conn.commit()

    rows = [row_to_dict(r) for r in store.recent(cfg.days, cfg.limit)]
    write_page(rows, cfg, stats=store.stats())
    state_path = cfg.root / "data" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(store.export_state(), ensure_ascii=False), encoding="utf-8")
    _log(f"写入 {written} 篇中文速读，页面已更新（{len(rows)} 篇）")
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="泌尿外科文献雷达")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    sub = parser.add_subparsers(dest="command")

    build = sub.add_parser("build", help="抓取并生成页面（默认）")
    build.add_argument("--days", type=int, help="回溯天数")
    build.add_argument("--limit", type=int, help="页面最多展示篇数")
    build.add_argument("--no-llm", action="store_true", help="不生成中文速读")

    sub.add_parser("check", help="核对期刊池：两边各能抓到多少条")
    sub.add_parser("doctor", help="自检模型接口：网关、模型、余额")

    apply_cmd = sub.add_parser("apply", help="把外部生成的 digest.json 写回库")
    apply_cmd.add_argument("file", help="JSON 文件路径")

    args = parser.parse_args(argv)
    if args.command == "apply":
        return cmd_apply(args)
    if args.command in (None, "build"):
        args.days = getattr(args, "days", None)
        args.limit = getattr(args, "limit", None)
        args.no_llm = getattr(args, "no_llm", False)
        return cmd_build(args)
    if args.command == "check":
        return cmd_check(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
