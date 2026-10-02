"""Database adapters and the factory the CLI uses to open them."""

from __future__ import annotations

import os
from collections.abc import Mapping

from query_gates.adapters.base import (
    ColumnInfo,
    Connection,
    Cursor,
    Database,
    DBAPIDatabase,
    QueryResult,
)
from query_gates.adapters.sqlite import SQLiteDatabase
from query_gates.adapters.sqlserver import SQLServerDatabase, SQLServerSettings
from query_gates.errors import SpecError

ADAPTERS = ("sqlite", "sqlserver")

__all__ = [
    "ADAPTERS",
    "ColumnInfo",
    "Connection",
    "Cursor",
    "DBAPIDatabase",
    "Database",
    "QueryResult",
    "SQLServerDatabase",
    "SQLServerSettings",
    "SQLiteDatabase",
    "open_database",
]


def open_database(
    adapter: str, target: str, *, env: Mapping[str, str] | None = None
) -> Database:
    """Open ``target`` with the named adapter.

    ``target`` is a file path for ``sqlite`` and a database name for ``sqlserver`` (server and
    credentials come from the ``QG_DB_*`` environment variables).
    """
    if adapter == "sqlite":
        return SQLiteDatabase.from_path(target)
    if adapter == "sqlserver":
        settings = SQLServerSettings.from_env(os.environ if env is None else env)
        return SQLServerDatabase.connect(target, settings)
    raise SpecError([f"unknown adapter {adapter!r}, expected one of {ADAPTERS}"])
