from __future__ import annotations

import sqlite3

import pytest

from conftest import build_contract_connection
from query_gates.adapters import SQLiteDatabase, open_database
from query_gates.errors import AdapterError, SpecError
from query_gates.sqlutil import ReadOnlyViolation


def test_query_returns_columns_and_rows(db):
    sql = "SELECT status_id, label FROM agreement_statuses WHERE status_id <= ? ORDER BY 1"
    result = db.query(sql, [22])
    assert result.columns == ("status_id", "label")
    assert result.rows == [(21, "ACTIVE"), (22, "BROKEN")]


def test_query_one_requires_exactly_one_row(db):
    assert db.query_one("SELECT COUNT(*) AS n FROM agreements") == {"n": 6}
    with pytest.raises(AdapterError, match="exactly 1 row, got 2"):
        db.query_one("SELECT status_id FROM agreement_statuses WHERE status_id < 23")
    with pytest.raises(AdapterError, match="exactly 1 row, got 0"):
        db.query_one("SELECT status_id FROM agreement_statuses WHERE status_id < 0")


def test_catalog_introspection(db):
    assert "AGREEMENTS" in db.list_tables()
    columns = db.list_columns()
    assert "AGREEMENTS.INSTALLMENT_NO" in columns
    assert "AGENTS.LOGIN" in columns
    assert "AGREEMENTS.NOT_A_COLUMN" not in columns


def test_describe_table_in_ordinal_order(db):
    info = db.describe_table("agreement_statuses")
    assert [c.name for c in info] == ["status_id", "label"]
    assert info[0].type == "integer"
    assert db.describe_table("no_such_table") == []


def test_guard_blocks_writes_before_the_engine_sees_them(db):
    with pytest.raises(ReadOnlyViolation):
        db.query("DELETE FROM agreements")
    assert db.query_one("SELECT COUNT(*) AS n FROM agreements")["n"] == 6


def test_engine_level_read_only_even_when_the_guard_is_bypassed():
    conn = build_contract_connection()
    SQLiteDatabase(conn)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM agreements")


def test_from_path_opens_the_file_read_only(tmp_path):
    path = tmp_path / "gates.db"
    writer = sqlite3.connect(path)
    writer.execute("CREATE TABLE t (x INTEGER)")
    writer.execute("INSERT INTO t VALUES (7)")
    writer.commit()
    writer.close()

    database = SQLiteDatabase.from_path(path)
    try:
        assert database.query_one("SELECT x FROM t") == {"x": 7}
        with pytest.raises(sqlite3.OperationalError):
            database._connection.execute("INSERT INTO t VALUES (8)")
        # mode=ro must hold even when the connection-level pragma is switched off
        database._connection.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database._connection.execute("INSERT INTO t VALUES (8)")
    finally:
        database.close()


def test_from_path_missing_file(tmp_path):
    with pytest.raises(AdapterError, match="not found"):
        SQLiteDatabase.from_path(tmp_path / "absent.db")


def test_context_manager_closes(tmp_path):
    path = tmp_path / "ctx.db"
    sqlite3.connect(path).close()
    with SQLiteDatabase.from_path(path) as database:
        assert database.list_tables() == set()
    with pytest.raises(sqlite3.ProgrammingError):
        database._connection.execute("SELECT 1")


def test_open_database_factory(tmp_path):
    path = tmp_path / "f.db"
    sqlite3.connect(path).close()
    assert isinstance(open_database("sqlite", str(path)), SQLiteDatabase)
    with pytest.raises(SpecError, match="unknown adapter"):
        open_database("oracle", "x")


def test_driver_errors_are_wrapped(db):
    with pytest.raises(AdapterError, match="query failed: no such table: ghosts"):
        db.query("SELECT * FROM ghosts")
