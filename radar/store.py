"""SQLite 存储：去重、缓存中文速读、记录首次见到的时间。

用 SQLite 而不是每次全量重算，是为了两件事：
  1. 同一篇论文第二天不会再推一遍（按 DOI 主键冲突直接更新）；
  2. 中文速读很贵，生成过就存下来，重跑不会重复花钱。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from .clock import today as _today

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    key            TEXT PRIMARY KEY,
    doi            TEXT DEFAULT '',
    pmid           TEXT DEFAULT '',
    title          TEXT DEFAULT '',
    abstract       TEXT DEFAULT '',
    journal        TEXT DEFAULT '',
    abbr           TEXT DEFAULT '',
    grp            TEXT DEFAULT '',
    tier           INTEGER DEFAULT 0,
    published      TEXT DEFAULT '',
    authors        TEXT DEFAULT '[]',
    pubtypes       TEXT DEFAULT '[]',
    cited_by       INTEGER DEFAULT 0,
    url            TEXT DEFAULT '',
    sources        TEXT DEFAULT '[]',
    score          REAL DEFAULT 0,
    reasons        TEXT DEFAULT '[]',
    zh_title       TEXT,
    zh_design      TEXT,
    zh_finding     TEXT,
    zh_practice    TEXT,
    digest_model   TEXT,
    digest_attempts INTEGER DEFAULT 0,
    pushed_at      TEXT,
    first_seen     TEXT,
    last_seen      TEXT
);
CREATE INDEX IF NOT EXISTS idx_published ON papers(published);
CREATE INDEX IF NOT EXISTS idx_score ON papers(score);
"""

