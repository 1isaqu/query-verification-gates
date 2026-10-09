from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from conftest import build_contract_connection
from query_gates.adapters import SQLiteDatabase
from query_gates.report import (
    GateError,
    Report,
    cell,
    fmt_number,
    fmt_pct,
    to_dict,
    to_markdown,
)
from query_gates.runner import run_verification
from query_gates.spec import parse_spec

GOOD_SQL = (
    "WITH ranked AS (SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.agreement_id, a.portfolio_id, "
    "a.installment_no ORDER BY a.issued_at DESC, a.row_id DESC) AS rn FROM agreements a "
    "WHERE a.issued_at >= ? AND a.issued_at < ? AND a.status_id IN (21, 23, 27)) "
    "SELECT SUM(amount) FROM ranked r JOIN agents ag ON ag.agent_id = r.agent_id "
    "WHERE r.rn = 1 AND ag.login NOT LIKE 'system%' AND ag.login NOT LIKE 'test%'"
)
PATH_A = "SELECT SUM(amount) AS total FROM agreements"
PATH_B = "SELECT SUM(x) FROM (SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)"
STATUS_BY_SCOPE = (
    "SELECT s.status_id AS code, s.label AS label, COUNT(*) AS n "
    "FROM agreements a JOIN agreement_statuses s ON s.status_id = a.status_id "
    "JOIN agreement_debts d ON d.agreement_id = a.agreement_id AND d.portfolio_id = a.portfolio_id "
    "JOIN debt_attributes da ON da.debt_id = d.debt_id WHERE da.portfolio_label = ? "
    "GROUP BY s.status_id, s.label ORDER BY s.status_id"
)
NOW = datetime(2026, 2, 1, 9, 30, 0, tzinfo=timezone.utc)
GATES = ("schema", "reconciliation", "ledger", "mappings")


def make_spec(**overrides):
    raw = {
        "analysis": "generated amount",
        "databases": ["main"],
        "schema": {
            "columns": ["agreements.amount", "agreements.status_id"],
            "code_tables": [{"table": "agreement_statuses", "key": "status_id", "label": "label"}],
        },
        "claims": [{"label": "total amount", "claimed": 780, "query_a": PATH_A, "query_b": PATH_B}],
        "ledgers": [
            {
                "name": "main query",
                "sql": GOOD_SQL,
                "declared": {
                    "installment_dedup": ["applied", "latest row per installment"],
                    "status_set": ["applied", "approved set"],
                    "date_window": ["applied", ""],
                    "excluded_actors": ["applied", ""],
                },
            }
        ],
        "mappings": [
            {
                "name": "status labels",
                "scopes": ["Portfolio A-01", "Portfolio A-02"],
                "query": STATUS_BY_SCOPE,
                "expected": {"Portfolio A-01": {"21": "ACTIVE", "23": "PAID"}},
            }
        ],
    }
    raw.update(overrides)
    return parse_spec(raw)


def run(spec, db):
    return run_verification(spec, {"main": db}, now=NOW)


def test_everything_passes(db):
    report = run(make_spec(), db)
    assert report.ok
    assert report.exit_code == 0
    assert report.failures() == []
    assert report.errors == []
    assert {g: report.gate_status(g) for g in GATES} == {
        "schema": "pass",
        "reconciliation": "pass",
        "ledger": "pass",
        "mappings": "pass",
    }
    assert report.as_of == "2026-02-01"
    assert report.generated_at == "2026-02-01T09:30:00+00:00"


def test_as_of_in_the_spec_wins_over_the_clock(db):
    report = run(make_spec(as_of="2026-01-31"), db)
    assert report.as_of == "2026-01-31"


def test_a_reconciliation_failure_is_a_verdict(db):
    path_b = "SELECT SUM(amount) FROM agreements WHERE status_id <> 25"
    claims = [{"label": "t", "query_a": PATH_A, "query_b": path_b}]
    report = run(make_spec(claims=claims), db)
    assert report.status == "fail"
    assert report.exit_code == 1
    assert report.gate_status("reconciliation") == "fail"
    assert report.failures() == [
        ("reconciliation", "t: path A and path B differ by 9.615% (tolerance 0.50%)")
    ]


def test_missing_column_fails_gate_one(db):
    schema = {"columns": ["agreements.no_such_column"]}
    report = run(make_spec(schema=schema), db)
    assert report.gate_status("schema") == "fail"
    assert "AGREEMENTS.NO_SUCH_COLUMN" in report.failures()[0][1]


