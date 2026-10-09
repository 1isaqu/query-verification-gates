from __future__ import annotations

import json
import sqlite3
import sys

import pytest

from conftest import build_contract_connection
from query_gates import __version__
from query_gates.cli import main

PATH_A = "SELECT SUM(amount) AS total FROM agreements"
PATH_B = "SELECT SUM(x) FROM (SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)"
LEDGER_SQL = (
    "SELECT SUM(a.amount) FROM agreements a WHERE a.status_id IN (21, 23) "
    "AND a.issued_at >= ? AND a.issued_at < ?"
)
SPEC = {
    "analysis": "cli smoke",
    "databases": ["main"],
    "schema": {"columns": ["agreements.amount"]},
    "claims": [{"label": "total", "query_a": PATH_A, "query_b": PATH_B}],
    "ledgers": [
        {
            "name": "q",
            "sql": LEDGER_SQL,
            "declared": {
                "installment_dedup": ["omitted", "counts rows, not installments"],
                "status_set": "applied",
                "date_window": "applied",
            },
        }
    ],
    "mappings": [
        {
            "name": "labels",
            "scopes": ["Portfolio A-01"],
            "query": "SELECT status_id AS code, label FROM agreement_statuses WHERE ? <> ''",
        }
    ],
}


@pytest.fixture
def workspace(tmp_path):
    db_path = tmp_path / "gates.db"
    disk = sqlite3.connect(db_path)
    build_contract_connection().backup(disk)
    disk.close()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(SPEC), encoding="utf-8")
    return tmp_path, db_path, spec_path


def write_spec(path, **overrides):
    path.write_text(json.dumps({**SPEC, **overrides}), encoding="utf-8")


def run_cli(*argv, environ=None):
    return main([str(a) for a in argv], environ={} if environ is None else environ)


class TestRun:
    def test_pass_prints_markdown_and_exits_zero(self, workspace, capsys):
        _, db_path, spec_path = workspace
        assert run_cli("run", spec_path, "--db", f"main={db_path}") == 0
        out = capsys.readouterr().out
        assert "**Result: PASS** (exit code 0)" in out
        assert "| total | main | 780 | 780 |" in out

    def test_json_format(self, workspace, capsys):
        _, db_path, spec_path = workspace
        assert run_cli("run", spec_path, "--db", f"main={db_path}", "--format", "json") == 0
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "pass"
        assert data["reconciliations"][0]["a"] == 780.0

    def test_output_file(self, workspace, capsys):
        tmp_path, db_path, spec_path = workspace
        target = tmp_path / "report.md"
        assert run_cli("run", spec_path, "--db", f"main={db_path}", "--output", target) == 0
        assert target.read_text(encoding="utf-8").startswith("# Verification: cli smoke")
        assert f"PASS: report written to {target}" in capsys.readouterr().out

    def test_gate_failure_exits_one(self, workspace, capsys):
        _, db_path, spec_path = workspace
        bad = "SELECT SUM(amount) FROM agreements WHERE status_id <> 25"
        write_spec(spec_path, claims=[{"label": "t", "query_a": PATH_A, "query_b": bad}])
        assert run_cli("run", spec_path, "--db", f"main={db_path}") == 1
        assert "FAIL: do not report the numbers" in capsys.readouterr().out

    def test_inconclusive_exits_three(self, workspace, capsys):
        _, db_path, spec_path = workspace
        ghost = {"label": "t", "query_a": "SELECT SUM(x) FROM ghosts", "query_b": PATH_B}
        write_spec(spec_path, claims=[ghost])
        assert run_cli("run", spec_path, "--db", f"main={db_path}") == 3
        assert "INCONCLUSIVE" in capsys.readouterr().out

    def test_bad_spec_exits_two_and_lists_every_problem(self, workspace, capsys):
        _, db_path, spec_path = workspace
        write_spec(spec_path, color="red", claims=[{"query_a": PATH_A}])
        assert run_cli("run", spec_path, "--db", f"main={db_path}") == 2
        err = capsys.readouterr().err
        assert "unknown keys ['color']" in err
        assert "claims[0]: 'label' is required" in err

    def test_missing_db_mapping_exits_two(self, workspace, capsys):
        _, _, spec_path = workspace
        assert run_cli("run", spec_path) == 2
        assert "no --db main=PATH given" in capsys.readouterr().err

    def test_malformed_db_option_exits_two(self, workspace, capsys):
        _, _, spec_path = workspace
        assert run_cli("run", spec_path, "--db", "main") == 2
        assert "--db expects NAME=TARGET" in capsys.readouterr().err

    def test_unopenable_database_exits_three(self, workspace, capsys):
        tmp_path, _, spec_path = workspace
        assert run_cli("run", spec_path, "--db", f"main={tmp_path / 'absent.db'}") == 3
        assert "SQLite database not found" in capsys.readouterr().err

    def test_adapter_comes_from_the_environment_and_the_env_file(
        self, workspace, capsys, monkeypatch
    ):
        tmp_path, _, spec_path = workspace
        monkeypatch.setitem(sys.modules, "pyodbc", None)
        assert run_cli("run", spec_path, environ={"QG_ADAPTER": "sqlserver"}) == 3
        assert "needs pyodbc" in capsys.readouterr().err

        env_file = tmp_path / "settings.env"
        env_file.write_text("QG_ADAPTER=sqlserver\n", encoding="utf-8")
        assert run_cli("run", spec_path, "--env-file", env_file) == 3
        assert "needs pyodbc" in capsys.readouterr().err

    def test_missing_env_file_exits_two(self, workspace, capsys):
        tmp_path, db_path, spec_path = workspace
        missing = tmp_path / "x.env"
        code = run_cli("run", spec_path, "--db", f"main={db_path}", "--env-file", missing)
        assert code == 2
        assert "cannot read env file" in capsys.readouterr().err


