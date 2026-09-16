from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Result


@runtime_checkable
class SearchConnector(Protocol):
    """Minimal contract. Returns normalized Results; raw_rank is 1-based origin
    position. Connectors must not dedupe, classify, or rerank — that is the
    pipeline's job."""

    name: str

    def search(self, query: str, *, limit: int = 10) -> list[Result]:
        ...
