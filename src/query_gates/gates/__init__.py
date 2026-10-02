"""The four gates. Each is a plain function returning a small result object."""

from query_gates.gates.ledger import LedgerResult, LedgerRow, evaluate_ledger
from query_gates.gates.mappings import MappingResult, check_mappings
from query_gates.gates.reconcile import Reconciliation, reconcile
from query_gates.gates.schema import CodeTable, SchemaResult, check_schema

__all__ = [
    "CodeTable",
    "LedgerResult",
    "LedgerRow",
    "MappingResult",
    "Reconciliation",
    "SchemaResult",
    "check_mappings",
    "check_schema",
    "evaluate_ledger",
    "reconcile",
]
