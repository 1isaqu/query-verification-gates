# query-verification-gates

A small Python library and command line tool that makes a numeric claim about a business auditable.
Before a number from analytical SQL is reported, it must pass four gates: the columns it relies on
exist in the live database, it reconciles when computed a second, independent way, every filter the
production query should carry is declared applied or deliberately omitted, and every code-to-label
mapping it depends on was re-derived for the scope it is used in. The tool exits non-zero when any
gate fails, so it fits in a pipeline as well as in a review.

It has no runtime dependencies (SQLite ships with Python), reads only, and never stores data.

## What it does and why it is interesting

Analytical SQL fails quietly. The query runs and returns a plausible figure, and the figure is wrong
because a duplicate row was counted twice, a test account was not excluded, a column meant
something else, or a status code was read through a label map that belongs to another scope. Nothing
raises an error. Each gate here targets one of those failure modes.

| Gate | Question it answers | How |
|---|---|---|
| 1. Schema | Does every table, column and code table the query uses exist? | One catalogue read per database; every reference is compared against it. Aliases in SQL are resolved to tables to seed the list |
| 2. Reconciliation | Do two independent routes to the number agree? | Two single-row queries compared with a relative tolerance. Identical queries are rejected, near-identical ones warned about, drift from a previously claimed value is shown but never fails |
| 3. Filter ledger | Is every filter the query should carry accounted for? | A registry of rules, each an `assert` (invariant, detected automatically) or a `choice` (must be declared `applied` or `omitted`, with a reason). Detectors cross-check the declarations |
| 4. Mappings | Does each code mean what the analysis assumes, in this scope? | A catalogue query per scope; missing labels, ambiguity, mismatches against an expected mapping, and the same code reading differently across scopes are reported |

Things that make it more than a checklist:

- The filter registry is user-extensible, from Python or from the JSON spec, and the built-in rules
  are only generic examples.
- A gate that is not exercised fails unless the spec waives it with a stated reason, so a spec
  cannot pass by leaving a check out.
- Verdicts (the numbers are wrong) are kept apart from errors (a gate could not be evaluated), and
  the exit code says which.
- Read-only is enforced in layers: a statement guard, engine-level read-only for SQLite, and a
  configurable isolation level for SQL Server.

## Quick start

Needs Python 3.10 or newer. This was developed and tested on 3.14.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python examples/build_demo_db.py demo.db        # a throwaway SQLite file with invented rows
query-gates run examples/spec.json --db main=demo.db
```

The demo database is built at run time from invented rows in `examples/build_demo_db.py`. It is
gitignored and never committed. The command prints a markdown report and exits 0:

```
# Verification: demo: generated amount and right-party contacts, January 2026

**Result: PASS** (exit code 0), 1 warning(s) listed at the end

...

| gate | status |
|---|---|
| Gate 1: schema | pass |
| Gate 2: reconciliation | pass |
| Gate 3: filter ledger | pass |
| Gate 4: per-scope mappings | pass |

...

## Gate 2: reconciliation

| claim | database | path A | path B | delta | tolerance | claimed (drift) | result |
|---|---|--:|--:|--:|--:|--:|---|
| Generated amount, January (deduplicated) | main | 1,105 | 1,105 | 0.000% | 0.500% | 1,105 (0.000%) | ok |
| Right-party contacts, January | main | 8 | 8 | 0.000% | 0.500% | 8 (0.000%) | ok |

...
```

(Abbreviated: the full report also lists the schema check, every ledger row and the per-scope
mapping tables.)

The one warning is intentional. The second ledger entry in the example spec checks a query that
deduplicates with an anti-join instead of `ROW_NUMBER()`; the detector cannot see that spelling, so
it says so while the analyst's declaration stands.

Now break something. Renaming a column makes Gate 1 fail and the exit code 1:

```bash
python -c "import sqlite3; c = sqlite3.connect('demo.db'); c.execute('ALTER TABLE agents RENAME COLUMN login TO username'); c.commit()"
query-gates run examples/spec.json --db main=demo.db
```

```
**Result: FAIL: do not report the numbers** (exit code 1), 1 warning(s) listed at the end
...
## Gate 1: schema

- main: 22 references checked, MISSING AGENTS.LOGIN
```

(Gate 2 shows `error` in that run because its queries use the missing column: the gate could not be
evaluated. A definite failure outranks an inconclusive result, so the exit code is 1.)

Other commands:

```bash
query-gates rules                                        # list the filter registry
query-gates ledger query.sql --declare status_set=applied \
    --declare "instalment_dedup=omitted:row counts only"  # check one SQL file, no database needed
