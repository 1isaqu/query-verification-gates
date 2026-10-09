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
| `sqlutil.py` | Comment and literal stripping in one pass, the statement guard, table and column reference extraction |
| `adapters/base.py` | The `Database` interface and `DBAPIDatabase`, a base class for any PEP 249 driver with an `INFORMATION_SCHEMA` catalog. It applies the guard and rolls back after every statement |
| `adapters/sqlite.py` | Standard-library adapter; opens files read-only and turns `query_only` on |
| `adapters/sqlserver.py` | Optional adapter: settings from `QG_DB_*`, connection-string quoting, autocommit off, isolation level before every statement, password scrubbing in connection errors |
| `registry.py` | `FilterRule`, `FilterRegistry`, predicate helpers, the built-in rules, building a registry from spec config |
| `gates/schema.py` | Gate 1: two catalog queries (columns, then tables), every reference compared against the results |
| `gates/reconcile.py` | Gate 2: independence checks, two single-row queries, relative comparison |
| `gates/ledger.py` | Gate 3: declarations parsed and judged against detectors |
| `gates/mappings.py` | Gate 4: per-scope catalog queries and their checks |
| `spec.py` | The JSON spec: dataclasses, validation, file references |
| `runner.py` | Orchestration; keeps verdicts apart from errors |
| `report.py` | The `Report` model, verdict and exit code, markdown and JSON renderers |
| `introspect.py` | Markdown dumps of live columns and code tables |
| `env.py` | A tiny `.env` reader |
| `cli.py` | `run`, `ledger`, `schema`, `mappings`, `introspect`, `rules` |

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

Anything that can do those five things can be verified. `list_table_names()` is a sixth, optional
method: it returns the catalog's own spelling of each table name for display, and defaults to the
upper-cased names from `list_tables()`. `DBAPIDatabase` implements all of them over a PEP 249
connection: it applies the statement guard, calls a `_prepare` hook before each statement, rolls
the connection back after it, and wraps driver exceptions in `AdapterError`. `SQLiteDatabase` and
`SQLServerDatabase` are thin subclasses. A new engine is a subclass plus, if the catalog is not
`INFORMATION_SCHEMA`, three overrides: `list_table_names`, `list_columns` and `describe_table`.

Parameters use the `?` placeholder style, which both `sqlite3` and `pyodbc` accept.

## Statement checks

Before any statement reaches a cursor, `assert_read_only` strips comments and string literals,
splits the text on `;`, and requires every piece to start with an allowed keyword. It also searches
each piece for write, procedure and administration keywords (`SELECT ... INTO` and pass-through
functions such as `OPENQUERY` included). T-SQL accepts several statements in one batch with no
separator, so the guard cannot count statements. The keyword search is the only thing that can
notice a second one, and it knows only the words on its list. Adapters that allow batches (SQL
Server, for a leading `DECLARE`) have every piece checked.

The token pass that blanks literals and comments follows SQL Server and standard quoting:
`'...'` with `''` for a quote, `[...]` with `]]` for a closing bracket, `"..."` with `""`, `--`
line comments and `/* */` block comments. Two kinds of text are refused outright because engines
read them differently:

- a block comment that contains a second `/*`. T-SQL and PostgreSQL nest comments, SQLite and MySQL
  end the comment at the first `*/`, so `/* /* */ ' */ ... --'` is a comment plus a literal for one
  and a comment plus a statement for the other;
- a comment that starts `/*!` or `/*M!`, whose body MySQL and MariaDB execute.

What the token pass does not model: backslash escapes inside strings, backtick identifiers and
dollar-quoted strings. On an engine that uses them (MySQL, PostgreSQL) a crafted string can hide a
statement from the guard. The guard is pattern matching and nothing more.

SQLite files are opened with `mode=ro` and `PRAGMA query_only = ON`, so on SQLite the engine itself
refuses writes.

The SQL Server adapter has no engine-side read-only mode. It opens pyodbc with autocommit off and
`DBAPIDatabase` rolls back after every statement, successful or not, so an ordinary data or schema
change that got past the guard is undone. Some effects survive a rollback (a sequence value, a
server-level command). The isolation level set before every statement keeps the verifier from
blocking other sessions; it does not stop writes. Only a login without write rights is a real
boundary.

## Verdicts and errors

Two different things can go wrong, and the report keeps them apart:

- A failure is a verdict: the paths disagree, a column is missing, an assert is not detected,
  a mapping is wrong, a gate was not exercised. The numbers are not reportable.
- An error means a gate could not be evaluated: a query failed, a database was not opened, a
  write was refused. The report is inconclusive.

| Condition | Exit code |
|---|---|
| No failure and no error | 0 |
| At least one failure | 1 |
| Errors only | 3 |
| Bad spec or arguments (before anything runs) | 2 |
| Unhandled exception | 4 |

## Extension points

- A new rule is a `FilterRule` built in Python, or an entry under `registry.rules` in the spec.
- A new engine is a subclass of `Database` or `DBAPIDatabase`.
- A new check in an existing gate goes where the gate judges its result object, with a field on
  that object for the report. Gates are small functions over result objects.

## What is not here

No parser. SQL is treated as text, with the consequences described in `docs/decisions.md`. No
query execution plans, no state between runs, no network access of its own beyond the database
connection you give it.