def test_ledger_and_mapping_failures_block(db):
    ledgers = [{"name": "q", "sql": GOOD_SQL, "declared": {}}]
    expected = {"Portfolio A-01": {"21": "CLOSED"}}
    mappings = [
        {"name": "m", "scopes": ["Portfolio A-01"], "query": STATUS_BY_SCOPE, "expected": expected}
    ]
    report = run(make_spec(ledgers=ledgers, mappings=mappings), db)
    assert report.exit_code == 1
    gates = {gate for gate, _ in report.failures()}
    assert gates == {"ledger", "mappings"}


def test_a_query_that_cannot_run_makes_the_report_inconclusive(db):
    claims = [{"label": "t", "query_a": "SELECT SUM(x) FROM ghosts", "query_b": PATH_B}]
    report = run(make_spec(claims=claims), db)
    assert report.status == "error"
    assert report.exit_code == 3
    assert report.gate_status("reconciliation") == "error"
    assert report.errors[0].gate == "reconciliation"
    assert "no such table: ghosts" in report.errors[0].message
    assert report.gate_status("schema") == "pass"  # other gates still ran


def test_a_failure_outranks_an_error(db):
    claims = [{"label": "t", "query_a": "SELECT SUM(x) FROM ghosts", "query_b": PATH_B}]
    report = run(make_spec(claims=claims, schema={"columns": ["agreements.nope"]}), db)
    assert report.errors
    assert report.exit_code == 1


def test_write_statements_are_refused_and_nothing_changes(db):
    claims = [{"label": "t", "query_a": "DELETE FROM agreements", "query_b": PATH_B}]
    report = run(make_spec(claims=claims), db)
    assert report.exit_code == 3
    assert "read-only SQL only" in report.errors[0].message
    assert db.query_one("SELECT COUNT(*) AS n FROM agreements")["n"] == 6


def test_an_unopened_database_is_an_error(db):
    report = run_verification(make_spec(), {}, now=NOW)
    messages = [e.message for e in report.errors]
    assert "database 'main' was not opened" in messages
    assert report.exit_code == 3


def test_a_gate_that_is_not_exercised_fails_unless_waived(db):
    report = run(make_spec(mappings=[]), db)
    assert report.exit_code == 1
    assert report.failures() == [("mappings", "not exercised and not waived")]
    assert report.gate_status("mappings") == "not run"

    waived = run(make_spec(mappings=[], waivers={"mappings": "no code columns involved"}), db)
    assert waived.ok
    assert waived.gate_status("mappings") == "waived"


def test_schema_inference_is_bound_to_the_claims_database(db):
    claims = [
        {
            "label": "t",
            "query_a": "SELECT SUM(a.amount) FROM agreements a WHERE a.nonexistent = 1",
            "query_b": PATH_B,
        }
    ]
    schema = {"infer_from_queries": True}
    report = run(make_spec(claims=claims, schema=schema), db)
    assert "AGREEMENTS.NONEXISTENT" in report.failures()[0][1]


def test_schema_runs_once_per_database(db):
    other = SQLiteDatabase(build_contract_connection())
    spec = make_spec(databases=["main", "second"])
    spec.claims[0].database = "main"
    spec.mappings[0].database = "main"
    report = run_verification(spec, {"main": db, "second": other}, now=NOW)
    assert [s.database for s in report.schema] == ["main", "second"]
    other.close()


