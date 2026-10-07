from __future__ import annotations

import pytest

from query_gates.gates.ledger import (
    FAIL,
    NOT_APPLICABLE,
    OK,
    UNDECLARED,
    UNREASONED,
    WARN,
    evaluate_ledger,
    parse_declarations,
)
from query_gates.registry import builtin_registry

GOOD_SQL = """
WITH ranked AS (
    SELECT a.*, ROW_NUMBER() OVER (
        PARTITION BY a.agreement_id, a.portfolio_id, a.installment_no
        ORDER BY a.issued_at DESC, a.row_id DESC) AS rn
    FROM agreements a
    WHERE a.issued_at >= ? AND a.issued_at < ? AND a.status_id IN (21, 23, 27)
)
SELECT SUM(r.amount)
FROM ranked r JOIN agents ag ON ag.agent_id = r.agent_id
WHERE r.rn = 1 AND ag.login NOT LIKE 'system%' AND ag.login NOT LIKE 'test%'
"""
ALL_DECLARED = {
    "installment_dedup": ["applied", "latest row per installment"],
    "status_set": ["applied", "approved set"],
    "date_window": ["applied", ""],
    "excluded_actors": ["applied", ""],
}


def run(sql=GOOD_SQL, declared=None, dialect="generic"):
    declared = ALL_DECLARED if declared is None else declared
    return evaluate_ledger(sql, declared, builtin_registry(), name="q", dialect=dialect)


def statuses(result):
    return {row.key: row.status for row in result.rows}


def test_fully_declared_good_sql_passes():
    result = run()
    assert result.ok
    assert result.blockers == []
    assert result.warnings == []
    applicable = {row.key for row in result.applicable_rows}
    assert applicable == {"installment_dedup", "status_set", "date_window", "excluded_actors"}
    assert all(row.detected for row in result.applicable_rows)


def test_rules_that_do_not_touch_the_query_are_not_applicable():
    st = statuses(run())
    assert st["composite_agreement_key"] == NOT_APPLICABLE
    assert st["right_party_two_flag"] == NOT_APPLICABLE
    assert st["read_only_hint"] == NOT_APPLICABLE


def test_undeclared_choice_blocks():
    declared = {k: v for k, v in ALL_DECLARED.items() if k != "status_set"}
    result = run(declared=declared)
    assert not result.ok
    assert statuses(result)["status_set"] == UNDECLARED
    assert result.blockers == ["status_set: UNDECLARED"]


def test_omission_needs_a_reason():
    declared = {**ALL_DECLARED, "excluded_actors": "omitted"}
    result = run(sql=GOOD_SQL.replace("NOT LIKE 'system%'", "<> 'x'"), declared=declared)
    assert statuses(result)["excluded_actors"] == UNREASONED
    assert not result.ok

    declared = {**ALL_DECLARED, "excluded_actors": ["omitted", "totals are per actor anyway"]}
    result = run(sql=GOOD_SQL.replace("NOT LIKE 'system%'", "<> 'x'"), declared=declared)
    assert statuses(result)["excluded_actors"] == OK
    assert result.ok


def test_detector_disagreement_is_a_warning_not_a_failure():
    no_dedup = GOOD_SQL.replace("ROW_NUMBER()", "COUNT(*)")
    result = run(sql=no_dedup)
    assert statuses(result)["installment_dedup"] == WARN
    assert result.ok
    assert "declared applied, detector did not find it" in result.warnings[0]

    declared = {**ALL_DECLARED, "installment_dedup": ["omitted", "counts only"]}
    result = run(declared=declared)
    assert statuses(result)["installment_dedup"] == WARN
    assert "declared omitted, detector found it" in result.warnings[0]
    assert result.ok


def test_unknown_and_malformed_declarations_are_problems():
    result = run(declared={**ALL_DECLARED, "date_windwo": "applied", "status_set": "maybe"})
    assert not result.ok
    assert any("'date_windwo' is not a rule" in p for p in result.problems)
    assert any("'status_set'" in p and "disposition" in p for p in result.problems)


@pytest.mark.parametrize(
    "raw",
    ["applied", ["applied"], ["applied", "why"], ("applied", "why"), {"disposition": "applied"}],
)
def test_declaration_spellings(raw):
    parsed, problems = parse_declarations({"status_set": raw})
    assert problems == []
    assert parsed["status_set"].disposition == "applied"


