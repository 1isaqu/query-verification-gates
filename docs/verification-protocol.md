# Verification protocol

This is the procedure the tool automates. It covers any numeric claim about a business that leaves a
working session: a KPI value, a count, a ratio, a total in an audit, a cohort statistic. A document
that only restates rules and executes nothing needs no reconciliation (Gate 2). Gates 1, 3 and 4
still cover any query it cites.

A number is reported only when all four gates pass. If one fails, report that failure and leave the
number out.

## Why gates

Analytical SQL fails quietly, and the README describes how. Each gate below targets one of those
failure modes.

## Gate 1: live schema assertion

Before the analysis runs, check that each `table.column` it references, and each code or label
table, exists in the live database. Do not take a column name or a code set from nearby code or an
old document. Read it from the database.

- Run it once per database in scope. Two databases that share a layout are still separate
  catalogs.
- A reference helper can seed the list from raw SQL (aliases resolved to tables), but the explicit
  list is the authority. The helper is advisory: unqualified columns cannot be attributed to a
  table without a catalog.
- On failure, every missing reference is listed.

## Gate 2: dual-path reconciliation

Get the number by two routes that do not share a grouping, and require the results to agree within
a relative tolerance.

- Two paths are independent when they reach the same figure by different routes: a flat total and
  the sum of per-group totals, a window-function dedup and an anti-join dedup, a total by status and
  a total by scope, each summed.
- Identical query text is rejected. Near-identical text produces a warning, since the two routes
  may not differ in substance. Identical text with different parameters is a different computation
  and is allowed.
- Each query must return exactly one row. Use one numeric column, or name the column to compare.
- A previously claimed value may be recorded. The report shows the drift from it, but the drift
  cannot fail the gate: a figure that moved since it was first reported is expected when data keeps
  arriving. Only path A against path B decides.
- Timings and read counts have no second grouping path, so this gate does not apply to them.
  Repeat the measurement and compare the direction and size of the change.

## Gate 3: filter ledger

The output must account for every filter the production query is expected to carry, either as
applied or as omitted on purpose with a stated reason. A filter nobody addressed blocks the report.

Registry entries come in two kinds:

| Kind | Meaning | Failure |
|---|---|---|
| assert | A correctness invariant. Checked automatically; nobody can declare it away. | Applies and not detected: the gate fails. |
| choice | A filter that is legitimately applicable or not depending on the analysis. | Applies and not declared, or omitted without a reason: the gate fails. |

Detectors are regular expressions over the SQL text and serve as a second opinion on the
declaration. When a detector contradicts a declaration, the report shows a warning and a person
decides. It is not a failure.
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
- A number that can be computed only one way cannot pass Gate 2. Report it with that caveat.
