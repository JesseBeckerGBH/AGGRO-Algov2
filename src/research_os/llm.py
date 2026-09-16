"""Provider-agnostic text synthesis.

One `synthesize(system, user)` call. Provider chosen by the `LLM_PROVIDER`
env var (default: gemini); model by `LLM_MODEL` (per-provider default
otherwise). Keys read from the environment / .env:

    gemini     -> GEMINI_API_KEY  (or GOOGLE_API_KEY)
    anthropic  -> ANTHROPIC_API_KEY
    openai     -> OPENAI_API_KEY

stdlib only. The HTTP call is injectable (`_transport`) so tests never touch
the network.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

from .env import load_dotenv

Transport = Callable[[str, dict[str, str], bytes], dict]

_DEFAULT_PROVIDER = "gemini"
_DEFAULT_MODEL = {
    "gemini": "gemini-3.6-flash",
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
}
_KEY_ENV = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}


class LLMKeyMissing(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def _key_for(provider: str) -> str:
    for name in _KEY_ENV.get(provider, ()):
        val = os.environ.get(name)
        if val:
            return val
    envs = " or ".join(_KEY_ENV.get(provider, ("<none>",)))
    raise LLMKeyMissing(f"{provider}: set {envs} (in .env or the environment)")


def available_provider() -> str | None:
    """First provider that has a key present. Used by `--brief auto`."""
    load_dotenv()
    for provider in ("gemini", "anthropic", "openai"):
        if any(os.environ.get(n) for n in _KEY_ENV[provider]):
            return provider
    return None


def _http_post(url: str, headers: dict[str, str], body: bytes,
               *, retries: int = 3, backoff: float = 1.5) -> dict:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = LLMError(f"HTTP {e.code}: {detail}")
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(backoff ** attempt)
                continue
            raise last
        except (urllib.error.URLError, TimeoutError) as e:
            last = LLMError(str(e))
            if attempt < retries:
                time.sleep(backoff ** attempt)
                continue
            raise last
    raise last or LLMError("request failed")


# -- per-provider request builders / response parsers --------------------

def _call_gemini(model, key, system, user, max_tokens, temperature, transport) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }).encode("utf-8")
    data = transport(url, {"Content-Type": "application/json"}, body)
    try:
        cand = data["candidates"][0]
        text = "".join(
            p.get("text", "") for p in cand.get("content", {}).get("parts", [])
        ).strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"gemini: unexpected response shape ({e}): {str(data)[:300]}")
    if not text:
        reason = data.get("candidates", [{}])[0].get("finishReason", "?")
        raise LLMError(
            f"gemini returned no text (finishReason={reason}); "
            f"raise max_tokens or check the prompt"
        )
    return text


def _call_anthropic(model, key, system, user, max_tokens, temperature, transport) -> str:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    }
    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    data = transport(url, headers, body)
    try:
        return "".join(
            blk.get("text", "") for blk in data["content"] if blk.get("type") == "text"
        ).strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"anthropic: unexpected response shape ({e}): {str(data)[:300]}")


def _call_openai(model, key, system, user, max_tokens, temperature, transport) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = json.dumps({
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    data = transport(url, headers, body)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise LLMError(f"openai: unexpected response shape ({e}): {str(data)[:300]}")


_DISPATCH = {
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
}


def synthesize(
    system: str,
    user: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = 8192,  # generous: reasoning-tier models spend budget before the visible answer
    temperature: float = 0.3,
    _transport: Transport | None = None,
) -> str:
    load_dotenv()
    provider = (provider or os.environ.get("LLM_PROVIDER") or _DEFAULT_PROVIDER).lower()
    if provider not in _DISPATCH:
        raise LLMError(f"unknown LLM_PROVIDER {provider!r} (have: {', '.join(_DISPATCH)})")
    model = model or os.environ.get("LLM_MODEL") or _DEFAULT_MODEL[provider]
    key = _key_for(provider)
    transport = _transport or _http_post
    return _DISPATCH[provider](
        model, key, system, user, max_tokens, temperature, transport
    )
