"""Loads the machine-readable judgment layer from configs/.

These three files are the product; everything here just reads them. Weights
are never edited in code — they change only through the adaptation loop
(configs/adaptation-rules.yaml), which is Stage 7 and not implemented yet.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


def repo_root() -> Path:
    # src/research_os/config.py -> repo root is three parents up
    return Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    path = repo_root() / "configs" / name
    if not path.exists():
        raise FileNotFoundError(f"missing config: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def source_classes() -> dict:
    return _load("source-classes.yaml")


@lru_cache(maxsize=1)
def query_families() -> dict:
    return _load("query-families.yaml")


@lru_cache(maxsize=1)
def adaptation_rules() -> dict:
    return _load("adaptation-rules.yaml")


def class_weight(source_class: str) -> float:
    classes = source_classes().get("classes", {})
    entry = classes.get(source_class)
    if entry is None:
        return 0.0
    return float(entry.get("weight", 0.0))


def angle_set(novelty_requirement: str) -> list[str]:
    sets = query_families().get("angle_sets", {})
    return list(sets.get(novelty_requirement, sets.get("medium", [])))
