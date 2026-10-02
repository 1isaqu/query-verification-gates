from __future__ import annotations

import sqlite3

import pytest

from query_gates.adapters import SQLiteDatabase
from query_gates.errors import SpecError
from query_gates.gates.mappings import ERROR, WARNING, check_mappings

CATALOGUE = [
    # (scope, code, label)
    ("Portfolio A-01", "10", "Promise"),
    ("Portfolio A-01", "20", "Refused"),
    ("Portfolio A-02", "10", "Callback"),
    ("Portfolio A-02", "20", "Refused"),
    ("Portfolio A-03", "10", "Promise"),
    ("Portfolio A-03", "10", "Callback"),
    ("Portfolio A-03", "30", None),
    ("Portfolio A-04", "40", "  "),
]
QUERY = "SELECT code, label, COUNT(*) AS n FROM result_catalog WHERE scope = ? GROUP BY code, label"


@pytest.fixture
def catalogue_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE result_catalog (scope TEXT, code TEXT, label TEXT)")
    conn.executemany("INSERT INTO result_catalog VALUES (?, ?, ?)", CATALOGUE)
    conn.commit()
    return SQLiteDatabase(conn)


def check(database, scopes, **kwargs):
    return check_mappings(
        database, name="results", database_name="main", scopes=scopes, query=QUERY, **kwargs
    )


def levels(result):
    return [(i.level, i.message) for i in result.issues]


def test_clean_scope_has_no_issues(catalogue_db):
    result = check(catalogue_db, ["Portfolio A-01"])
    assert result.ok
    assert result.issues == []
    rows = result.per_scope["Portfolio A-01"]
    assert [(r["code"], r["label"], r["n"]) for r in rows] == [
        ("10", "Promise", 1),
        ("20", "Refused", 1),
    ]


def test_each_scope_is_derived_separately_and_divergence_is_flagged(catalogue_db):
    result = check(catalogue_db, ["Portfolio A-01", "Portfolio A-02"])
    assert result.ok  # divergence is a warning: it is the reason the gate exists, not a failure
    assert result.warnings == [
        "code 10 is labelled differently across scopes (Portfolio A-01, Portfolio A-02): "
        "never reuse a mapping between scopes"
    ]
    assert result.per_scope["Portfolio A-01"][0]["label"] == "Promise"
    assert result.per_scope["Portfolio A-02"][0]["label"] == "Callback"


def test_ambiguous_and_missing_labels_are_errors(catalogue_db):
    result = check(catalogue_db, ["Portfolio A-03"])
    assert not result.ok
    messages = result.errors
    assert "Portfolio A-03: code 10 maps to several labels: 'Callback', 'Promise'" in messages
    assert "Portfolio A-03: code 30 has no label" in messages


def test_blank_label_counts_as_missing(catalogue_db):
    result = check(catalogue_db, ["Portfolio A-04"])
    assert result.errors == ["Portfolio A-04: code 40 has no label"]


def test_empty_scope_warns_unless_rows_are_required(catalogue_db):
    lenient = check(catalogue_db, ["Portfolio Z-99"])
    assert lenient.ok
    assert lenient.warnings == ["Portfolio Z-99: no rows returned, mapping is unverifiable"]
    strict = check(catalogue_db, ["Portfolio Z-99"], require_rows=True)
    assert not strict.ok


def test_expected_mapping_is_enforced_case_insensitively(catalogue_db):
    expected = {"Portfolio A-01": {"10": "promise", "20": "Refused"}}
    assert check(catalogue_db, ["Portfolio A-01"], expected=expected).ok

    wrong = {"Portfolio A-01": {"10": "Callback"}}
    result = check(catalogue_db, ["Portfolio A-01"], expected=wrong)
    assert levels(result) == [
        (ERROR, "Portfolio A-01: code 10 expected 'Callback', catalogue says 'Promise'")
    ]


def test_expected_code_that_never_appears_is_a_warning(catalogue_db):
    result = check(catalogue_db, ["Portfolio A-01"], expected={"Portfolio A-01": {"99": "Ghost"}})
    assert result.ok
    assert levels(result) == [(WARNING, "Portfolio A-01: expected code 99 was not observed")]


def test_scope_token_can_sit_anywhere_in_the_parameters(catalogue_db):
    query = "SELECT code, label FROM result_catalog WHERE code <> ? AND scope = ?"
    result = check_mappings(
        catalogue_db, name="n", database_name="main", scopes=["Portfolio A-01"], query=query,
        params=["20", "$scope"],
    )  # fmt: skip
    assert [r["code"] for r in result.per_scope["Portfolio A-01"]] == ["10"]


def test_query_must_return_code_and_label(catalogue_db):
    with pytest.raises(SpecError, match="missing \\['label'\\]"):
        check_mappings(
            catalogue_db, name="n", database_name="main", scopes=["x"],
            query="SELECT code FROM result_catalog WHERE scope = ?",
        )  # fmt: skip


def test_column_names_are_matched_case_insensitively(catalogue_db):
    query = "SELECT code AS CODE, label AS Label FROM result_catalog WHERE scope = ?"
    result = check_mappings(
        catalogue_db, name="n", database_name="main", scopes=["Portfolio A-01"], query=query
    )
    assert result.ok
    assert len(result.per_scope["Portfolio A-01"]) == 2
