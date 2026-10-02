from __future__ import annotations

import pytest

from query_gates.errors import AdapterError, SpecError
from query_gates.gates.reconcile import Reconciliation, reconcile

PATH_A = "SELECT SUM(amount) AS total FROM agreements"
PATH_B = (
    "SELECT SUM(per_agent) FROM "
    "(SELECT SUM(amount) AS per_agent FROM agreements GROUP BY agent_id)"
)


def test_two_independent_routes_agree(db):
    result = reconcile(db, "total amount", PATH_A, PATH_B, database_name="main")
    assert result.a == result.b == 780.0
    assert result.ok
    assert result.rel_diff == 0.0
    assert result.warnings == []


def test_disagreement_beyond_tolerance_fails(db):
    path_b = "SELECT SUM(amount) FROM agreements WHERE status_id <> 7"
    result = reconcile(db, "total amount", PATH_A, path_b)
    assert (result.a, result.b) == (780.0, 705.0)
    assert not result.ok
    assert result.rel_diff == pytest.approx(75 / 780)


@pytest.mark.parametrize(("b", "ok"), [(1004.0, True), (1005.0, True), (1006.0, False)])
def test_tolerance_boundary_is_relative_to_the_larger_value(db, b, ok):
    result = reconcile(db, "x", "SELECT 1000.0 AS v", f"SELECT {b} AS w", tolerance=0.005)
    assert result.ok is ok


def test_custom_tolerance_is_honoured(db):
    loose = reconcile(db, "x", "SELECT 100.0 AS v", "SELECT 90.0 AS w", tolerance=0.2)
    strict = reconcile(db, "x", "SELECT 100.0 AS v", "SELECT 90.0 AS w", tolerance=0.01)
    assert loose.ok
    assert not strict.ok


def test_identical_queries_are_rejected(db):
    with pytest.raises(SpecError, match="not independent"):
        reconcile(db, "x", "SELECT 1.0 AS v", "select   1.0 AS v -- same")


def test_same_text_is_independent_only_when_parameters_differ(db):
    sql = "SELECT SUM(amount) FROM agreements WHERE agent_id = ?"
    with pytest.raises(SpecError, match="not independent"):
        reconcile(db, "x", sql, sql, params_a=["agent_01"], params_b=["agent_01"])
    result = reconcile(db, "x", sql, sql, params_a=["agent_01"], params_b=["agent_02"])
    assert (result.a, result.b) == (280.0, 500.0)


def test_literals_make_queries_different(db):
    result = reconcile(
        db, "x",
        "SELECT SUM(amount) FROM agreements WHERE agent_id = 'agent_01'",
        "SELECT SUM(amount) FROM agreements WHERE agent_id = 'agent_02'",
    )  # fmt: skip
    assert not result.ok


def test_similar_queries_raise_a_warning(db):
    result = reconcile(
        db, "x",
        "SELECT SUM(amount) FROM agreements WHERE status_id IN (1, 3)",
        "SELECT SUM(amount) FROM agreements WHERE status_id IN (3, 1)",
    )  # fmt: skip
    assert result.ok
    assert result.similarity > 0.9
    assert "similar" in result.warnings[0]


def test_parameters_are_bound_for_each_path(db):
    result = reconcile(
        db, "agent_01 amount",
        "SELECT SUM(amount) FROM agreements WHERE agent_id = ?",
        "SELECT SUM(per_status) FROM (SELECT SUM(amount) AS per_status FROM agreements "
        "WHERE agent_id = ? GROUP BY status_id)",
        params_a=["agent_01"], params_b=["agent_01"],
    )  # fmt: skip
    assert result.ok
    assert result.a == 280.0


def test_column_selection_for_multi_column_results(db):
    a = "SELECT COUNT(*) AS n, SUM(amount) AS total FROM agreements"
    b = (
        "SELECT SUM(per_agent) AS total, 0 AS n FROM "
        "(SELECT SUM(amount) AS per_agent FROM agreements GROUP BY agent_id)"
    )
    assert reconcile(db, "x", a, b, column="total").a == 780.0
    with pytest.raises(SpecError, match="one numeric column"):
        reconcile(db, "x", a, b)
    with pytest.raises(SpecError, match="not in result columns"):
        reconcile(db, "x", a, b, column="absent")


def test_null_and_non_numeric_results_are_errors(db):
    with pytest.raises(AdapterError, match="NULL"):
        reconcile(db, "x", "SELECT NULL AS v", "SELECT 1.0 AS w")
    with pytest.raises(AdapterError, match="not numeric"):
        reconcile(db, "x", "SELECT 'abc' AS v", "SELECT 1.0 AS w")
    with pytest.raises(AdapterError, match="NULL"):
        reconcile(db, "x", "SELECT SUM(amount) FROM agreements WHERE 1 = 0", "SELECT 1.0 AS w")


def test_a_query_that_returns_many_rows_is_an_error(db):
    with pytest.raises(AdapterError, match="exactly 1 row, got 6"):
        reconcile(db, "x", "SELECT amount FROM agreements", "SELECT 1.0 AS w")


@pytest.mark.parametrize("tolerance", [-0.1, 1.0, 5])
def test_tolerance_must_be_a_fraction(db, tolerance):
    with pytest.raises(SpecError, match="tolerance"):
        reconcile(db, "x", "SELECT 1.0 AS v", "SELECT 2.0 AS w", tolerance=tolerance)


class TestReconciliationMath:
    def test_both_zero_agree(self):
        assert Reconciliation("x", "main", 0.0, 0.0, 0.005).ok

    def test_one_zero_one_not_disagree(self):
        assert not Reconciliation("x", "main", 0.0, 1.0, 0.005).ok

    def test_claim_drift_is_reported_but_does_not_fail(self):
        result = Reconciliation("x", "main", 110.0, 110.0, 0.005, claimed=100.0)
        assert result.ok
        assert result.claim_drift == pytest.approx(10 / 110)

    def test_no_claim_means_no_drift(self):
        assert Reconciliation("x", "main", 1.0, 1.0, 0.005).claim_drift is None
