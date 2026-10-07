from __future__ import annotations

import json

import pytest

from query_gates.errors import SpecError
from query_gates.spec import load_spec, parse_spec

A = "SELECT SUM(amount) FROM agreements"
B = "SELECT SUM(x) FROM (SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)"


def valid(**overrides):
    spec = {
        "analysis": "total amount",
        "databases": ["auto", "consumer"],
        "claims": [{"label": "total", "database": "auto", "query_a": A, "query_b": B}],
    }
    spec.update(overrides)
    return spec


def problems(raw, **kwargs):
    with pytest.raises(SpecError) as info:
        parse_spec(raw, **kwargs)
    return info.value.problems


def test_minimal_spec_gets_defaults():
    spec = parse_spec({"analysis": "x"})
    assert spec.databases == ["main"]
    assert spec.dialect == "generic"
    assert spec.as_of is None
    assert spec.schema is None
    assert spec.claims == spec.ledgers == spec.mappings == []
    assert "status_set" in spec.registry


def test_full_spec_round_trip():
    spec = parse_spec(
        valid(
            as_of="2026-03-31",
            dialect="sqlite",
            schema={
                "columns": ["agreements.amount"],
                "tables": ["agents"],
                "code_tables": ["agreement_statuses", {"table": "agents", "key": "agent_id"}],
                "infer_from_queries": True,
            },
            ledgers=[{"name": "q", "sql": A, "declared": {"status_set": "applied"}}],
            mappings=[
                {
                    "name": "m",
                    "database": "consumer",
                    "scopes": ["Portfolio A-01"],
                    "query": "SELECT 1 AS code, 'x' AS label WHERE ? = ?",
                    "params": ["$scope", "Portfolio A-01"],
                    "expected": {"Portfolio A-01": {"1": "x"}},
                    "require_rows": True,
                }
            ],
            waivers={"mappings": "no code columns involved"},
        )
    )
    claim = spec.claims[0]
    assert (claim.database, claim.tolerance, claim.claimed) == ("auto", 0.005, None)
    assert spec.schema.infer_from_queries
    assert [c.table for c in spec.schema.code_tables] == ["agreement_statuses", "agents"]
    assert spec.ledgers[0].dialect == "sqlite"  # inherited from the top level
    assert spec.mappings[0].params == ["$scope", "Portfolio A-01"]
    assert spec.mappings[0].require_rows
    assert spec.waivers == {"mappings": "no code columns involved"}
    assert spec.as_of == "2026-03-31"


def test_claim_database_defaults_to_the_first():
    spec = parse_spec(valid(claims=[{"label": "t", "query_a": A, "query_b": B}]))
    assert spec.claims[0].database == "auto"


def test_all_problems_are_reported_together():
    found = problems(
        {
            "analysis": "x",
            "color": "red",
            "as_of": "yesterday",
            "databases": ["a", "a"],
            "claims": [
                {"query_a": A, "query_b": B},
                {"label": "t", "database": "ghost", "query_a": A},
                {"label": "u", "query_a": A, "query_b": B, "tolerance": 3, "extra": 1},
                "not an object",
            ],
            "waivers": {"nonsense": "x", "ledger": ""},
        }
    )
    text = "\n".join(found)
    for fragment in (
        "unknown keys ['color']",
        "'as_of' must be an ISO date",
        "must not repeat a name",
        "claims[0]: 'label' is required",
        "database 'ghost' is not listed",
        "exactly one of 'query_b' or 'query_b_file'",
        "tolerance' must satisfy",
        "unknown keys ['extra']",
        "claims[3]: must be an object",
        "unknown gate 'nonsense'",
        "waiving 'ledger' needs a reason",
    ):
        assert fragment in text


def test_spec_must_be_an_object_and_have_a_name():
    assert problems([1, 2]) == ["spec must be a JSON object"]
    assert "'analysis' is required" in "; ".join(problems({}))


def test_identical_queries_are_a_spec_error():
    raw = valid(claims=[{"label": "t", "query_a": A, "query_b": A.lower() + " -- same"}])
    assert "not independent" in "; ".join(problems(raw))
    differing_params = valid(
        claims=[{"label": "t", "query_a": A, "query_b": A, "params_a": [1], "params_b": [2]}]
    )
    parse_spec(differing_params)