def test_declaration_values_are_case_insensitive_and_trimmed():
    parsed, _ = parse_declarations({"a": " Applied ", "b": ["OMITTED", " why "]})
    assert parsed["a"].disposition == "applied"
    assert (parsed["b"].disposition, parsed["b"].reason) == ("omitted", "why")


def test_non_declarations_are_problems():
    _, problems = parse_declarations({"a": 5, "b": [], "c": None})
    assert len(problems) == 3


class TestAsserts:
    def test_composite_key_must_equate_both_columns(self):
        bad = "SELECT 1 FROM agreement_debts d JOIN agreements a ON a.agreement_id = d.agreement_id"
        good = bad + " AND a.portfolio_id = d.portfolio_id"
        assert statuses(run(bad))["composite_agreement_key"] == FAIL
        assert statuses(run(good))["composite_agreement_key"] == OK

    def test_an_assert_cannot_be_declared_away(self):
        bad = "SELECT 1 FROM agreement_debts d JOIN agreements a ON a.agreement_id = d.agreement_id"
        result = run(bad, declared={"composite_agreement_key": ["omitted", "trust me"]})
        assert statuses(result)["composite_agreement_key"] == FAIL
        assert not result.ok

    @pytest.mark.parametrize(
        ("clause", "expected"),
        [
            ("cr.answered = 1 AND cr.right_party = 1", OK),
            ("right_party = 1 AND answered = 1", OK),
            ("cr.right_party = 1", FAIL),
            ("cr.answered = 1", FAIL),
            ("cr.answered = 1 OR cr.right_party = 1", FAIL),
        ],
    )
    def test_right_party_needs_both_flags(self, clause, expected):
        sql = (
            "SELECT COUNT(*) FROM contact_events e "
            "JOIN contact_results cr ON cr.result_id = e.result_id "
            f"WHERE {clause}"
        )
        assert statuses(run(sql, declared={}))["right_party_two_flag"] == expected


class TestReadOnlyHint:
    HINTED = (
        "WITH x AS (SELECT agent_id FROM agents WITH (NOLOCK)) "
        "SELECT 1 FROM x JOIN agreements a (NOLOCK) ON a.agent_id = x.agent_id"
    )

    def test_applies_only_to_the_sqlserver_dialect(self):
        assert statuses(run(self.HINTED, {}, dialect="sqlite"))["read_only_hint"] == NOT_APPLICABLE
        assert statuses(run(self.HINTED, {}, dialect="sqlserver"))["read_only_hint"] == OK

    def test_every_table_needs_the_hint(self):
        sql = "SELECT 1 FROM agents a WITH (NOLOCK) JOIN agreements g ON g.agent_id = a.agent_id"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == FAIL

    def test_legacy_alias_then_hint_spelling(self):
        sql = "SELECT 1 FROM agents a (NOLOCK)"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == OK

    @pytest.mark.parametrize(
        "hint",
        [
            "WITH (NOLOCK, INDEX(ix_agents))",
            "WITH (INDEX(ix_agents), NOLOCK)",
            "WITH (READUNCOMMITTED)",
            "(READUNCOMMITTED)",
        ],
    )
    def test_a_valid_hint_list_is_accepted(self, hint):
        sql = f"SELECT 1 FROM agents a {hint} WHERE a.agent_id = 1"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == OK

    def test_a_nested_hint_without_a_non_blocking_hint_fails(self):
        sql = "SELECT 1 FROM agents a WITH (INDEX(ix_agents)) WHERE a.agent_id = 1"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == FAIL

    def test_ctes_and_table_functions_are_not_held_to_the_hint(self):
        sql = "WITH x AS (SELECT 1 AS n) SELECT * FROM x JOIN generate_series(1, 2) g ON 1 = 1"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == NOT_APPLICABLE

    def test_a_hint_in_a_comment_does_not_count(self):
        sql = "SELECT 1 FROM agents a -- WITH (NOLOCK)\n"
        assert statuses(run(sql, {}, dialect="sqlserver"))["read_only_hint"] == FAIL