query-gates schema query.sql --db main=demo.db           # assert the tables and columns it uses exist
query-gates introspect --db main=demo.db --code-table agreement_statuses:status_id:label
```

## Architecture

```
spec.json --> spec.py (validate) --> Spec
                                       |
cli.py --opens--> adapters/ --Database-+
                                       |
                 runner.py ------------+
                    |
    +---------------+---------------+----------------+
    |               |               |                |
 gates/schema  gates/reconcile  gates/ledger    gates/mappings
 (Gate 1)      (Gate 2)         (Gate 3)        (Gate 4)
                                    |
                               registry.py
                    |
                 Report --> markdown | JSON | exit code
```

The gates are plain functions that return small result objects. They never print and never choose
the exit code; the runner collects results into a `Report`, and the report owns the verdict. The
adapter layer is one small interface (`query`, `list_tables`, `list_columns`, `describe_table`,
`close`) with a base class for any PEP 249 driver. See `docs/architecture.md`.

## Using your own data

1. **Pick an adapter.** SQLite takes a file path. SQL Server takes a database name, with the server
   and credentials from environment variables (see Configuration). Any other PEP 249 driver can be
   wrapped with `DBAPIDatabase`.
2. **Map your schema, or your rules.** The tool itself needs no particular schema. The built-in
   filter rules and the demo assume the neutral schema in `docs/data-contract.md`. Either expose
   your tables under those names with views, or set `registry.builtin` to `false` and define rules
   for your own tables in the spec.
3. **Write a spec.** Start from `examples/spec.json`; `docs/spec-format.md` lists every key. Give
   each number two independent queries, declare the filters of the production query, and list the
   scopes whose code mappings the number depends on.
4. **Run it.** `query-gates run my-spec.json --db main=path/to/file.db` for SQLite, or
   `--adapter sqlserver --db main=DatabaseName` with `QG_DB_*` set.
5. **Calibrate the invented defaults.** The default excluded-actor patterns (`system%`, `test%`)
   and the default tolerance (0.5 percent) are invented defaults. Set the actor patterns to what
   your system uses for automation and test accounts, and the tolerance per claim.

Use a database login that cannot write. The read-only guard is a safety net, not a security
boundary.

From Python:

```python
from query_gates import SQLiteDatabase, load_spec, run_verification, to_markdown

spec = load_spec("examples/spec.json")
with SQLiteDatabase.from_path("demo.db") as database:
    report = run_verification(spec, {"main": database})
print(to_markdown(report))
raise SystemExit(report.exit_code)
```

A custom rule:

```python
from query_gates import FilterRule, builtin_registry
from query_gates.registry import ASSERT, matches_all, touches

