"""URL canonicalization, domain extraction, content hashing, and dedupe.

Algorithmic loops manufacture false variety: many links that resolve to the
same few ideas. Collapsing them is a de-biasing step, not housekeeping
(docs/collection/connector-strategy.md, docs/ranking/reranking-logic.md).
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Result

# Tracking / session params that never change document identity.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src",
    "spm", "cmpid", "cid", "_hsenc", "_hsmi", "yclid", "wt_mc", "s_cid",
}

_WS = re.compile(r"\s+")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("", "http", "https") else parts.scheme

    host = parts.hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"

    path = re.sub(r"/+", "/", parts.path) or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    kept.sort()
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def content_hash(title: str, snippet: str) -> str:
    norm = _WS.sub(" ", f"{title} {snippet}".lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def snippet_hash(snippet: str) -> str:
    """Hash of the snippet body alone. Catches syndicated / mirrored content
    that carries a different title. Empty for snippets too short to be a
    reliable fingerprint."""
    norm = _WS.sub(" ", snippet.lower()).strip()
    if len(norm) < 40:
        return ""
    return hashlib.sha1(norm[:240].encode("utf-8")).hexdigest()


def enrich(results: list[Result]) -> list[Result]:
    for r in results:
        r.canonical_url = canonical_url(r.url)
        r.domain = domain_of(r.canonical_url)
        r.content_hash = content_hash(r.title, r.snippet)
        r.snippet_hash = snippet_hash(r.snippet)
    return results


def dedupe(results: list[Result]) -> list[Result]:
    """Keep the best-ranked instance of each canonical URL and each content hash."""
    seen_url: set[str] = set()
    seen_hash: set[str] = set()
    out: list[Result] = []
    for r in sorted(results, key=lambda x: x.raw_rank):
        if r.canonical_url in seen_url or r.content_hash in seen_hash:
            continue
        seen_url.add(r.canonical_url)
        seen_hash.add(r.content_hash)
        out.append(r)
    return out
