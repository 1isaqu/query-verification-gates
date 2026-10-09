# Verification protocol

This page describes what a run of `query-gates run` checks, gate by gate: which spec keys a gate
reads, what result object it returns, and which statuses and exit codes follow. It ends with the
sections of the report, since the report is what travels with a number.

Report a number only when the run exits with code 0. With any other exit code, report the failure
instead of the number.

## How a run ends

A gate can end in two different ways, and the report keeps them apart.

- A verdict: the gate ran and the answer is no. A column is missing, the two paths disagree, an
  invariant is not detected, a mapping is wrong, or a gate was neither exercised nor waived.
- An error: the gate could not be evaluated. A query failed, the guard refused a statement, a
  database could not be opened.

| Exit code | Meaning |
|---|---|
| 0 | No verdict against the number and no error |
| 1 | At least one verdict against the number |
| 2 | The spec or the arguments are invalid. Nothing ran |
| 3 | No verdict, but at least one gate could not be evaluated, or a database could not be opened |
| 4 | Unhandled internal error |

A verdict outranks an error: if one gate fails and another errors, the exit code is 1.

## Gate 1: schema

Reads `schema` from the spec: `columns` (`table.column`), `tables`, `code_tables` (a table name, or
a table with its key and label columns) and `infer_from_queries`. The check runs once for every name
in `databases`, because two databases with the same layout are still two catalogs.

Returns one `SchemaResult` per database: the references that were `checked`, the ones that are
`missing`, and the notes. Names are compared upper-cased, and a schema prefix or quoting is dropped.

| Outcome | Status |
|---|---|
| Nothing missing | pass |
| Anything missing (every miss is listed together, with its table) | fail |
| The catalog could not be read | error |

With `infer_from_queries`, the tables and qualified columns found in the claims and mappings of that
database are added to the list, aliases resolved to table names. That list is advisory: an
unqualified column cannot be attributed to a table, and ledger SQL is not bound to a database. The
explicit lists in the spec are what the gate relies on.

## Gate 2: reconciliation

Reads `claims` from the spec. Each claim has a `label`, a `database`, `query_a` and `query_b` (with
`params_a` and `params_b`), an optional `column` for queries that return several, a `tolerance`
(default 0.5 percent as a fraction, `0.005`) and an optional `claimed` value.

Returns one `Reconciliation` per claim: `a`, `b`, the relative difference
`|A - B| / max(|A|, |B|)` (zero when both are zero), the tolerance, the claimed value and its drift
from A, a similarity ratio between the two query texts, and warnings.

| Outcome | Status |
|---|---|
| Relative difference within tolerance | pass |
| Relative difference above tolerance | fail |
| `query_a` and `query_b` identical after normalization, with identical parameters | exit code 2 when the spec loads |
| Query texts more than 90 percent similar | warning, not blocking |
| A query does not return exactly one row, or its value is NULL, not numeric or not finite | error |
| `claimed` differs from A | shown as drift, never a status |

Two routes are independent when they reach the same figure through different groupings.
`examples/spec.json` has two: a deduplicated total computed with a `ROW_NUMBER()` CTE and summed
once, against the same total deduplicated with `NOT EXISTS` and summed per portfolio; and a count of
contacts against the sum of per-agent counts. The normalization that decides "identical" ignores
whitespace, case and comments outside string literals. Literals count as written, so two queries
that filter on different literals, or on the same letters in a different case, are different.
Identical text with different parameters is allowed.

A timing or a read count has no second grouping path. Repeat the measurement and compare the
direction and size of the change instead of using this gate.

## Gate 3: filter ledger

Reads `ledgers` (a `name`, the `sql` of the production query, a `dialect`, and `declared`) and the
`registry` section, which says which rules exist.

Returns one `LedgerResult` per query, with a row for every rule in the registry. Each `LedgerRow`
holds the rule key, its kind, the declaration, whether the detector found the filter (or `None`
when the rule does not apply to this SQL), a status and a note. The result also carries `problems`,
which are bookkeeping errors in the declarations.

A rule is an `assert` (an invariant) or a `choice` (a filter whose presence depends on the
analysis). The status of a row that applies:

| Row | Status | Blocks |
|---|---|---|
| Rule does not apply to this SQL or dialect | n/a | no |
| Assert, detector finds it | ok | no |
| Assert, detector does not find it. A declaration cannot change this | FAIL | yes |
| Choice with no declaration | UNDECLARED | yes |
| Choice declared `omitted` with no reason | UNREASONED | yes |
| Choice declared `applied` and detected, or `omitted` and not detected | ok | no |
| Choice declared `applied` but not detected, or `omitted` but detected | WARN | no |

