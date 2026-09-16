"""Marginalia Search connector.

Marginalia (https://marginalia-search.com) is an independent engine that
deliberately indexes the small, non-commercial, text-first web. Its domain
distribution barely overlaps a mainstream engine's, which makes it the right
second connector for testing whether multi-engine retrieval actually widens
the source pool (docs/collection/connector-strategy.md).

Public JSON API, no signup: a shared key (`public`) is built in; override with
MARGINALIA_API_KEY. The shared key is rate-limited to roughly one request per
second, so calls are throttled here.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ..env import load_dotenv
from ..models import Result

_ENDPOINT = "https://api.marginalia.nu"


class MarginaliaConnector:
    name = "marginalia"

    def __init__(self, *, min_interval: float = 1.2, max_retries: int = 3,
                 backoff: float = 2.0):
        load_dotenv()
        self.key = os.environ.get("MARGINALIA_API_KEY", "public")
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff = backoff
        self._last_call = 0.0

    def search(self, query: str, *, limit: int = 10) -> list[Result]:
        self._throttle()
        path = f"/{urllib.parse.quote(self.key)}/search/{urllib.parse.quote(query)}"
        params = urllib.parse.urlencode({"count": max(1, min(limit, 20))})
        req = urllib.request.Request(
            f"{_ENDPOINT}{path}?{params}",
            headers={"Accept": "application/json",
                     "User-Agent": "research-os/0.1 (+stage-3 connector test)"},
        )
        payload = self._get_with_retry(req)
        items = payload.get("results") or []
        out: list[Result] = []
        for i, item in enumerate(items[:limit], start=1):
            url = (item.get("url") or "").strip()
            if not url:
                continue
            out.append(
                Result(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    snippet=(item.get("description") or item.get("snippet") or "").strip(),
                    connector=self.name,
                    raw_rank=i,
                )
            )
        return out

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get_with_retry(self, req: urllib.request.Request) -> dict:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                if attempt < self.max_retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise
        raise RuntimeError(f"marginalia search failed after {self.max_retries} attempts: {last}")
