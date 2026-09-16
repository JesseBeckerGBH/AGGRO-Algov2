from datetime import datetime, timezone

import pytest

from research_os import adaptation
from research_os.canonical import enrich
from research_os.classify import classify
from research_os.memory import Memory
from research_os.models import Mission, Result
from research_os.rerank import rerank

MISSION = Mission(id="m1", objective="predictive features for tennis outcome",
                  success_condition="three features", novelty_requirement="high")


def _mem(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.upsert_mission(MISSION, "2026-01-01T00:00:00+00:00")
    return mem


def _fail(mem, cls, signal, detail, run_at):
    mem.record_failures("m1", [{"failure_class": cls, "signal": signal,
                                "severity": "high", "detail": detail}], run_at)


def test_propose_operator_lever_for_domain_dominance(tmp_path):
    mem = _mem(tmp_path)
    for i in range(3):
        _fail(mem, "novelty_failure", "repeat_domain_dominance",
              "arxiv.org is 5/8 of the surfaced set", f"2026-02-0{i+1}T00:00:00+00:00")
    props = adaptation.propose(MISSION, mem)
    dom = [p for p in props if p.lever == "domain_weight_adjustment"]
    assert dom and dom[0].auto is False
    assert dom[0].change["delta"] == {"arxiv.org": -0.05}


def test_propose_and_apply_auto_connector_routing(tmp_path):
    mem = _mem(tmp_path)
    for i in range(2):
        _fail(mem, "connector_failure", "HTTPError", "marginalia: 503 unavailable",
              f"2026-02-0{i+1}T00:00:00+00:00")
    props = [p for p in adaptation.propose(MISSION, mem) if p.lever == "connector_routing"]
    assert props and props[0].auto is True and props[0].change == {"demote": {"marginalia": 1}}

    now = "2026-03-01T00:00:00+00:00"
    v = adaptation.apply(props[0], "m1", mem, now)
    assert v > 0
    overlay = mem.active_overlay("m1")
    assert overlay["connector_demotions"] == {"marginalia": 1}
    log = (adaptation._log_path()).read_text(encoding="utf-8")
    assert "connector_routing" in log


def test_propose_does_not_restack_an_already_active_flag_lever(tmp_path):
    mem = _mem(tmp_path)
    for i in range(2):
        _fail(mem, "novelty_failure", "repeat_domain_dominance",
              "arxiv.org is 5/8 of the surfaced set", f"2026-02-0{i+1}T00:00:00+00:00")
    mem.add_policy_version("m1", "2026-02-02T00:00:00+00:00", "query_family_regeneration",
                           "seed", {"force_angles": []}, status="active")
    props = [p for p in adaptation.propose(MISSION, mem) if p.lever == "query_family_regeneration"]
    assert props == []  # already active -> not proposed again


def test_gate_blocks_on_cooldown(tmp_path):
    mem = _mem(tmp_path)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mem.add_policy_version("m1", now_iso, "connector_routing", "seed",
                           {"demote": {"brave": 1}}, status="active")
    now_days = datetime.now(timezone.utc).timestamp() / 86400.0
    p = adaptation.Proposal("connector_routing", {"demote": {"brave": 1}}, "t", True, "r")
    ok, reason = adaptation.gate(p, "m1", mem, now_days=now_days)
    assert not ok and "cooldown" in reason


def test_gate_enforces_primary_floor_invariant(tmp_path):
    mem = _mem(tmp_path)
    now_days = datetime.now(timezone.utc).timestamp() / 86400.0
    p = adaptation.Proposal("class_weight_adjustment", {"delta": {"primary": -0.5}},
                            "t", False, "r")
    ok, reason = adaptation.gate(p, "m1", mem, now_days=now_days)
    assert not ok and "primary" in reason


def test_evaluate_rolls_back_when_quality_declines(tmp_path):
    mem = _mem(tmp_path)
    # three good briefings, then a change, then five worse ones
    b = Mission  # noqa
    for i in range(3):
        _brief(mem, f"2026-04-0{i+1}T00:00:00+00:00", ny=0.8, ps=0.9, ds=0.4)
    change_at = "2026-05-01T00:00:00+00:00"
    v = mem.add_policy_version("m1", change_at, "connector_routing", "t",
                               {"demote": {"marginalia": 1}}, status="active")
    for i in range(5):
        _brief(mem, f"2026-05-1{i}T00:00:00+00:00", ny=0.1, ps=0.3, ds=0.05)

    decisions = adaptation.evaluate("m1", mem, "2026-06-01T00:00:00+00:00")
    d = [x for x in decisions if x["version"] == v][0]
    assert d["decision"] == "rolled_back"
    assert [r["status"] for r in mem.list_policy("m1") if r["version"] == v] == ["rolled_back"]


def _brief(mem, ts, *, ny, ps, ds):
    from research_os.models import Briefing
    mem.record_briefing(Briefing("m1", ts, "body", 10, 6, 3), novelty_yield=ny,
                        primary_share=ps, disconfirming_share=ds)
    mem.conn.execute("UPDATE briefings SET generated_at = ? WHERE briefing_id = "
                     "(SELECT MAX(briefing_id) FROM briefings)", (ts,))
    mem.conn.commit()


def _one_arxiv():
    r = [Result(title="a", url="https://arxiv.org/abs/1",
                snippet="serve plus one tennis match outcome", raw_rank=1)]
    enrich(r); classify(r)
    return r


def test_overlay_domain_delta_lowers_score(tmp_path):
    base = rerank(_one_arxiv(), MISSION)[0].final_score
    down = rerank(_one_arxiv(), MISSION,
                  overlay={"domain_weight_delta": {"arxiv.org": -0.4}})[0].final_score
    assert down < base and down == pytest.approx(base * 0.6, rel=1e-3)
