"""Gate 2: a number is computed twice by independent routes and the two must agree."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from query_gates.adapters import Database
from query_gates.errors import AdapterError, SpecError
from query_gates.sqlutil import canonical

DEFAULT_TOLERANCE = 0.005  # 0.5 percent, relative
SIMILARITY_WARNING = 0.9
_NUMERIC = (int, float, Decimal)


@dataclass
class Reconciliation:
    label: str
    database: str
    a: float
    b: float
    tolerance: float
    claimed: float | None = None
    similarity: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def rel_diff(self) -> float:
        scale = max(abs(self.a), abs(self.b))
        return 0.0 if scale == 0 else abs(self.a - self.b) / scale

    @property
    def ok(self) -> bool:
        return self.rel_diff <= self.tolerance

    @property
    def claim_drift(self) -> float | None:
        """Distance of path A from the previously claimed value. Reported, never failing: data
        grows and a relative date moves, so only A versus B decides the gate."""
        if self.claimed is None:
            return None
        scale = max(abs(self.claimed), abs(self.a))
        return 0.0 if scale == 0 else abs(self.a - self.claimed) / scale


def reconcile(
    database: Database,
    label: str,
    query_a: str,
    query_b: str,
    *,
    database_name: str = "",
    tolerance: float = DEFAULT_TOLERANCE,
    claimed: float | None = None,
    params_a: Sequence[Any] = (),
    params_b: Sequence[Any] = (),
    column: str | None = None,
) -> Reconciliation:
    """Run both queries, each of which must yield one row, and compare the chosen number."""
    if not 0 <= tolerance < 1:
        raise SpecError([f"{label}: tolerance must satisfy 0 <= tolerance < 1, got {tolerance}"])
    similarity = _independence(label, query_a, query_b, params_a, params_b)
    a = _number(database.query_one(query_a, params_a), column, f"{label} (path A)")
    b = _number(database.query_one(query_b, params_b), column, f"{label} (path B)")
    result = Reconciliation(label, database_name, a, b, tolerance, claimed, similarity)
    if similarity > SIMILARITY_WARNING:
        result.warnings.append(
            f"queries are {similarity:.0%} similar: check the groupings really differ"
        )
    return result


def _independence(
    label: str, query_a: str, query_b: str, params_a: Sequence[Any], params_b: Sequence[Any]
) -> float:
    canon_a, canon_b = canonical(query_a), canonical(query_b)
    if canon_a == canon_b and list(params_a) == list(params_b):
        raise SpecError([f"{label}: query_a and query_b are identical, not independent"])
    return SequenceMatcher(None, canon_a, canon_b).ratio()


def _number(row: dict[str, Any], column: str | None, where: str) -> float:
    if column:
        if column not in row:
            raise SpecError([f"{where}: column {column!r} not in result columns {list(row)}"])
        value = row[column]
    elif len(row) == 1:
        value = next(iter(row.values()))
    else:
        raise SpecError([f"{where}: expected one numeric column, got {list(row)}; set 'column'"])
    return _to_float(value, where)


def _to_float(value: Any, where: str) -> float:
    if value is None:
        raise AdapterError(f"{where}: result is NULL")
    if isinstance(value, bool) or not isinstance(value, _NUMERIC):
        raise AdapterError(f"{where}: result {value!r} is not numeric")
    number = float(value)
    if not math.isfinite(number):
        raise AdapterError(f"{where}: result is not finite")
    return number
