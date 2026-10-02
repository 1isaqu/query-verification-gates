"""A minimal ``.env`` reader, so the package needs no dependency for ``--env-file``."""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

from query_gates.errors import SpecError


def load_env_file(path: str | Path, environ: MutableMapping[str, str]) -> list[str]:
    """Add ``KEY=VALUE`` lines to ``environ`` without overriding variables already set.

    Returns the names that were added, never their values.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError([f"cannot read env file {target}: {exc.strerror or exc}"]) from None
    added = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in environ:
            environ[key] = value.strip().strip("\"'")
            added.append(key)
    return added
