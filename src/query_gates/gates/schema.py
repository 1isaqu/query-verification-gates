"""Gate 1: every referenced column and code table exists in the live database."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from query_gates.adapters import Database


@dataclass(frozen=True)
class CodeTable:
    """A catalogue that maps codes to labels. Key and label columns are checked when given."""

    table: str
    key: str | None = None
    label: str | None = None


@dataclass
class SchemaResult:
    database: str
    checked: list[str]
    missing: list[str]
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def normalise_ref(ref: str) -> str:
    """Upper-case ``table.column`` from ``schema.table.column`` or a quoted spelling."""
    cleaned = ref.strip().replace("[", "").replace("]", "").replace('"', "")
    return ".".join(cleaned.upper().split(".")[-2:])


def check_schema(
    name: str,
    database: Database,
    *,
    columns: Iterable[str] = (),
    tables: Iterable[str] = (),
    code_tables: Sequence[CodeTable] = (),
) -> SchemaResult:
    """Compare the references against one catalogue read; never guess a column name."""
    live_columns = database.list_columns()
    live_tables = database.list_tables()
    result = SchemaResult(name, [], [])
    for ref in columns:
        _check_column(result, normalise_ref(ref), live_columns)
    for table in tables:
        _check_table(result, table.strip().upper(), live_tables)
    for code_table in code_tables:
        _check_code_table(result, code_table, live_tables, live_columns)
    return result


def _check_column(result: SchemaResult, ref: str, live: set[str]) -> None:
    result.checked.append(ref)
    if "." not in ref:
        result.missing.append(f"{ref} (not in table.column form)")
    elif ref not in live:
        result.missing.append(ref)


def _check_table(result: SchemaResult, table: str, live: set[str]) -> None:
    result.checked.append(f"{table} (table)")
    if table not in live:
        result.missing.append(f"{table} (table)")


def _check_code_table(
    result: SchemaResult, code_table: CodeTable, live_tables: set[str], live_columns: set[str]
) -> None:
    table = code_table.table.strip().upper()
    result.checked.append(f"{table} (code table)")
    if table not in live_tables:
        result.missing.append(f"{table} (code table)")
        return
    for column in (code_table.key, code_table.label):
        if column and f"{table}.{column.strip().upper()}" not in live_columns:
            result.missing.append(f"{table}.{column.strip().upper()} (code table column)")
