"""Build a throwaway SQLite database with invented rows, to try query-gates without real data.

    python examples/build_demo_db.py demo.db
    query-gates run examples/spec.json --db main=demo.db

The rows are made up and deliberately small. They contain the situations the gates exist for: a
re-written installment, a row on each edge of the date window, actors that must be excluded, and
statuses that are not part of the generated set.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DDL = """
CREATE TABLE agreement_statuses (status_id INTEGER PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE agents (agent_id TEXT PRIMARY KEY, display_name TEXT, login TEXT NOT NULL);
CREATE TABLE debts (
    debt_id INTEGER PRIMARY KEY, portfolio_id INTEGER, contract_no TEXT, balance REAL,
    due_at TEXT, days_overdue INTEGER
);
CREATE TABLE debt_attributes (debt_id INTEGER PRIMARY KEY, portfolio_label TEXT);
CREATE TABLE agreement_debts (agreement_id INTEGER, portfolio_id INTEGER, debt_id INTEGER);
CREATE TABLE agreements (
    row_id INTEGER PRIMARY KEY, agreement_id INTEGER, portfolio_id INTEGER, installment_no INTEGER,
    amount REAL, paid_amount REAL, issued_at TEXT, due_at TEXT, paid_at TEXT,
    status_id INTEGER, agent_id TEXT
);
CREATE TABLE contact_results (
    result_id INTEGER PRIMARY KEY, label TEXT NOT NULL, answered INTEGER, right_party INTEGER
);
CREATE TABLE contact_events (
    event_id INTEGER PRIMARY KEY, debt_owner_id INTEGER, result_id INTEGER, agent_id TEXT,
    occurred_at TEXT
);
"""

# Invented ids. The example spec treats ACTIVE, BROKEN, PAID, AUTO_BROKEN and PAID_MANUAL as the
# "generated" set.
STATUSES = [
    (21, "ACTIVE"),
    (22, "BROKEN"),
    (23, "PAID"),
    (24, "PENDING_EXCEPTION"),
    (25, "REJECTED"),
    (26, "AUTO_BROKEN"),
    (27, "PAID_MANUAL"),
]
AGENTS = [
    ("agent_01", "Agent 01", "agent_01"),
    ("agent_02", "Agent 02", "agent_02"),
    ("agent_03", "Agent 03", "agent_03"),
    ("system_01", "System 01", "system_batch"),
    ("test_01", "Test 01", "test_user"),
]
DEBTS = [
    (1, 1, "C-0001", 450.0, "2025-12-01", 35),
    (2, 1, "C-0002", 620.0, "2025-11-15", 51),
    (3, 1, "C-0003", 300.0, "2025-12-20", 16),
    (4, 2, "C-0004", 900.0, "2025-10-30", 67),
    (5, 2, "C-0005", 150.0, "2025-12-28", 8),
    (6, 3, "C-0006", 720.0, "2025-11-01", 65),
]
PORTFOLIOS = [
    (1, "Portfolio A-01"),
    (2, "Portfolio A-01"),
    (3, "Portfolio A-01"),
    (4, "Portfolio A-02"),
    (5, "Portfolio A-02"),
    (6, "Portfolio B-01"),
]
LINKS = [
    (1001, 1, 1), (1002, 1, 2), (1003, 1, 3), (1004, 2, 4), (1005, 2, 5), (1006, 2, 5),
    (1007, 3, 6), (1008, 3, 6), (1009, 3, 6), (1010, 1, 1), (1011, 3, 6),
]  # fmt: skip
# row_id, agreement, portfolio, installment, amount, paid, issued, due, paid_at, status, agent
AGREEMENTS = [
    (1, 1001, 1, 0, 150.0, 150.0, "2026-01-05", "2026-01-12", "2026-01-11", 23, "agent_01"),
    (2, 1001, 1, 1, 80.0, 0.0, "2026-01-05", "2026-02-12", None, 21, "agent_01"),
    (3, 1001, 1, 2, 80.0, 0.0, "2026-01-05", "2026-03-12", None, 21, "agent_01"),
    (4, 1002, 1, 0, 200.0, 0.0, "2026-01-08", "2026-01-15", None, 22, "agent_02"),
    (5, 1003, 1, 0, 120.0, 120.0, "2026-01-09", "2026-01-16", "2026-01-20", 27, "agent_02"),
    (6, 1003, 1, 1, 60.0, 0.0, "2026-01-09", "2026-02-16", None, 21, "agent_02"),
    (7, 1003, 1, 1, 65.0, 0.0, "2026-01-20", "2026-02-16", None, 21, "agent_02"),  # re-written
    (8, 1004, 2, 0, 300.0, 300.0, "2026-01-10", "2026-01-17", "2026-01-17", 23, "agent_03"),
    (9, 1005, 2, 0, 90.0, 0.0, "2026-01-11", "2026-01-18", None, 24, "agent_03"),
    (10, 1006, 2, 0, 110.0, 0.0, "2026-01-12", "2026-01-19", None, 25, "agent_01"),
    (11, 1007, 3, 0, 500.0, 0.0, "2026-01-13", "2026-01-20", None, 21, "system_01"),
    (12, 1008, 3, 0, 70.0, 0.0, "2026-01-14", "2026-01-21", None, 26, "agent_03"),
    (13, 1009, 3, 0, 95.0, 0.0, "2026-02-01", "2026-02-08", None, 21, "agent_01"),  # next window
    (14, 1010, 1, 0, 40.0, 0.0, "2026-01-01", "2026-01-08", None, 21, "agent_02"),  # first day
    (15, 1011, 3, 0, 55.0, 0.0, "2026-01-15", "2026-01-22", None, 21, "test_01"),
]
RESULTS = [
    (101, "No answer", 0, 0),
    (102, "Wrong number", 1, 0),
    (103, "Promise to pay", 1, 1),
    (104, "Callback requested", 1, 1),
    (105, "Automated message sent", 1, 0),
    (106, "Refused to pay", 1, 1),
]
EVENTS = [
    (1, 1, 103, "agent_01", "2026-01-05 10:00:00"),
    (2, 1, 104, "agent_01", "2026-01-06 11:30:00"),
    (3, 2, 101, "agent_02", "2026-01-07 09:15:00"),
    (4, 2, 106, "agent_02", "2026-01-08 14:00:00"),
    (5, 3, 103, "agent_02", "2026-01-09 16:45:00"),
    (6, 4, 102, "agent_03", "2026-01-10 10:05:00"),
    (7, 4, 103, "agent_03", "2026-01-10 10:20:00"),
    (8, 5, 105, "system_01", "2026-01-11 08:00:00"),
    (9, 5, 104, "agent_03", "2026-01-12 13:10:00"),
    (10, 6, 103, "test_01", "2026-01-13 12:00:00"),
    (11, 6, 106, "agent_01", "2026-01-14 15:30:00"),
    (12, 6, 101, "agent_01", "2026-01-15 09:00:00"),
    (13, 1, 103, "agent_01", "2026-02-02 10:00:00"),
    (14, 3, 104, "agent_02", "2026-01-31 23:59:59"),
]


def build(connection: sqlite3.Connection) -> None:
    """Create the tables and insert the invented rows."""
    connection.executescript(DDL)
    inserts = [
        ("agreement_statuses", STATUSES),
        ("agents", AGENTS),
        ("debts", DEBTS),
        ("debt_attributes", PORTFOLIOS),
        ("agreement_debts", LINKS),
        ("agreements", AGREEMENTS),
        ("contact_results", RESULTS),
        ("contact_events", EVENTS),
    ]
    for table, rows in inserts:
        marks = ", ".join("?" * len(rows[0]))
        connection.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    connection.commit()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python examples/build_demo_db.py PATH", file=sys.stderr)
        return 2
    target = Path(argv[0])
    target.unlink(missing_ok=True)
    connection = sqlite3.connect(target)
    try:
        build(connection)
    finally:
        connection.close()
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
