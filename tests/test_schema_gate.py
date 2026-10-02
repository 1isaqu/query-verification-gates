from __future__ import annotations

from query_gates.gates.schema import CodeTable, check_schema, normalise_ref


def test_all_present(db):
    result = check_schema(
        "main",
        db,
        columns=["agreements.amount", "AGENTS.Login"],
        tables=["agreement_debts"],
        code_tables=[CodeTable("agreement_statuses", "status_id", "label")],
    )
    assert result.ok
    assert len(result.checked) == 4


def test_every_miss_is_listed(db):
    result = check_schema(
        "main",
        db,
        columns=["agreements.no_such_column", "no_such_table.x", "amount"],
        tables=["ghosts"],
        code_tables=[
            CodeTable("no_such_catalogue"),
            CodeTable("agreement_statuses", "status_id", "description"),
        ],
    )
    assert not result.ok
    assert result.missing == [
        "AGREEMENTS.NO_SUCH_COLUMN",
        "NO_SUCH_TABLE.X",
        "AMOUNT (not in table.column form)",
        "GHOSTS (table)",
        "NO_SUCH_CATALOGUE (code table)",
        "AGREEMENT_STATUSES.DESCRIPTION (code table column)",
    ]


def test_schema_qualified_and_quoted_refs_are_normalised(db):
    assert normalise_ref("[dbo].[Agreements].[Amount]") == "AGREEMENTS.AMOUNT"
    assert normalise_ref('"agents"."login"') == "AGENTS.LOGIN"
    assert check_schema("main", db, columns=["main.agreements.amount"]).ok


def test_nothing_to_check_is_trivially_ok(db):
    result = check_schema("main", db)
    assert result.ok
    assert result.checked == []
