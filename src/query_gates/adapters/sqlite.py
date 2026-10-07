"""SQLite adapter (standard library). Used by the tests, the demo and for verifying exports."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from query_gates.adapters.base import ColumnInfo, DBAPIDatabase
from query_gates.errors import AdapterError


class SQLiteDatabase(DBAPIDatabase):
    """Read-only SQLite access.

    The constructor turns ``PRAGMA query_only`` on for the connection it is given, so write data
    first and wrap the connection afterwards. ``from_path`` opens the file with ``mode=ro`` as well,
    which makes the engine itself refuse writes.
    """

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA query_only = ON")
        super().__init__(connection)

    @classmethod
    def from_path(cls, path: str | Path) -> SQLiteDatabase:
        target = Path(path)
        if not target.is_file():
            raise AdapterError(f"SQLite database not found: {target}")
        uri = f"{target.resolve().as_uri()}?mode=ro"
        try:
            return cls(sqlite3.connect(uri, uri=True))
        except sqlite3.Error as exc:
            raise AdapterError(f"cannot open SQLite database {target}: {exc}") from exc

    def list_table_names(self) -> list[str]:
        sql = "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        return sorted((str(row[0]) for row in self.query(sql).rows), key=str.upper)

    def list_columns(self) -> set[str]:
        sql = (
            "SELECT m.name, p.name FROM sqlite_master m, pragma_table_info(m.name) p "
            "WHERE m.type IN ('table', 'view')"
        )
        return {f"{row[0]}.{row[1]}".upper() for row in self.query(sql).rows}

    def describe_table(self, table: str) -> list[ColumnInfo]:
        sql = 'SELECT name, type, "notnull" FROM pragma_table_info(?) ORDER BY cid'
        rows: Sequence[Any] = self.query(sql, [table]).rows
        return [ColumnInfo(str(r[0]), str(r[1]).lower() or "any", not r[2]) for r in rows]
