"""Gate 3: every production filter is declared applied or deliberately omitted.

The ledger cross-checks what the analyst *declares* against what a regex detector *sees* in the
SQL. Detectors are heuristics: a disagreement is a warning for a human, never a hard failure. The
exceptions are ``assert`` rules, which are checked without any declaration, and the bookkeeping
errors (undeclared choice, omission without a reason, unknown or malformed declaration).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from query_gates.registry import ASSERT, FilterRegistry, FilterRule
from query_gates.sqlutil import strip_comments, strip_noise

APPLIED = "applied"
OMITTED = "omitted"
DISPOSITIONS = (APPLIED, OMITTED)

OK = "ok"
NOT_APPLICABLE = "n/a"
WARN = "WARN"
FAIL = "FAIL"
UNDECLARED = "UNDECLARED"
UNREASONED = "UNREASONED"
BLOCKING = frozenset({FAIL, UNDECLARED, UNREASONED})


@dataclass(frozen=True)
class Declaration:
    disposition: str
    reason: str = ""


@dataclass(frozen=True)
class LedgerRow:
    key: str
    name: str
    kind: str
    declaration: Declaration | None
    detected: bool | None  # None when the rule does not apply to this SQL
    status: str
    note: str

    @property
    def blocking(self) -> bool:
        return self.status in BLOCKING


@dataclass
class LedgerResult:
    name: str
    dialect: str
    rows: list[LedgerRow]
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not any(row.blocking for row in self.rows)

    @property
    def applicable_rows(self) -> list[LedgerRow]:
        return [row for row in self.rows if row.status != NOT_APPLICABLE]

    @property
    def warnings(self) -> list[str]:
        return [f"{row.name}: {row.note}" for row in self.rows if row.status == WARN]

    @property
    def blockers(self) -> list[str]:
        found = [f"{row.key}: {row.status}" for row in self.rows if row.blocking]
        return found + self.problems


def parse_declarations(raw: Mapping[str, Any]) -> tuple[dict[str, Declaration], list[str]]:
    """Accept ``"applied"``, ``["omitted", "why"]`` or ``{"disposition": ..., "reason": ...}``."""
    parsed: dict[str, Declaration] = {}
    problems: list[str] = []
    for key, value in raw.items():
        disposition, reason = _split_declaration(value)
        if disposition not in DISPOSITIONS:
            problems.append(f"declared {key!r}: disposition must be one of {DISPOSITIONS}")
        else:
            parsed[key] = Declaration(disposition, reason)
    return parsed, problems


def _split_declaration(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value.strip().lower(), ""
    if isinstance(value, (list, tuple)) and value:
        return str(value[0]).strip().lower(), str(value[1]).strip() if len(value) > 1 else ""
    if isinstance(value, Mapping):
        disposition = str(value.get("disposition", "")).strip().lower()
        return disposition, str(value.get("reason", "")).strip()
    return "", ""


def evaluate_ledger(
    sql: str,
    declared: Mapping[str, Any],
    registry: FilterRegistry,
    *,
    name: str = "query",
    dialect: str = "generic",
) -> LedgerResult:
    """Judge every rule in ``registry`` against ``sql`` and the analyst's declarations."""
    structure = strip_noise(sql, unquote_identifiers=True)
    detail = strip_comments(sql, unquote_identifiers=True)
    declarations, problems = parse_declarations(declared)
    problems += [
        f"declared {key!r} is not a rule in the registry (typo?)"
        for key in declarations
        if key not in registry
    ]
    rows = [
        _judge(rule, declarations.get(rule.key), structure, detail, dialect) for rule in registry
    ]
    return LedgerResult(name, dialect, rows, problems)


def _judge(
    rule: FilterRule, declaration: Declaration | None, structure: str, detail: str, dialect: str
) -> LedgerRow:
    applicable = rule.applies_to_dialect(dialect) and rule.applies(structure)
    detected = rule.detect(detail) if applicable else None
    status, note = _status(rule, declaration, applicable, detected)
    return LedgerRow(rule.key, rule.name, rule.kind, declaration, detected, status, note)


def _status(
    rule: FilterRule, declaration: Declaration | None, applicable: bool, detected: bool | None
) -> tuple[str, str]:
    if not applicable:
        return NOT_APPLICABLE, ""
    if rule.kind == ASSERT:
        if detected:
            return OK, "invariant detected"
        return FAIL, "invariant not detected; an assert cannot be declared away"
    if declaration is None:
        return UNDECLARED, "declare it applied or omitted"
    if declaration.disposition == OMITTED and not declaration.reason:
        return UNREASONED, "an omission needs a reason"
    if declaration.disposition == APPLIED and not detected:
        return WARN, "declared applied, detector did not find it"
    if declaration.disposition == OMITTED and detected:
        return WARN, "declared omitted, detector found it"
    return OK, declaration.reason
