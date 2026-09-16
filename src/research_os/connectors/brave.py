"""Brave Search API connector.

Needs BRAVE_SEARCH_API_KEY in the environment (or a .env file — see
.env.example). Free tier: https://brave.com/search/api/ . stdlib only.
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

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class BraveConnector:
    name = "brave"

    def __init__(self, *, max_retries: int = 3, backoff: float = 1.5):
        load_dotenv()
        self.api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        self.max_retries = max_retries
        self.backoff = backoff

    def search(self, query: str, *, limit: int = 10) -> list[Result]:
        if not self.api_key:
            raise RuntimeError(
                "BRAVE_SEARCH_API_KEY not set. Add it to .env "
                "(cp .env.example .env) or use the fixture connector."
            )
        params = urllib.parse.urlencode(
            {"q": query, "count": max(1, min(limit, 20)), "result_filter": "web"}
        )
        req = urllib.request.Request(
            f"{_ENDPOINT}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "research-os/0.1 (+vertical-slice)",
            },
        )
        payload = self._get_with_retry(req)
        web = (payload.get("web") or {}).get("results") or []
        out: list[Result] = []
        for i, item in enumerate(web[:limit], start=1):
            out.append(
                Result(
                    title=item.get("title", "").strip(),
                    url=item.get("url", "").strip(),
                    snippet=(item.get("description") or "").strip(),
                    connector=self.name,
                    raw_rank=i,
                )
            )
        return out

    def _get_with_retry(self, req: urllib.request.Request) -> dict:
        last: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                if attempt < self.max_retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise
        raise RuntimeError(f"brave search failed after {self.max_retries} attempts: {last}")