class TestDetectors:
    def test_between_is_not_a_half_open_window(self):
        sql = GOOD_SQL.replace(
            "a.issued_at >= ? AND a.issued_at < ?", "a.issued_at BETWEEN ? AND ?"
        )
        assert statuses(run(sql))["date_window"] == WARN

    def test_inclusive_upper_bound_is_not_half_open(self):
        sql = GOOD_SQL.replace("a.issued_at < ?", "a.issued_at <= ?")
        assert statuses(run(sql))["date_window"] == WARN

    def test_dedup_partition_order_does_not_matter(self):
        sql = GOOD_SQL.replace(
            "a.agreement_id, a.portfolio_id, a.installment_no",
            "a.installment_no, a.portfolio_id, a.agreement_id",
        )
        assert statuses(run(sql))["installment_dedup"] == OK

    def test_dedup_must_order_descending(self):
        sql = GOOD_SQL.replace("ORDER BY a.issued_at DESC, a.row_id DESC", "ORDER BY a.issued_at")
        assert statuses(run(sql))["installment_dedup"] == WARN

    def test_comments_do_not_satisfy_a_detector(self):
        sql = GOOD_SQL.replace("AND ag.login NOT LIKE 'test%'", "-- AND ag.login NOT LIKE 'test%'")
        assert statuses(run(sql))["excluded_actors"] == WARN

    def test_a_table_name_inside_a_literal_does_not_make_a_rule_apply(self):
        sql = "SELECT 'agreements' AS label"
        assert all(row.status == NOT_APPLICABLE for row in run(sql, {}).rows)

    def test_bracket_quoted_identifiers_are_understood(self):
        sql = GOOD_SQL.replace("FROM agreements a", "FROM [agreements] a")
        assert statuses(run(sql))["status_set"] == OK


class TestDetectorsDoNotOverMatch:
    """A pattern that is present for another purpose must not count as the filter."""

    def only_status(self, predicate):
        return (
            "SELECT 1 FROM agreements a JOIN agreement_statuses s ON s.status_id = a.status_id "
            f"WHERE {predicate}"
        )

    @pytest.mark.parametrize(
        ("predicate", "expected"),
        [
            ("1 = 1", WARN),  # the join on status_id is not a restriction
            ("a.status_id = 21", OK),
            ("a.status_id <> 25", OK),
            ("a.status_id != ?", OK),
            ("a.status_id = :status", OK),
            ("a.status_id NOT IN (24, 25)", OK),
            ("a.status_id IN (21, 23)", OK),
            ("a.status_id = s.status_id", WARN),
        ],
    )
    def test_status_set_needs_a_value_not_a_column(self, predicate, expected):
        declared = {"status_set": "applied", "installment_dedup": ["omitted", "n/a"],
                    "date_window": ["omitted", "n/a"]}  # fmt: skip
        result = run(self.only_status(predicate), declared)
        assert statuses(result)["status_set"] == expected

    def ranked(self, filter_clause):
        return (
            "SELECT * FROM (SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.agreement_id, "
            "a.portfolio_id, a.installment_no ORDER BY a.issued_at DESC) AS rn FROM agreements a) r"
            f" {filter_clause}"
        )

    @pytest.mark.parametrize(
        ("clause", "expected"),
        [
            ("WHERE r.rn = 1", OK),
            ("WHERE rn <= 1", OK),
            ("WHERE r.rn < 2", OK),
            ("", WARN),  # ranked but never filtered: nothing was deduplicated
            ("WHERE r.rn = 10", WARN),
            ("WHERE r.rn = 2", WARN),
        ],
    )
    def test_dedup_must_filter_the_rank_to_one(self, clause, expected):
        declared = {"installment_dedup": "applied", "status_set": ["omitted", "n/a"],
                    "date_window": ["omitted", "n/a"]}  # fmt: skip
        assert statuses(run(self.ranked(clause), declared))["installment_dedup"] == expected

    def test_composite_key_must_be_equated_through_the_link_table(self):
        sql = (
            "SELECT 1 FROM agreement_debts d "
            "JOIN agreements a ON a.agreement_id = d.agreement_id "
            "JOIN agents g ON g.portfolio_id = a.portfolio_id"  # portfolio_id equated elsewhere
        )
        assert statuses(run(sql, {}))["composite_agreement_key"] == FAIL

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 FROM agreement_debts d JOIN agreements a "
            "ON d.agreement_id = a.agreement_id AND d.portfolio_id = a.portfolio_id",
            "SELECT 1 FROM agreement_debts d JOIN agreements a "
            "ON a.portfolio_id = d.portfolio_id AND a.agreement_id = d.agreement_id",
            "SELECT 1 FROM dbo.agreement_debts JOIN agreements "
            "ON agreement_debts.agreement_id = agreements.agreement_id "
            "AND agreement_debts.portfolio_id = agreements.portfolio_id",
            "SELECT 1 FROM agreement_debts JOIN agreements USING (portfolio_id, agreement_id)",
        ],
    )
    def test_composite_key_spellings(self, sql):
        assert statuses(run(sql, {}))["composite_agreement_key"] == OK
