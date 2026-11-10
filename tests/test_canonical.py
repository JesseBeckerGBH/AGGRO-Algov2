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
        Result(title="A study, revised", url="https://arxiv.org/abs/2", snippet="expanded body", raw_rank=3),
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
        "https://arxiv.org/abs/2",
        "https://example.com/x",
    }


def test_arxiv_versions_and_pdf_links_collapse_to_one_paper():
    base = canonical_url("https://arxiv.org/abs/2401.10101")
    assert base == "https://arxiv.org/abs/2401.10101"
    for u in (
        "https://arxiv.org/abs/2401.10101v2",
        "https://arxiv.org/pdf/2401.10101v3.pdf",
        "http://www.arxiv.org/pdf/2401.10101",
        "https://export.arxiv.org/abs/2401.10101v1",
    ):
        assert canonical_url(u) == base


def test_arxiv_old_style_ids_and_non_paper_paths():
    assert canonical_url("https://arxiv.org/abs/hep-th/9901001v2") == \
        "https://arxiv.org/abs/hep-th/9901001"
    # not a paper path: left alone
    assert canonical_url("https://arxiv.org/list/cs.LG/recent") == \
        "https://arxiv.org/list/cs.LG/recent"
    # distinct papers stay distinct
    assert canonical_url("https://arxiv.org/abs/2401.10102") != \
        canonical_url("https://arxiv.org/abs/2401.10101")
