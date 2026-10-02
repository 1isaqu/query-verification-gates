"""The JSON verification spec: parsing and validation (see docs/spec-format.md).

Everything structural is checked here, before any database is touched, and every problem found is
reported at once. Long SQL can live in files: ``sql_file``, ``query_a_file``, ``query_b_file`` and
``query_file`` are read relative to the spec.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from query_gates.errors import SpecError
from query_gates.gates.reconcile import DEFAULT_TOLERANCE
from query_gates.gates.schema import CodeTable
from query_gates.registry import FilterRegistry, registry_from_config
from query_gates.sqlutil import canonical

GATES = ("schema", "reconciliation", "ledger", "mappings")
_TOP_KEYS = {
    "analysis", "as_of", "dialect", "databases", "schema", "claims", "ledgers", "mappings",
    "registry", "waivers",
}  # fmt: skip
_SCALARS = (str, int, float, bool, type(None))


@dataclass
class SchemaSpec:
    columns: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    code_tables: list[CodeTable] = field(default_factory=list)
    infer_from_queries: bool = False


@dataclass
class Claim:
    label: str
    database: str
    query_a: str
    query_b: str
    tolerance: float = DEFAULT_TOLERANCE
    claimed: float | None = None
    params_a: list[Any] = field(default_factory=list)
    params_b: list[Any] = field(default_factory=list)
    column: str | None = None


@dataclass
class LedgerEntry:
    name: str
    sql: str
    declared: dict[str, Any]
    dialect: str


@dataclass
class MappingEntry:
    name: str
    database: str
    scopes: list[str]
    query: str
    params: list[Any]
    expected: dict[str, dict[str, str]]
    require_rows: bool = False


@dataclass
class Spec:
    analysis: str
    databases: list[str]
    dialect: str = "generic"
    as_of: str | None = None
    schema: SchemaSpec | None = None
    claims: list[Claim] = field(default_factory=list)
    ledgers: list[LedgerEntry] = field(default_factory=list)
    mappings: list[MappingEntry] = field(default_factory=list)
    registry: FilterRegistry = field(default_factory=FilterRegistry)
    waivers: dict[str, str] = field(default_factory=dict)

    def sql_for(self, database: str) -> list[str]:
        """Queries bound to one database, used to infer schema references.

        Ledger SQL is not bound to a database, so it never feeds inference.
        """
        sql = [q for c in self.claims if c.database == database for q in (c.query_a, c.query_b)]
        return sql + [m.query for m in self.mappings if m.database == database]


def load_spec(path: str | Path) -> Spec:
    """Read, parse and validate a spec file."""
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError([f"cannot read spec {target}: {exc.strerror or exc}"]) from None
    except json.JSONDecodeError as exc:
        raise SpecError([f"{target} is not valid JSON: {exc}"]) from None
    return parse_spec(data, base_dir=target.parent)


def parse_spec(data: Any, base_dir: Path | None = None) -> Spec:
    """Validate a spec given as a dict. All problems are collected and raised together."""
    reader = _Reader(base_dir)
    if not isinstance(data, Mapping):
        raise SpecError(["spec must be a JSON object"])
    reader.only_keys(data, _TOP_KEYS, "spec")
    databases = reader.string_list(data, "databases", "spec", default=["main"])
    if len(set(databases)) != len(databases):
        reader.fail("spec", "'databases' must not repeat a name")
    spec = Spec(
        analysis=reader.string(data, "analysis", "spec", required=True) or "unnamed",
        databases=databases,
        dialect=reader.string(data, "dialect", "spec") or "generic",
        as_of=reader.iso_date(data, "as_of", "spec"),
        schema=reader.schema(data.get("schema")),
        claims=reader.claims(data.get("claims", []), databases),
        ledgers=reader.ledgers(data.get("ledgers", []), data.get("dialect") or "generic"),
        mappings=reader.mappings(data.get("mappings", []), databases),
        waivers=reader.waivers(data.get("waivers", {})),
    )
    spec.registry = reader.registry(data.get("registry"))
    if reader.problems:
        raise SpecError(reader.problems)
    return spec


class _Reader:
    """Accumulates problems instead of stopping at the first one."""

    def __init__(self, base_dir: Path | None) -> None:
        self.base_dir = base_dir
        self.problems: list[str] = []

    def fail(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def only_keys(self, obj: Mapping[str, Any], allowed: set[str], where: str) -> None:
        unknown = sorted(set(obj) - allowed)
        if unknown:
            self.fail(where, f"unknown keys {unknown}")

    def string(
        self, obj: Mapping[str, Any], key: str, where: str, *, required: bool = False
    ) -> str | None:
        value = obj.get(key)
        if value is None:
            if required:
                self.fail(where, f"'{key}' is required")
            return None
        if not isinstance(value, str) or not value.strip():
            self.fail(where, f"'{key}' must be a non-empty string")
            return None
        return value

    def string_list(
        self, obj: Mapping[str, Any], key: str, where: str, *, default: list[str] | None = None
    ) -> list[str]:
        value = obj.get(key, default)
        if value is None:
            return []
        if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
            self.fail(where, f"'{key}' must be a non-empty list of strings")
            return default or []
        return list(value)

    def boolean(self, obj: Mapping[str, Any], key: str, where: str) -> bool:
        value = obj.get(key, False)
        if not isinstance(value, bool):
            self.fail(where, f"'{key}' must be true or false")
        return bool(value)

    def number(self, obj: Mapping[str, Any], key: str, where: str) -> float | None:
        value = obj.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.fail(where, f"'{key}' must be a number")
            return None
        return float(value)

    def params(self, obj: Mapping[str, Any], key: str, where: str) -> list[Any]:
        value = obj.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, _SCALARS) for v in value):
            self.fail(where, f"'{key}' must be a list of scalar values")
            return []
        return list(value)

    def iso_date(self, obj: Mapping[str, Any], key: str, where: str) -> str | None:
        value = self.string(obj, key, where)
        if value is None:
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            self.fail(where, f"'{key}' must be an ISO date (YYYY-MM-DD), got {value!r}")
        return value

    def sql(self, obj: Mapping[str, Any], key: str, where: str) -> str:
        """The text under ``key`` (a string or list of lines) or the file named by ``key_file``."""
        inline, file_key = obj.get(key), f"{key}_file"
        named = obj.get(file_key)
        if (inline is None) == (named is None):
            self.fail(where, f"exactly one of '{key}' or '{file_key}' is required")
            return ""
        if inline is None:
            return self._read_file(str(named), f"{where}.{file_key}")
        if isinstance(inline, list) and all(isinstance(line, str) for line in inline):
            inline = "\n".join(inline)
        if isinstance(inline, str) and inline.strip():
            return inline
        self.fail(where, f"'{key}' must be a non-empty string or a list of strings")
        return ""

    def _read_file(self, name: str, where: str) -> str:
        if self.base_dir is None:
            self.fail(where, "file references need a spec loaded from disk")
            return ""
        try:
            return (self.base_dir / name).read_text(encoding="utf-8")
        except OSError as exc:
            self.fail(where, f"cannot read {name!r}: {exc.strerror or exc}")
            return ""

    def database(self, obj: Mapping[str, Any], databases: list[str], where: str) -> str:
        name = self.string(obj, "database", where) or (databases[0] if databases else "")
        if name not in databases:
            self.fail(where, f"database {name!r} is not listed in 'databases'")
        return name

    def schema(self, raw: Any) -> SchemaSpec | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            self.fail("schema", "must be an object")
            return None
        self.only_keys(raw, {"columns", "tables", "code_tables", "infer_from_queries"}, "schema")
        return SchemaSpec(
            columns=self._optional_list(raw, "columns", "schema"),
            tables=self._optional_list(raw, "tables", "schema"),
            code_tables=self._code_tables(raw.get("code_tables", [])),
            infer_from_queries=self.boolean(raw, "infer_from_queries", "schema"),
        )

    def _optional_list(self, obj: Mapping[str, Any], key: str, where: str) -> list[str]:
        return self.string_list(obj, key, where) if key in obj else []

    def _code_tables(self, raw: Any) -> list[CodeTable]:
        if not isinstance(raw, list):
            self.fail("schema.code_tables", "must be a list")
            return []
        found = []
        for index, item in enumerate(raw):
            where = f"schema.code_tables[{index}]"
            if isinstance(item, str):
                found.append(CodeTable(item))
            elif isinstance(item, Mapping):
                self.only_keys(item, {"table", "key", "label"}, where)
                table = self.string(item, "table", where, required=True)
                found.append(CodeTable(table or "", item.get("key"), item.get("label")))
            else:
                self.fail(where, "must be a table name or an object")
        return found

    def claims(self, raw: Any, databases: list[str]) -> list[Claim]:
        return [
            self._claim(item, index, databases) for index, item in self._objects(raw, "claims")
        ]

    def _objects(self, raw: Any, key: str) -> list[tuple[int, Mapping[str, Any]]]:
        if not isinstance(raw, list):
            self.fail(key, "must be a list")
            return []
        items = []
        for index, item in enumerate(raw):
            if isinstance(item, Mapping):
                items.append((index, item))
            else:
                self.fail(f"{key}[{index}]", "must be an object")
        return items

    def _claim(self, item: Mapping[str, Any], index: int, databases: list[str]) -> Claim:
        where = f"claims[{index}]"
        allowed = {"label", "database", "tolerance", "claimed", "params_a", "params_b", "column"}
        queries = {"query_a", "query_b", "query_a_file", "query_b_file"}
        self.only_keys(item, allowed | queries, where)
        tolerance = self.number(item, "tolerance", where)
        if tolerance is not None and not 0 <= tolerance < 1:
            self.fail(where, "'tolerance' must satisfy 0 <= tolerance < 1")
        claim = Claim(
            label=self.string(item, "label", where, required=True) or "",
            database=self.database(item, databases, where),
            query_a=self.sql(item, "query_a", where),
            query_b=self.sql(item, "query_b", where),
            tolerance=DEFAULT_TOLERANCE if tolerance is None else tolerance,
            claimed=self.number(item, "claimed", where),
            params_a=self.params(item, "params_a", where),
            params_b=self.params(item, "params_b", where),
            column=self.string(item, "column", where),
        )
        same_text = canonical(claim.query_a) == canonical(claim.query_b)
        if claim.query_a and same_text and claim.params_a == claim.params_b:
            self.fail(where, "query_a and query_b are identical, so they are not independent")
        return claim

    def ledgers(self, raw: Any, default_dialect: str) -> list[LedgerEntry]:
        entries = []
        for index, item in self._objects(raw, "ledgers"):
            where = f"ledgers[{index}]"
            self.only_keys(item, {"name", "sql", "sql_file", "dialect", "declared"}, where)
            declared = item.get("declared", {})
            if not isinstance(declared, Mapping):
                self.fail(where, "'declared' must be an object")
                declared = {}
            entries.append(
                LedgerEntry(
                    name=self.string(item, "name", where) or f"ledger-{index}",
                    sql=self.sql(item, "sql", where),
                    declared=dict(declared),
                    dialect=self.string(item, "dialect", where) or default_dialect,
                )
            )
        return entries

    def mappings(self, raw: Any, databases: list[str]) -> list[MappingEntry]:
        entries = []
        for index, item in self._objects(raw, "mappings"):
            where = f"mappings[{index}]"
            allowed = {"name", "database", "scopes", "params", "expected", "require_rows"}
            self.only_keys(item, allowed | {"query", "query_file"}, where)
            scopes = self.string_list(item, "scopes", where)
            entries.append(
                MappingEntry(
                    name=self.string(item, "name", where) or f"mapping-{index}",
                    database=self.database(item, databases, where),
                    scopes=scopes,
                    query=self.sql(item, "query", where),
                    params=self.params(item, "params", where) if "params" in item else ["$scope"],
                    expected=self._expected(item.get("expected", {}), scopes, where),
                    require_rows=self.boolean(item, "require_rows", where),
                )
            )
        return entries

    def _expected(self, raw: Any, scopes: list[str], where: str) -> dict[str, dict[str, str]]:
        ok = isinstance(raw, Mapping) and all(
            isinstance(v, Mapping) and all(isinstance(x, str) for x in v.values())
            for v in raw.values()
        )
        if not ok:
            self.fail(where, "'expected' must map scope -> {code: label}")
            return {}
        for scope in raw:
            if scope not in scopes:
                self.fail(where, f"'expected' names scope {scope!r} that is not in 'scopes'")
        return {scope: dict(codes) for scope, codes in raw.items()}

    def waivers(self, raw: Any) -> dict[str, str]:
        if not isinstance(raw, Mapping):
            self.fail("waivers", "must be an object of gate -> reason")
            return {}
        for gate, reason in raw.items():
            if gate not in GATES:
                self.fail("waivers", f"unknown gate {gate!r}, expected one of {GATES}")
            if not isinstance(reason, str) or not reason.strip():
                self.fail("waivers", f"waiving {gate!r} needs a reason")
        return {str(g): str(r) for g, r in raw.items()}

    def registry(self, raw: Any) -> FilterRegistry:
        if raw is not None and not isinstance(raw, Mapping):
            self.fail("registry", "must be an object")
            return FilterRegistry()
        try:
            return registry_from_config(raw)
        except SpecError as exc:
            self.problems.extend(exc.problems)
            return FilterRegistry()