class TestRendering:
    def test_markdown_carries_the_protocol_output_contract(self, db):
        text = to_markdown(run(make_spec(), db))
        assert text.startswith("# Verification: generated amount")
        assert "**Result: PASS** (exit code 0)" in text
        assert "As of 2026-02-01; generated 2026-02-01T09:30:00+00:00." in text
        assert "## Gate 1: schema" in text
        assert "- main: 3 references checked, all present." in text
        assert "| total amount | main | 780 | 780 | 0.000% | 0.500% | 780 (0.000%) | ok |" in text
        assert "| Installment dedup | choice | applied | found | ok: latest row per" in text
        assert "**Portfolio A-01**" in text
        assert "| 21 | ACTIVE | 3 |" in text
        assert "labeled differently" not in text  # same labels in both scopes

    def test_failures_and_errors_are_listed(self, db):
        claims = [{"label": "t", "query_a": "SELECT SUM(x) FROM ghosts", "query_b": PATH_B}]
        text = to_markdown(run(make_spec(claims=claims, mappings=[]), db))
        assert "**Result: FAIL: do not report the numbers**" in text
        assert "## Failures" in text
        assert "- mappings: not exercised and not waived" in text
        assert "## Errors (gate could not be evaluated)" in text
        assert "- reconciliation / t:" in text

    def test_waivers_are_shown(self, db):
        text = to_markdown(run(make_spec(mappings=[], waivers={"mappings": "n/a here"}), db))
        assert "Waived: n/a here" in text

    def test_json_is_complete_and_serializable(self, db):
        data = to_dict(run(make_spec(), db))
        round_tripped = json.loads(json.dumps(data))
        assert round_tripped["status"] == "pass"
        assert round_tripped["gates"]["ledger"] == "pass"
        rec = round_tripped["reconciliations"][0]
        assert (rec["a"], rec["b"], rec["claimed"]) == (780.0, 780.0, 780.0)
        assert round_tripped["mappings"][0]["per_scope"]["Portfolio A-01"][0]["label"] == "ACTIVE"
        assert round_tripped["ledgers"][0]["rows"][0]["key"] == "read_only_hint"
        assert round_tripped["errors"] == []

    def test_a_ledger_with_no_applicable_rule_says_so(self, db):
        ledgers = [{"name": "plain", "sql": "SELECT 1", "declared": {}}]
        assert "No registered filter applies to this query." in to_markdown(
            run(make_spec(ledgers=ledgers), db)
        )

    def test_divergent_scopes_show_up_as_a_warning_line(self, db):
        query = (
            "SELECT 1 AS code, "
            "CASE WHEN ? = 'Portfolio A-01' THEN 'Promise' ELSE 'Callback' END AS label"
        )
        mappings = [{"name": "m", "scopes": ["Portfolio A-01", "Portfolio A-02"], "query": query}]
        text = to_markdown(run(make_spec(mappings=mappings), db))
        assert "- WARN: code 1 is labeled differently across scopes" in text


class TestHelpers:
    @pytest.mark.parametrize(
        ("value", "text"),
        [(780.0, "780"), (0.123456, "0.1235"), (1234567.891, "1,234,567.891"), (0.0, "0")],
    )
    def test_fmt_number(self, value, text):
        assert fmt_number(value) == text

    def test_fmt_pct_and_cell(self):
        assert fmt_pct(0.00496) == "0.496%"
        assert cell("a|b\nc") == "a\\|b c"

    def test_exit_codes(self):
        base = {"analysis": "x", "as_of": "2026-01-01", "generated_at": "t"}
        waivers = dict.fromkeys(GATES, "r")
        assert Report(**base, waivers=waivers).exit_code == 0
        errored = Report(**base, waivers=waivers, errors=[GateError("schema", "s", "m")])
        assert errored.exit_code == 3
        assert Report(**base).exit_code == 1  # nothing exercised, nothing waived


class TestWarnings:
    def warned_report(self, db):
        ledgers = [
            {
                "name": "anti-join dedup",
                "sql": GOOD_SQL.replace("ROW_NUMBER()", "COUNT(*)"),
                "declared": {
                    "installment_dedup": ["applied", "anti-join"],
                    "status_set": "applied",
                    "date_window": "applied",
                    "excluded_actors": "applied",
                },
            }
        ]
        return run(make_spec(ledgers=ledgers), db)

    def test_warnings_do_not_block_but_are_listed(self, db):
        report = self.warned_report(db)
        assert report.ok
        assert report.warnings() == [
            (
                "ledger",
                "anti-join dedup: Installment dedup: declared applied, detector did not find it",
            )
        ]
        text = to_markdown(report)
        assert "**Result: PASS** (exit code 0), 1 warning(s) listed at the end" in text
        assert "## Warnings (non-blocking)" in text
        assert to_dict(report)["warnings"][0]["gate"] == "ledger"

    def test_clean_report_has_no_warning_section(self, db):
        text = to_markdown(run(make_spec(), db))
        assert "Warnings" not in text
        assert "warning(s)" not in text

    def test_blocks_are_separated_by_blank_lines(self, db):
        lines = to_markdown(self.warned_report(db)).splitlines()
        for number, line in enumerate(lines):
            if line.startswith("### ") and number:
                assert lines[number - 1] == "", f"no blank line before {line!r}"
