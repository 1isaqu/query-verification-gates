"""Dump a live catalog as markdown, to build or refresh a data dictionary.

Gate 1 is only as good as the dictionary you check against. This reads column names and types
straight from the database (never from neighboring code) and dumps code tables, so the dictionary
can be pasted from live facts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from query_gates.adapters import Database
from query_gates.errors import SpecError
from query_gates.gates.schema import CodeTable

_IDENTIFIER = re.compile(r"^[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)*$")


def describe_tables(database: Database, tables: Sequence[str] | None = None) -> str:
    """Markdown column tables. With no table names, every table the catalog lists."""
    names = list(tables) if tables else sorted(database.list_tables())
    return "\n".join(_describe_one(database, name) for name in names)


def _describe_one(database: Database, table: str) -> str:
    columns = database.describe_table(table)
    if not columns:
        return f"### {table}: NOT FOUND\n"
    lines = [f"### {table} ({len(columns)} columns)", ""]
    lines += ["| Column | Type | Null |", "|---|---|:-:|"]
    lines += [f"| `{c.name}` | {c.type} | {'yes' if c.nullable else 'no'} |" for c in columns]
    return "\n".join([*lines, ""])


def dump_code_table(database: Database, code_table: CodeTable) -> str:
    """Markdown of every (code, label) pair in a catalog table."""
    table, key, label = code_table.table, code_table.key, code_table.label
    if not key or not label:
        raise SpecError([f"code table {table!r} needs a key and a label column"])
    for identifier in (table, key, label):
        if not _IDENTIFIER.match(identifier):
            raise SpecError([f"{identifier!r} is not a plain identifier"])
    result = database.query(f"SELECT {key}, {label} FROM {table} ORDER BY 1")
    lines = [f"#### {table}", "", f"| {key} | {label} |", "|---|---|"]
    lines += [f"| {row[0]} | {row[1]} |" for row in result.rows]
    return "\n".join([*lines, ""])
