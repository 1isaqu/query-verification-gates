# Data sources

This repository ships no dataset and reads no data of its own. Data comes in three tiers; only the
first one exists here.

| Tier | What it is | Status in this repository |
|---|---|---|
| 0 | Seeded synthetic data | Yes. Invented rows in `examples/build_demo_db.py` and small inline fixtures in `tests/`. Every id, amount, date, status label and login is arbitrary. None was fitted to, or checked against, real data |
| 1 | A public dataset fetched at run time | None. Nothing is downloaded |
| 2 | Your own database | Supported through an adapter. It is never committed, and the repository holds no connection details beyond placeholder names in `.env.example` |

## Tier 0 in detail

- `examples/build_demo_db.py` writes a throwaway SQLite file when you run it. The file is
  gitignored. The rows exist to contain the situations the gates look for (a re-written
  installment, rows on both edges of a date window, actors that must be excluded, statuses outside
  the generated set), not to resemble any real portfolio.
- The tests build their own tiny tables inline, in memory. They contain no fixture files.
- The status labels, the portfolio labels (`Portfolio A-01`) and the agent ids (`agent_01`) are
  invented. The default excluded-actor patterns (`system%`, `test%`) and the default tolerance
  (0.5 percent) are placeholders to replace with values calibrated on your own data.

## Tier 2: connecting your own data

Point an adapter at your database. `docs/data-contract.md` lists the table and column names the
built-in rules and the demo assume, and how to map your schema onto them. Reports can contain
values taken from your tables (code labels, query results), so read a report before sharing it.
