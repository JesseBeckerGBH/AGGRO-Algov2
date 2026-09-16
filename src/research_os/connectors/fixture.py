"""Offline connector with a canned corpus.

Lets the whole pipeline run with zero API keys, and gives the tests a stable
result set that deliberately exercises every branch: multiple source classes,
a repeated domain, a near-duplicate, and a low-trust content farm.
"""

from __future__ import annotations

from ..models import Result

# A realistic mixed-class result set for the example tennis-features mission.
_CORPUS: list[dict] = [
    {
        "title": "Surface-adjusted return games won as a predictor of match outcome",
        "url": "https://arxiv.org/abs/2401.10101",
        "snippet": "We show that return games won, adjusted for surface, carries "
                   "predictive signal beyond serve-based Elo in ATP matches.",
    },
    {
        "title": "tennis-abstract/point-by-point: raw ATP/WTA point logs",
        "url": "https://github.com/tennis-abstract/pbp-data",
        "snippet": "Open dataset of point-by-point sequences for tour-level matches, "
                   "2011-present, with serve+1 outcome tags.",
    },
    {
        "title": "Why serve+1 patterns matter more than ace rate",
        "url": "https://www.substack.com/@tennisxbt/serve-plus-one",
        "snippet": "A practitioner breakdown, by a named betting modeller, of why "
                   "first-strike patterns beat raw ace counts for prediction.",
    },
    {
        "title": "Point-level Elo: a technical primer",
        "url": "https://arstechnica.com/science/2023/06/point-level-elo-primer/",
        "snippet": "How point-level Elo differs from match Elo and where it "
                   "overfits on short samples.",
    },
    {
        "title": "Serve plus one - Tennis terminology",
        "url": "https://en.wikipedia.org/wiki/Serve_plus_one?utm_source=share",
        "snippet": "Serve plus one refers to the server's first groundstroke after "
                   "the serve; often the point-deciding shot.",
    },
    {
        "title": "Does surface really change return stats that much?",
        "url": "https://www.reddit.com/r/tennis/comments/xyz/surface_return_stats/",
        "snippet": "Forum thread arguing about clay vs hard return-games-won gaps "
                   "with some hand-collected numbers.",
    },
    {
        "title": "How to model tennis: feature ideas thread",
        "url": "https://stats.stackexchange.com/questions/998877/tennis-features",
        "snippet": "Q&A collecting candidate predictive features: fatigue, travel, "
                   "surface transition, return depth proxies.",
    },
    {
        "title": "10 SHOCKING Tennis Stats That Will Blow Your Mind",
        "url": "https://best-tennis-tips-guide.example/shocking-stats/",
        "snippet": "Listicle with affiliate links and no methodology. Ad-heavy.",
    },
    {
        "title": "Surface-adjusted return games won predicts outcomes (mirror)",
        "url": "https://arxiv.org/abs/2401.10101v2",
        "snippet": "We show that return games won, adjusted for surface, carries "
                   "predictive signal beyond serve-based Elo in ATP matches.",
    },
    {
        "title": "tennis-abstract/point-by-point: match charting project docs",
        "url": "https://github.com/tennis-abstract/charting-docs",
        "snippet": "Documentation for the volunteer match-charting schema: shot "
                   "types, directions, depth, and error categories.",
    },
]


class FixtureConnector:
    name = "fixture"

    def __init__(self, corpus: list[dict] | None = None):
        self._corpus = corpus if corpus is not None else _CORPUS

    def search(self, query: str, *, limit: int = 10) -> list[Result]:
        return [
            Result(
                title=item["title"],
                url=item["url"],
                snippet=item.get("snippet", ""),
                connector=self.name,
                raw_rank=i,
            )
            for i, item in enumerate(self._corpus[:limit], start=1)
        ]
