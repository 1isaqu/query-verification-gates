# Verification protocol

This is the procedure the tool automates. It applies to any numeric claim about a business that
leaves a working session: a KPI value, a count, a ratio, a total in an audit, a cohort statistic.
A report that only restates rules and executes no query is exempt from Gate 2; Gates 1, 3 and 4
still apply to every query it cites.

A number is reportable only when all four gates pass. If any gate fails, stop and report the
failure instead of the number.

## Why gates

Analytical SQL fails quietly. The query runs, returns a plausible figure, and is wrong because a
column meant something else, a duplicate row was counted twice, an exclusion filter was dropped, or
a code was read through a label map that belongs to a different scope. None of these raise an
error. Each gate targets one of these failure modes and makes the check explicit and repeatable.

## Gate 1: live schema assertion

Before running analytical SQL, assert that every referenced `table.column` and every code or label
table exists in the live database. Never infer a column name or a code set from neighbouring code
or from an old document: ask the database.

- Run it once per database in scope. Two databases that share a layout are still separate
  catalogues.
- A reference helper can seed the list from raw SQL (aliases resolved to tables), but the explicit
  list is the authority. The helper is advisory: unqualified columns cannot be attributed to a
  table without a catalogue.
- Failure: every missing reference is listed.

## Gate 2: dual-path reconciliation

Compute the number twice, by two independent grouping paths, and require the two results to agree
within a relative tolerance.

- "Independent" means a different route to the same figure: a flat total versus the sum of
  per-group totals, a window-function dedup versus an anti-join dedup, value-by-status versus
  value-by-scope summed.
- Two textually identical queries are rejected. Queries that are very similar produce a warning,
  because the groupings may not really differ. Identical text with different parameters is a
  different computation and is allowed.
- Each query must return exactly one row. Use one numeric column, or name the column to compare.
- A previously claimed value may be recorded. Drift from it is reported but never fails the gate:
  data grows and relative dates move. Only path A against path B decides.
- Measurement claims (timings, reads) have no grouping path. They are out of scope for this gate:
  re-run the measurement and compare direction and magnitude instead, and say so.

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

Every code-to-label mapping is re-derived from the live catalogue per scope and never reused across
scopes. A scope is whatever partitions your catalogue: a database, a product line, a portfolio, a
tenant. The same code can read differently in two scopes.

For each scope the gate runs a catalogue query that returns `code` and `label` and checks:

- every code has a label (a code missing from the catalogue is an error);
- a code maps to one label within the scope (several labels is an error);
- the mapping matches the expected one, when the analysis assumed a mapping;
- a scope that returns no rows is unverifiable (a warning, or an error when rows are required);
- when the same code reads differently across scopes, a warning says so. That is the reason the
  gate exists, not a failure.

## Output contract

A reported number carries:

1. the filter ledger: every applicable registry row marked applied, omitted (with its reason) or
   not applicable;
2. both reconciled values and the delta;
3. the per-scope mapping table for every code-to-label the number depends on;
4. the schema assertion: how many references were checked and that all were present;
5. the as-of date, and for relative-date queries a note that re-running later moves the figure.

The markdown report produced by `query-gates run` has exactly these parts.

## Gates that do not apply

A gate with nothing to check is not silently skipped. The spec must either exercise it or waive it
with a stated reason (for example "no code columns are involved"). The waiver and its reason appear
in the report. A gate that is neither exercised nor waived fails.

## Limitations

- Pattern-based detection. Gate 3 reads SQL as text. It can miss a filter written in an unusual
  way (a warning for a choice, a failure for an assert), and it can be satisfied by an unrelated
  occurrence of the same pattern. It cannot tell whether a filter is correct, only that a pattern
  is present, so the declaration stays the primary control.
- No execution plans. The tool reports what queries return, not how they run. Plan and index
  claims must be marked as inferred.
- Data drift. A claim pinned to a date window is reproducible; a claim relative to "now" is not.
  Compare those on structure and ratio.
- Gate 2 needs a real second path. If a number can only be computed one way, it has not passed
  Gate 2, and the claim is weaker for it.
