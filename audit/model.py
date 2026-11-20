"""Normalised source records for the multilingual guidance audit.

The CSVs in ``fixtures/`` are treated as read-only synthetic operations data.
This module only loads and indexes them; every judgement lives in
``audit.rules`` so the reasoning can be inspected next to the decision.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

RISK_HIGH = "high"
RISK_LOW = "low"

# Semantics that change what a person can actually do.  ``apply``/``end`` are
# navigation and progress wording: a bad translation there is annoying, not
# outcome-changing.  This mirrors units.csv risk but is asserted in tests so a
# silent fixture edit cannot downgrade a material unit.
OUTCOME_BEARING_CONCEPTS = frozenset({"eligible", "pay", "proof"})

BLIND = "blind"
SEEN_GOLD = "seen_gold"


@dataclass(frozen=True)
class Unit:
    id: str
    version: str
    concept: str
    risk: str
    meaning: str
    correction: str
    replacement: str

    @property
    def is_correction(self) -> bool:
        return bool(self.correction)


@dataclass(frozen=True)
class Translation:
    id: str
    unit: str
    language: str
    wording: str
    equivalence: str


@dataclass(frozen=True)
class Control:
    id: str
    language: str
    concept: str
    risk: str
    pair: str
    gold: str


@dataclass(frozen=True)
class Review:
    id: str
    reviewer: str
    translation: str
    control: str
    decision: str
    reason: str
    seen_gold: str
    at: str

    @property
    def is_blind(self) -> bool:
        return self.seen_gold != "yes"

    @property
    def target(self) -> str:
        """Exactly one of translation/control is set per DATA.md."""
        return self.translation or self.control


@dataclass(frozen=True)
class Route:
    id: str
    desk: str
    language: str
    steps: tuple[str, ...]
    version: str
    legacy: str


@dataclass(frozen=True)
class Window:
    id: str
    reviewer: str
    language: str
    concept: str
    start: str
    end: str
    minutes: int


@dataclass(frozen=True)
class Release:
    id: str
    route: str
    unit: str
    kind: str
    at: str
    link: str


@dataclass
class Dataset:
    units: dict[str, Unit]
    translations: dict[str, Translation]
    controls: dict[str, Control]
    reviews: dict[str, Review]
    routes: dict[str, Route]
    windows: dict[str, Window]
    releases: dict[str, Release]
    # Derived indexes.
    reviews_by_translation: dict[str, list[Review]] = field(default_factory=dict)
    reviews_by_control: dict[str, list[Review]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for review in self.reviews.values():
            if review.translation:
                self.reviews_by_translation.setdefault(review.translation, []).append(review)
            else:
                self.reviews_by_control.setdefault(review.control, []).append(review)
        for bucket in (self.reviews_by_translation, self.reviews_by_control):
            for rows in bucket.values():
                rows.sort(key=lambda r: (r.at, r.id))

    def translations_of(self, unit_id: str) -> list[Translation]:
        return [t for t in self.translations.values() if t.unit == unit_id]

    def units_in_family(self, concept: str, language: str) -> list[Unit]:
        """All source units of a concept that have at least one translation in a language."""
        langs = {t.unit: t.language for t in self.translations.values()}
        return [u for u in self.units.values() if u.concept == concept and langs.get(u.id) == language]

    def routes_using_wording(self, language: str, wording: str) -> list[Route]:
        return [
            r
            for r in self.routes.values()
            if r.language == language
            and any(self.translations[s].wording == wording for s in r.steps)
        ]


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _typed(rows: Iterable[dict[str, str]], cls, tuple_fields: tuple[str, ...] = ()) -> dict:
    out = {}
    for raw in rows:
        data = dict(raw)
        for name in tuple_fields:
            data[name] = tuple(part for part in data[name].split(";") if part)
        if cls is Window:
            data["minutes"] = int(data["minutes"])
        obj = cls(**data)
        out[obj.id] = obj
    return out


def load(fixtures: Path | None = None) -> Dataset:
    fixtures = fixtures or Path(__file__).resolve().parents[1] / "fixtures"
    controls = _typed(_rows(fixtures / "controls.csv"), Control)
    translations = _typed(_rows(fixtures / "translations.csv"), Translation)
    units = _typed(_rows(fixtures / "units.csv"), Unit)
    reviews = _typed(_rows(fixtures / "reviews.csv"), Review)
    routes = _typed(_rows(fixtures / "routes.csv"), Route, ("steps",))
    window_rows = []
    for row in _rows(fixtures / "windows.csv"):
        row["start"] = row.pop("from")
        row["end"] = row.pop("to")
        window_rows.append(row)
    windows = _typed(window_rows, Window)
    releases = _typed(_rows(fixtures / "releases.csv"), Release)
    return Dataset(units, translations, controls, reviews, routes, windows, releases)
