import sqlite3
import unittest

import support  # noqa: F401  (sys.path setup)
import coordination
from orchestra_control import db as control_db
from orchestra_hub.api import ACTIVITY_FIELDS, TASK_FIELDS
from orchestra_hub.db import SUPPORTED_CONTROL_SCHEMA_VERSIONS, SUPPORTED_SCHEMA_VERSIONS

# Tables and columns the Hub actually reads. A coordinator change that drops or
# renames any of them must fail here even when SCHEMA_VERSION is unchanged.
REQUIRED_SHAPE = {
    "tasks": set(TASK_FIELDS) - {
        "short_id", "initiative", "blocked_by", "parallel_with",
        "preparation_status", "disposition", "stop_requested_at",
    },
    "activities": set(ACTIVITY_FIELDS) | {"task_id"},
}


class SchemaCrossCheckTest(unittest.TestCase):
    def test_hub_supports_installed_coordination_schema(self) -> None:
        self.assertIn(
            coordination.SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS,
            "coordination.SCHEMA_VERSION changed. Review Hub compatibility "
            "(fields, queries, fingerprint), then update "
            "SUPPORTED_SCHEMA_VERSIONS deliberately.",
        )
        self.assertIn(control_db.SCHEMA_VERSION, SUPPORTED_CONTROL_SCHEMA_VERSIONS)

    def test_installed_schema_provides_every_column_the_hub_reads(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            for statement in coordination.SCHEMA_STATEMENTS:
                connection.execute(statement)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertLessEqual(
                set(REQUIRED_SHAPE), tables,
                "coordination schema no longer creates a table the Hub reads. "
                "Fix the Hub query before shipping.",
            )
            for table, required in REQUIRED_SHAPE.items():
                columns = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertLessEqual(
                    required, columns,
                    f"coordination table {table} no longer provides every "
                    "column the Hub reads.",
                )
        finally:
            connection.close()
