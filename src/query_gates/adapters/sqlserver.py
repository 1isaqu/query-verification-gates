"""SQL Server adapter (optional extra: ``pip install query-verification-gates[sqlserver]``).

Connection settings come from ``QG_DB_*`` environment variables only. This module is unit-tested
through an injected fake DB-API connection; it has not been run against a live server.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from query_gates.adapters.base import Connection, Cursor, DBAPIDatabase
from query_gates.errors import AdapterError, AdapterUnavailable

ISOLATION_LEVELS = (
    "READ UNCOMMITTED",
    "READ COMMITTED",
    "REPEATABLE READ",
    "SNAPSHOT",
    "SERIALIZABLE",
)
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SQLServerSettings:
    driver: str = "ODBC Driver 18 for SQL Server"
    host: str = "localhost"
    port: int | None = None
    user: str = ""
    password: str = field(default="", repr=False)
    trust_server_cert: bool = False
    char_encoding: str = "utf-8"
    isolation: str = "READ UNCOMMITTED"
    schema: str | None = None
    login_timeout: int = 10
    query_timeout: int = 120

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SQLServerSettings:
        def text(name: str, default: str) -> str:
            return env.get(f"QG_DB_{name}", "").strip() or default

        isolation = text("ISOLATION", cls.isolation).upper()
        if isolation not in ISOLATION_LEVELS:
            raise AdapterError(f"QG_DB_ISOLATION must be one of {ISOLATION_LEVELS}")
        port = text("PORT", "")
        return cls(
            driver=text("DRIVER", cls.driver),
            host=text("HOST", cls.host),
            port=_to_int("QG_DB_PORT", port) if port else None,
            user=text("USER", ""),
            password=env.get("QG_DB_PASSWORD", ""),
            trust_server_cert=text("TRUST_SERVER_CERT", "no").lower() in _TRUE,
            char_encoding=text("CHAR_ENCODING", cls.char_encoding),
            isolation=isolation,
            schema=text("SCHEMA", "") or None,
            login_timeout=_to_int("QG_DB_LOGIN_TIMEOUT", text("LOGIN_TIMEOUT", "10")),
            query_timeout=_to_int("QG_DB_QUERY_TIMEOUT", text("QUERY_TIMEOUT", "120")),
        )

    def connection_string(self, database: str) -> str:
        server = f"{self.host},{self.port}" if self.port else self.host
        pairs = [
            ("DRIVER", _odbc_value(self.driver, braces=True)),
            ("SERVER", _odbc_value(server)),
            ("DATABASE", _odbc_value(database)),
        ]
        if self.user:
            pairs += [("UID", _odbc_value(self.user)), ("PWD", _odbc_value(self.password))]
        else:
            pairs.append(("Trusted_Connection", "yes"))
        pairs.append(("TrustServerCertificate", "yes" if self.trust_server_cert else "no"))
        return ";".join(f"{key}={value}" for key, value in pairs)


def _to_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise AdapterError(f"{name} must be an integer, got {value!r}") from None


def _odbc_value(value: str, *, braces: bool = False) -> str:
    """Quote an ODBC connection-string value when it contains characters that would end it."""
    if braces or any(ch in value for ch in ";{}") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


Connector = Callable[[str, SQLServerSettings], Connection]


def pyodbc_connector(connection_string: str, settings: SQLServerSettings) -> Connection:
    """Open an autocommit pyodbc connection. Imported lazily so the extra stays optional."""
    try:
        import pyodbc
    except ImportError:
        raise AdapterUnavailable(
            "the SQL Server adapter needs pyodbc: pip install 'query-verification-gates[sqlserver]'"
        ) from None
    conn = pyodbc.connect(connection_string, timeout=settings.login_timeout, autocommit=True)
    conn.timeout = settings.query_timeout
    conn.setdecoding(pyodbc.SQL_CHAR, encoding=settings.char_encoding)
    conn.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")
    return conn  # type: ignore[no-any-return]


class SQLServerDatabase(DBAPIDatabase):
    """SQL Server access through any DB-API connection (pyodbc by default).

    Batches are allowed so that a leading ``DECLARE`` works, but every statement in the batch must
    still be a read. The isolation level is set before every statement; the default is the
    non-blocking ``READ UNCOMMITTED`` so that verification never blocks the system it audits.
    """

    dialect = "sqlserver"
    allowed_starts = ("SELECT", "WITH", "DECLARE")
    allow_batches = True

    def __init__(self, connection: Connection, settings: SQLServerSettings | None = None) -> None:
        self.settings = settings or SQLServerSettings()
        super().__init__(connection, schema=self.settings.schema)

    def _prepare(self, cursor: Cursor) -> None:
        cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {self.settings.isolation}")

    @classmethod
    def connect(
        cls,
        database: str,
        settings: SQLServerSettings | None = None,
        *,
        connector: Connector = pyodbc_connector,
    ) -> SQLServerDatabase:
        settings = settings or SQLServerSettings()
        try:
            connection = connector(settings.connection_string(database), settings)
        except AdapterError:
            raise
        except Exception as exc:  # driver errors are not a stable type across drivers
            detail = _scrub(exc, settings)
            raise AdapterError(f"cannot connect to database {database!r}: {detail}") from None
        return cls(connection, settings)


def _scrub(exc: Exception, settings: SQLServerSettings) -> str:
    """Error text with the password removed, in case a driver echoes the connection string."""
    text = str(exc)
    return text.replace(settings.password, "***") if settings.password else text
