"""读取 config.toml，并给出一份带默认值的配置对象。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Journal:
    name: str
    abbr: str
    issn: str
    tier: int
    group: str
    issn_alt: str = ""


class Config:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        raw = tomllib.loads(self.path.read_text("utf-8"))
        self.raw = raw

        g = raw.get("general", {})
        self.days = int(g.get("days", 10))
        self.limit = int(g.get("limit", 90))
        self.crossref_rows = int(g.get("crossref_rows", 40))
        self.pubmed_retmax = int(g.get("pubmed_retmax", 600))
        self.prune_days = int(g.get("prune_days", 365))
        self.mailto = str(g.get("mailto", "your@email.com"))
        self.user_agent = str(g.get("user_agent", "uro-radar/0.1"))
        self.timezone = str(g.get("timezone", "Asia/Shanghai"))

        s = raw.get("scoring", {})
        self.tier_weight = float(s.get("tier_weight", 12))
        self.interest_weight = float(s.get("interest_weight", 2))
        self.interest_cap = float(s.get("interest_cap", 10))
        self.fresh_days = int(s.get("fresh_days", 3))
        self.fresh_bonus = float(s.get("fresh_bonus", 4))

        llm = raw.get("llm", {})
        self.llm_enabled = bool(llm.get("enabled", True))
        self.llm_base_url = str(llm.get("base_url", "https://api.deepseek.com/v1")).rstrip("/")
        self.llm_model = str(llm.get("model", "deepseek-chat"))
        self.llm_key_env = str(llm.get("api_key_env", "LLM_API_KEY"))
        self.llm_batch = int(llm.get("batch", 6))
        self.llm_max_per_run = int(llm.get("max_per_run", 30))
        # 有些网关（Cloudflare 前面那种）会把默认的 python-urllib UA 直接挡掉，
        # 返回 1010/403。这里给模型接口单独一个正常的浏览器 UA。
        self.llm_user_agent = str(
            llm.get(
                "user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            )
        )
        # opencode-go 的网关要求带一个会话 ID 头，否则 400 MissingSessionID。
        # 普通的 OpenAI 兼容接口不需要，留空即可。
        self.llm_session_header = str(llm.get("session_header", "x-opencode-session"))
        # 推理模型的思考过程也算进 max_tokens，给小了会返回空内容
        self.llm_max_tokens = int(llm.get("max_tokens", 4000))

        r = raw.get("render", {})
        self.title = str(r.get("title", "泌尿外科文献雷达"))
        self.subtitle = str(r.get("subtitle", ""))
        self.top_n = int(r.get("top_n", 5))

        self.journals = [
            Journal(
                name=str(j["name"]),
                abbr=str(j["abbr"]),
                issn=str(j.get("issn", "")).strip(),
                tier=int(j.get("tier", 3)),
                group=str(j.get("group", "综合")),
                issn_alt=str(j.get("issn_alt", "")).strip(),
            )
            for j in raw.get("journals", [])
        ]
        self.by_abbr = {j.abbr: j for j in self.journals}
        self.by_name = {j.name.lower(): j for j in self.journals}
        self.interests = [str(k).lower() for k in raw.get("interests", [])]
        self.output = self.root / "public" / "index.html"
        self.db = self.root / "data" / "radar.db"
