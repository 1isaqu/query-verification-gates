"""The SQL Server adapter is exercised through a fake DB-API connection: no driver, no server."""

from __future__ import annotations

import sys

import pytest

from query_gates.adapters import SQLServerDatabase, SQLServerSettings, open_database
from query_gates.adapters.sqlserver import _odbc_value, pyodbc_connector
from query_gates.errors import AdapterError, AdapterUnavailable
from query_gates.sqlutil import ReadOnlyViolation


class FakeCursor:
    def __init__(self, log, results):
        self._log = log
        self._results = list(results)
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self._log.append((sql, params))
        if sql.startswith("SET "):
            self.description, self._rows = None, []
            return
        columns, rows = self._results.pop(0)
        self.description = [(name,) for name in columns] if columns else None
        self._rows = rows

    def nextset(self):
        return False

    def fetchall(self):
        return self._rows

    def close(self):
        self._log.append(("<cursor closed>", None))


class FakeConnection:
    def __init__(self, results=()):
        self.log = []
        self.results = list(results)
        self.closed = False

    def cursor(self):
        return FakeCursor(self.log, self.results)

    def close(self):
        self.closed = True


def make(results=(), **settings):
    conn = FakeConnection(results)
    return SQLServerDatabase(conn, SQLServerSettings(**settings)), conn


class TestSettings:
    def test_defaults_and_trusted_connection(self):
        text = SQLServerSettings().connection_string("SalesDb")
        assert text == (
            "DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;DATABASE=SalesDb;"
            "Trusted_Connection=yes;TrustServerCertificate=no"
        )

    def test_user_password_port_and_trust(self):
        env = {
            "QG_DB_HOST": "db.example.invalid",
            "QG_DB_PORT": "1444",
            "QG_DB_USER": "report_reader",
            "QG_DB_PASSWORD": "change-me",
            "QG_DB_TRUST_SERVER_CERT": "yes",
        }
        text = SQLServerSettings.from_env(env).connection_string("SalesDb")
        assert "SERVER=db.example.invalid,1444" in text
        assert "UID=report_reader;PWD=change-me" in text
        assert "Trusted_Connection" not in text
        assert text.endswith("TrustServerCertificate=yes")

    def test_password_is_not_in_repr(self):
        assert "change-me" not in repr(SQLServerSettings(password="change-me"))

    def test_values_with_separators_are_braced(self):
        assert _odbc_value("pa;ss}word") == "{pa;ss}}word}"
        assert _odbc_value(" padded ") == "{ padded }"
        assert _odbc_value("plain") == "plain"

    def test_bad_values_are_rejected(self):
        with pytest.raises(AdapterError, match="QG_DB_ISOLATION"):
            SQLServerSettings.from_env({"QG_DB_ISOLATION": "NO LOCKS"})
        with pytest.raises(AdapterError, match="QG_DB_PORT must be an integer"):
            SQLServerSettings.from_env({"QG_DB_PORT": "abc"})


class TestQueries:
    def test_isolation_is_set_before_every_statement(self):
        database, conn = make([(("n",), [(5,)])], isolation="SNAPSHOT")
        assert database.query_one("SELECT COUNT(*) AS n FROM agreements") == {"n": 5}
        statements = [sql for sql, _ in conn.log]
        assert statements[0] == "SET TRANSACTION ISOLATION LEVEL SNAPSHOT"
        assert statements[1] == "SELECT COUNT(*) AS n FROM agreements"
        assert statements[-1] == "<cursor closed>"

    def test_parameters_are_passed_as_a_tuple(self):
        database, conn = make([(("x",), [(1,)])])
        database.query("SELECT 1 AS x WHERE ? = ?", [1, 1])
        assert conn.log[1] == ("SELECT 1 AS x WHERE ? = ?", (1, 1))

    def test_declare_batch_is_allowed_writes_are_not(self):
        database, conn = make([(("d",), [(1,)])])
        database.query("DECLARE @from DATE = '2026-01-01'; SELECT 1 AS d")
        n_logged = len(conn.log)
        with pytest.raises(ReadOnlyViolation):
            database.query("DECLARE @x INT; UPDATE agreements SET amount = 0")
        with pytest.raises(ReadOnlyViolation):
            database.query("EXEC sp_who")
        assert len(conn.log) == n_logged  # nothing reached the cursor

    def test_catalogue_queries_use_information_schema(self):
        database, conn = make(
            [(("TABLE_NAME", "COLUMN_NAME"), [("agreements", "amount"), ("agents", "login")]),
             (("TABLE_NAME",), [("agreements",), ("agents",)])]
        )  # fmt: skip
        assert database.list_columns() == {"AGREEMENTS.AMOUNT", "AGENTS.LOGIN"}
        assert database.list_tables() == {"AGREEMENTS", "AGENTS"}
        issued = [sql for sql, _ in conn.log if sql.startswith("SELECT")]
        assert issued[0] == "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS"
        assert issued[1] == "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"

    def test_schema_filter_is_parameterised(self):
        database, conn = make([(("TABLE_NAME",), [("t",)])], schema="reporting")
        database.list_tables()
        sql, params = conn.log[1]
        assert sql.endswith("WHERE UPPER(TABLE_SCHEMA) = UPPER(?)")
        assert params == ("reporting",)

    def test_describe_table_formats_types(self):
        rows = [("name", "varchar", 40, None, None, "NO"),
                ("note", "varchar", -1, None, None, "YES"),
                ("amount", "decimal", None, 12, 2, "NO"),
                ("n", "int", None, 10, 0, "YES")]  # fmt: skip
        columns = ("COLUMN_NAME", "DATA_TYPE", "L", "P", "S", "IS_NULLABLE")
        database, _ = make([(columns, rows)])
        info = database.describe_table("agreements")
        assert [(c.name, c.type, c.nullable) for c in info] == [
            ("name", "varchar(40)", False),
            ("note", "varchar(max)", True),
            ("amount", "decimal(12,2)", False),
            ("n", "int", True),
        ]

    def test_close_closes_the_connection(self):
        database, conn = make()
        database.close()
        assert conn.closed


class TestConnect:
    def test_connector_receives_the_connection_string(self):
        seen = {}

        def connector(text, settings):
            seen["text"] = text
            return FakeConnection()

        settings = SQLServerSettings(user="report_reader", password="change-me")
        database = SQLServerDatabase.connect("SalesDb", settings, connector=connector)
        assert "DATABASE=SalesDb" in seen["text"]
        assert database.dialect == "sqlserver"

    def test_driver_errors_are_wrapped_and_the_password_is_scrubbed(self):
        def connector(text, settings):
            raise RuntimeError(f"login failed for {text}")

        settings = SQLServerSettings(user="report_reader", password="change-me-please")
        with pytest.raises(AdapterError) as info:
            SQLServerDatabase.connect("SalesDb", settings, connector=connector)
        assert "change-me-please" not in str(info.value)
        assert "***" in str(info.value)

    def test_missing_pyodbc_gives_an_install_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyodbc", None)
        with pytest.raises(AdapterUnavailable, match=r"query-verification-gates\[sqlserver\]"):
            pyodbc_connector("DRIVER={x}", SQLServerSettings())

    def test_factory_reads_the_environment(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyodbc", None)
        with pytest.raises(AdapterUnavailable):
            open_database("sqlserver", "SalesDb", env={"QG_DB_HOST": "db.example.invalid"})
