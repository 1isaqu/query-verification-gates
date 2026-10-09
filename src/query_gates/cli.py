"""Command line interface.

Exit codes
    0  every gate passed
    1  a gate was evaluated and failed (the numbers are not reportable)
    2  bad spec or arguments (nothing was run)
    3  inconclusive: a gate could not be evaluated, or a database could not be opened
    4  internal error
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path

from query_gates import __version__
from query_gates.adapters import ADAPTERS, Database, open_database
from query_gates.env import load_env_file
from query_gates.errors import QueryGatesError, SpecError
from query_gates.gates.ledger import DISPOSITIONS, LedgerResult, evaluate_ledger
from query_gates.gates.mappings import MappingResult, check_mappings
from query_gates.gates.schema import CodeTable, check_schema
from query_gates.introspect import describe_tables, dump_code_table
from query_gates.registry import FilterRegistry, builtin_registry
from query_gates.report import (
    ledger_md,
    ledger_to_dict,
    mapping_md,
    mapping_to_dict,
    to_dict,
    to_markdown,
)
from query_gates.runner import run_verification
from query_gates.spec import load_spec, parse_spec
from query_gates.sqlutil import extract_refs, extract_tables

EXIT_USAGE, EXIT_INCONCLUSIVE, EXIT_INTERNAL = 2, 3, 4


# --------------------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="query-gates",
        description="Make numeric claims about a business auditable: four gates over SQL.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run all four gates from a JSON spec")
    run.add_argument("spec", type=Path)
    run.add_argument("--format", choices=("markdown", "json"), default="markdown")
    run.add_argument("--output", type=Path, help="write the report here instead of stdout")
    _add_database_options(run)
    run.set_defaults(handler=_cmd_run)

    ledger = sub.add_parser("ledger", help="check one SQL file against the filter registry")
    ledger.add_argument("sql", type=Path)
    ledger.add_argument(
        "--declare", action="append", default=[], metavar="KEY=applied|omitted[:why]"
    )
    ledger.add_argument("--dialect", default="generic")
    ledger.add_argument("--spec", type=Path, help="use the registry defined in this spec")
    ledger.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ledger.set_defaults(handler=_cmd_ledger)

    schema = sub.add_parser("schema", help="assert the tables and columns a SQL file uses exist")
    schema.add_argument("sql", type=Path)
    schema.add_argument("--code-table", action="append", default=[], metavar="TABLE[:KEY:LABEL]")
    _add_database_options(schema)
    schema.set_defaults(handler=_cmd_schema)

    mappings = sub.add_parser("mappings", help="re-derive code-to-label mappings per scope")
    mappings.add_argument("query", type=Path, help="SQL with one ? that returns code and label")
    mappings.add_argument("--scope", action="append", required=True, metavar="SCOPE")
    mappings.add_argument("--expected", type=Path, help='JSON file: {"SCOPE": {"CODE": "label"}}')
    mappings.add_argument("--require-rows", action="store_true", help="an empty scope is an error")
    mappings.add_argument("--format", choices=("markdown", "json"), default="markdown")
    _add_database_options(mappings)
    mappings.set_defaults(handler=_cmd_mappings)

    intro = sub.add_parser("introspect", help="dump columns and code tables as markdown")
    intro.add_argument("--table", action="append", default=[], help="limit to these tables")
    intro.add_argument("--code-table", action="append", default=[], metavar="TABLE:KEY:LABEL")
    _add_database_options(intro)
    intro.set_defaults(handler=_cmd_introspect)

    rules = sub.add_parser("rules", help="list the filter registry")
    rules.add_argument("--spec", type=Path, help="include rules defined in this spec")
    rules.set_defaults(handler=_cmd_rules)
    return parser


def _add_database_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter", choices=ADAPTERS, help="default: $QG_ADAPTER or sqlite")
    parser.add_argument(
        "--db",
        action="append",
        default=[],
        metavar="NAME=TARGET",
        help="map a database name to a SQLite file or a SQL Server database name",
    )
    parser.add_argument("--env-file", type=Path, help="read QG_DB_* settings from this file")


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def _environment(args: argparse.Namespace, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    if getattr(args, "env_file", None):
        load_env_file(args.env_file, env)
    return env


def _targets(items: Sequence[str]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for item in items:
        name, sep, target = item.partition("=")
        if not sep or not name.strip() or not target.strip():
            raise SpecError([f"--db expects NAME=TARGET, got {item!r}"])
        targets[name.strip()] = target.strip()
    return targets


@contextmanager
def _opened(
    args: argparse.Namespace, env: Mapping[str, str], names: Sequence[str]
) -> Iterator[dict[str, Database]]:
    adapter = args.adapter or env.get("QG_ADAPTER") or "sqlite"
    targets = _targets(args.db)
    with ExitStack() as stack:
        opened: dict[str, Database] = {}
        for name in names:
            target = targets.get(name) or (name if adapter == "sqlserver" else "")
            if not target:
                raise SpecError([f"no --db {name}=PATH given for database {name!r}"])
            opened[name] = stack.enter_context(open_database(adapter, target, env=env))
        yield opened


def _parse_code_table(text: str) -> CodeTable:
    parts = text.split(":")
    if len(parts) not in (1, 3) or not all(parts):
        raise SpecError([f"--code-table expects TABLE or TABLE:KEY:LABEL, got {text!r}"])
    return CodeTable(*parts)


def _parse_declares(items: Sequence[str]) -> dict[str, list[str]]:
    declared: dict[str, list[str]] = {}
    for item in items:
        key, sep, rest = item.partition("=")
        disposition, _, reason = rest.partition(":")
        if not sep or not key or disposition.lower() not in DISPOSITIONS:
            raise SpecError([f"--declare expects KEY=applied|omitted[:reason], got {item!r}"])
        declared[key.strip()] = [disposition.lower(), reason.strip()]
    return declared


def _emit(text: str, output: Path | None, summary: str = "") -> None:
    if output is None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        return
    output.write_bytes(text.encode("utf-8"))
    print(f"{summary}report written to {output}")


def _registry(spec_path: Path | None) -> FilterRegistry:
    return load_spec(spec_path).registry if spec_path else builtin_registry()


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    spec = load_spec(args.spec)
    with _opened(args, env, spec.databases) as databases:
        report = run_verification(spec, databases)
    text = json.dumps(to_dict(report), indent=2) if args.format == "json" else to_markdown(report)
    _emit(text, args.output, summary=f"{report.status.upper()}: ")
    return report.exit_code


def _cmd_ledger(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    sql = _read_text(args.sql)
    result = evaluate_ledger(
        sql,
        _parse_declares(args.declare),
        _registry(args.spec),
        name=args.sql.name,
        dialect=args.dialect,
    )
    _emit(_render_ledger(result, args.format), None)
    return 0 if result.ok else 1


def _render_ledger(result: LedgerResult, fmt: str) -> str:
    return json.dumps(ledger_to_dict(result), indent=2) if fmt == "json" else ledger_md(result)


def _cmd_schema(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    sql = _read_text(args.sql)
    columns, tables = extract_refs(sql), extract_tables(sql)
    code_tables = [_parse_code_table(text) for text in args.code_table]
    print(f"inferred from SQL (advisory): {len(tables)} table(s), {len(columns)} column(s)")
    names = list(_targets(args.db)) or _no_database()
    ok = True
    with _opened(args, env, names) as databases:
        for name, database in databases.items():
            result = check_schema(
                name, database, columns=columns, tables=tables, code_tables=code_tables
            )
            verdict = "all present" if result.ok else "MISSING " + ", ".join(result.missing)
            print(f"{name}: {len(result.checked)} references checked, {verdict}")
            ok = ok and result.ok
    return 0 if ok else 1


def _cmd_mappings(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    names = list(_targets(args.db)) or _no_database()
    entry = {
        "name": args.query.name,
        "scopes": args.scope,
        "query": _read_text(args.query),
        "expected": _read_json(args.expected) if args.expected else {},
        "require_rows": args.require_rows,
    }
    spec = parse_spec(
        {
            "analysis": entry["name"],
            "databases": names,
            "mappings": [{**entry, "database": name} for name in names],
        }
    )
    with _opened(args, env, names) as databases:
        results = [
            check_mappings(
                databases[e.database],
                name=e.name,
                database_name=e.database,
                scopes=e.scopes,
                query=e.query,
                params=e.params,
                expected=e.expected,
                require_rows=e.require_rows,
            )
            for e in spec.mappings
        ]
    _emit(_render_mappings(results, args.format), None)
    return 0 if all(r.ok for r in results) else 1


def _render_mappings(results: Sequence[MappingResult], fmt: str) -> str:
    if fmt == "json":
        return json.dumps([mapping_to_dict(r) for r in results], indent=2)
    return "\n\n".join(mapping_md(r) for r in results)


def _cmd_introspect(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    code_tables = [_parse_code_table(text) for text in args.code_table]
    names = list(_targets(args.db)) or _no_database()
    with _opened(args, env, names) as databases:
        for name, database in databases.items():
            print(f"# {name}\n")
            print(describe_tables(database, args.table or None))
            for code_table in code_tables:
                print(dump_code_table(database, code_table))
    return 0


def _cmd_rules(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    lines = ["| key | kind | dialects | description |", "|---|---|---|---|"]
    for rule in _registry(args.spec):
        dialects = ", ".join(sorted(rule.dialects)) if rule.dialects else "all"
        lines.append(f"| {rule.key} | {rule.kind} | {dialects} | {rule.description} |")
    _emit("\n".join(lines), None)
    return 0


def _no_database() -> list[str]:
    raise SpecError(["at least one --db NAME=TARGET is required"])


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError([f"cannot read {path}: {exc.strerror or exc}"]) from None


def _read_json(path: Path) -> object:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise SpecError([f"{path} is not valid JSON: {exc}"]) from None


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    """Run the CLI and return the exit code. ``argparse`` exits with 2 on a usage error."""
    for stream in (sys.stdout, sys.stderr):
        # A Windows console defaults to a legacy code page and would garble non-ASCII labels.
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    args = build_parser().parse_args(argv)
    try:
        env = _environment(args, os.environ if environ is None else environ)
        return int(args.handler(args, env))
    except SpecError as exc:
        print("error: " + "\nerror: ".join(exc.problems), file=sys.stderr)
        return EXIT_USAGE
    except QueryGatesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INCONCLUSIVE
    except Exception:  # noqa: BLE001 - last resort, keep the traceback visible
        traceback.print_exc()
        return EXIT_INTERNAL
