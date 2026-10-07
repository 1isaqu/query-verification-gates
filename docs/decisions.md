# Key design decisions

Each entry says what was decided and why, including what it costs.

## 1. A gate that is not exercised fails

A spec that leaves out the mapping gate would otherwise pass vacuously, and a green result would
suggest all four checks ran. Instead each gate needs at least one entry or an explicit waiver with a
reason. The waiver is printed in the report. Cost: a small spec needs a few waiver lines.

## 2. Two kinds of filter rule: assert and choice

Some filters are invariants (every join through a link table uses the composite key). Others are
judgment calls that depend on the analysis (is this an installment count or a contract count?).
Treating them alike either nags about invariants or lets judgment calls slip.

- `assert`: checked automatically when the rule applies. No declaration can waive it.
- `choice`: must be declared `applied` or `omitted`; an omission must carry a reason.

## 3. Detectors are heuristics, so disagreement is a warning

A regular expression can miss a valid spelling and can be fooled by an unusual one. If a failing
detector blocked the report, people would learn to bend the SQL to satisfy it. If a missing
declaration is blocking but a detector disagreement is only a warning, the human decision stays
primary and the tool stays honest about what it can see. Asserts are the exception because they
have no declaration to fall back on.

## 4. Typos in declarations are errors

A declaration for a key that is not in the registry (`date_windwo`) would otherwise be accepted
silently, and the rule it was meant to cover would look unaddressed or, worse, addressed. Unknown
keys are reported as a problem that blocks the ledger.

## 5. Independence is checked, not assumed

Gate 2 only means something when the two paths differ. Identical queries are rejected. Very similar
ones produce a warning. The comparison normalizes whitespace, case and comments but not string
literals, so two queries that filter on different literals are different. Identical text with
different parameters is allowed, because it is a different computation.

## 6. Relative tolerance, and claimed drift never fails

The paths are compared relatively, so the tolerance means the same thing for a count and for a
currency total. A previously claimed value is shown with its drift but cannot fail the gate: data
grows and relative dates move, so a changed number is not a verification failure. Only A against B
is.

## 7. Dedup is by installment, not by contract

The built-in dedup rule keeps the latest row per `(agreement_id, portfolio_id, installment_no)`.
Re-written rows are an installment-level phenomenon. Counting by contract is a separate metric with
a different denominator, and conflating the two changes ratios. The rule's description says so, so
the distinction is visible where the rule is used.

## 8. Read-only is layered, and the guard is honest about its limits

The guard refuses writes before they reach a cursor, SQLite is opened read-only at the engine
level, and the SQL Server adapter sets an isolation level on every statement. The documentation
says plainly that the guard is a pattern match and that a read-only login is the actual boundary.

## 9. Non-blocking isolation by default on SQL Server

Verification should never block the system it audits, so the default isolation level is
`READ UNCOMMITTED`. The cost is dirty reads, which can show up as small differences between two
paths run moments apart; that is what the tolerance is for. `QG_DB_ISOLATION` changes it, and the
value is checked against a fixed list before it is put into a statement.

## 10. Connection settings only from the environment

Credentials never appear in a spec, a command line or a report. The password is a non-printing
field in the settings object and is scrubbed from driver error text before it can be shown. An
`--env-file` option reads a local file into a copy of the environment; it never touches the real
one and never prints values.

## 11. Pattern-based SQL analysis, not a parser

A real parser per dialect would be heavy and would still disagree with the database in edge cases.
The helpers do one tokenizing pass for comments and literals, then regular expressions. Known
limits, all documented and tested: comma joins are not inspected, unqualified columns are not
attributed to a table, and a detector can miss an unusual spelling.

Detectors can also over-match, which is the more dangerous direction: a pattern that is present for
another purpose looks like the filter. The built-in detectors are therefore written to be as
specific as a regex allows. A status restriction needs a value on the right, so
`a.status_id = s.status_id` (a join) does not count. A `ROW_NUMBER()` dedup counts only when its rank
is filtered to 1. The composite-key assert needs both key equalities to involve the link table
(through its alias, or `USING`). Each of these has a test that fails against the looser pattern.
What remains cannot be fixed with a regex: an assert can still be satisfied by an unrelated
occurrence of its pattern in a large query, which is why a `choice` keeps a human declaration as
the primary control and why asserts are limited to patterns that are hard to produce by accident.
The guard against writes refuses on any doubt about the leading keyword, and is documented as a
pattern match, not a boundary.

## 12. One database interface, a stdlib adapter for tests

`SQLiteDatabase` ships in the standard library, so the whole tool can be exercised without a server,
and the test suite uses it for real queries rather than mocks. The SQL Server adapter is covered
through an injected fake DB-API connection that checks the exact statements and connection string
it produces. It has not been run against a live server.

## 13. Validate the spec before touching a database

Every structural problem in a spec is collected and reported together, with exit code 2, before any
connection is opened. A spec error should cost seconds, not a half-finished run.

## 14. Meaningful exit codes

`0` pass, `1` failed, `2` bad input, `3` inconclusive, `4` internal. A pipeline can tell "the
numbers are wrong" from "the database was unreachable" from "the spec has a typo", and a definite
failure outranks an inconclusive result.

## 15. No `.sql` files in the repository

The example spec carries its SQL inline as lists of lines. A spec may reference SQL files for real
use (`sql_file` and friends), but nothing in this repository needs one.
