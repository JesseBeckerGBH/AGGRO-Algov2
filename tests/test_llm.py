import json

import pytest

from research_os import llm
from research_os.briefing import synthesize
from research_os.canonical import enrich
from research_os.classify import classify
from research_os.models import Mission, Result


def _capture():
    """A fake transport that records the last call and returns a canned reply
    in the shape of whichever provider was addressed."""
    seen = {}

    def transport(url, headers, body):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(body.decode("utf-8"))
        text = "## Bottom line\nStub briefing body.\n"
        if "generativelanguage.googleapis.com" in url:
            return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        if "api.anthropic.com" in url:
            return {"content": [{"type": "text", "text": text}]}
        if "api.openai.com" in url:
            return {"choices": [{"message": {"content": text}}]}
        raise AssertionError(f"unexpected url {url}")

    return transport, seen


@pytest.mark.parametrize(
    "provider, key_env, url_needle",
    [
        ("gemini", "GEMINI_API_KEY", "generativelanguage.googleapis.com"),
        ("anthropic", "ANTHROPIC_API_KEY", "api.anthropic.com"),
        ("openai", "OPENAI_API_KEY", "api.openai.com"),
    ],
)
def test_each_provider_builds_request_and_parses_reply(provider, key_env, url_needle, monkeypatch):
    monkeypatch.setenv(key_env, "test-key")
    transport, seen = _capture()
    out = llm.synthesize("SYS", "USER", provider=provider, _transport=transport)
    assert out == "## Bottom line\nStub briefing body."
    assert url_needle in seen["url"]


def test_missing_key_raises_with_env_name(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    with pytest.raises(llm.LLMKeyMissing) as ei:
        llm.synthesize("s", "u", provider="gemini", _transport=lambda *a: {})
    assert "GEMINI_API_KEY" in str(ei.value)


def test_available_provider_picks_first_key_present(monkeypatch):
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    assert llm.available_provider() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.available_provider() == "anthropic"


def test_briefing_synthesize_wraps_llm_output_and_appends_accurate_footer(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    transport, seen = _capture()

    mission = Mission(
        id="m", objective="predictive features for tennis outcome",
        success_condition="three features", novelty_requirement="high",
        vocabulary_seed=["serve plus one"],
    )
    rs = [
        Result(title="Preprint", url="https://arxiv.org/abs/1", snippet="serve plus one", raw_rank=1),
        Result(title="Forum", url="https://reddit.com/r/tennis/a", snippet="chatter", raw_rank=2),
    ]
    enrich(rs)
    classify(rs)

    brief = synthesize(mission, rs, top=2, retrieved=9, _transport=transport)
    assert "Stub briefing body." in brief.body
    assert "retrieved 9 · surfaced 2" in brief.body
    assert brief.surfaced == 2
    # the model was handed the reranked results and the governing structure
    sent = seen["body"]
    payload = json.dumps(sent)
    assert "arxiv.org" in payload and "reddit.com" in payload
    assert "What contradicts it" in payload
