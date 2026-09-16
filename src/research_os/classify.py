"""Assign exactly one source class to every result.

Priority order follows source-classes.yaml `classification_signals`. The slice
implements the signals that work from a URL + snippet alone: operator
allow/block lists, domain map, and TLD patterns. The content-inspection
signals (citation_density, originality_estimate, ad_density, correction_history)
are stubbed to return None until there is fetched page content to run them on.
"""

from __future__ import annotations

from .config import source_classes
from .models import Mission, Result

# Seeded from source-classes.yaml `example_domains` plus obvious members of each
# class. Operator overrides in configs/source-classes.yaml:domain_allowlist win
# over everything here.
_DOMAIN_MAP: dict[str, str] = {
    # primary — the thing itself
    "sec.gov": "primary",
    "arxiv.org": "primary",
    "github.com": "primary",
    "patents.google.com": "primary",
    "regulations.gov": "primary",
    "courtlistener.com": "primary",
    "federalregister.gov": "primary",
    "data.gov": "primary",
    "who.int": "primary",
    "nih.gov": "primary",
    "europa.eu": "primary",
    "biorxiv.org": "primary",
    "ssrn.com": "primary",
    # high-trust secondary
    "nature.com": "high_trust_secondary",
    "science.org": "high_trust_secondary",
    "economist.com": "high_trust_secondary",
    "ft.com": "high_trust_secondary",
    "wsj.com": "high_trust_secondary",
    "reuters.com": "high_trust_secondary",
    "apnews.com": "high_trust_secondary",
    "arstechnica.com": "high_trust_secondary",
    "technologyreview.com": "high_trust_secondary",
    # aggregator
    "en.wikipedia.org": "aggregator",
    "wikipedia.org": "aggregator",
    "news.google.com": "aggregator",
    "techmeme.com": "aggregator",
    # community
    "reddit.com": "community",
    "news.ycombinator.com": "community",
    "stackoverflow.com": "community",
    "stackexchange.com": "community",
    "quora.com": "community",
    "x.com": "community",
    "twitter.com": "community",
    "medium.com": "community",
    "substack.com": "named_expert",  # per-author; treated as named_expert by default
}

_PRIMARY_TLDS = (".gov", ".mil", ".int")
_SECONDARY_TLDS = (".edu", ".ac.uk", ".edu.au")

_DEFAULT_CLASS = "aggregator"  # unknown domain, plausible article: discovery-tier, never terminal


def _registrable(domain: str) -> str:
    """Rough eTLD+1 for map lookups (handles a.b.example.com -> example.com)."""
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    # keep last two labels, or three for known two-part TLDs
    if parts[-2] in ("co", "com", "ac", "gov", "org", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _is_first_party(reg: str, mission: Mission | None) -> bool:
    """Is this domain's own company the subject of the mission? A vendor's own
    site reporting its own pricing/announcement is first-party material
    (source-classes.yaml: "first-party announcements and direct statements"),
    even when it isn't in the static domain map. Guarded to root names of at
    least 4 characters to avoid noise matches ("co", "io", ...).
    """
    if not mission:
        return False
    root = reg.split(".")[0].lower()
    if len(root) < 4:
        return False
    terms = " ".join(mission.topical_terms())
    return root in terms


def classify_one(result: Result, mission: Mission | None = None) -> tuple[str, str]:
    cfg = source_classes()
    domain = result.domain
    reg = _registrable(domain)

    allowlist = cfg.get("domain_allowlist") or {}
    blocklist = set(cfg.get("domain_blocklist") or [])

    for d in (domain, reg):
        if d in blocklist:
            return "blocked", "domain_blocklist"
    for d in (domain, reg):
        if d in allowlist:
            return str(allowlist[d]), "operator_override"

    if domain in _DOMAIN_MAP:
        return _DOMAIN_MAP[domain], "domain_map"
    if reg in _DOMAIN_MAP:
        return _DOMAIN_MAP[reg], "domain_map"

    if _is_first_party(reg, mission):
        return "primary", "first_party_domain"

    if domain.endswith(_PRIMARY_TLDS):
        return "primary", "domain_pattern"
    if domain.endswith(_SECONDARY_TLDS):
        return "high_trust_secondary", "domain_pattern"

    return _DEFAULT_CLASS, "default"


def classify(results: list[Result], mission: Mission | None = None) -> list[Result]:
    valid = set(source_classes().get("classes", {}))
    for r in results:
        cls, signal = classify_one(r, mission)
        if cls not in valid:
            cls, signal = _DEFAULT_CLASS, "default:unknown-class"
        r.source_class = cls
        r.class_signal = signal
    return results
