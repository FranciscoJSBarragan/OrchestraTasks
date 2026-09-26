"""Read-only consistent snapshot access to the coordination database."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
SUPPORTED_CONTROL_SCHEMA_VERSIONS = frozenset({3, 4, 5, 6, 7, 8})


class HubUnavailable(Exception):
    """The coordination database cannot be read right now."""

    def __init__(self, condition: str, detail: str) -> None:
        super().__init__(detail)
        self.condition = condition
        self.detail = detail


@contextmanager
def read_snapshot(database: Path) -> Iterator[sqlite3.Connection]:
    with _read_snapshot(
        database,
        supported_versions=SUPPORTED_SCHEMA_VERSIONS,
        label="coordination",
    ) as connection:
        yield connection


@contextmanager
def read_control_snapshot(database: Path) -> Iterator[sqlite3.Connection]:
    with _read_snapshot(
        database,
        supported_versions=SUPPORTED_CONTROL_SCHEMA_VERSIONS,
        label="control",
    ) as connection:
        yield connection


@contextmanager
def _read_snapshot(
    database: Path,
    *,
    supported_versions: frozenset[int],
    label: str,
) -> Iterator[sqlite3.Connection]:
    if not database.is_file():
        raise HubUnavailable("missing", f"database not found: {database}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro", uri=True, timeout=2,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 2000")
        connection.execute("BEGIN DEFERRED")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in supported_versions:
            raise HubUnavailable(
                "unsupported-schema",
                f"unsupported {label} schema version: {version}",
            )
        yield connection
    except HubUnavailable:
        raise
    except sqlite3.OperationalError as error:
        message = str(error)
        condition = "busy" if "locked" in message or "busy" in message else "error"
        raise HubUnavailable(condition, f"database is {condition}: {message}") from error
    except sqlite3.Error as error:
        raise HubUnavailable("error", f"database error: {error}") from error
    finally:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            connection.close()
