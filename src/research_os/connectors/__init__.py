"""Search connectors. Acquisition channels, never truth engines
(docs/collection/connector-strategy.md)."""

from __future__ import annotations

from .base import SearchConnector
from .brave import BraveConnector
from .fixture import FixtureConnector
from .marginalia import MarginaliaConnector

_REGISTRY = {
    "fixture": FixtureConnector,
    "offline": FixtureConnector,
    "brave": BraveConnector,
    "marginalia": MarginaliaConnector,
}


def get_connector(name: str) -> SearchConnector:
    key = (name or "fixture").strip().lower()
    try:
        return _REGISTRY[key]()
    except KeyError:
        raise ValueError(
            f"unknown connector: {name!r} (have: {', '.join(sorted(set(_REGISTRY)))})"
        )


def get_connectors(spec: str) -> list[SearchConnector]:
    """Comma-separated spec -> connector instances, order preserved, deduped."""
    seen: set[str] = set()
    out: list[SearchConnector] = []
    for part in (spec or "fixture").split(","):
        name = part.strip().lower()
        if name and name not in seen:
            seen.add(name)
            out.append(get_connector(name))
    return out or [get_connector("fixture")]


__all__ = [
    "SearchConnector", "BraveConnector", "FixtureConnector", "MarginaliaConnector",
    "get_connector", "get_connectors",
]