registry = builtin_registry()
registry.register(FilterRule(
    key="tenant_scope", name="Tenant scope", kind=ASSERT,
    description="Every invoices query is scoped to one tenant.",
    applies=touches("invoices"), detect=matches_all(r"\btenant_id\s*="),
))
```

## Configuration

The SQL Server adapter is configured only through environment variables. Copy `.env.example` to a
gitignored `.env`, or pass `--env-file`. Variables that are already set win over the file.

| Variable | Default | Meaning |
|---|---|---|
| `QG_ADAPTER` | `sqlite` | Adapter when `--adapter` is not given: `sqlite` or `sqlserver` |
| `QG_DB_DRIVER` | `ODBC Driver 18 for SQL Server` | ODBC driver name |
| `QG_DB_HOST` | `localhost` | Server host |
| `QG_DB_PORT` | none | Server port |
| `QG_DB_USER` | empty | Login. Empty means integrated authentication |
| `QG_DB_PASSWORD` | empty | Password. Never printed, scrubbed from driver errors |
| `QG_DB_SCHEMA` | none | Restrict catalogue reads to one schema |
| `QG_DB_TRUST_SERVER_CERT` | `no` | `yes` only for local servers with self-signed certificates |
| `QG_DB_CHAR_ENCODING` | `utf-8` | Decoding for `CHAR` and `VARCHAR` columns |
| `QG_DB_ISOLATION` | `READ UNCOMMITTED` | One of `READ UNCOMMITTED`, `READ COMMITTED`, `REPEATABLE READ`, `SNAPSHOT`, `SERIALIZABLE` |
| `QG_DB_LOGIN_TIMEOUT` | `10` | Seconds |
| `QG_DB_QUERY_TIMEOUT` | `120` | Seconds |

Command line:

| Command | Purpose |
|---|---|
| `run SPEC` | All four gates from a spec. `--db NAME=TARGET` (repeatable), `--adapter`, `--env-file`, `--format markdown\|json`, `--output FILE` |
| `ledger SQL` | Gate 3 for one file. `--declare KEY=applied\|omitted[:reason]`, `--dialect`, `--spec`, `--format` |
| `schema SQL` | Gate 1 for one file. `--db`, `--adapter`, `--env-file`, `--code-table TABLE[:KEY:LABEL]` |
| `introspect` | Markdown dump of live columns and code tables. `--table`, `--code-table TABLE:KEY:LABEL` |
| `rules` | List the filter registry. `--spec` includes rules defined in a spec |

Exit codes: `0` every gate passed, `1` a gate failed or was neither exercised nor waived, `2` bad
spec or arguments, `3` inconclusive (a gate could not be evaluated or a database could not be
opened), `4` internal error.

## Running the tests

```bash
pip install -e ".[dev]"
python -m pytest
ruff check .
```

The suite uses tiny inline fixtures with invented values and in-memory SQLite for real queries. The
SQL Server adapter is tested through an injected fake DB-API connection that checks the statements
and the connection string it produces. A test also executes the quick start above: it builds the
demo database, runs `examples/spec.json`, and checks that seeded breakages (a renamed column, a
missing label, a changed label, a dropped filter on one path) flip the exit code.

## Project layout

```
src/query_gates/
  sqlutil.py            comment/literal stripping, read-only guard, reference extraction
  registry.py           filter rules, registry, built-in examples, spec-driven registry
  spec.py               JSON spec parsing and validation
  runner.py             runs the gates, separates verdicts from errors
  report.py             Report model, verdict, markdown and JSON renderers
  introspect.py         markdown dumps of live columns and code tables
  env.py                minimal .env reader
  cli.py, __main__.py   command line interface
  errors.py             exception types
  adapters/             Database interface, DB-API base, SQLite, SQL Server
  gates/                schema, reconcile, ledger, mappings
tests/                  pytest suite
examples/               demo database builder and example spec
docs/                   architecture, data contract, decisions, glossary, spec format, protocol
.github/workflows/      CI: install, lint, test, quick-start smoke test
```

## Key design decisions

The reasoning is in `docs/decisions.md`. In short:

- A gate that is not exercised fails unless waived with a reason.
- Two rule kinds: asserts cannot be declared away, choices must be declared with a reason.
- Detectors are heuristics, so a detector that disagrees with a declaration is a warning, not a
  block. Typos in declarations are errors.
- Independence of the two paths is checked, not assumed.
- Drift from a previously claimed value is reported but never fails the gate; only A against B does.
- Read-only in layers, with an honest statement that a read-only login is the real boundary.
- SQL is analysed as text, not parsed. Every limit errs toward a warning, never a silent pass.
- Verdicts and errors have different exit codes.

## Limitations

- **SQL Server is not exercised against a live server.** The adapter is unit-tested with a fake
  connection (statement text, connection-string construction and quoting, isolation level, the
  read-only guard, password scrubbing). The `INFORMATION_SCHEMA` queries and the `pyodbc` connector
  have never run against a real instance.
- **Only Python 3.14 was used locally.** `requires-python` is >= 3.10 and the CI workflow lists 3.10,
  3.12 and 3.14, but that workflow has not been run because the repository has not been pushed.
- **SQL is analysed with regular expressions.** Comma joins (`FROM a, b`) are not inspected by the
  non-blocking-hint rule, unqualified columns are not attributed to a table when inferring schema
  references, and a detector can miss an unusual spelling of a filter. It can tell that a filter is
  present, not that it is correct.
- **Gate 2 cannot prove independence.** It rejects identical queries and warns on near-identical
  ones; whether two routes really differ is the analyst's call.
- **The read-only guard is a pattern match.** It is a seat belt. Use a login without write rights.
- **Numbers are compared as floats.** Decimal results are converted, which is fine for the
  tolerances this is meant for and not for exact-cent equality checks.
- **Narrow query contract.** Parameters use `?` placeholders; a mapping query must return columns
  named `code` and `label`; a reconciliation query must return exactly one row.
- **The built-in rules assume the neutral schema** in `docs/data-contract.md`, and only the
  read-hint rule is dialect-gated.
- It verifies a claim; it does not profile data, fix queries, or schedule runs.

## License and provenance

MIT, see `LICENSE`. This is an independent portfolio project. It is not affiliated with any company,
and it contains no real data: every table name, portfolio label, agent id and row in the examples
and tests is invented.
