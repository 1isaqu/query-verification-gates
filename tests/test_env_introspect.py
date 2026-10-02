from __future__ import annotations

import pytest

from query_gates.env import load_env_file
from query_gates.errors import SpecError
from query_gates.gates.schema import CodeTable
from query_gates.introspect import describe_tables, dump_code_table


class TestEnvFile:
    def test_loads_without_overriding_and_never_returns_values(self, tmp_path):
        path = tmp_path / "settings.env"
        path.write_text(
            "# a comment\n\nQG_DB_HOST=db.example.invalid\nQG_DB_USER='report_reader'\n"
            'QG_DB_PASSWORD="change-me"\nQG_DB_PORT = 1444\nnot a pair\nQG_DB_SCHEMA=\n',
            encoding="utf-8",
        )
        environ = {"QG_DB_USER": "already-set"}
        added = load_env_file(path, environ)
        assert added == ["QG_DB_HOST", "QG_DB_PASSWORD", "QG_DB_PORT", "QG_DB_SCHEMA"]
        assert environ["QG_DB_USER"] == "already-set"
        assert environ["QG_DB_PASSWORD"] == "change-me"
        assert environ["QG_DB_PORT"] == "1444"
        assert environ["QG_DB_SCHEMA"] == ""

    def test_missing_file(self, tmp_path):
        with pytest.raises(SpecError, match="cannot read env file"):
            load_env_file(tmp_path / "absent.env", {})


class TestIntrospect:
    def test_describe_one_table(self, db):
        text = describe_tables(db, ["agreement_statuses"])
        assert text.startswith("### agreement_statuses (2 columns)")
        assert "| `status_id` | integer | yes |" in text
        assert "| `label` | text | yes |" in text

    def test_describe_all_tables_is_sorted_and_reports_unknown_tables(self, db):
        text = describe_tables(db)
        order = [line for line in text.splitlines() if line.startswith("### ")]
        assert order == sorted(order)
        assert any(line.startswith("### AGREEMENTS (") for line in order)
        assert "### ghosts: NOT FOUND" in describe_tables(db, ["ghosts"])

    def test_dump_code_table(self, db):
        text = dump_code_table(db, CodeTable("agreement_statuses", "status_id", "label"))
        assert "| status_id | label |" in text
        assert "| 24 | PENDING_EXCEPTION |" in text
        assert text.index("| 21 | ACTIVE |") < text.index("| 27 | PAID_MANUAL |")

    @pytest.mark.parametrize(
        "code_table",
        [CodeTable("agreement_statuses"), CodeTable("agreement_statuses", "status_id")],
    )
    def test_code_table_needs_key_and_label(self, db, code_table):
        with pytest.raises(SpecError, match="needs a key and a label"):
            dump_code_table(db, code_table)

    def test_identifiers_are_validated(self, db):
        with pytest.raises(SpecError, match="not a plain identifier"):
            dump_code_table(db, CodeTable("agreement_statuses; DROP TABLE x", "a", "b"))
