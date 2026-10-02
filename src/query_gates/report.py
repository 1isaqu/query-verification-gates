"""The verification report: the data model, the verdict and its two renderings.

Verdict and exit codes
    0  every gate passed
    1  at least one gate was evaluated and failed (or was neither exercised nor waived)
    3  nothing failed outright but at least one gate could not be evaluated (inconclusive)

Exit code 2 (bad spec or arguments) never reaches a report; it is raised before anything runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from query_gates.gates.ledger import LedgerResult
from query_gates.gates.mappings import MappingResult
from query_gates.gates.reconcile import Reconciliation
from query_gates.gates.schema import SchemaResult

PASS, FAIL, ERROR = "pass", "fail", "error"
EXIT_PASS, EXIT_FAIL, EXIT_INCONCLUSIVE = 0, 1, 3
GATE_TITLES = {
    "schema": "Gate 1: schema",
    "reconciliation": "Gate 2: reconciliation",
    "ledger": "Gate 3: filter ledger",
    "mappings": "Gate 4: per-scope mappings",
}


@dataclass
class GateError:
    """A gate that could not be evaluated (connection lost, query failed, guard refused)."""

    gate: str
    subject: str
    message: str


@dataclass
class Report:
    analysis: str
    as_of: str
    generated_at: str
    schema: list[SchemaResult] = field(default_factory=list)
    reconciliations: list[Reconciliation] = field(default_factory=list)
    ledgers: list[LedgerResult] = field(default_factory=list)
    mappings: list[MappingResult] = field(default_factory=list)
    errors: list[GateError] = field(default_factory=list)
    waivers: dict[str, str] = field(default_factory=dict)

    def entries(self, gate: str) -> int:
        """How many checks of this gate ran or failed to run."""
        results = {
            "schema": self.schema,
            "reconciliation": self.reconciliations,
            "ledger": self.ledgers,
            "mappings": self.mappings,
        }[gate]
        return len(results) + sum(1 for e in self.errors if e.gate == gate)

    def failures(self) -> list[tuple[str, str]]:
        """Blocking findings as ``(gate, message)`` pairs."""
        found: list[tuple[str, str]] = []
        for s in self.schema:
            if not s.ok:
                found.append(("schema", f"{s.database}: missing {', '.join(s.missing)}"))
        found += [("reconciliation", _failure_text(r)) for r in self.reconciliations if not r.ok]
        found += [("ledger", f"{led.name}: {b}") for led in self.ledgers for b in led.blockers]
        found += [("mappings", f"{m.name}: {e}") for m in self.mappings for e in m.errors]
        found += [(g, "not exercised and not waived") for g in self.not_exercised()]
        return found

    def not_exercised(self) -> list[str]:
        return [g for g in GATE_TITLES if self.entries(g) == 0 and g not in self.waivers]

    def gate_status(self, gate: str) -> str:
        if self.entries(gate) == 0:
            return "waived" if gate in self.waivers else "not run"
        if any(g == gate for g, _ in self.failures()):
            return FAIL
        return ERROR if any(e.gate == gate for e in self.errors) else PASS

    @property
    def status(self) -> str:
        if self.failures():
            return FAIL
        return ERROR if self.errors else PASS

    @property
    def exit_code(self) -> int:
        return {PASS: EXIT_PASS, FAIL: EXIT_FAIL, ERROR: EXIT_INCONCLUSIVE}[self.status]

    @property
    def ok(self) -> bool:
        return self.status == PASS


def _failure_text(r: Reconciliation) -> str:
    return (
        f"{r.label}: path A and path B differ by {r.rel_diff * 100:.3f}% "
        f"(tolerance {r.tolerance * 100:.2f}%)"
    )


# --------------------------------------------------------------------------------------
# Number formatting
# --------------------------------------------------------------------------------------


def fmt_number(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def fmt_pct(fraction: float) -> str:
    return f"{fraction * 100:.3f}%"


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------


def to_dict(report: Report) -> dict[str, Any]:
    return {
        "analysis": report.analysis,
        "as_of": report.as_of,
        "generated_at": report.generated_at,
        "status": report.status,
        "exit_code": report.exit_code,
        "gates": {gate: report.gate_status(gate) for gate in GATE_TITLES},
        "failures": [{"gate": g, "message": m} for g, m in report.failures()],
        "schema": [schema_to_dict(s) for s in report.schema],
        "reconciliations": [reconciliation_to_dict(r) for r in report.reconciliations],
        "ledgers": [ledger_to_dict(led) for led in report.ledgers],
        "mappings": [mapping_to_dict(m) for m in report.mappings],
        "errors": [
            {"gate": e.gate, "subject": e.subject, "message": e.message} for e in report.errors
        ],
        "waivers": report.waivers,
    }


def schema_to_dict(s: SchemaResult) -> dict[str, Any]:
    return {"database": s.database, "ok": s.ok, "checked": s.checked, "missing": s.missing}


def reconciliation_to_dict(r: Reconciliation) -> dict[str, Any]:
    return {
        "label": r.label, "database": r.database, "ok": r.ok, "a": r.a, "b": r.b,
        "relative_difference": r.rel_diff, "tolerance": r.tolerance, "claimed": r.claimed,
        "claim_drift": r.claim_drift, "warnings": r.warnings,
    }  # fmt: skip


def ledger_to_dict(led: LedgerResult) -> dict[str, Any]:
    return {
        "name": led.name,
        "dialect": led.dialect,
        "ok": led.ok,
        "problems": led.problems,
        "rows": [
            {
                "key": row.key,
                "kind": row.kind,
                "status": row.status,
                "detected": row.detected,
                "disposition": row.declaration.disposition if row.declaration else None,
                "reason": row.declaration.reason if row.declaration else None,
                "note": row.note,
            }
            for row in led.rows
        ],
    }


def mapping_to_dict(m: MappingResult) -> dict[str, Any]:
    return {
        "name": m.name,
        "database": m.database,
        "ok": m.ok,
        "issues": [{"level": i.level, "message": i.message} for i in m.issues],
        "per_scope": m.per_scope,
    }


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def to_markdown(report: Report) -> str:
    lines = [f"# Verification: {report.analysis}", "", *_summary_lines(report)]
    sections = [
        ("schema", [_schema_md(s) for s in report.schema]),
        ("reconciliation", [reconciliation_table(report.reconciliations)]),
        ("ledger", [ledger_md(led) for led in report.ledgers]),
        ("mappings", [mapping_md(m) for m in report.mappings]),
    ]
    for gate, blocks in sections:
        lines += ["", f"## {GATE_TITLES[gate]}", ""]
        lines += _gate_body(report, gate, blocks)
    lines += _tail_lines(report)
    return "\n".join(lines).rstrip() + "\n"


def _summary_lines(report: Report) -> list[str]:
    verdict = {PASS: "PASS", FAIL: "FAIL: do not report the numbers", ERROR: "INCONCLUSIVE"}
    lines = [
        f"**Result: {verdict[report.status]}** (exit code {report.exit_code})",
        "",
        f"As of {report.as_of}; generated {report.generated_at}. Values were computed live. A "
        "query that depends on the current date returns different numbers when run again later.",
        "",
        "| gate | status |",
        "|---|---|",
    ]
    return lines + [f"| {GATE_TITLES[g]} | {report.gate_status(g)} |" for g in GATE_TITLES]


def _gate_body(report: Report, gate: str, blocks: list[str]) -> list[str]:
    body = [block for block in blocks if block]
    if body:
        return body
    if report.entries(gate) > 0:
        return ["No result: see the errors below."]
    if gate in report.waivers:
        return [f"Waived: {report.waivers[gate]}"]
    return ["Not exercised."]


def _schema_md(s: SchemaResult) -> str:
    if s.ok:
        return f"- {s.database}: {len(s.checked)} references checked, all present."
    return f"- {s.database}: {len(s.checked)} references checked, MISSING {', '.join(s.missing)}"


def reconciliation_table(items: list[Reconciliation]) -> str:
    if not items:
        return ""
    rows = [
        "| claim | database | path A | path B | delta | tolerance | claimed (drift) | result |",
        "|---|---|--:|--:|--:|--:|--:|---|",
    ]
    for r in items:
        claimed = ""
        if r.claimed is not None:
            claimed = f"{fmt_number(r.claimed)} ({fmt_pct(r.claim_drift or 0)})"
        verdict = "ok" if r.ok else "FAIL"
        rows.append(
            f"| {cell(r.label)} | {cell(r.database)} | {fmt_number(r.a)} | {fmt_number(r.b)} "
            f"| {fmt_pct(r.rel_diff)} | {fmt_pct(r.tolerance)} | {claimed} | {verdict} |"
        )
    notes = [f"- {r.label}: {w}" for r in items for w in r.warnings]
    return "\n".join(rows + (["", *notes] if notes else []))


def ledger_md(led: LedgerResult) -> str:
    out = [f"### {led.name} (dialect: {led.dialect})", ""]
    rows = led.applicable_rows
    if rows:
        out += ["| filter | kind | disposition | detector | status |", "|---|---|---|---|---|"]
        for row in rows:
            declared = row.declaration.disposition if row.declaration else "-"
            seen = {True: "found", False: "not found", None: "n/a"}[row.detected]
            status = row.status if not row.note else f"{row.status}: {row.note}"
            out.append(f"| {cell(row.name)} | {row.kind} | {declared} | {seen} | {cell(status)} |")
    else:
        out.append("No registered filter applies to this query.")
    out += ["", *[f"- PROBLEM: {p}" for p in led.problems]]
    return "\n".join(out).rstrip()


def mapping_md(m: MappingResult) -> str:
    out = [f"### {m.name} ({m.database}): re-derived per scope"]
    for scope, rows in m.per_scope.items():
        out += ["", f"**{cell(scope)}**", ""]
        out += _scope_rows(rows)
    if m.issues:
        out += ["", *[f"- {i.level}: {i.message}" for i in m.issues]]
    return "\n".join(out)


def _scope_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(no rows)"]
    extras = [k for k in rows[0] if k not in ("code", "label")]
    header = ["code", "label", *extras]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        values = [row["code"], row["label"] if row["label"] is not None else "(none)"]
        lines.append("| " + " | ".join(cell(v) for v in values + [row[k] for k in extras]) + " |")
    return lines


def _tail_lines(report: Report) -> list[str]:
    lines: list[str] = []
    waived = [(g, r) for g, r in report.waivers.items() if report.entries(g) > 0]
    if report.failures():
        lines += ["", "## Failures", "", *[f"- {g}: {m}" for g, m in report.failures()]]
    if report.errors:
        lines += ["", "## Errors (gate could not be evaluated)", ""]
        lines += [f"- {e.gate} / {e.subject}: {e.message}" for e in report.errors]
    if waived:
        lines += ["", "## Waivers", "", *[f"- {g}: {r}" for g, r in waived]]
    return lines
