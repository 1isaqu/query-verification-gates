# Glossary

Terms used in the code, the spec and the docs.

## Verification terms

| Term | Meaning |
|---|---|
| Claim | A numeric statement about the business that is about to be reported |
| Gate | One of the four checks a claim must pass |
| Path A / path B | The two independent routes to the same number (Gate 2) |
| Relative difference | `abs(A - B) / max(abs(A), abs(B))`; zero when both are zero |
| Tolerance | The largest relative difference Gate 2 accepts |
| Claimed value | A number reported earlier; its drift from path A is shown but never fails |
| Filter ledger | The table that lists every applicable filter with its disposition (Gate 3) |
| Rule | One entry in the filter registry: key, kind, `applies`, `detect`, optional dialects |
| Assert | A rule that is checked automatically and cannot be declared away |
| Choice | A rule that must be declared applied or omitted (with a reason) |
| Disposition | `applied` or `omitted` |
| Detector | The regular-expression test that sees whether a filter is in the SQL |
| Waiver | A declared decision not to exercise a gate, with a reason |
| Scope | A partition of a catalog (database, product line, portfolio, tenant) |
| Mapping | The set of `(code, label)` pairs observed in one scope (Gate 4) |
| Dialect | The SQL flavor a ledger entry is written in; gates some rules |
| Verdict | A failure of a gate. The numbers are not reportable |
| Error | A gate that could not be evaluated. The report is inconclusive |
| As-of date | The date a claim refers to |

## Domain terms in the example schema

| Term | Meaning |
|---|---|
| Agreement | A repayment arrangement between a debtor and the portfolio owner, made of installments |
| Installment | One scheduled payment of an agreement. `installment_no = 0` is the first installment (down payment) |
| Re-written row | A second row for the same installment, created by a correction or a system retry. Only the latest counts |
| Generated agreement | An agreement counted on the day it was made, including ones that later break |
| Broken / auto-broken agreement | An agreement the debtor stopped honoring, or that lapsed after a grace period |
| Exception (pending approval) | An agreement outside normal limits, waiting for approval |
| Rejected | An approval that was denied |
| Portfolio | A set of debts owned by one client and collected together |
| Debt | One contract with an outstanding balance |
| Agent | A person (or automation) that works debts |
| Outreach attempt | One contact attempt (call, message) recorded as a contact event |
| Answered | Someone picked up |
| Right-party contact (CPC) | The person reached is the debtor or an authorized party: `answered = 1 AND right_party = 1` |
| Grace period | Days after a due date in which a payment still counts as on time |
| Half-open window | A date range with an inclusive lower and an exclusive upper bound: `>= start AND < end` |