class TestLedger:
    def test_declared_query_passes(self, workspace, capsys):
        tmp_path, *_ = workspace
        sql = tmp_path / "q.sql"
        sql.write_text(LEDGER_SQL, encoding="utf-8")
        code = run_cli(
            "ledger", sql,
            "--declare", "status_set=applied",
            "--declare", "date_window=applied:window is the reporting month",
            "--declare", "installment_dedup=omitted:row counts only",
        )  # fmt: skip
        assert code == 0
        out = capsys.readouterr().out
        assert "| Half-open date window | choice | applied | found |" in out

    def test_undeclared_choices_exit_one(self, workspace, capsys):
        tmp_path, *_ = workspace
        sql = tmp_path / "q.sql"
        sql.write_text(LEDGER_SQL, encoding="utf-8")
        assert run_cli("ledger", sql) == 1
        assert "UNDECLARED" in capsys.readouterr().out

    def test_json_dialect_and_registry_from_spec(self, workspace, capsys):
        tmp_path, _, spec_path = workspace
        sql = tmp_path / "q.sql"
        sql.write_text("SELECT 1 FROM agents", encoding="utf-8")
        assert run_cli("ledger", sql, "--dialect", "sqlserver", "--format", "json") == 1
        rows = {r["key"]: r["status"] for r in json.loads(capsys.readouterr().out)["rows"]}
        assert rows["read_only_hint"] == "FAIL"

        write_spec(spec_path, registry={"builtin": False})
        assert run_cli("ledger", sql, "--spec", spec_path) == 0

    @pytest.mark.parametrize("declare", ["status_set", "status_set=maybe", "=applied"])
    def test_bad_declare_exits_two(self, workspace, capsys, declare):
        tmp_path, *_ = workspace
        sql = tmp_path / "q.sql"
        sql.write_text("SELECT 1", encoding="utf-8")
        assert run_cli("ledger", sql, "--declare", declare) == 2
        assert "--declare expects" in capsys.readouterr().err

    def test_unreadable_sql_exits_two(self, workspace, capsys):
        tmp_path, *_ = workspace
        assert run_cli("ledger", tmp_path / "absent.sql") == 2
        assert "cannot read" in capsys.readouterr().err


class TestSchemaAndIntrospect:
    def test_schema_pass_and_fail(self, workspace, capsys):
        tmp_path, db_path, _ = workspace
        sql = tmp_path / "q.sql"
        sql.write_text(
            "SELECT a.amount FROM agreements a JOIN agents g ON g.agent_id = a.agent_id", "utf-8"
        )
        assert run_cli("schema", sql, "--db", f"main={db_path}") == 0
        out = capsys.readouterr().out
        assert "inferred from SQL (advisory): 2 table(s), 3 column(s)" in out
        assert "main: 5 references checked, all present" in out

        sql.write_text("SELECT a.no_such_column FROM agreements a", encoding="utf-8")
        code = run_cli("schema", sql, "--db", f"main={db_path}", "--code-table", "ghosts")
        assert code == 1
        assert "MISSING AGREEMENTS.NO_SUCH_COLUMN, GHOSTS (code table)" in capsys.readouterr().out

    def test_schema_needs_a_database(self, workspace, capsys):
        tmp_path, *_ = workspace
        sql = tmp_path / "q.sql"
        sql.write_text("SELECT 1", encoding="utf-8")
        assert run_cli("schema", sql) == 2
        assert "at least one --db" in capsys.readouterr().err

    def test_introspect(self, workspace, capsys):
        _, db_path, _ = workspace
        code = run_cli(
            "introspect", "--db", f"main={db_path}", "--table", "agents",
            "--code-table", "agreement_statuses:status_id:label",
        )  # fmt: skip
        assert code == 0
        out = capsys.readouterr().out
        assert "# main" in out
        assert "### agents (3 columns)" in out
        assert "| 23 | PAID |" in out

    def test_bad_code_table_exits_two(self, workspace, capsys):
        _, db_path, _ = workspace
        assert run_cli("introspect", "--db", f"main={db_path}", "--code-table", "a:b") == 2
        assert "--code-table expects" in capsys.readouterr().err


