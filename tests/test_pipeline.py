from pathlib import Path

from research_os import pipeline
from research_os.connectors.fixture import FixtureConnector
from research_os.pipeline import load_mission, run_mission

MISSION_FILE = Path(__file__).resolve().parents[1] / "missions" / "example-tennis-features.yaml"


def test_full_slice_runs_and_briefs(tmp_path):
    db = tmp_path / "mem.sqlite"
    mission = load_mission(MISSION_FILE)
    out = run_mission(mission, connector="fixture", limit=10, top=6, db_path=db)

    assert out.retrieved > 0
    assert 0 < out.briefing.surfaced <= 6
    body = out.briefing.body
    for section in ("## Bottom line", "## What is new", "## What contradicts it",
                    "## Confidence", "## Next question"):
        assert section in body
    # governing rule visible in the outcome: a primary source leads
    assert out.reranked[0].source_class in {"primary", "high_trust_secondary", "named_expert"}
    assert db.exists()


def test_second_run_sees_memory_and_shrinks_whats_new(tmp_path):
    db = tmp_path / "mem.sqlite"
    mission = load_mission(MISSION_FILE)

    first = run_mission(mission, connector="fixture", db_path=db)
    second = run_mission(mission, connector="fixture", db_path=db)

    first_new = first.briefing.body.split("## What is new", 1)[1].split("##", 1)[0]
    second_new = second.briefing.body.split("## What is new", 1)[1].split("##", 1)[0]
    assert "- " in first_new
    # everything was stored on run 1, so run 2 has nothing new
    assert "Nothing here is new" in second_new
    assert all(r.seen_before for r in second.reranked)


def test_multi_connector_merges_pools_and_reports_per_connector(tmp_path, monkeypatch):
    corpus_a = [{
        "title": "Surface-adjusted return games won predicts outcome",
        "url": "https://arxiv.org/abs/2401.10101",
        "snippet": "serve plus one and return games won predict tennis match outcome",
    }]
    corpus_b = [{
        "title": "Indie notes: point-level Elo for tennis",
        "url": "https://someindieblog.example/tennis-elo",
        "snippet": "point level elo calibration for tennis match outcome, long-tail write-up",
    }]
    a = FixtureConnector(corpus_a); a.name = "conn_a"
    b = FixtureConnector(corpus_b); b.name = "conn_b"
    monkeypatch.setattr(pipeline, "get_connectors", lambda spec: [a, b])

    mission = load_mission(MISSION_FILE)
    out = run_mission(mission, connector="conn_a,conn_b", db_path=tmp_path / "m.sqlite")

    domains = {r.domain for r in out.reranked}
    assert "arxiv.org" in domains and "someindieblog.example" in domains
    assert set(out.per_connector) == {"conn_a", "conn_b"}
    assert out.per_connector["conn_a"] > 0 and out.per_connector["conn_b"] > 0
