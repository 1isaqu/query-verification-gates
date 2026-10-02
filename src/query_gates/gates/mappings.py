"""Gate 4: a code-to-label mapping is re-derived per scope, never reused across scopes.

A scope is whatever partitions your catalogue: a database, a product line, a portfolio, a tenant.
The same code can mean different things in different scopes, and a mapping that was right for one
silently misreports another. For each scope the gate runs a catalogue query and checks what it
returns; it does not trust a mapping carried over from anywhere else.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from query_gates.adapters import Database
from query_gates.errors import SpecError

SCOPE_TOKEN = "$scope"
ERROR = "ERROR"
WARNING = "WARN"


class Issue(NamedTuple):
    level: str
    message: str


@dataclass
class MappingResult:
    name: str
    database: str
    per_scope: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def errors(self) -> list[str]:
        return [i.message for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[str]:
        return [i.message for i in self.issues if i.level == WARNING]


def check_mappings(
    database: Database,
    *,
    name: str,
    database_name: str,
    scopes: Sequence[str],
    query: str,
    params: Sequence[Any] = (SCOPE_TOKEN,),
    expected: Mapping[str, Mapping[str, str]] | None = None,
    require_rows: bool = False,
) -> MappingResult:
    """Derive the mapping for each scope from the live catalogue and judge it.

    ``query`` must return columns ``code`` and ``label`` (extra columns are carried into the
    report). Elements of ``params`` equal to ``"$scope"`` are replaced by the scope value.
    """
    result = MappingResult(name, database_name)
    for scope in scopes:
        bound = [scope if p == SCOPE_TOKEN else p for p in params]
        rows = _fetch_rows(database, query, bound)
        result.per_scope[scope] = rows
        _check_scope(result, scope, rows, (expected or {}).get(scope), require_rows)
    _check_across_scopes(result)
    return result


def _fetch_rows(database: Database, query: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    fetched = database.query(query, params)
    lowered = [c.lower() for c in fetched.columns]
    missing = [c for c in ("code", "label") if c not in lowered]
    if missing:
        raise SpecError([f"mapping query must return 'code' and 'label', missing {missing}"])
    rows = []
    for raw in fetched.rows:
        row = dict(zip(lowered, raw, strict=True))
        label = row["label"]
        row["code"] = str(row["code"])
        row["label"] = None if label is None or not str(label).strip() else str(label).strip()
        rows.append(row)
    return rows


def _check_scope(
    result: MappingResult,
    scope: str,
    rows: list[dict[str, Any]],
    expected: Mapping[str, str] | None,
    require_rows: bool,
) -> None:
    if not rows:
        level = ERROR if require_rows else WARNING
        result.issues.append(Issue(level, f"{scope}: no rows returned, mapping is unverifiable"))
    labels = _labels_by_code(rows)
    for row in rows:
        if row["label"] is None:
            result.issues.append(Issue(ERROR, f"{scope}: code {row['code']} has no label"))
    for code, found in labels.items():
        if len(found) > 1:
            shown = ", ".join(repr(label) for label in sorted(found))
            message = f"{scope}: code {code} maps to several labels: {shown}"
            result.issues.append(Issue(ERROR, message))
    for code, want in (expected or {}).items():
        _check_expected(result, scope, str(code), want, labels)


def _labels_by_code(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["label"] is not None:
            labels[row["code"]].add(row["label"])
    return labels


def _check_expected(
    result: MappingResult, scope: str, code: str, want: str, labels: dict[str, set[str]]
) -> None:
    found = labels.get(code)
    if not found:
        result.issues.append(Issue(WARNING, f"{scope}: expected code {code} was not observed"))
        return
    if str(want).strip().lower() not in {label.lower() for label in found}:
        shown = ", ".join(repr(label) for label in sorted(found))
        message = f"{scope}: code {code} expected {str(want)!r}, catalogue says {shown}"
        result.issues.append(Issue(ERROR, message))


def _check_across_scopes(result: MappingResult) -> None:
    seen: dict[str, dict[str, frozenset[str]]] = defaultdict(dict)
    for scope, rows in result.per_scope.items():
        for code, found in _labels_by_code(rows).items():
            seen[code][scope] = frozenset(label.lower() for label in found)
    for code, by_scope in seen.items():
        if len(set(by_scope.values())) > 1:
            result.issues.append(
                Issue(
                    WARNING,
                    f"code {code} is labelled differently across scopes "
                    f"({', '.join(sorted(by_scope))}): never reuse a mapping between scopes",
                )
            )
