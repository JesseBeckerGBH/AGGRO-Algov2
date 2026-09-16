from research_os import novelty
from research_os.models import Result


def _r(domain, *, seen=False, novel_domain=True):
    x = Result(title="t", url=f"https://{domain}/x", snippet="s")
    x.domain = domain
    x.seen_before = seen
    x.novel_domain = novel_domain
    return x


def test_fresh_novel_domain_scores_high():
    r = _r("newsite.example", seen=False, novel_domain=True)
    assert novelty.score_one(r, domain_share=0.0) == 1.0  # 0.45 + 0.30 + 0.25


def test_seen_and_concentrated_scores_low():
    r = _r("arxiv.org", seen=True, novel_domain=False)
    # 0 (domain not novel) + 0 (seen) + 0.25*(1-0.8) = 0.05
    assert novelty.score_one(r, domain_share=0.8) == 0.05


def test_yield_is_share_above_bar():
    rs = [_r("a.example"), _r("b.example"), _r("c.example")]
    novelty.score(rs, domain_shares={})
    rs[2].novelty_score = 0.1  # force one below the bar
    assert novelty.yield_of(rs) == round(2 / 3, 4)
    assert novelty.yield_of([]) == 0.0
