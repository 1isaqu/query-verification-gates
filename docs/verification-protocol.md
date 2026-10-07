# Verification protocol

This is the procedure the tool automates. It covers any numeric claim about a business that leaves a
working session: a KPI value, a count, a ratio, a total in an audit, a cohort statistic. Gate 2 can
be skipped for a write-up that only restates rules and runs no query. Gates 1, 3 and 4 still apply
to every query such a write-up cites.

All four gates must pass before a number is reported. When one fails, report the failure in place
of the number.

## Why gates

Analytical SQL fails quietly, and the README describes how. Each gate below targets one of those
failure modes.

## Gate 1: live schema assertion

Before running analytical SQL, assert that every referenced `table.column` and every code or label
table exists in the live database. Do not take a column name or a code set from nearby code or an
old document. Read it from the database.

- Run it once per database in scope. Two databases that share a layout are still separate
  catalogs.
- A reference helper can seed the list from raw SQL (aliases resolved to tables), but the explicit
  list is the authority. The helper is advisory: unqualified columns cannot be attributed to a
  table without a catalog.
- On failure, every missing reference is listed.

## Gate 2: dual-path reconciliation

Compute the number twice, by two independent grouping paths, and require the two results to agree
within a relative tolerance.

- Two paths are independent when they reach the same figure by different routes: a flat total and
  the sum of per-group totals, a window-function dedup and an anti-join dedup, a total by status and
  a total by scope, each summed.
- Two textually identical queries are rejected. Queries that are very similar produce a warning,
  because the groupings may not really differ. Identical text with different parameters is a
  different computation and is allowed.
- Each query must return exactly one row. Use one numeric column, or name the column to compare.
- A previously claimed value may be recorded. Drift from it is reported but never fails the gate:
  data grows and relative dates move. Only path A against path B decides.
- Timings and read counts have no second grouping path, so this gate does not apply to them.
  Repeat the measurement and compare the direction and size of the change.

## Gate 3: filter ledger

Every filter the production query is expected to apply must be listed in the output as applied or
deliberately omitted, with a reason. An unaddressed filter stops the report.

Registry entries come in two kinds:

| Kind | Meaning | Failure |
|---|---|---|
| assert | A correctness invariant. Checked automatically; nobody can declare it away. | Applies and not detected: the gate fails. |
| choice | A filter that is legitimately applicable or not depending on the analysis. | Applies and not declared, or omitted without a reason: the gate fails. |

Detectors are regular expressions over the SQL text. They cross-check the declaration; they do not
replace it. A declaration that the detector contradicts is a warning for a human, not a failure.
Declaring a filter that is not in the registry is an error, because it is almost always a typo that
would otherwise silently pass.

The registry is yours to extend. The built-in rules are generic examples (non-blocking read hint,
installment dedup, status set, half-open date window, excluded actors, composite join key,
right-party contact flags); see `docs/data-contract.md` for the tables they assume.

## Gate 4: per-scope code-to-label validation

Every code-to-label mapping is re-derived from the live catalog per scope and never reused across
scopes. A scope is whatever partitions your catalog: a database, a product line, a portfolio, a
tenant. The same code can read differently in two scopes.

For each scope the gate runs a catalog query that returns `code` and `label` and checks:

- every code has a label (a code missing from the catalog is an error);
- a code maps to one label within the scope (several labels is an error);
- the mapping matches the expected one, when the analysis assumed a mapping;
- a scope that returns no rows is unverifiable (a warning, or an error when rows are required);
- when the same code reads differently across scopes, a warning says so. That is the reason the
  gate exists, not a failure.

## Output contract

A reported number carries:

1. the filter ledger: each registry rule that applies to the query, with its declaration (applied,
   or omitted with its reason) and what the detector found;
2. both reconciled values and the delta;
3. the per-scope mapping table for every code-to-label the number depends on;
4. the schema assertion: how many references were checked and that all were present;
5. the as-of date, and for relative-date queries a note that re-running later moves the figure.

The markdown report produced by `query-gates run` has these parts. Its ledger table lists only the
rules that apply to each query. The JSON report also lists the rules that do not apply, with the
status `n/a`.

## Gates that do not apply

A gate with nothing to check is not silently skipped. The spec must either exercise it or waive it
with a stated reason (for example "no code columns are involved"). The waiver and its reason appear
in the report. A gate that is neither exercised nor waived fails.

## Limitations

- Gate 3 detectors are regular expressions over SQL text. An unusual spelling of a filter can slip
  past one (a warning for a choice, a failure for an assert), and a pattern that appears for some
  other reason can satisfy one. A detector confirms that a pattern is present, not that the filter
  is right, so the analyst's declaration is what counts.
- This tool does not read execution plans. It reports what queries return, not how they run.
- A claim pinned to a date window can be reproduced later. A claim relative to the current date
  cannot, so compare those on structure and ratio.
- Gate 2 is only as good as the second path. When a number has just one way to be computed, the
  gate has not been passed, and the claim should be reported as weaker.
