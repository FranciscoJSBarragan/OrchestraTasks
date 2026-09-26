"""Read-only snapshot database access tests (SPEC section 5 / PLAN Task 4)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.db import (  # noqa: E402
    HubUnavailable,
    SUPPORTED_CONTROL_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSIONS,
    read_snapshot,
)


class DbTests(unittest.TestCase):
    def test_supported_schema_versions(self) -> None:
        self.assertEqual(SUPPORTED_SCHEMA_VERSIONS, frozenset({1}))
        self.assertEqual(SUPPORTED_CONTROL_SCHEMA_VERSIONS, frozenset({3, 4, 5, 6, 7, 8}))

    def test_missing_file_raises_hub_unavailable_missing(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "absent.sqlite3"
        with self.assertRaises(HubUnavailable) as raised:
            with read_snapshot(missing):
                pass
        self.assertEqual(raised.exception.condition, "missing")

    def test_unsupported_schema_raises_hub_unavailable(self) -> None:
        database = support.create_state_db(Path(tempfile.mkdtemp()))
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(HubUnavailable) as raised:
            with read_snapshot(database):
                pass
        self.assertEqual(raised.exception.condition, "unsupported-schema")

    def test_wal_reader_writer_nonblocking_consistent_snapshot(self) -> None:
        database = support.create_state_db(Path(tempfile.mkdtemp()))
        task = support.insert_task(database, status="active")

        with read_snapshot(database) as snapshot:
            status = snapshot.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task["id"],),
            ).fetchone()[0]
            self.assertEqual(status, "active")

            writer = sqlite3.connect(database, timeout=0)
            try:
                writer.execute("PRAGMA busy_timeout = 0")
                writer.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?",
                    ("completed", task["id"]),
                )
                writer.commit()
            finally:
                writer.close()

            status_same_snapshot = snapshot.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task["id"],),
            ).fetchone()[0]
            self.assertEqual(status_same_snapshot, "active")

        with read_snapshot(database) as fresh:
            status_fresh = fresh.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (task["id"],),
            ).fetchone()[0]
            self.assertEqual(status_fresh, "completed")

    def test_writes_on_snapshot_connection_are_rejected(self) -> None:
        database = support.create_state_db(Path(tempfile.mkdtemp()))
        task = support.insert_task(database, status="active")
        with read_snapshot(database) as snapshot:
            with self.assertRaises(sqlite3.OperationalError):
                snapshot.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?",
                    ("completed", task["id"]),
                )


if __name__ == "__main__":
    unittest.main()
