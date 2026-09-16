from research_os.memory import Memory
from research_os.models import Mission, Result

NOW = "2026-01-01T00:00:00+00:00"

MISSION = Mission(
    id="m1", objective="predictive features for tennis outcome",
    success_condition="three features", novelty_requirement="high",
)


def _res(domain, cls="primary", title="t", snippet="s", angle="primary_source_hunt"):
    r = Result(title=title, url=f"https://{domain}/{title.replace(' ', '-')}", snippet=snippet)
    r.domain = domain
    r.source_class = cls
    r.query_angle = angle
    r.canonical_url = r.url
    r.content_hash = f"h-{domain}-{title}"
    return r


def test_domain_shares_and_priors(tmp_path):
    with Memory(tmp_path / "m.sqlite") as mem:
        mem.upsert_mission(MISSION, NOW)
        mem.record_domain_hits("m1", [_res("arxiv.org"), _res("arxiv.org"), _res("github.com")], NOW)
        shares, top = mem.domain_shares("m1")
        assert shares["arxiv.org"] == 2 / 3 and top == 2 / 3

        incoming = [_res("arxiv.org"), _res("newsite.example")]
        mem.mark_novelty("m1", incoming)
        assert incoming[0].domain_prior_hits == 2 and incoming[0].novel_domain is False
        assert incoming[1].domain_prior_hits == 0 and incoming[1].novel_domain is True


def test_harvest_vocab_threshold_and_operator_exclusion(tmp_path):
    with Memory(tmp_path / "m.sqlite") as mem:
        mem.upsert_mission(MISSION, NOW)
        # "return depth" in two distinct primary titles -> candidate
        # "point elo" in only one -> not yet
        results = [
            _res("arxiv.org", title="return depth and point elo signals"),
            _res("github.com", title="measuring return depth from charting"),
            _res("reddit.com", cls="community", title="return depth return depth return depth"),
        ]
        new = mem.harvest_vocab("m1", results, operator_terms=["surface adjusted"], now=NOW)
        assert "return depth" in new
        assert "point elo" not in new  # only one primary source
        terms = {row["term"] for row in mem.list_vocab("m1")}
        assert "return depth" in terms

        # operator terms are never harvested
        new2 = mem.harvest_vocab("m1", results, operator_terms=["return depth"], now=NOW)
        assert "return depth" not in new2


def test_promote_reject_roundtrip(tmp_path):
    with Memory(tmp_path / "m.sqlite") as mem:
        mem.upsert_mission(MISSION, NOW)
        mem.harvest_vocab("m1", [
            _res("arxiv.org", title="surface elo ratings for calibration"),
            _res("dash.harvard.edu", title="surface elo and return games"),
        ], operator_terms=[], now=NOW)

        assert mem.promoted_terms("m1") == []
        assert mem.set_vocab_status("m1", "surface elo", "promoted", NOW) is True
        assert mem.promoted_terms("m1") == ["surface elo"]
        assert mem.set_vocab_status("m1", "nonexistent term", "promoted", NOW) is False
