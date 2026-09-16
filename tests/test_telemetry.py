from research_os import telemetry
from research_os.models import Briefing, Mission, Result

MISSION = Mission(id="m", objective="predictive features for tennis outcome",
                  success_condition="three features", novelty_requirement="high")


def _r(domain, cls, angle="technical"):
    x = Result(title="t", url=f"https://{domain}/x", snippet="s")
    x.domain = domain
    x.source_class = cls
    x.query_angle = angle
    return x


def _brief(words=50):
    return Briefing("m", "", "word " * words, retrieved=10, surfaced=6, novel_domains=3)


def test_connector_error_becomes_connector_failure():
    ev = telemetry.detect_failures(
        MISSION, [_r("arxiv.org", "primary")], _brief(),
        connector_errors={"brave": ("HTTPError", "429 rate limited")}, retrieved=8,
    )
    assert any(e["failure_class"] == "connector_failure" and "brave" in e["detail"] for e in ev)


def test_empty_pool_short_circuits():
    ev = telemetry.detect_failures(
        MISSION, [], _brief(), connector_errors={}, retrieved=0,
    )
    assert len(ev) == 1
    assert ev[0]["signal"] == "empty_result_set"


def test_no_primary_is_coverage_failure():
    surfaced = [_r("reddit.com", "community"), _r("x.com", "community")]
    ev = telemetry.detect_failures(MISSION, surfaced, _brief(), connector_errors={}, retrieved=5)
    assert any(e["failure_class"] == "coverage_failure" for e in ev)


def test_domain_dominance_is_novelty_failure():
    surfaced = [_r("arxiv.org", "primary") for _ in range(5)] + [_r("github.com", "primary")]
    ev = telemetry.detect_failures(MISSION, surfaced, _brief(), connector_errors={}, retrieved=20)
    assert any(e["signal"] == "repeat_domain_dominance" for e in ev)


def test_low_trust_top_three_is_ranking_failure():
    surfaced = [_r("farm.example", "low_trust"), _r("arxiv.org", "primary"),
                _r("github.com", "primary"), _r("nature.com", "high_trust_secondary")]
    ev = telemetry.detect_failures(MISSION, surfaced, _brief(), connector_errors={}, retrieved=20)
    assert any(e["failure_class"] == "ranking_failure" for e in ev)


def test_overlong_briefing_is_briefing_failure():
    surfaced = [_r("arxiv.org", "primary")]
    ev = telemetry.detect_failures(MISSION, surfaced, _brief(words=800),
                                   connector_errors={}, retrieved=10)
    assert any(e["signal"] == "too_long" for e in ev)


class _StubMem:
    def __init__(self, ny, conc, ps, ds):
        self._ny, self._conc, self._ps, self._ds = ny, conc, ps, ds

    def recent_novelty_yield(self, mission_id, n=3):
        return self._ny

    def domain_shares(self, mission_id):
        return {}, self._conc

    def recent_primary_share(self, mission_id, n=3):
        return self._ps

    def recent_disconfirming_share(self, mission_id, n=3):
        return self._ds


def test_drift_clean():
    rep = telemetry.check_drift("m", _StubMem(ny=0.5, conc=0.20, ps=0.5, ds=0.2))
    assert rep.clean and rep.breaches == []


def test_drift_breaches_become_failure_events():
    from research_os.telemetry import DriftReport, drift_to_failures
    rep = DriftReport("m", {}, breaches=[
        ("novelty_yield", "0% < floor 20%"),
        ("primary_share", "10% < floor 30%"),
    ])
    events = drift_to_failures(rep)
    classes = {e["failure_class"] for e in events}
    assert classes == {"novelty_failure", "coverage_failure"}
    assert all("drift:" in e["detail"] for e in events)


def test_drift_flags_each_threshold():
    rep = telemetry.check_drift("m", _StubMem(ny=0.05, conc=0.6, ps=0.1, ds=0.02))
    names = {n for n, _ in rep.breaches}
    assert names == {
        "novelty_yield", "domain_concentration", "primary_share", "disconfirming_share",
    }
