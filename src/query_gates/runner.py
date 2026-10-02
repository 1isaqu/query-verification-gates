"""Run a spec against open databases and produce a ``Report``.

Failures of a gate (a number that does not reconcile, a missing column) and failures to evaluate a
gate (the connection dropped, a query was rejected) are kept apart: the first is a verdict, the
second is an error that makes the report inconclusive.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from query_gates.adapters import Database
from query_gates.errors import QueryGatesError
from query_gates.gates.ledger import evaluate_ledger
from query_gates.gates.mappings import check_mappings
from query_gates.gates.reconcile import reconcile
from query_gates.gates.schema import check_schema
from query_gates.registry import FilterRegistry
from query_gates.report import GateError, Report
from query_gates.spec import Spec
from query_gates.sqlutil import extract_refs, extract_tables


def run_verification(
    spec: Spec,
    databases: Mapping[str, Database],
    *,
    registry: FilterRegistry | None = None,
    now: datetime | None = None,
) -> Report:
    """Evaluate all four gates. ``databases`` maps each name in ``spec.databases`` to a database."""
    moment = now or datetime.now(timezone.utc)
    report = Report(
        analysis=spec.analysis,
        as_of=spec.as_of or moment.date().isoformat(),
        generated_at=moment.replace(microsecond=0).isoformat(),
        waivers=dict(spec.waivers),
    )
    _run_schema(spec, databases, report)
    _run_claims(spec, databases, report)
    _run_ledgers(spec, registry or spec.registry, report)
    _run_mappings(spec, databases, report)
    return report


def _database(databases: Mapping[str, Database], name: str) -> Database:
    if name not in databases:
        raise QueryGatesError(f"database {name!r} was not opened")
    return databases[name]


def _describe(exc: Exception) -> str:
    return str(exc) if isinstance(exc, QueryGatesError) else f"{type(exc).__name__}: {exc}"


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _run_schema(spec: Spec, databases: Mapping[str, Database], report: Report) -> None:
    if spec.schema is None:
        return
    for name in spec.databases:
        columns, tables = list(spec.schema.columns), list(spec.schema.tables)
        if spec.schema.infer_from_queries:
            for sql in spec.sql_for(name):
                columns += extract_refs(sql)
                tables += extract_tables(sql)
        try:
            report.schema.append(
                check_schema(
                    name,
                    _database(databases, name),
                    columns=_unique(columns),
                    tables=_unique(tables),
                    code_tables=spec.schema.code_tables,
                )
            )
        except Exception as exc:  # noqa: BLE001 - any failure makes the gate inconclusive
            report.errors.append(GateError("schema", name, _describe(exc)))


def _run_claims(spec: Spec, databases: Mapping[str, Database], report: Report) -> None:
    for claim in spec.claims:
        try:
            report.reconciliations.append(
                reconcile(
                    _database(databases, claim.database),
                    claim.label,
                    claim.query_a,
                    claim.query_b,
                    database_name=claim.database,
                    tolerance=claim.tolerance,
                    claimed=claim.claimed,
                    params_a=claim.params_a,
                    params_b=claim.params_b,
                    column=claim.column,
                )
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(GateError("reconciliation", claim.label, _describe(exc)))


def _run_ledgers(spec: Spec, registry: FilterRegistry, report: Report) -> None:
    for entry in spec.ledgers:
        report.ledgers.append(
            evaluate_ledger(
                entry.sql, entry.declared, registry, name=entry.name, dialect=entry.dialect
            )
        )


def _run_mappings(spec: Spec, databases: Mapping[str, Database], report: Report) -> None:
    for entry in spec.mappings:
        try:
            report.mappings.append(
                check_mappings(
                    _database(databases, entry.database),
                    name=entry.name,
                    database_name=entry.database,
                    scopes=entry.scopes,
                    query=entry.query,
                    params=entry.params,
                    expected=entry.expected,
                    require_rows=entry.require_rows,
                )
            )
        except Exception as exc:  # noqa: BLE001
            report.errors.append(GateError("mappings", entry.name, _describe(exc)))
