"""Web search uses a fixed provider endpoint; result pages are never fetched."""
from __future__ import annotations

import ipaddress
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


def private_config(base=None):
    configured = os.getenv("KB_EXPLORE_CONFIG")
    paths = [Path(configured).expanduser()] if configured else [
        Path.home() / ".config/wikitide/explore-config.json",
        (base or Path(__file__).resolve().parent) / "work/explore-config.json",
    ]
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass
    return {}


def public_web_url(value):
    if not isinstance(value, str) or len(value) > 2048 or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or not host:
            return False
        if parsed.port not in {None, 80, 443}:
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host and not host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
    except ValueError:
        return False


class WebSearch:
    def __init__(self):
        key = os.getenv("TAVILY_API_KEY", private_config().get("tavily_api_key", ""))
        self.key = key.strip() if isinstance(key, str) else ""
        self._cache = {}
        self._lock = threading.Lock()

    @property
    def ready(self):
        return bool(self.key)

    def search(self, query):
        if not self.ready:
            raise HTTPException(503, "联网搜索尚未配置，请先关闭联网搜索，或由管理员配置 Tavily 搜索密钥。")
        query = query.strip()[:1000]
        with self._lock:
            cached = self._cache.get(query)
            if cached and time.monotonic() - cached[0] < 120:
                return [dict(source) for source in cached[1]]
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False) as client:
                response = client.post("https://api.tavily.com/search", headers={
                    "Authorization": "Bearer " + self.key,
                }, json={"query": query, "search_depth": "basic", "max_results": 5,
                         "include_answer": False, "include_raw_content": False})
            if response.status_code != 200:
                raise HTTPException(503, "联网搜索暂时不可用，请稍后重试，或关闭联网搜索后继续提问。")
            results = response.json()["results"]
            if not isinstance(results, list):
                raise ValueError("Invalid search results")
            sources, seen = [], set()
            for result in results[:10]:
                if not isinstance(result, dict):
                    continue
                url, title, content = result.get("url"), result.get("title"), result.get("content")
                if not public_web_url(url) or url in seen or not isinstance(title, str) or not isinstance(content, str) or not content.strip():
                    continue
                seen.add(url)
                sources.append({"kind": "web", "title": title[:240], "url": url,
                                "section": urlsplit(url).hostname, "excerpt": content[:1800],
                                "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                if len(sources) == 5:
                    break
            with self._lock:
                if len(self._cache) >= 128:
                    self._cache.clear()
                self._cache[query] = (time.monotonic(), sources)
            return [dict(source) for source in sources]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(503, "联网搜索连接失败，请重试或关闭联网搜索；问题已保留。") from exc
