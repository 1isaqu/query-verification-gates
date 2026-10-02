"""query-verification-gates: four gates that make a numeric claim about a business auditable.

Gate 1 schema, Gate 2 dual-path reconciliation, Gate 3 filter ledger, Gate 4 per-scope mappings.
See the README for the CLI and ``docs/`` for the spec format and the design.
"""

__version__ = "0.1.0"

from query_gates.adapters import (
    Database,
    DBAPIDatabase,
    SQLiteDatabase,
    SQLServerDatabase,
    SQLServerSettings,
    open_database,
)
from query_gates.errors import (
    AdapterError,
    AdapterUnavailable,
    QueryGatesError,
    SpecError,
)
from query_gates.gates import (
    CodeTable,
    check_mappings,
    check_schema,
    evaluate_ledger,
    reconcile,
)
from query_gates.registry import (
    FilterRegistry,
    FilterRule,
    builtin_registry,
    registry_from_config,
)
from query_gates.report import Report, to_dict, to_markdown
from query_gates.runner import run_verification
from query_gates.spec import Spec, load_spec, parse_spec
from query_gates.sqlutil import ReadOnlyViolation, assert_read_only

__all__ = [
    "AdapterError",
    "AdapterUnavailable",
    "CodeTable",
    "DBAPIDatabase",
    "Database",
    "FilterRegistry",
    "FilterRule",
    "QueryGatesError",
    "ReadOnlyViolation",
    "Report",
    "SQLServerDatabase",
    "SQLServerSettings",
    "SQLiteDatabase",
    "Spec",
    "SpecError",
    "__version__",
    "assert_read_only",
    "builtin_registry",
    "check_mappings",
    "check_schema",
    "evaluate_ledger",
    "load_spec",
    "open_database",
    "parse_spec",
    "reconcile",
    "registry_from_config",
    "run_verification",
    "to_dict",
    "to_markdown",
]
