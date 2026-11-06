"""把当天的 Top N 推到手机上。

四个通道，有哪个环境变量就用哪个，一个都没有就静默跳过：
  企业微信群机器人   WECOM_WEBHOOK     免费、不限量、群卡片，最推荐
  Server酱          SERVERCHAN_KEY    直接进个人微信，免费额度每天几条
  Bark              BARK_URL          iOS 锁屏推送，需要装 Bark App
  邮件              SMTP_* / MAIL_TO  最稳，但容易被当垃圾邮件

为什么不写成「选一个渠道」：多配几个不冲突，坏了一个不影响别的。
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.parse
from email.header import Header
from email.mime.text import MIMEText

from .config import Config
from .net import Http


def _link(item: dict) -> str:
    if item.get("doi"):
        return "https://doi.org/" + item["doi"]
    if item.get("pmid"):
        return "https://pubmed.ncbi.nlm.nih.gov/" + str(item["pmid"]) + "/"
    return item.get("url") or ""


def _short(text: str, limit: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(items: list[dict], cfg: Config) -> tuple[str, str]:
    """返回 (标题, markdown 正文)。企业微信 markdown 上限 4096 字节，这里控制在 3000 以内。"""
    from . import clock

    today = clock.today()
    title = f"泌尿文献日报 · {today.month}月{today.day}日 · {len(items)} 篇"

    blocks = [f"**{cfg.title}**  {today.isoformat()}", ""]
    for i, item in enumerate(items, start=1):
        head = _short(item.get("zh_title") or item.get("title") or "", 60)
        meta = f"{item.get('abbr', '')} · {float(item.get('score') or 0):.0f} 分"
        blocks.append(f"**{i}. {head}**")
        blocks.append(f'<font color="comment">{meta}</font>')
        finding = _short(item.get("zh_finding") or "", 90)
        if finding:
            blocks.append(f"> {finding}")
        link = _link(item)
        if link:
            blocks.append(f"[原文]({link})")
        blocks.append("")

    more = f"[查看全部 {cfg.notify_total_hint} 篇]({cfg.notify_page_url})" if cfg.notify_page_url else ""
    if more:
        blocks.append(more)

    body = "\n".join(blocks)
    if len(body.encode("utf-8")) > 3800:
        body = body.encode("utf-8")[:3700].decode("utf-8", "ignore") + "\n…"
    return title, body


def _wecom(http: Http, body: str) -> str:
    hook = os.environ.get("WECOM_WEBHOOK", "").strip()
    if not hook:
        return ""
    payload = {"msgtype": "markdown", "markdown": {"content": body}}
    resp = http.post_json(hook, payload)
    if resp.get("errcode") not in (0, None):
        raise RuntimeError(f"企业微信 errcode={resp.get('errcode')} {resp.get('errmsg')}")
    return "企业微信 ✓"


def _serverchan(http: Http, title: str, body: str) -> str:
    key = (os.environ.get("SERVERCHAN_KEY") or os.environ.get("SCKEY") or "").strip()
    if not key:
        return ""
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = http.post_form(url, {"title": title, "desp": body}) or {}
    if data.get("code") not in (0, None):
        raise RuntimeError(f"Server酱 code={data.get('code')} {data.get('message')}")
    return "Server酱 ✓"


def _bark(http: Http, title: str, body: str, cfg: Config) -> str:
    base = os.environ.get("BARK_URL", "").strip().rstrip("/")
    if not base:
        return ""
    plain = "\n".join(line for line in body.splitlines() if not line.startswith("<") and not line.startswith("["))
    url = base + "/" + urllib.parse.quote(title) + "/" + urllib.parse.quote(_short(plain, 800))
    params = {"group": "文献雷达", "url": cfg.notify_page_url} if cfg.notify_page_url else {"group": "文献雷达"}
    http.get_json(url, params)
    return "Bark ✓"


def _email(title: str, body: str) -> str:
    host = os.environ.get("SMTP_HOST", "").strip()
    to_addr = os.environ.get("MAIL_TO", "").strip()
    if not host or not to_addr:
        return ""
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASS", "").strip()
    sender = os.environ.get("MAIL_FROM", "").strip() or user

    html = "".join(
        f"<p>{line}</p>" if line.strip() else "<br>"
        for line in body.replace("<font color=\"comment\">", "<i>").replace("</font>", "</i>").replace("**", "").splitlines()
    )
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = sender
    msg["To"] = to_addr

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            if user:
                server.login(user, password)
            server.sendmail(sender, [a.strip() for a in to_addr.split(",")], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls(context=context)
            if user:
                server.login(user, password)
            server.sendmail(sender, [a.strip() for a in to_addr.split(",")], msg.as_string())
    return "邮件 ✓"


def send(items: list[dict], cfg: Config, http: Http, log=print) -> bool:
    """返回 True 表示至少有一个通道成功。"""
    title, body = render(items, cfg)
    return _dispatch(title, body, cfg, http, log)


def send_test(cfg: Config, http: Http, log=print) -> bool:
    """不依赖数据库的连通性测试：收到就说明通道配好了。"""
    return _dispatch(
        "文献雷达 · 推送测试",
        "收到这条，说明推送通道配好了。明早 7:30 会推当天最高分的几篇文献。",
        cfg,
        http,
        log,
    )


def _dispatch(title: str, body: str, cfg: Config, http: Http, log=print) -> bool:
    results, errors = [], []

    for name, fn in (
        ("企业微信", lambda: _wecom(http, body)),
        ("Server酱", lambda: _serverchan(http, title, body)),
        ("Bark", lambda: _bark(http, title, body, cfg)),
        ("邮件", lambda: _email(title, body)),
    ):
        try:
            message = fn()
        except Exception as exc:
            errors.append(f"{name} ✗ {str(exc)[:120]}")
            continue
        if message:
            results.append(message)

    for line in results + errors:
        log(f"  · {line}")

    if not results and not errors:
        log("  · 没有配置任何推送通道（WECOM_WEBHOOK / SERVERCHAN_KEY / BARK_URL / SMTP_HOST）")
        return False
    return bool(results)
