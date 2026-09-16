from research_os.canonical import canonical_url, content_hash, dedupe, domain_of
from research_os.models import Result


def test_canonical_url_strips_tracking_www_and_trailing_slash():
    a = canonical_url("http://www.Example.com/Path/?utm_source=x&b=2&a=1")
    assert a == "https://example.com/Path?a=1&b=2"


def test_canonical_url_collapses_and_normalises():
    assert canonical_url("https://example.com//a//b/") == "https://example.com/a/b"


def test_domain_of_drops_www():
    assert domain_of("https://www.sec.gov/cgi-bin/browse-edgar") == "sec.gov"


def test_dedupe_collapses_exact_dupes_keeps_distinct():
    rs = [
        Result(title="A study", url="https://arxiv.org/abs/1", snippet="same body", raw_rank=1),
        # same doc, only a tracking param differs -> exact dupe by canonical URL + hash
        Result(title="A study", url="https://arxiv.org/abs/1?utm_medium=rss", snippet="same body", raw_rank=2),
        # genuinely different document at a different URL and title -> survives dedupe
        # (its near-duplicate body is handled later, as a rerank penalty)
        Result(title="A study, revised", url="https://arxiv.org/abs/1v2", snippet="expanded body", raw_rank=3),
        Result(title="Different", url="https://example.com/x", snippet="other", raw_rank=4),
    ]
    for r in rs:
        r.canonical_url = canonical_url(r.url)
        r.content_hash = content_hash(r.title, r.snippet)
    out = dedupe(rs)
    assert len(out) == 3
    assert out[0].raw_rank == 1  # best-ranked instance of the exact dupe kept
    assert {r.canonical_url for r in out} == {
        "https://arxiv.org/abs/1",
        "https://arxiv.org/abs/1v2",
        "https://example.com/x",
    }