def test_schema_section_validation():
    found = "; ".join(problems(valid(schema={"columns": "agreements.amount", "x": 1})))
    assert "unknown keys ['x']" in found
    assert "'columns' must be a non-empty list of strings" in found
    found = "; ".join(problems(valid(schema={"code_tables": [5, {"key": "k"}]})))
    assert "schema.code_tables[0]: must be a table name or an object" in found
    assert "'table' is required" in found
    assert problems(valid(schema=[1])) == ["schema: must be an object"]


def test_mapping_section_validation():
    bad = {"name": "m", "scopes": ["s"], "query": "SELECT 1", "expected": {"other": {"1": "x"}}}
    found = "; ".join(problems(valid(mappings=[bad])))
    assert "names scope 'other' that is not in 'scopes'" in found
    bad = {"name": "m", "scopes": ["s"], "query": "SELECT 1", "expected": {"s": {"1": 5}}}
    assert "'expected' must map scope" in "; ".join(problems(valid(mappings=[bad])))
    assert "'scopes' must be a non-empty list" in "; ".join(
        problems(valid(mappings=[{"query": "SELECT 1", "scopes": []}]))
    )


def test_params_must_be_scalars():
    raw = valid(claims=[{"label": "t", "query_a": A, "query_b": B, "params_a": [[1]]}])
    assert "'params_a' must be a list of scalar values" in "; ".join(problems(raw))


def test_registry_problems_surface_as_spec_problems():
    assert "registry: unknown keys" in "; ".join(problems(valid(registry={"x": 1})))
    assert problems(valid(registry=[1])) == ["registry: must be an object"]


def test_sql_files_are_read_relative_to_the_spec(tmp_path):
    (tmp_path / "a.sql").write_text(A, encoding="utf-8")
    (tmp_path / "b.sql").write_text(B, encoding="utf-8")
    (tmp_path / "l.sql").write_text("SELECT 1 FROM agreements", encoding="utf-8")
    raw = {
        "analysis": "x",
        "claims": [{"label": "t", "query_a_file": "a.sql", "query_b_file": "b.sql"}],
        "ledgers": [{"name": "l", "sql_file": "l.sql"}],
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    spec = load_spec(path)
    assert spec.claims[0].query_a == A
    assert spec.ledgers[0].sql == "SELECT 1 FROM agreements"


def test_sql_may_be_a_list_of_lines():
    raw = {"analysis": "x", "ledgers": [{"name": "l", "sql": ["SELECT 1", "FROM agreements"]}]}
    assert parse_spec(raw).ledgers[0].sql == "SELECT 1\nFROM agreements"
    bad = {"analysis": "x", "ledgers": [{"name": "l", "sql": ["SELECT 1", 2]}]}
    assert "must be a non-empty string or a list of strings" in "; ".join(problems(bad))
    empty = {"analysis": "x", "ledgers": [{"name": "l", "sql": []}]}
    assert "must be a non-empty string or a list" in "; ".join(problems(empty))


def test_file_reference_problems(tmp_path):
    both = {"analysis": "x", "ledgers": [{"sql": "SELECT 1", "sql_file": "l.sql"}]}
    assert "exactly one of 'sql' or 'sql_file'" in "; ".join(problems(both, base_dir=tmp_path))
    missing = {"analysis": "x", "ledgers": [{"sql_file": "absent.sql"}]}
    assert "cannot read 'absent.sql'" in "; ".join(problems(missing, base_dir=tmp_path))
    assert "need a spec loaded from disk" in "; ".join(problems(missing))


def test_load_spec_errors(tmp_path):
    with pytest.raises(SpecError, match="cannot read spec"):
        load_spec(tmp_path / "absent.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(SpecError, match="not valid JSON"):
        load_spec(broken)


def test_sql_for_is_bound_to_a_database():
    spec = parse_spec(
        valid(
            ledgers=[{"name": "q", "sql": "SELECT 1 FROM agents"}],
            mappings=[{"name": "m", "database": "consumer", "scopes": ["s"], "query": "SELECT 2"}],
        )
    )
    assert spec.sql_for("auto") == [A, B]
    assert spec.sql_for("consumer") == ["SELECT 2"]