class TestMappings:
    QUERY = "SELECT status_id AS code, label FROM agreement_statuses WHERE ? <> ''"

    @pytest.fixture
    def query_file(self, workspace):
        tmp_path, *_ = workspace
        path = tmp_path / "labels.sql"
        path.write_text(self.QUERY, encoding="utf-8")
        return path

    def test_labels_are_derived_per_scope(self, workspace, query_file, capsys):
        _, db_path, _ = workspace
        code = run_cli("mappings", query_file, "--db", f"main={db_path}", "--scope", "S1",
                       "--scope", "S2")  # fmt: skip
        assert code == 0
        out = capsys.readouterr().out
        assert "### labels.sql (main): re-derived per scope" in out
        assert "**S1**" in out
        assert "**S2**" in out
        assert "| 21 | ACTIVE |" in out

    def test_an_expected_mapping_that_differs_exits_one(self, workspace, query_file, capsys):
        tmp_path, db_path, _ = workspace
        expected = tmp_path / "expected.json"
        expected.write_text(json.dumps({"S1": {"21": "BROKEN", "23": "paid"}}), encoding="utf-8")
        code = run_cli("mappings", query_file, "--db", f"main={db_path}", "--scope", "S1",
                       "--expected", expected)  # fmt: skip
        assert code == 1
        out = capsys.readouterr().out
        assert "ERROR: S1: code 21 expected 'BROKEN', catalog says 'ACTIVE'" in out
        assert "code 23" not in out  # labels compare without regard to case

    def test_json_format_and_two_databases(self, workspace, query_file, capsys):
        _, db_path, _ = workspace
        code = run_cli("mappings", query_file, "--db", f"one={db_path}", "--db", f"two={db_path}",
                       "--scope", "S1", "--format", "json")  # fmt: skip
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert [item["database"] for item in data] == ["one", "two"]
        assert data[0]["per_scope"]["S1"][0] == {"code": "21", "label": "ACTIVE"}

    def test_an_empty_scope_is_a_warning_unless_rows_are_required(
        self, workspace, tmp_path, capsys
    ):
        _, db_path, _ = workspace
        empty = tmp_path / "empty.sql"
        empty.write_text("SELECT status_id AS code, label FROM agreement_statuses WHERE ? = 'x'",
                         encoding="utf-8")  # fmt: skip
        args = ("mappings", empty, "--db", f"main={db_path}", "--scope", "S1")
        assert run_cli(*args) == 0
        assert "WARN: S1: no rows returned" in capsys.readouterr().out
        assert run_cli(*args, "--require-rows") == 1
        assert "ERROR: S1: no rows returned" in capsys.readouterr().out

    def test_problems_with_the_input_exit_two(self, workspace, query_file, tmp_path, capsys):
        _, db_path, _ = workspace
        db = ("--db", f"main={db_path}")
        assert run_cli("mappings", query_file, "--scope", "S1") == 2
        assert "at least one --db" in capsys.readouterr().err

        other_scope = tmp_path / "other.json"
        other_scope.write_text(json.dumps({"S9": {"21": "ACTIVE"}}), encoding="utf-8")
        code = run_cli("mappings", query_file, *db, "--scope", "S1", "--expected", other_scope)
        assert code == 2
        assert "names scope 'S9' that is not in 'scopes'" in capsys.readouterr().err

        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert run_cli("mappings", query_file, *db, "--scope", "S1", "--expected", broken) == 2
        assert "is not valid JSON" in capsys.readouterr().err

        wrong_columns = tmp_path / "wrong.sql"
        wrong_columns.write_text("SELECT status_id, label FROM agreement_statuses WHERE ? <> ''",
                                 encoding="utf-8")  # fmt: skip
        assert run_cli("mappings", wrong_columns, *db, "--scope", "S1") == 2
        assert "must return 'code' and 'label'" in capsys.readouterr().err

    def test_a_scope_is_required(self, query_file):
        with pytest.raises(SystemExit) as info:
            run_cli("mappings", query_file, "--db", "main=x.db")
        assert info.value.code == 2


class TestMisc:
    def test_rules_lists_the_registry(self, capsys):
        assert run_cli("rules") == 0
        out = capsys.readouterr().out
        assert "| read_only_hint | assert | sqlserver |" in out
        assert "| excluded_actors | choice | all |" in out

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as info:
            run_cli("--version")
        assert info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_missing_subcommand_is_a_usage_error(self):
        with pytest.raises(SystemExit) as info:
            run_cli()
        assert info.value.code == 2

    def test_unexpected_errors_exit_four_with_a_traceback(self, monkeypatch, capsys):
        def boom(*args):
            raise RuntimeError("kaboom")

        monkeypatch.setattr("query_gates.cli._registry", boom)
        assert run_cli("rules") == 4
        assert "RuntimeError: kaboom" in capsys.readouterr().err

    def test_module_entry_point(self, workspace):
        import subprocess

        _, db_path, spec_path = workspace
        done = subprocess.run(
            [sys.executable, "-m", "query_gates", "run", str(spec_path), "--db", f"main={db_path}"],
            capture_output=True, text=True, encoding="utf-8",
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert "Result: PASS" in done.stdout
