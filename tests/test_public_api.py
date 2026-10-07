"""The Python snippets shown in the README, executed."""

from __future__ import annotations

import query_gates
from query_gates import (
    FilterRule,
    SQLiteDatabase,
    builtin_registry,
    evaluate_ledger,
    parse_spec,
    reconcile,
    run_verification,
    to_markdown,
)
from query_gates.registry import ASSERT, matches_all, touches


def test_every_exported_name_exists():
    for name in query_gates.__all__:
        assert hasattr(query_gates, name), name


def test_run_a_spec_from_python(db):
    spec = parse_spec(
        {
            "analysis": "api smoke",
            "claims": [
                {
                    "label": "total amount",
                    "query_a": "SELECT SUM(amount) AS total FROM agreements",
                    "query_b": (
                        "SELECT SUM(x) FROM "
                        "(SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)"
                    ),
                }
            ],
            "waivers": {
                "schema": "covered elsewhere",
                "ledger": "no production query to check",
                "mappings": "no catalog involved",
            },
        }
    )
    report = run_verification(spec, {"main": db})
    assert report.exit_code == 0
    assert "| total amount | main | 780 | 780 |" in to_markdown(report)


def test_reconcile_directly(db):
    result = reconcile(
        db,
        "total",
        "SELECT SUM(amount) FROM agreements",
        "SELECT SUM(x) FROM (SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)",
    )
    assert result.ok


def test_a_custom_rule_registered_from_python():
    registry = builtin_registry()
    registry.register(
        FilterRule(
            key="tenant_scope",
            name="Tenant scope",
            kind=ASSERT,
            description="Every invoices query is scoped to one tenant.",
            applies=touches("invoices"),
            detect=matches_all(r"\btenant_id\s*="),
        )
    )
    bad = evaluate_ledger("SELECT SUM(total) FROM invoices", {}, registry)
    good = evaluate_ledger("SELECT SUM(total) FROM invoices WHERE tenant_id = ?", {}, registry)
    assert not bad.ok
    assert good.ok


def test_a_sqlite_database_can_be_opened_from_a_path(tmp_path):
    import sqlite3

    path = tmp_path / "api.db"
    sqlite3.connect(path).close()
    with SQLiteDatabase.from_path(path) as database:
        assert database.list_tables() == set()
