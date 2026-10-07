# Architecture

## Shape

```
                       spec.json
                           |
                      spec.py  (parse + validate, all problems at once)
                           |
                         Spec
                           |
cli.py --opens--> adapters/ --Database--+
                                        |
                  runner.py ------------+
                     |
     +---------------+----------------+-----------------+
     |               |                |                 |
 gates/schema   gates/reconcile   gates/ledger     gates/mappings
 (Gate 1)       (Gate 2)          (Gate 3)         (Gate 4)
     |               |                |                 |
     |               |            registry.py           |
     |               |            (filter rules)        |
     +---------------+----------------+-----------------+
                           |
                        Report  --->  markdown | JSON | exit code
```

The gates are plain functions. They take a `Database` (or just SQL text, for Gate 3) and return a
small result object. They never print and never decide the process exit code. The runner collects
results and errors into a `Report`, and the report owns the verdict.

## Modules

| Module | Responsibility |
|---|---|
| `sqlutil.py` | Comment and literal stripping in one pass, the read-only guard, table and column reference extraction |
| `adapters/base.py` | The `Database` interface and `DBAPIDatabase`, a base class for any PEP 249 driver with an `INFORMATION_SCHEMA` catalog |
| `adapters/sqlite.py` | Standard-library adapter; opens files read-only and turns `query_only` on |
| `adapters/sqlserver.py` | Optional adapter: settings from `QG_DB_*`, connection-string quoting, isolation level before every statement, password scrubbing |
| `registry.py` | `FilterRule`, `FilterRegistry`, predicate helpers, the built-in rules, building a registry from spec config |
| `gates/schema.py` | Gate 1: one catalog read, every reference compared against it |
| `gates/reconcile.py` | Gate 2: independence checks, two single-row queries, relative comparison |
| `gates/ledger.py` | Gate 3: declarations parsed and judged against detectors |
| `gates/mappings.py` | Gate 4: per-scope catalog queries and their checks |
| `spec.py` | The JSON spec: dataclasses, validation, file references |
| `runner.py` | Orchestration; keeps verdicts apart from errors |
| `report.py` | The `Report` model, verdict and exit code, markdown and JSON renderers |
| `introspect.py` | Markdown dumps of live columns and code tables |
| `env.py` | A tiny `.env` reader |
| `cli.py` | `run`, `ledger`, `schema`, `introspect`, `rules` |

## The database interface

The gates need five things from a database:

```python
class Database:
    def query(self, sql, params=()) -> QueryResult      # one read-only statement
    def list_tables(self) -> set[str]                   # upper-cased
    def list_columns(self) -> set[str]                  # upper-cased TABLE.COLUMN, one round trip
    def describe_table(self, table) -> list[ColumnInfo]
    def close(self) -> None
```

Anything that can do those five things can be verified. `DBAPIDatabase` implements them over a
PEP 249 connection: it applies the read-only guard, calls a `_prepare` hook before each statement,
and wraps driver exceptions in `AdapterError`. `SQLiteDatabase` and `SQLServerDatabase` are thin
subclasses. A new engine is a subclass plus, if the catalog is not `INFORMATION_SCHEMA`, three
overrides.

Parameters use the `?` placeholder style, which both `sqlite3` and `pyodbc` accept.

## Read-only, in three layers

1. **Guard.** Before any statement reaches a cursor, `assert_read_only` strips comments and string
   literals, requires a single statement (a batch only where the adapter allows it, with each
   statement checked), requires an allowed leading keyword, and refuses write and procedure
   keywords, `SELECT ... INTO` included. It is a seat belt, not a security boundary.
2. **Engine.** SQLite files are opened with `mode=ro` and `PRAGMA query_only = ON`. The SQL Server
   adapter sets a non-blocking isolation level before every statement.
3. **Credentials.** The only real protection against a write is a database login that cannot
   write. Use one.

## Verdicts and errors

Two different things can go wrong, and the report keeps them apart:

- A **failure** is a verdict: the paths disagree, a column is missing, an assert is not detected,
  a mapping is wrong, a gate was not exercised. The numbers are not reportable.
- An **error** means a gate could not be evaluated: a query failed, a database was not opened, a
  write was refused. The report is inconclusive.

| Condition | Exit code |
|---|---|
| No failure and no error | 0 |
| At least one failure | 1 |
| Errors only | 3 |
| Bad spec or arguments (before anything runs) | 2 |
| Unhandled exception | 4 |

## Extension points

- **New rule**: build a `FilterRule` (Python) or add an entry under `registry.rules` (spec).
- **New engine**: subclass `Database` or `DBAPIDatabase`.
- **New check in an existing gate**: gates are small functions over result objects; add the check
  where the result is judged and a field to the result for the report.

## What is not here

No parser. SQL is treated as text, with the consequences described in `docs/decisions.md`. No
query execution plans, no state between runs, no network access of its own beyond the database
connection you give it.
