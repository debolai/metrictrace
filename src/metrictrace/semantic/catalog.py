"""The governed metric catalog, read from Cube's ``/v1/meta``.

Every catalog metric is a view measure whose ``meta.kind`` is ``metric``. Its governance
fields are validated here, so a metric missing any of them fails loudly instead of
reaching the agent half-described.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MetricType = Literal["sum", "count", "count_distinct", "ratio", "average"]
Grain = Literal["order", "item", "payment", "review", "seller", "customer"]


class CatalogError(ValueError):
    """The semantic layer's metadata is incomplete or inconsistent."""


class MetricDefinition(BaseModel):
    """One governed metric, as published by the semantic layer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    view: str
    title: str
    description: str
    unit: str
    synonyms: tuple[str, ...]
    higher_is_better: bool
    owner: str
    grain: Grain
    maturity_days: int = Field(ge=0)
    decomposable_by: tuple[str, ...]
    metric_type: MetricType
    numerator: str | None = None
    denominator: str | None = None
    view_dimensions: tuple[str, ...]
    time_dimension: str = "purchase_date"

    @model_validator(mode="after")
    def _check(self) -> MetricDefinition:
        if self.metric_type in ("ratio", "average") and not (self.numerator and self.denominator):
            raise ValueError(
                f"{self.name}: ratio and average metrics need numerator and denominator"
            )
        missing = set(self.decomposable_by) - set(self.view_dimensions)
        if missing:
            raise ValueError(
                f"{self.name}: decomposable_by not in view {self.view}: {sorted(missing)}"
            )
        if self.time_dimension not in self.view_dimensions:
            raise ValueError(f"{self.name}: view {self.view} has no {self.time_dimension}")
        return self

    def member(self, name: str | None = None) -> str:
        """Fully qualified Cube member, e.g. ``sales.gmv``."""
        return f"{self.view}.{name or self.name}"


class AmbiguousTerm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[str, ...]
    default: str
    note: str | None = None


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)

    metrics: dict[str, MetricDefinition]
    ambiguous_terms: dict[str, AmbiguousTerm] = Field(default_factory=dict)

    def get(self, name: str) -> MetricDefinition:
        try:
            return self.metrics[name]
        except KeyError:
            raise CatalogError(f"unknown metric {name!r}") from None

    def resolve(self, term: str) -> list[str]:
        """Catalog metrics a word may refer to: exact names, synonyms, then ambiguous terms."""
        word = term.strip().lower()
        if word in self.metrics:
            return [word]
        ambiguous = self.ambiguous_terms.get(word)
        if ambiguous is not None:
            return list(ambiguous.candidates)
        return sorted(name for name, m in self.metrics.items() if word in m.synonyms)


def _short(member: str) -> str:
    return member.split(".", 1)[1]


def _snake(key: str) -> str:
    """Cube returns ``meta`` keys camelCased (``higherIsBetter``); the model uses snake_case."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _view_metrics(view: dict[str, Any], problems: list[str]) -> list[MetricDefinition]:
    """Catalog metrics published by one view; problems are appended, not raised."""
    if view.get("type") != "view":
        problems.append(f"{view['name']} is a public cube; only views may be public")
        return []
    dimensions = tuple(_short(d["name"]) for d in view.get("dimensions", []))
    measures = {_short(m["name"]) for m in view.get("measures", [])}
    found = []
    for measure in view.get("measures", []):
        fields = {_snake(key): value for key, value in (measure.get("meta") or {}).items()}
        if fields.get("kind") != "metric":
            continue
        name = _short(measure["name"])
        try:
            metric = MetricDefinition(
                name=name,
                view=view["name"],
                title=measure.get("shortTitle") or measure.get("title") or name,
                description=measure.get("description") or "",
                view_dimensions=dimensions,
                **{k: v for k, v in fields.items() if k != "kind"},
            )
        except (ValidationError, TypeError) as error:
            problems.append(f"{view['name']}.{name}: {error}")
            continue
        problems.extend(
            f"{metric.member()}: component {part} is not in the view"
            for part in (metric.numerator, metric.denominator)
            if part is not None and part not in measures
        )
        if not metric.description:
            problems.append(f"{metric.member()}: missing description")
        found.append(metric)
    return found


def _ambiguous_terms(path: Path, metrics: dict[str, MetricDefinition]) -> dict[str, AmbiguousTerm]:
    raw = yaml.safe_load(path.read_text())["terms"]
    terms = {word: AmbiguousTerm(**spec) for word, spec in raw.items()}
    for word, term in terms.items():
        unknown = (set(term.candidates) | {term.default}) - set(metrics)
        if unknown or term.default not in term.candidates:
            raise CatalogError(f"ambiguous term {word!r} refers to unknown metrics {unknown}")
    return terms


def catalog_from_meta(meta: dict[str, Any], ambiguous_terms_path: Path | None = None) -> Catalog:
    """Build and validate the catalog from a ``/v1/meta`` response."""
    metrics: dict[str, MetricDefinition] = {}
    problems: list[str] = []
    for view in meta.get("cubes", []):
        for metric in _view_metrics(view, problems):
            if metric.name in metrics:
                problems.append(f"{metric.name} is defined in more than one view")
            metrics[metric.name] = metric
    if problems:
        raise CatalogError("invalid semantic layer metadata:\n  " + "\n  ".join(problems))
    terms = _ambiguous_terms(ambiguous_terms_path, metrics) if ambiguous_terms_path else {}
    return Catalog(metrics=metrics, ambiguous_terms=terms)
