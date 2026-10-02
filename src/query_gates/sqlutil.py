"""SQL text helpers shared by the adapters and the gates.

Everything here is pattern based. It is deliberately not a SQL parser: the gates need a cheap,
dependency-free approximation that errs in a documented direction (never reports a filter as
present when it is not, never lets a write statement through the read-only guard).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NamedTuple

# One pass over the text so that "--" inside a literal and a quote inside a comment are both safe.
_TOKEN = re.compile(
    r"(?P<lit>'(?:[^']|'')*')"
    r"|(?P<lc>--[^\n]*)"
    r"|(?P<bc>/\*.*?\*/)"
    r"|(?P<qid>\"[^\"]*\"|\[[^\]]*\])",
    re.DOTALL,
)


def strip_comments(sql: str) -> str:
    """Remove comments. String literals and quoted identifiers are kept as written."""

    def repl(match: re.Match[str]) -> str:
        return " " if match.lastgroup in ("lc", "bc") else match.group(0)

    return _TOKEN.sub(repl, sql)


def strip_noise(sql: str, *, unquote_identifiers: bool = False) -> str:
    """Remove comments, blank out string literals, and neutralise quoted identifiers.

    With ``unquote_identifiers`` a quoted identifier keeps its text (spaces become underscores) so
    that ``[dbo].[agreements]`` can still be recognised as a table reference.
    """

    def repl(match: re.Match[str]) -> str:
        kind = match.lastgroup
        if kind == "lit":
            return "''"
        if kind == "qid":
            inner = match.group(0)[1:-1].replace(" ", "_")
            return inner if unquote_identifiers else "_q_"
        return " "

    return _TOKEN.sub(repl, sql)


def canonical(sql: str) -> str:
    """Whitespace-collapsed, lower-cased form used to compare two queries for identity."""
    return re.sub(r"\s+", " ", strip_comments(sql)).strip().lower()


# --------------------------------------------------------------------------------------
# Read-only guard
# --------------------------------------------------------------------------------------


class ReadOnlyViolation(ValueError):
    """Raised when SQL handed to an adapter is not a read-only statement."""


# Words that can hide inside a statement that starts with an allowed keyword, e.g. a CTE followed
# by DELETE, or SELECT ... INTO. Statements that start with anything else fail the start check.
_FORBIDDEN = re.compile(
    r"(?<![\w$#@])(?:INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|INTO)(?!\w)"
    r"|(?<![\w$#@])(?:SP|XP)_\w*",
    re.IGNORECASE,
)
_FIRST_WORD = re.compile(r"^[\s(]*([A-Za-z_]+)")
DEFAULT_ALLOWED_STARTS = ("SELECT", "WITH", "VALUES")


def assert_read_only(
    sql: str,
    *,
    allowed_starts: Iterable[str] = DEFAULT_ALLOWED_STARTS,
    allow_batches: bool = False,
) -> None:
    """Refuse anything that is not a plain read.

    A seat belt, not a security boundary: the real protection is a read-only database login.
    ``SELECT ... INTO`` and procedure calls are refused because they write or run arbitrary code.
    """
    statements = [s.strip() for s in strip_noise(sql).split(";") if s.strip()]
    if not statements:
        raise ReadOnlyViolation("empty SQL")
    if len(statements) > 1 and not allow_batches:
        raise ReadOnlyViolation("only a single statement is allowed")
    allowed = {word.upper() for word in allowed_starts}
    for statement in statements:
        _check_statement(statement, allowed)


def _check_statement(statement: str, allowed: set[str]) -> None:
    first = _FIRST_WORD.match(statement)
    if first is None or first.group(1).upper() not in allowed:
        shown = first.group(1) if first else statement[:20]
        raise ReadOnlyViolation(f"statement must start with {sorted(allowed)}, got {shown!r}")
    hit = _FORBIDDEN.search(statement)
    if hit:
        raise ReadOnlyViolation(f"read-only SQL only, found {hit.group(0).upper()!r}")


# --------------------------------------------------------------------------------------
# Table and column references
# --------------------------------------------------------------------------------------

# Words that can follow a table name but are never an alias.
_CLAUSE_WORDS = (
    "WHERE|ON|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|OUTER|NATURAL|GROUP|ORDER|HAVING|UNION|INTERSECT|"
    "EXCEPT|LIMIT|OFFSET|FETCH|WINDOW|WITH|OPTION|USING|SET|FOR|SELECT"
)
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+(?P<table>[A-Za-z_][\w.$#]*)(?P<call>\()?"
    rf"(?:\s+(?:AS\s+)?(?!(?:{_CLAUSE_WORDS})\b)(?P<alias>[A-Za-z_]\w*))?"
    r"(?:\s*(?:WITH\s*)?\((?P<hint>[^()]*)\))?",
    re.IGNORECASE,
)
_CTE_NAME = re.compile(
    r"(?:\bWITH\b|,)\s*([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s+AS\s*\(", re.IGNORECASE
)
# Keywords whose FROM is not a table source.
_FROM_IN_FUNCTION = re.compile(
    r"\b(EXTRACT|TRIM|SUBSTRING|OVERLAY)\s*\(([^()]*?)\bFROM\b", re.IGNORECASE
)
_DISTINCT_FROM = re.compile(r"\bDISTINCT\s+FROM\b", re.IGNORECASE)
_QUALIFIED = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,2})\b")


class TableRef(NamedTuple):
    """One table named in a FROM or JOIN clause."""

    table: str  # last dotted part, original case
    alias: str | None
    hint: str | None  # text inside a trailing hint clause, e.g. "NOLOCK"


def _scrub(sql: str) -> str:
    clean = strip_noise(sql, unquote_identifiers=True)
    clean = _FROM_IN_FUNCTION.sub(r"\1(\2,", clean)
    return _DISTINCT_FROM.sub("DISTINCT,", clean)


def cte_names(sql: str) -> set[str]:
    """Lower-cased names defined by WITH ... AS ( ... ) in this statement."""
    return {name.lower() for name in _CTE_NAME.findall(_scrub(sql))}


def iter_table_refs(sql: str) -> list[TableRef]:
    """Physical table references in FROM/JOIN clauses, excluding CTEs and table functions.

    Comma joins (``FROM a, b``) are not inspected: only the first table of such a list is seen.
    """
    clean = _scrub(sql)
    ctes = {name.lower() for name in _CTE_NAME.findall(clean)}
    refs: list[TableRef] = []
    for match in _TABLE_REF.finditer(clean):
        table = match.group("table").split(".")[-1]
        if match.group("call") or table.lower() in ctes:
            continue
        hint = match.group("hint")
        refs.append(TableRef(table, match.group("alias"), hint.strip() if hint else None))
    return refs


def extract_tables(sql: str) -> list[str]:
    """Sorted, lower-cased physical table names the SQL reads from."""
    return sorted({ref.table.lower() for ref in iter_table_refs(sql)})


def extract_refs(sql: str) -> list[str]:
    """Best-effort ``table.column`` references, with aliases resolved to table names.

    Only qualified references are returned (``a.amount``). Unqualified columns cannot be attributed
    to a table without a catalogue, and references through CTEs or derived tables are skipped.
    The result seeds Gate 1; it is advisory, never the authority.
    """
    lookup: dict[str, str] = {}
    for ref in iter_table_refs(sql):
        lookup[ref.table.lower()] = ref.table
        if ref.alias:
            lookup[ref.alias.lower()] = ref.table
    found: set[str] = set()
    for match in _QUALIFIED.finditer(_scrub(sql)):
        *_, qualifier, column = match.group(1).split(".")
        table = lookup.get(qualifier.lower())
        if table:
            found.add(f"{table}.{column}".lower())
    return sorted(found)
