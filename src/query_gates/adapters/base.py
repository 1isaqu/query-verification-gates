"""The database abstraction the gates talk to, and a base class for DB-API drivers.

The gates only need five things from a database: run a read-only query, list tables, list columns,
describe one table and close. Anything that can do that can be verified. ``DBAPIDatabase`` covers
every PEP 249 driver whose catalog is exposed through ``INFORMATION_SCHEMA``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from query_gates.errors import AdapterError
from query_gates.sqlutil import DEFAULT_ALLOWED_STARTS, assert_read_only


class Cursor(Protocol):
    """The subset of a PEP 249 cursor this package uses."""

    description: Any

    def execute(self, operation: str, parameters: Sequence[Any] = ..., /) -> Any: ...

    def fetchall(self) -> list[Any]: ...

    def close(self) -> None: ...


class Connection(Protocol):
    """The subset of a PEP 249 connection this package uses."""

    def cursor(self) -> Cursor: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool


class Database(ABC):
    """What the gates need from a database. Implementations must only ever read."""

    dialect: str = "generic"

    @abstractmethod
    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        """Run one read-only statement and return the first result set that has columns."""

    @abstractmethod
    def list_tables(self) -> set[str]:
        """Upper-cased table (and view) names."""

    @abstractmethod
    def list_columns(self) -> set[str]:
        """Upper-cased ``TABLE.COLUMN`` for every column, fetched in one round trip."""

    @abstractmethod
    def describe_table(self, table: str) -> list[ColumnInfo]:
        """Columns of one table in ordinal order. Empty if the table does not exist."""

    @abstractmethod
    def close(self) -> None: ...

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any]:
        """Run a query that must return exactly one row; return it as ``{column: value}``."""
        result = self.query(sql, params)
        if len(result.rows) != 1:
            raise AdapterError(f"query must return exactly 1 row, got {len(result.rows)}")
        return dict(zip(result.columns, result.rows[0], strict=True))

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class DBAPIDatabase(Database):
    """A ``Database`` over any PEP 249 connection with an ``INFORMATION_SCHEMA`` catalog."""

    allowed_starts: tuple[str, ...] = DEFAULT_ALLOWED_STARTS
    allow_batches: bool = False

    def __init__(self, connection: Connection, *, schema: str | None = None) -> None:
        self._connection = connection
        self._schema = schema

    def _prepare(self, cursor: Cursor) -> None:
        """Hook run before every statement (e.g. to set an isolation level)."""

    def query(self, sql: str, params: Sequence[Any] = ()) -> QueryResult:
        assert_read_only(sql, allowed_starts=self.allowed_starts, allow_batches=self.allow_batches)
        cursor = self._connection.cursor()
        try:
            self._prepare(cursor)
            if params:
                cursor.execute(sql, tuple(params))
            else:
                cursor.execute(sql)
            return _collect(cursor)
        except AdapterError:
            raise
        except Exception as exc:  # driver exception types differ per driver
            raise AdapterError(f"query failed: {exc}") from exc
        finally:
            cursor.close()

    def list_tables(self) -> set[str]:
        sql, params = self._catalog_sql("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES")
        return {str(row[0]).upper() for row in self.query(sql, params).rows}

    def list_columns(self) -> set[str]:
        sql, params = self._catalog_sql(
            "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS"
        )
        return {f"{row[0]}.{row[1]}".upper() for row in self.query(sql, params).rows}

    def describe_table(self, table: str) -> list[ColumnInfo]:
        sql = (
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, "
            "NUMERIC_SCALE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE UPPER(TABLE_NAME) = UPPER(?)"
        )
        params: list[Any] = [table]
        if self._schema:
            sql += " AND UPPER(TABLE_SCHEMA) = UPPER(?)"
            params.append(self._schema)
        rows = self.query(sql + " ORDER BY ORDINAL_POSITION", params).rows
        return [
            ColumnInfo(
                str(r[0]), format_type(str(r[1]), r[2], r[3], r[4]), str(r[5]).upper() == "YES"
            )
            for r in rows
        ]

    def close(self) -> None:
        self._connection.close()

    def _catalog_sql(self, base: str) -> tuple[str, list[Any]]:
        if self._schema:
            return f"{base} WHERE UPPER(TABLE_SCHEMA) = UPPER(?)", [self._schema]
        return base, []


def _collect(cursor: Cursor) -> QueryResult:
    """Return the first result set that has columns, skipping row-count-only results."""
    while cursor.description is None:
        advance = getattr(cursor, "nextset", None)
        if advance is None or not advance():
            raise AdapterError("statement returned no result set")
    columns = tuple(str(d[0]) for d in cursor.description)
    return QueryResult(columns, [tuple(row) for row in cursor.fetchall()])


def format_type(
    data_type: str, char_length: int | None, precision: int | None, scale: int | None
) -> str:
    """Render an INFORMATION_SCHEMA type: ``varchar(40)``, ``varchar(max)``, ``decimal(12,2)``."""
    if char_length == -1:
        return f"{data_type}(max)"
    if char_length is not None:
        return f"{data_type}({char_length})"
    if data_type.lower() in ("decimal", "numeric") and precision is not None:
        return f"{data_type}({precision},{scale})"
    return data_type
