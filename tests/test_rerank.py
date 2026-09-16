from research_os.canonical import enrich
from research_os.classify import classify
from research_os.models import Mission, Result
from research_os.rerank import rerank

MISSION = Mission(
    id="t", objective="predictive features for tennis outcome",
    success_condition="three features", novelty_requirement="high",
    vocabulary_seed=["serve plus one"],
)


def _mk(title, url, rank):
    return Result(title=title, url=url, snippet="serve plus one signal", raw_rank=rank)


def test_class_beats_origin_rank():
    """A primary source several positions down still outranks a community post at #1."""
    rs = [
        _mk("forum chatter", "https://reddit.com/r/tennis/a", 1),
        _mk("forum chatter 2", "https://reddit.com/r/tennis/b", 2),
        _mk("preprint", "https://arxiv.org/abs/2401.1", 5),
    ]
    enrich(rs); classify(rs)
    ordered = rerank(rs, MISSION)
    assert ordered[0].domain == "arxiv.org"
    assert ordered[0].source_class == "primary"


def test_low_trust_is_buried():
    rs = [
        _mk("listicle", "https://best-tips-guide.example/x", 1),
        _mk("preprint", "https://arxiv.org/abs/2401.2", 2),
    ]
    enrich(rs); classify(rs)
    # force the farm into low_trust to check the weight, not the classifier
    rs[0].source_class = "low_trust"
    ordered = rerank(rs, MISSION)
    assert ordered[-1].domain == "best-tips-guide.example"
    assert ordered[-1].final_score < ordered[0].final_score


def test_bare_year_does_not_count_as_topical_overlap():
    mission = Mission(
        id="t", objective="Track Miro's 2026 enterprise license repackaging",
        success_condition="x", novelty_requirement="high",
        vocabulary_seed=["miro enterprise pricing"],
    )
    # shares only the digits "2026" with the mission -- should be treated as
    # off-topic, not kept alive by a coincidental year match
    off_topic = _mk("World Cup 2026 discussion", "https://facebook.com/groups/x", 1)
    off_topic.snippet = "World Cup 2026 news and discussion groups"
    enrich([off_topic]); classify([off_topic])
    ordered = rerank([off_topic], mission)
    assert ordered == []  # dropped before ranking, not merely down-weighted


def test_domain_repetition_penalty_hits_third_hit():
    rs = [
        _mk("a", "https://arxiv.org/abs/1", 1),
        _mk("b", "https://arxiv.org/abs/2", 2),
        _mk("c", "https://arxiv.org/abs/3", 3),
    ]
    enrich(rs); classify(rs)
    ordered = rerank(rs, MISSION)
    third = next(r for r in ordered if r.url.endswith("/3"))
    assert "domain_repetition" in third.penalties
