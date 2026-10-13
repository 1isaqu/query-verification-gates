# Data contract

`query-gates` itself needs no particular schema. Gates 1, 2 and 4 run whatever SQL your spec
contains, and Gate 3 runs whatever rules your registry contains.

The built-in rules, the demo database and the tests assume the neutral collections
schema below. Point your own database at it with views or table aliases, or replace the built-in
rules with ones that match your schema (see "Adapting the built-in rules").

All names are generic. Types are indicative.

## Tables

| Table | Meaning | Key columns |
|---|---|---|
| `agreements` | One row per installment of an agreement | `agreement_id`, `portfolio_id`, `installment_no` (0 = first installment), `amount`, `paid_amount`, `issued_at`, `due_at`, `paid_at`, `status_id`, `agent_id` |
| `agreement_debts` | Links agreements to debts | `agreement_id`, `portfolio_id`, `debt_id` |
| `debts` | Debt (contract) detail | `debt_id`, `portfolio_id`, `contract_no`, `balance`, `due_at`, `days_overdue` |
| `debt_attributes` | Extra attributes; holds the portfolio label | `debt_id`, `portfolio_label` |
| `contact_events` | One row per outreach attempt | `event_id`, `debt_owner_id`, `result_id`, `agent_id`, `occurred_at` |
| `contact_results` | Result catalog | `result_id`, `label`, `answered` (bit), `right_party` (bit) |
| `agreement_statuses` | Status catalog | `status_id`, `label` |
| `agents` | Users | `agent_id`, `display_name`, `login` |

Notes:

- `agreements` has more than one row per `(agreement_id, portfolio_id, installment_no)` when an
  installment was re-written. The demo and the tests add a `row_id` column as a tie-breaker for
  "latest row"; any monotonic column works.
- `contact_events.debt_owner_id` joins to `debts.debt_id` in the demo. Map it to whatever links an
  outreach attempt to a debt in your system.
- No table here holds a personal identifier, and none should be added for verification.

## Status semantics

The example data uses these statuses. The ids are invented; your ids differ. What matters is the
meaning, and that the sets built from them are shared constants rather than literals typed per
query.

| Label | Meaning |
|---|---|
| `ACTIVE` | Agreement in force |
| `BROKEN` | Agreement broken by the debtor |
| `PAID` | Paid |
| `PENDING_EXCEPTION` | Waiting for an exception approval |
| `REJECTED` | Approval denied |
| `AUTO_BROKEN` | Broken automatically after a grace period |
| `PAID_MANUAL` | Paid and settled by hand |

Sets such as "approved" and "generated" are configuration, not schema. The demo treats the
generated set as `ACTIVE`, `BROKEN`, `PAID`, `AUTO_BROKEN` and `PAID_MANUAL`: an agreement counts
as generated on the day it is made, even if it later breaks.

## Standard rules the built-ins encode

| Rule key | Kind | Applies when SQL touches | What it expects |
|---|---|---|---|
| `read_only_hint` | assert (dialect `sqlserver`) | any table | Every table reference carries a `NOLOCK` or `READUNCOMMITTED` hint, written `WITH (...)` or in the legacy `(NOLOCK)` form. The hint list may hold other hints, including one with its own parentheses such as `INDEX(ix)` |
| `composite_agreement_key` | assert | `agreement_debts` | Both `agreement_id` and `portfolio_id` equated through the `agreement_debts` alias, or `USING (agreement_id, portfolio_id)` |
| `right_party_two_flag` | assert | `contact_results` | `answered = 1 AND right_party = 1`, in either order |
| `installment_dedup` | choice | `agreements` | `ROW_NUMBER() OVER (PARTITION BY` agreement_id, portfolio_id, installment_no `ORDER BY ... DESC)` whose rank is then filtered to 1 |
| `status_set` | choice | `agreements` | `status_id` in a list, or compared (`=`, `<>`, `!=`) with a literal or parameter. A comparison with another column (a join) does not count |
| `date_window` | choice | `agreements`, `contact_events` | `issued_at` or `occurred_at` with `>=` as the lower and `<` as the upper bound |
| `excluded_actors` | choice | `agents` | `NOT LIKE` for each configured pattern; defaults `system%` and `test%`. A pattern with no `%` or `_` (an exact login) may also use `<>`, `!=` or `NOT IN`; those compare literally, so they do not count for a wildcard pattern. Pattern text is compared without regard to case |

The default actor patterns are invented. Calibrate them to whatever your own system uses for
automation and test accounts: `registry.excluded_actor_patterns` in the spec, or
`query_gates.registry.excluded_actors_rule([...])` from Python.

## Adapting the built-in rules

Three options, from least to most work:

1. Create views named as above over your tables. Nothing else changes.
2. Set `registry.builtin` to `false` (or list keys under `registry.disable`) and define rules for
   your own tables with `applies_when` / `detected_when` regular expressions. See
   `docs/spec-format.md`.
3. Build a `FilterRegistry` in Python and register `FilterRule` objects with arbitrary predicates.

## Connecting a database

| Adapter | Target | Settings |
|---|---|---|
| `sqlite` | A file path (opened read-only) | none |
| `sqlserver` | A database name on the server | `QG_DB_*` environment variables, see the README and `.env.example` |

The SQL Server adapter uses `INFORMATION_SCHEMA` for Gate 1 and the introspection command. Any
other PEP 249 driver whose catalog exposes `INFORMATION_SCHEMA.TABLES` and `.COLUMNS` can be
wrapped with `DBAPIDatabase`.
