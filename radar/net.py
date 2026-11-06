"""极简 HTTP 客户端：只依赖标准库，带重试和退避。

学术接口大多免费但会限速（PubMed 无 key 时 3 次/秒，Crossref 会 429），
所以这里统一做指数退避，调用方不用关心。
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request


class Http:
    def __init__(self, user_agent: str, timeout: int = 30, retries: int = 4):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries

    def _request(self, url, params, headers, data, timeout):
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params, doseq=True)
        hdr = {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
        if headers:
            hdr.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def get(self, url, params=None, headers=None, allow_404=False) -> bytes | None:
        last = None
        for attempt in range(self.retries):
            try:
                return self._request(url, params, headers, None, self.timeout)
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and allow_404:
                    return None
                if exc.code in (429, 500, 502, 503, 504):
                    last = exc
                else:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            time.sleep(min(2 ** attempt, 8) * 0.6 + random.random() * 0.4)
        raise RuntimeError(f"GET 失败 {url} :: {last}")

    def get_json(self, url, params=None, allow_404=False):
        raw = self.get(url, params=params, allow_404=allow_404)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8", "replace"))

    def post_json(self, url, payload, headers=None, timeout=180):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdr = {"Content-Type": "application/json"}
        if headers:
            hdr.update(headers)
        last = None
        for attempt in range(self.retries):
            try:
                raw = self._request(url, None, hdr, data, timeout)
                return json.loads(raw.decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                # 4xx 大多是参数问题，重试没意义，直接把内容抛出去方便排查
                if 400 <= exc.code < 500 and exc.code != 429:
                    body = ""
                    try:
                        body = exc.read().decode("utf-8", "replace")[:500]
                    except Exception:
                        pass
                    raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
                last = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            time.sleep(min(2 ** attempt, 10) * 0.6 + random.random() * 0.4)
        raise RuntimeError(f"POST 失败 {url} :: {last}")
