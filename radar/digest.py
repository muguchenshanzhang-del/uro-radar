"""中文速读：把英文摘要压成四行（标题 / 设计 / 结果 / 意义）。

走任意 OpenAI 兼容接口（DeepSeek、Kimi、通义、OpenAI、本地 vLLM 都行）。
没有配 key 就直接跳过 —— 页面照常生成，只是不显示中文块。
"""

from __future__ import annotations

import json
import os

from .config import Config
from .net import Http
from .store import Store

SYSTEM = (
    "你是泌尿外科主治医师，每天给科室同事写一份文献速递。"
    "你只输出 JSON，不写任何解释性文字。"
    "所有内容用简体中文，术语用国内临床习惯的说法（如 TURP、PCNL、BCG 保留英文缩写）。"
    "绝不编造摘要里没有的数字、样本量或结论；摘要没写的就写“摘要未给出”。"
)

PROMPT = """下面是一批泌尿外科新文献。对每一篇输出四行中文速读。

要求：
- zh_title：中文标题，不超过 28 字，不要直译得生硬
- zh_design：研究设计与样本量，不超过 24 字（例：多中心 RCT，n=412）
- zh_finding：主要结果，必须带关键数字，不超过 40 字
- zh_practice：对临床或科研的意义，不超过 28 字，要具体，不要写“值得进一步研究”

严格输出这个 JSON，不要加 markdown 代码块：
{{"items": [{{"id": 1, "zh_title": "...", "zh_design": "...", "zh_finding": "...", "zh_practice": "..."}}]}}

文献列表：
{payload}
"""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").replace("\r", " ").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _payload(rows: list) -> str:
    items = []
    for i, row in enumerate(rows, start=1):
        items.append(
            {
                "id": i,
                "journal": row["journal"],
                "types": row["pubtypes"],
                "title": row["title"],
                "abstract": _truncate(row["abstract"], 1500) or "（无摘要）",
            }
        )
    return json.dumps(items, ensure_ascii=False, indent=1)


def parse_response(text: str, expected: int) -> dict[int, dict]:
    """模型偶尔会套 ```json 或者前后加一句话，这里做一次宽松解析。"""
    if not text:
        return {}
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    out: dict[int, dict] = {}
    for item in data.get("items") or []:
        try:
            idx = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if not 1 <= idx <= expected:
            continue
        out[idx] = {
            "zh_title": _truncate(item.get("zh_title", ""), 80),
            "zh_design": _truncate(item.get("zh_design", ""), 80),
            "zh_finding": _truncate(item.get("zh_finding", ""), 160),
            "zh_practice": _truncate(item.get("zh_practice", ""), 80),
        }
    return out


def _call(http: Http, cfg: Config, api_key: str, prompt: str) -> str:
    url = f"{cfg.llm_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": cfg.llm_user_agent}
    base = {
        "model": cfg.llm_model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2400,
    }
    try:
        resp = http.post_json(url, {**base, "response_format": {"type": "json_object"}}, headers)
    except RuntimeError:
        # 有些兼容接口不认 response_format，去掉再试一次
        resp = http.post_json(url, base, headers)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError(f"返回里没有 choices：{json.dumps(resp, ensure_ascii=False)[:300]}")
    return choices[0].get("message", {}).get("content", "") or ""


def run(store: Store, cfg: Config, http: Http, rows: list, log=print) -> int:
    if not rows:
        return 0
    if not cfg.llm_enabled:
        log("  · LLM 已关闭，跳过中文速读")
        return 0

    api_key = os.environ.get(cfg.llm_key_env, "").strip()
    if not api_key:
        log(f"  · 没有设置环境变量 {cfg.llm_key_env}，跳过中文速读（页面用英文摘要）")
        return 0

    rows = rows[: cfg.llm_max_per_run]
    saved = 0
    for i in range(0, len(rows), cfg.llm_batch):
        chunk = rows[i : i + cfg.llm_batch]
        prompt = PROMPT.format(payload=_payload(chunk))
        try:
            text = _call(http, cfg, api_key, prompt)
            parsed = parse_response(text, len(chunk))
        except Exception as exc:  # 网络/额度/解析都可能出问题，别让整条流水线挂掉
            message = str(exc)
            log(f"  ! 第 {i // cfg.llm_batch + 1} 批失败：{message}")
            # key 错了、没钱了、被限流 —— 后面每一批都会一样失败，
            # 而且不该消耗重试次数（否则充值之前这几篇就被永久跳过）
            if any(code in message for code in ("HTTP 401", "HTTP 402", "HTTP 403", "HTTP 429")):
                log("  · 这是密钥/余额/限流问题，本次到此为止，明天自动再试")
                break
            store.bump_attempts([r["key"] for r in chunk])
            continue
        for idx, item in parsed.items():
            store.save_digest(chunk[idx - 1]["key"], item, cfg.llm_model)
            saved += 1
        missing = [r["key"] for idx, r in enumerate(chunk, start=1) if idx not in parsed]
        store.bump_attempts(missing)
        log(f"  · 第 {i // cfg.llm_batch + 1} 批：{len(parsed)}/{len(chunk)} 篇")
    store.conn.commit()
    return saved
