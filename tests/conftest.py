"""Shared fixtures. All rows are invented and kept inline, tiny on purpose."""

from __future__ import annotations

import sqlite3

import pytest

from query_gates.adapters import SQLiteDatabase

DDL = """
CREATE TABLE agreement_statuses (status_id INTEGER PRIMARY KEY, label TEXT);
CREATE TABLE agents (agent_id TEXT PRIMARY KEY, display_name TEXT, login TEXT);
CREATE TABLE debt_attributes (debt_id INTEGER PRIMARY KEY, portfolio_label TEXT);
CREATE TABLE agreement_debts (agreement_id INTEGER, portfolio_id INTEGER, debt_id INTEGER);
CREATE TABLE agreements (
    row_id INTEGER PRIMARY KEY, agreement_id INTEGER, portfolio_id INTEGER, installment_no INTEGER,
    amount REAL, paid_amount REAL, issued_at TEXT, due_at TEXT, paid_at TEXT,
    status_id INTEGER, agent_id TEXT
);
"""

STATUSES = [
    (21, "ACTIVE"), (22, "BROKEN"), (23, "PAID"), (24, "PENDING_EXCEPTION"),
    (25, "REJECTED"), (26, "AUTO_BROKEN"), (27, "PAID_MANUAL"),
]  # fmt: skip
AGENTS = [("agent_01", "Agent 01", "agent_01"), ("agent_02", "Agent 02", "agent_02")]
DEBTS = [(1, "Portfolio A-01"), (2, "Portfolio A-01"), (3, "Portfolio A-02")]
LINKS = [(100, 1, 1), (101, 1, 2), (102, 2, 3)]
# (row_id, agreement, portfolio, installment, amount, paid, issued, due, paid_at, status, agent)
AGREEMENTS = [
    (1, 100, 1, 0, 100.0, 100.0, "2026-01-10", "2026-01-15", "2026-01-14", 23, "agent_01"),
    (2, 100, 1, 1, 50.0, 0.0, "2026-01-10", "2026-02-15", None, 21, "agent_01"),
    (3, 100, 1, 1, 55.0, 0.0, "2026-01-11", "2026-02-15", None, 21, "agent_01"),  # re-written row
    (4, 101, 1, 0, 200.0, 0.0, "2026-01-12", "2026-01-20", None, 21, "agent_02"),
    (5, 102, 2, 0, 300.0, 300.0, "2026-01-12", "2026-01-18", "2026-01-18", 23, "agent_02"),
    (6, 103, 1, 0, 75.0, 0.0, "2026-01-13", "2026-01-20", None, 25, "agent_01"),
]


def build_contract_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    conn.executemany("INSERT INTO agreement_statuses VALUES (?, ?)", STATUSES)
    conn.executemany("INSERT INTO agents VALUES (?, ?, ?)", AGENTS)
    conn.executemany("INSERT INTO debt_attributes VALUES (?, ?)", DEBTS)
    conn.executemany("INSERT INTO agreement_debts VALUES (?, ?, ?)", LINKS)
    conn.executemany("INSERT INTO agreements VALUES (?,?,?,?,?,?,?,?,?,?,?)", AGREEMENTS)
    conn.commit()
    return conn


@pytest.fixture
def db():
    database = SQLiteDatabase(build_contract_connection())
    yield database
    database.close()
