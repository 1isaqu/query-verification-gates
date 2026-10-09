# Spec format

A spec is one JSON file that says what to verify. `query-gates run spec.json` validates it first
(every problem is reported at once, exit code 2), then opens the databases and runs the gates.
`examples/spec.json` is a complete working example.

Unknown keys are errors everywhere, so a typo fails loudly instead of being ignored.

## SQL text

Wherever a spec takes SQL (`query_a`, `query_b`, `sql`, `query`) it can be given three ways:

```json
"sql": "SELECT 1 FROM agreements"
"sql": ["SELECT 1", "FROM agreements"]
"sql_file": "queries/total.sql"
```

A list is joined with newlines, which keeps multi-line SQL readable in JSON. `*_file` paths are read
relative to the spec file. Exactly one form must be present.

## Top level

| Key | Type | Default | Meaning |
|---|---|---|---|
| `analysis` | string | required | Name shown in the report |
| `as_of` | `YYYY-MM-DD` | today (UTC) | The date the claim refers to |
| `dialect` | string | `generic` | Default dialect for ledgers (`sqlserver` turns on the read-hint rule) |
| `databases` | list of names | `["main"]` | Logical database names. The CLI maps each to a file or database via `--db NAME=TARGET` |
| `schema` | object | none | Gate 1 |
| `claims` | list | `[]` | Gate 2 |
| `ledgers` | list | `[]` | Gate 3 |
| `mappings` | list | `[]` | Gate 4 |
| `registry` | object | built-ins | Gate 3 rule set |
| `waivers` | object | `{}` | Gates deliberately not exercised, with a reason |

A gate with no entries and no waiver fails: a spec cannot pass by leaving a gate out.

## `schema` (Gate 1)

| Key | Type | Meaning |
|---|---|---|
| `columns` | list of `table.column` | Must exist. Checked on every database in `databases`. A schema prefix is ignored |
| `tables` | list of names | Must exist |
| `code_tables` | list | Each a table name, or `{"table": ..., "key": ..., "label": ...}`. The table must exist; the key and label columns too when given |
| `infer_from_queries` | bool | Also check the tables and qualified columns found in the claims and mappings of each database. Advisory: unqualified columns are not inferred, and ledger SQL is not bound to a database so it is not used |

## `claims` (Gate 2)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `label` | string | required | Name of the number |
| `database` | name | first database | Where both paths run |
| `query_a`, `query_b` | SQL | required | Two independent routes to the number. Each returns exactly one row |
| `params_a`, `params_b` | list of scalars | `[]` | Positional `?` parameters for each path |
| `column` | string | none | Column to compare when a query returns several; otherwise there must be exactly one column |
| `tolerance` | number, `0 <= t < 1` | `0.005` | Maximum relative difference between the paths |
| `claimed` | number | none | A previously reported value. Drift is shown, never fails |

The relative difference is `|A - B| / max(|A|, |B|)`; two zeros agree. Identical queries (after
whitespace, case and comment normalization) with identical parameters are rejected when the spec is
loaded.

## `ledgers` (Gate 3)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | `ledger-N` | Name in the report |
| `sql` / `sql_file` | SQL | required | The production query to check |
| `dialect` | string | top-level `dialect` | Which dialect-gated rules apply |
| `declared` | object | `{}` | Rule key to disposition |

A disposition is `"applied"`, `"omitted"`, `["applied", "reason"]` or
`{"disposition": "omitted", "reason": "..."}`. An omission needs a non-empty reason. A key that is
not in the registry is an error.

## `mappings` (Gate 4)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | `mapping-N` | Name in the report |
| `database` | name | first database | Where the catalog query runs |
| `scopes` | list of strings | required | The partitions to derive the mapping for |
| `query` / `query_file` | SQL | required | Returns columns `code` and `label` (any other columns are shown in the report) |
| `params` | list | `["$scope"]` | Positional parameters. Every element equal to `"$scope"` is replaced by the current scope |
| `expected` | object | `{}` | `{scope: {code: label}}`. Labels compare case-insensitively |
| `require_rows` | bool | `false` | A scope with no rows is an error instead of a warning |

The same check is available for one query without a spec: `query-gates mappings QUERY --scope S
--db NAME=TARGET`, with `--expected FILE` (the `expected` object as JSON) and `--require-rows`.

## `registry` (Gate 3 rule set)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `builtin` | bool | `true` | Start from the built-in rules |
| `disable` | list of keys | `[]` | Remove built-in rules |
| `excluded_actor_patterns` | list of strings | `["system%", "test%"]` | Patterns the `excluded_actors` rule requires |
| `rules` | list | `[]` | Your own rules |

A custom rule:

```json
{
  "key": "tenant_scope",
  "name": "Tenant scope",
  "kind": "assert",
  "description": "Every invoices query is scoped to one tenant.",
  "applies_when": ["\\bFROM\\s+invoices\\b"],
  "detected_when": ["\\btenant_id\\s*="],
  "dialects": ["sqlserver"]
}
```

| Key | Meaning |
|---|---|
| `key` | Required, unique. Reusing a built-in key needs `"replace": true` |
| `name`, `description` | Shown in the report and by `query-gates rules` |
| `kind` | `assert` or `choice` (default `choice`) |
| `applies_when` | Regular expressions that must all match for the rule to apply. Empty means always. They see the SQL with comments removed and string literals blanked |
| `detected_when` | Regular expressions that must all match for the filter to count as present. At least one is required. They see the SQL with comments removed and string literals kept |
| `dialects` | Restrict the rule to these dialects. Empty means every dialect |

Regular expressions are case-insensitive and let `.` match newlines.

## `waivers`

```json
"waivers": { "mappings": "no code columns are involved in this number" }
```

Valid gates: `schema`, `reconciliation`, `ledger`, `mappings`. The reason is required and appears in
the report.

## Minimal example

```json
{
  "analysis": "total amount",
  "claims": [
    {
      "label": "total amount",
      "query_a": "SELECT SUM(amount) AS total FROM agreements",
      "query_b": "SELECT SUM(x) FROM (SELECT SUM(amount) AS x FROM agreements GROUP BY agent_id)"
    }
  ],
  "waivers": {
    "schema": "covered by the full spec",
    "ledger": "no production query to check here",
    "mappings": "no catalog involved"
  }
}
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Every gate passed |
| 1 | A gate was evaluated and failed, or was neither exercised nor waived |
| 2 | Bad spec or arguments; nothing ran |
| 3 | Inconclusive: a gate could not be evaluated, or a database could not be opened |
| 4 | Internal error |

If a report contains both a failure and an error, the exit code is 1: a definite failure outranks
an inconclusive result.
