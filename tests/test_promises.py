"""Product-promise and golden-file tests.

These guard what we tell customers, not individual functions:
  1. The offline pipeline's brief does not change silently (golden file).
  2. Every item in a brief can be traced to a source link.
  3. Near-duplicate sources do not inflate the evidence.

Regenerate the golden file deliberately (and review the diff):
    PowerShell:  $env:UPDATE_GOLDEN="1"; pytest tests/test_promises.py; Remove-Item Env:UPDATE_GOLDEN
"""
import os
import re
from pathlib import Path

import pytest

from research_os.canonical import canonical_url
from research_os.pipeline import load_mission, run_mission

ROOT = Path(__file__).resolve().parents[1]
MISSION_FILE = ROOT / "missions" / "example-tennis-features.yaml"
GOLDEN = Path(__file__).parent / "golden" / "tennis_brief.md"


def _brief_body(tmp_path) -> str:
    mission = load_mission(MISSION_FILE)
    out = run_mission(mission, connector="fixture", limit=10, top=6,
                      db_path=tmp_path / "m.sqlite")
    return out.briefing.body.replace("\r\n", "\n").strip() + "\n"


def test_brief_matches_golden(tmp_path):
    body = _brief_body(tmp_path)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(body, encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "no golden file yet; run once with UPDATE_GOLDEN=1"
    expected = GOLDEN.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert body == expected, (
        "Brief output changed. If intentional, regenerate with UPDATE_GOLDEN=1 "
        "and review the diff; never edit the golden file just to go green."
    )


def test_every_brief_item_links_its_source(tmp_path):
    """The core pitch: every claim is traceable to a source link."""
    body = _brief_body(tmp_path)
    items = [ln for ln in body.splitlines() if ln.startswith("- **")]
    assert items, "brief has no evidence items"
    for ln in items:
        assert re.search(r"https?://", ln), f"no source link on: {ln[:80]}"


@pytest.mark.xfail(strict=True, reason="arXiv version suffix (v2) is not collapsed to one source")
def test_arxiv_version_suffix_collapses():
    assert canonical_url("https://arxiv.org/abs/2401.10101v2") == \
        canonical_url("https://arxiv.org/abs/2401.10101")


def test_llm_brief_gets_mechanical_sources_even_if_model_cites_nothing(monkeypatch):
    """The model is not trusted to cite. Sources are appended by code."""
    from research_os.briefing import synthesize
    from research_os.canonical import enrich
    from research_os.classify import classify
    from research_os.models import Mission, Result

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def transport(url, headers, body):
        return {"candidates": [{"content": {"parts": [{"text": "## Bottom line\nNo links here.\n"}]}}]}

    mission = Mission(id="m", objective="tennis outcome features",
                      success_condition="three features")
    rs = [Result(title="Preprint", url="https://arxiv.org/abs/1", snippet="s", raw_rank=1),
          Result(title="Forum", url="https://reddit.com/r/tennis/a", snippet="c", raw_rank=2)]
    enrich(rs)
    classify(rs)
    body = synthesize(mission, rs, top=2, retrieved=2, _transport=transport).body
    assert "## Sources" in body
    assert "https://arxiv.org/abs/1" in body
    assert "https://reddit.com/r/tennis/a" in body
