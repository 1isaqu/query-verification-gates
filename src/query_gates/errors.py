"""Exception types. Each maps to one CLI exit code (see ``cli.py``)."""

from __future__ import annotations

from collections.abc import Sequence


class QueryGatesError(Exception):
    """Base class for every error this package raises on purpose."""


class SpecError(QueryGatesError):
    """The verification spec (or a CLI argument) is malformed. Reported before any query runs."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class AdapterError(QueryGatesError):
    """A database could not be opened, or a query could not be executed."""


class AdapterUnavailable(AdapterError):
    """An optional driver is not installed."""
