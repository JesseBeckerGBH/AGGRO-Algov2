from research_os.connectors.marginalia import MarginaliaConnector

_SAMPLE = {
    "license": "CC-BY-NC-SA",
    "query": "tennis match outcome",
    "results": [
        {
            "url": "https://someindieblog.example/tennis-elo-notes",
            "title": "Notes on point-level Elo for tennis",
            "description": "A long-tail write-up on calibrating point Elo.",
            "quality": 8.1,
        },
        {
            "url": "",  # dropped: no url
            "title": "junk",
            "description": "x",
        },
        {
            "url": "https://another.example/serve-plus-one",
            "title": "Serve+1 tactical patterns",
            "snippet": "alt key name for the body text",
        },
    ],
}


def test_parses_results_and_skips_urlless(monkeypatch):
    c = MarginaliaConnector(min_interval=0)
    monkeypatch.setattr(c, "_get_with_retry", lambda req: _SAMPLE)
    out = c.search("tennis match outcome", limit=10)

    assert [r.url for r in out] == [
        "https://someindieblog.example/tennis-elo-notes",
        "https://another.example/serve-plus-one",
    ]
    assert out[0].connector == "marginalia"
    # raw_rank preserves the origin position, so the skipped url-less row leaves a gap
    assert out[0].raw_rank == 1 and out[1].raw_rank == 3
    assert out[0].snippet.startswith("A long-tail")
    assert out[1].snippet == "alt key name for the body text"  # falls back to 'snippet'


def test_builds_public_key_url(monkeypatch):
    c = MarginaliaConnector(min_interval=0)
    seen = {}

    def fake(req):
        seen["url"] = req.full_url
        return {"results": []}

    monkeypatch.setattr(c, "_get_with_retry", fake)
    c.search("serve plus one", limit=5)
    assert "/public/search/serve%20plus%20one" in seen["url"]
    assert "count=5" in seen["url"]


def test_get_connectors_dedupes_and_orders():
    from research_os.connectors import get_connectors

    conns = get_connectors("marginalia, fixture , marginalia")
    assert [c.name for c in conns] == ["marginalia", "fixture"]
