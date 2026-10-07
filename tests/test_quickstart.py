"""The README quick start, executed: build the demo database, run the example spec."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from query_gates.cli import main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_SPEC = ROOT / "examples" / "spec.json"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_demo_db", ROOT / "examples" / "build_demo_db.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo_db(tmp_path, builder):
    path = tmp_path / "demo.db"
    assert builder.main([str(path)]) == 0
    return path


def run_example(db_path, *extra):
    return main(["run", str(EXAMPLE_SPEC), "--db", f"main={db_path}", *extra], environ={})


def seed(db_path, statement):
    connection = sqlite3.connect(db_path)
    connection.execute(statement)
    connection.commit()
    connection.close()


def test_quick_start_passes_with_the_documented_numbers(demo_db, capsys):
    assert run_example(demo_db) == 0
    out = capsys.readouterr().out
    assert "**Result: PASS** (exit code 0), 1 warning(s) listed at the end" in out
    assert "| Generated amount, January (deduplicated) | main | 1,105 | 1,105 |" in out
    assert "| Right-party contacts, January | main | 8 | 8 |" in out
    assert "Gate 1: schema | pass" in out


def test_builder_usage_error(builder, capsys):
    assert builder.main([]) == 2
    assert "usage:" in capsys.readouterr().err


def test_the_demo_data_contains_what_the_gates_exist_for(demo_db):
    connection = sqlite3.connect(demo_db)
    rewritten = connection.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM agreements GROUP BY agreement_id, portfolio_id, "
        "installment_no HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    connection.close()
    assert rewritten == 1


def test_a_renamed_column_fails_gate_one(demo_db, capsys):
    seed(demo_db, "ALTER TABLE agents RENAME COLUMN login TO username")
    assert run_example(demo_db) == 1
    out = capsys.readouterr().out
    assert "AGENTS.LOGIN" in out
    assert "Gate 1: schema | fail" in out


def test_a_missing_label_fails_gate_four(demo_db, capsys):
    seed(demo_db, "UPDATE agreement_statuses SET label = '  ' WHERE status_id = 26")
    assert run_example(demo_db) == 1
    assert "code 26 has no label" in capsys.readouterr().out


def test_a_changed_label_fails_the_expected_mapping(demo_db, capsys):
    seed(demo_db, "UPDATE agreement_statuses SET label = 'CLOSED' WHERE status_id = 22")
    assert run_example(demo_db) == 1
    assert "code 22 expected 'BROKEN', catalog says 'CLOSED'" in capsys.readouterr().out


def test_dropping_a_filter_from_one_path_fails_gate_two(demo_db, tmp_path, capsys):
    raw = json.loads(EXAMPLE_SPEC.read_text(encoding="utf-8"))
    lines = raw["claims"][0]["query_b"]
    status_filter = "      AND a.status_id IN (21, 22, 23, 26, 27)"
    lines.remove(status_filter)  # path B now also counts pending and rejected rows
    broken = tmp_path / "broken-spec.json"
    broken.write_text(json.dumps(raw), encoding="utf-8")

    code = main(["run", str(broken), "--db", f"main={demo_db}"], environ={})
    out = capsys.readouterr().out
    assert code == 1
    assert "path A and path B differ by" in out
    assert "| Generated amount, January (deduplicated) | main | 1,105 | 1,305 |" in out