A declaration for a key that is not in the registry, or with a disposition other than `applied` or
`omitted`, is a problem and blocks the ledger. A typo would otherwise pass silently.

The detectors are regular expressions over the SQL text. They act as a second opinion on the
declaration, which is why a disagreement is a warning and a person decides. The built-in rules are
generic examples: non-blocking read hint, composite join key, right-party contact flags,
installment dedup, status set, half-open date window and excluded actors. `docs/data-contract.md`
lists the tables they assume, and the registry can be extended from the spec or from Python.

## Gate 4: per-scope mappings

Reads `mappings` from the spec (also available as `query-gates mappings`). Each entry has a `name`,
a `database`, the `scopes` to derive, a `query` that returns the columns `code` and `label`,
`params` (default: each `"$scope"` element is replaced by the current scope), an `expected` mapping
of `{scope: {code: label}}`, and `require_rows`.

A scope is whatever partitions your catalog: a database, a product line, a portfolio, a tenant. The
same code can read differently in two of them, so the query runs once per scope and no mapping is
carried from one scope to another.

Returns one `MappingResult` per entry: the rows found in each scope and a list of issues, each with
the level `ERROR` or `WARN`.

| Condition | Level |
|---|---|
| A code has no label | ERROR |
| One code has several labels within a scope | ERROR |
| `expected` names a label that the catalog does not give for that code (compared without regard to case) | ERROR |
| A scope returns no rows and `require_rows` is set | ERROR |
| A scope returns no rows | WARN |
| An expected code was not observed in the scope | WARN |
| The same code is labeled differently in different scopes | WARN |

The last row is a warning on purpose. Reading a code differently across scopes is the reason the
gate exists, and the warning is a reminder not to reuse one scope's mapping in another. A gate result
passes when it has no `ERROR`.

## Gates that do not apply

A gate with no entries and no waiver fails with "not exercised and not waived". A spec can set
`waivers` to a map of gate name (`schema`, `reconciliation`, `ledger`, `mappings`) to a reason. A
waiver needs a non-empty reason, and the reason appears in the report. This is the right way to
handle a gate that has nothing to check, such as a document that only restates rules and executes no
query (no reconciliation) or an analysis that uses no code columns (no mappings). A number that can
be computed only one way cannot pass Gate 2 and should be reported with its waiver reason visible.

## What a report contains

`query-gates run` prints a markdown report. Its sections, in order:

1. A title line, `# Verification: <analysis>`.
2. A summary: the result line with the exit code and the number of warnings, the as-of date and the
   time of the run (with a note that a query relative to the current date returns different values
   when run again), and a table of the four gates with the status `pass`, `fail`, `error`, `waived`
   or `not run`.
3. `Gate 1: schema`: one line per database with the number of references checked and either
   "all present" or the list of missing ones.
4. `Gate 2: reconciliation`: a table with the claim, the database, both values, the delta, the
   tolerance, the claimed value with its drift, and the result, followed by any warnings.
5. `Gate 3: filter ledger`: for each query, a table of the rules that apply to it (filter, kind,
   disposition, detector, status), then the problems found in the declarations.
6. `Gate 4: per-scope mappings`: for each catalog query, a code and label table per scope, then the
   issues.
7. Only when there is something to show: `Failures`, `Errors (gate could not be evaluated)`,
   `Warnings (non-blocking)` and `Waivers`.

A gate section with no result reads `Not exercised.`, `Waived: <reason>` or `No result: see the
errors below.` The JSON report (`--format json`) carries the same data and also lists the rules that
do not apply, with the status `n/a`.

## Limits

- The Gate 3 detectors are regular expressions over SQL text. An unusual spelling of a filter can
  slip past one (a warning for a choice, a failure for an assert), and a pattern present for another
  reason can satisfy one. A detector shows that a pattern is there, not that the filter is right, so
  the declaration is what counts.
- The tool reports what queries return, not how they run. It reads no execution plans.
- A claim pinned to a date window can be reproduced later. A claim relative to the current date
  cannot be reproduced later, so compare those runs by their ratios and the shape of the result,
  not by absolute values.
- Gate 2 cannot prove that two routes are independent. It rejects identical queries and warns on
  near-identical ones; the rest is the analyst's judgment.