UPSERT = """
INSERT INTO papers (
    key, doi, pmid, title, abstract, journal, abbr, grp, tier, published,
    authors, pubtypes, cited_by, url, sources, score, reasons, first_seen, last_seen
) VALUES (
    :key, :doi, :pmid, :title, :abstract, :journal, :abbr, :grp, :tier, :published,
    :authors, :pubtypes, :cited_by, :url, :sources, :score, :reasons, :first_seen, :last_seen
)
ON CONFLICT(key) DO UPDATE SET
    doi        = excluded.doi,
    pmid       = excluded.pmid,
    title      = excluded.title,
    abstract   = CASE WHEN length(excluded.abstract) >= length(papers.abstract)
                      THEN excluded.abstract ELSE papers.abstract END,
    journal    = excluded.journal,
    abbr       = excluded.abbr,
    grp        = excluded.grp,
    tier       = excluded.tier,
    published  = CASE WHEN excluded.published != '' THEN excluded.published ELSE papers.published END,
    authors    = excluded.authors,
    pubtypes   = excluded.pubtypes,
    cited_by   = MAX(papers.cited_by, excluded.cited_by),
    url        = CASE WHEN excluded.url != '' THEN excluded.url ELSE papers.url END,
    sources    = excluded.sources,
    score      = excluded.score,
    reasons    = excluded.reasons,
    last_seen  = excluded.last_seen
"""


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # 老库没有 pushed_at 这一列（推送是后来加的），就地补上
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(papers)")}
        if "pushed_at" not in columns:
            self.conn.execute("ALTER TABLE papers ADD COLUMN pushed_at TEXT")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def upsert(self, rows: list[dict]) -> None:
        if not rows:
            return
        self.conn.executemany(UPSERT, rows)
        self.conn.commit()

    def recent(self, days: int, limit: int) -> list[sqlite3.Row]:
        cutoff = (_today() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            """
            SELECT * FROM papers
            WHERE title != ''
              AND ((published >= ?) OR (published = '' AND last_seen >= ?))
            ORDER BY score DESC, published DESC
            LIMIT ?
            """,
            (cutoff, cutoff, limit),
        )
        return list(cur.fetchall())

    def pending_digest(self, cutoff: str, limit: int) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            """
            SELECT * FROM papers
            WHERE zh_title IS NULL
              AND digest_attempts < 3
              AND title != ''
              AND published >= ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        return list(cur.fetchall())

    def save_digest(self, key: str, item: dict, model: str) -> None:
        self.conn.execute(
            """
            UPDATE papers SET zh_title = ?, zh_design = ?, zh_finding = ?, zh_practice = ?,
                              digest_model = ?, digest_attempts = digest_attempts + 1
            WHERE key = ?
            """,
            (
                item.get("zh_title", ""),
                item.get("zh_design", ""),
                item.get("zh_finding", ""),
                item.get("zh_practice", ""),
                model,
                key,
            ),
        )

    def bump_attempts(self, keys: list[str]) -> None:
        if not keys:
            return
        self.conn.executemany(
            "UPDATE papers SET digest_attempts = digest_attempts + 1 WHERE key = ?",
            [(k,) for k in keys],
        )
        self.conn.commit()

    def unpushed(self, days: int, limit: int) -> list[sqlite3.Row]:
        """最近 N 天里还没推过的高分文章。"""
        cutoff = (_today() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            """
            SELECT * FROM papers
            WHERE title != '' AND pushed_at IS NULL AND published >= ?
            ORDER BY score DESC, published DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        return list(cur.fetchall())

    def mark_pushed(self, keys: list[str], when: str | None = None) -> None:
        if not keys:
            return
        when = when or _today().isoformat()
        self.conn.executemany(
            "UPDATE papers SET pushed_at = ? WHERE key = ?", [(when, k) for k in keys]
        )
        self.conn.commit()

    def prune(self, days: int) -> int:
        cutoff = (_today() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "DELETE FROM papers WHERE published != '' AND published < ?", (cutoff,)
        )
        # 只剩 key 的占位行（去重记忆）也按时效清掉，否则会无限增长
        self.conn.execute("DELETE FROM papers WHERE title = '' AND first_seen < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    # ---------------------------------------------------------------- 状态导出
    # 公开仓库里不能放摘要原文，所以只导出两样东西：
    #   1. seen     —— 哪些 DOI 已经见过（去重靠它，不然每天重复推）
    #   2. digests  —— 已经花钱生成过的中文速读（不导出就会重复调用模型）
    def export_state(self) -> dict:
        seen = {
            row["key"]: row["first_seen"]
            for row in self.conn.execute("SELECT key, first_seen FROM papers")
        }
        pushed = {
            row["key"]: row["pushed_at"]
            for row in self.conn.execute(
                "SELECT key, pushed_at FROM papers WHERE pushed_at IS NOT NULL"
            )
        }
        digests = {
            row["key"]: {
                "zh_title": row["zh_title"],
                "zh_design": row["zh_design"],
                "zh_finding": row["zh_finding"],
                "zh_practice": row["zh_practice"],
                "model": row["digest_model"],
            }
            for row in self.conn.execute(
                "SELECT key, zh_title, zh_design, zh_finding, zh_practice, digest_model "
                "FROM papers WHERE zh_title IS NOT NULL"
            )
        }
        return {"version": 1, "seen": seen, "digests": digests, "pushed": pushed}

    def import_state(self, data: dict) -> None:
        today = _today().isoformat()
        stubs = [
            (key, first_seen or today, first_seen or today)
            for key, first_seen in (data.get("seen") or {}).items()
            if key
        ]
        self.conn.executemany(
            "INSERT INTO papers (key, first_seen, last_seen, published) VALUES (?,?,?,'') "
            "ON CONFLICT(key) DO NOTHING",
            stubs,
        )
        self.conn.executemany(
            "UPDATE papers SET zh_title = ?, zh_design = ?, zh_finding = ?, zh_practice = ?, "
            "digest_model = ? WHERE key = ?",
            [
                (
                    item.get("zh_title"),
                    item.get("zh_design"),
                    item.get("zh_finding"),
                    item.get("zh_practice"),
                    item.get("model"),
                    key,
                )
                for key, item in (data.get("digests") or {}).items()
                if item.get("zh_title")
            ],
        )
        self.conn.executemany(
            "UPDATE papers SET pushed_at = ? WHERE key = ?",
            [(ts, key) for key, ts in (data.get("pushed") or {}).items() if key],
        )
        self.conn.commit()

    def stats(self) -> dict:
        today = _today().isoformat()
        total = self.conn.execute("SELECT COUNT(*) FROM papers WHERE title != ''").fetchone()[0]
        with_zh = self.conn.execute(
            "SELECT COUNT(*) FROM papers WHERE zh_title IS NOT NULL AND title != ''"
        ).fetchone()[0]
        new_today = self.conn.execute(
            "SELECT COUNT(*) FROM papers WHERE first_seen = ? AND title != ''", (today,)
        ).fetchone()[0]
        return {"total": total, "with_zh": with_zh, "new_today": new_today}


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("authors", "pubtypes", "reasons", "sources"):
        try:
            d[field] = json.loads(d.get(field) or "[]")
        except (TypeError, json.JSONDecodeError):
            d[field] = []
    return d
