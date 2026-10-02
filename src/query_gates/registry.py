"""The filter registry behind Gate 3.

A rule describes one filter a production query is expected to carry. Two kinds exist:

* ``assert``: a correctness invariant. If the rule applies and the detector cannot see it, the
  gate fails. Nobody can declare it away.
* ``choice``: a filter that is legitimately applicable or not depending on the analysis. If the
  rule applies, the analyst must declare it ``applied`` or ``omitted`` (with a reason).

The built-in rules are generic examples over the neutral data contract in
``docs/data-contract.md``. They are a starting point: register your own, or disable these.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from query_gates.errors import SpecError
from query_gates.sqlutil import iter_table_refs

ASSERT = "assert"
CHOICE = "choice"
KINDS = (ASSERT, CHOICE)

Predicate = Callable[[str], bool]

# Invented defaults. Calibrate against the actors your own system uses for automation and tests.
DEFAULT_EXCLUDED_ACTORS = ("system%", "test%")


@dataclass(frozen=True)
class FilterRule:
    """One filter. ``applies`` sees SQL with literals blanked, ``detect`` sees literals intact."""

    key: str
    name: str
    kind: str
    description: str
    applies: Predicate
    detect: Predicate
    dialects: frozenset[str] | None = None  # None means every dialect

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise SpecError([f"rule {self.key!r}: kind must be one of {KINDS}, got {self.kind!r}"])

    def applies_to_dialect(self, dialect: str) -> bool:
        return self.dialects is None or dialect in self.dialects


class FilterRegistry:
    """An ordered, user-extensible collection of rules keyed by ``FilterRule.key``."""

    def __init__(self, rules: Sequence[FilterRule] = ()) -> None:
        self._rules: dict[str, FilterRule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: FilterRule, *, replace: bool = False) -> None:
        if rule.key in self._rules and not replace:
            raise SpecError([f"rule {rule.key!r} is already registered (use replace=True)"])
        self._rules[rule.key] = rule

    def unregister(self, key: str) -> None:
        if key not in self._rules:
            raise SpecError([f"cannot disable unknown rule {key!r}"])
        del self._rules[key]

    def get(self, key: str) -> FilterRule | None:
        return self._rules.get(key)

    def keys(self) -> list[str]:
        return list(self._rules)

    def copy(self) -> FilterRegistry:
        return FilterRegistry(list(self._rules.values()))

    def __iter__(self) -> Iterator[FilterRule]:
        return iter(self._rules.values())

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, key: object) -> bool:
        return key in self._rules


# --------------------------------------------------------------------------------------
# Predicate helpers
# --------------------------------------------------------------------------------------


def always(_sql: str) -> bool:
    return True


def matches_all(*patterns: str) -> Predicate:
    """True when every regular expression matches somewhere in the SQL."""
    compiled = [_compile(p) for p in patterns]
    return lambda sql: all(rx.search(sql) for rx in compiled)


def touches(*tables: str) -> Predicate:
    """True when any of the named tables appears as a word in the SQL."""
    rx = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in tables) + r")\b", re.IGNORECASE)
    return lambda sql: rx.search(sql) is not None


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise SpecError([f"invalid regular expression {pattern!r}: {exc}"]) from None


# --------------------------------------------------------------------------------------
# Built-in rules
# --------------------------------------------------------------------------------------


def _all_tables_hinted(sql: str) -> bool:
    refs = iter_table_refs(sql)
    return bool(refs) and all(r.hint and "NOLOCK" in r.hint.upper() for r in refs)


def _has_table_refs(sql: str) -> bool:
    return bool(iter_table_refs(sql))


_DATE_COL = r"[\w.]*\b(?:issued_at|occurred_at)\b"


def _half_open_window(sql: str) -> bool:
    """Lower bound with >=, upper bound with < (never <=). BETWEEN fails both patterns."""
    lower = re.search(_DATE_COL + r"\s*>=", sql, re.IGNORECASE)
    upper = re.search(_DATE_COL + r"\s*<(?![=>])", sql, re.IGNORECASE)
    return bool(lower and upper)


def excluded_actors_rule(
    patterns: Sequence[str] = DEFAULT_EXCLUDED_ACTORS, *, key: str = "excluded_actors"
) -> FilterRule:
    """A choice rule: automation and test actors are excluded in SQL, never after the fact.

    Every pattern must appear as ``NOT LIKE 'p'``, ``<> 'p'``, ``!= 'p'`` or in ``NOT IN (...)``.
    """
    detectors = [_actor_exclusion(p) for p in patterns]
    return FilterRule(
        key=key,
        name="Excluded actors",
        kind=CHOICE,
        description=(
            "Automation and test actors are filtered inside the SQL: "
            + ", ".join(patterns)
            + ". Filtering after aggregation cannot remove their share of a total."
        ),
        applies=touches("agents"),
        detect=lambda sql: all(d.search(sql) for d in detectors),
    )


def _actor_exclusion(pattern: str) -> re.Pattern[str]:
    quoted = re.escape(pattern)
    return re.compile(
        rf"(?:NOT\s+LIKE|<>|!=)\s*'{quoted}'|NOT\s+IN\s*\([^)]*'{quoted}'[^)]*\)",
        re.IGNORECASE,
    )


def builtin_rules(excluded_actors: Sequence[str] = DEFAULT_EXCLUDED_ACTORS) -> list[FilterRule]:
    return [
        FilterRule(
            "read_only_hint", "Non-blocking read hint on every table", ASSERT,
            "Every table reference carries the dialect's non-blocking read hint "
            "(SQL Server: WITH (NOLOCK)), so verification never blocks the audited system. "
            "Comma joins are not inspected.",
            _has_table_refs, _all_tables_hinted, frozenset({"sqlserver"}),
        ),
        FilterRule(
            "composite_agreement_key", "Agreement joins use the composite key", ASSERT,
            "Joining agreement_debts must equate both agreement_id and portfolio_id. An "
            "agreement_id reused across portfolios otherwise matches the wrong portfolio and "
            "inflates its totals.",
            touches("agreement_debts"),
            matches_all(
                r"\bagreement_id\s*=\s*[\w.]*\bagreement_id\b",
                r"\bportfolio_id\s*=\s*[\w.]*\bportfolio_id\b",
            ),
        ),
        FilterRule(
            "right_party_two_flag", "Right-party contact needs both flags", ASSERT,
            "A right-party contact is answered = 1 AND right_party = 1. The second flag alone "
            "also fires on automated sends and dropped calls.",
            touches("contact_results"),
            matches_all(
                r"[\w.]*\banswered\s*=\s*1\s+AND\s+[\w.]*\bright_party\s*=\s*1\b"
                r"|[\w.]*\bright_party\s*=\s*1\s+AND\s+[\w.]*\banswered\s*=\s*1\b"
            ),
        ),
        FilterRule(
            "instalment_dedup", "Installment dedup", CHOICE,
            "agreements can hold re-written rows. Keep the latest row per (agreement_id, "
            "portfolio_id, installment_no) with ROW_NUMBER() ... ORDER BY ... DESC, and apply "
            "business filters inside the derived table, before ROW_NUMBER(). Dedup is by "
            "installment, not by contract.",
            touches("agreements"),
            matches_all(
                r"ROW_NUMBER\s*\(\s*\)\s*OVER\s*\(\s*PARTITION\s+BY"
                r"(?=[^)]*\bagreement_id)(?=[^)]*\bportfolio_id)(?=[^)]*\binstallment_no)"
                r"[^)]*ORDER\s+BY[^)]*\bDESC"
            ),
        ),
        FilterRule(
            "status_set", "Status set", CHOICE,
            "Restrict agreements to a named status set (for example generated, approved or "
            "exception). Use a shared constant, never a literal list typed per query.",
            touches("agreements"),
            matches_all(r"\bstatus_id\s*(?:IN\s*\(|=)"),
        ),
        FilterRule(
            "date_window", "Half-open date window", CHOICE,
            "Bound the date as lower >= and upper < (never <=, never BETWEEN) so adjacent "
            "windows neither overlap nor drop the boundary instant. Bind dates as parameters.",
            touches("agreements", "contact_events"),
            _half_open_window,
        ),
        excluded_actors_rule(excluded_actors),
    ]


def builtin_registry(excluded_actors: Sequence[str] = DEFAULT_EXCLUDED_ACTORS) -> FilterRegistry:
    return FilterRegistry(builtin_rules(excluded_actors))


# --------------------------------------------------------------------------------------
# Spec-driven registry
# --------------------------------------------------------------------------------------

_REGISTRY_KEYS = {"builtin", "disable", "excluded_actor_patterns", "rules"}
_RULE_KEYS = {
    "key", "name", "kind", "description", "applies_when", "detected_when", "dialects", "replace",
}  # fmt: skip


def registry_from_config(config: Mapping[str, Any] | None) -> FilterRegistry:
    """Build a registry from the ``registry`` section of a spec (see docs/spec-format.md)."""
    config = dict(config or {})
    unknown = set(config) - _REGISTRY_KEYS
    if unknown:
        raise SpecError([f"registry: unknown keys {sorted(unknown)}"])
    if config.get("builtin", True):
        patterns = config.get("excluded_actor_patterns", DEFAULT_EXCLUDED_ACTORS)
        registry = builtin_registry(_string_list(patterns, "registry.excluded_actor_patterns"))
    else:
        registry = FilterRegistry()
    for key in _string_list(config.get("disable", []), "registry.disable"):
        registry.unregister(key)
    for index, raw in enumerate(config.get("rules", [])):
        rule, replace = _rule_from_config(raw, f"registry.rules[{index}]")
        registry.register(rule, replace=replace)
    return registry


def _rule_from_config(raw: Any, where: str) -> tuple[FilterRule, bool]:
    if not isinstance(raw, Mapping):
        raise SpecError([f"{where}: must be an object"])
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise SpecError([f"{where}: unknown keys {sorted(unknown)}"])
    if not raw.get("key") or not isinstance(raw["key"], str):
        raise SpecError([f"{where}: 'key' is required"])
    detected = _string_list(raw.get("detected_when", []), f"{where}.detected_when")
    if not detected:
        raise SpecError([f"{where}: 'detected_when' needs at least one regular expression"])
    applies = _string_list(raw.get("applies_when", []), f"{where}.applies_when")
    dialects = _string_list(raw.get("dialects", []), f"{where}.dialects")
    rule = FilterRule(
        key=raw["key"],
        name=str(raw.get("name", raw["key"])),
        kind=str(raw.get("kind", CHOICE)),
        description=str(raw.get("description", "")),
        applies=matches_all(*applies) if applies else always,
        detect=matches_all(*detected),
        dialects=frozenset(dialects) if dialects else None,
    )
    return rule, bool(raw.get("replace", False))


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        raise SpecError([f"{where}: must be a list of strings"])
    return list(value)
