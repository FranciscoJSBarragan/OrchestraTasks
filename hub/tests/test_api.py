"""Allowlisted API payloads and attention model tests (SPEC §7-8 / PLAN Task 6)."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.api import (  # noqa: E402
    ACTIVITY_FIELDS,
    ARTIFACT_FIELDS,
    TASK_FIELDS,
    summary_payload,
    task_detail_payload,
    tasks_payload,
)
from orchestra_hub.config import HubConfig, PinnedRepository  # noqa: E402
from orchestra_hub.fingerprint import MATERIAL_FINGERPRINT_VERSION  # noqa: E402


NOW = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
PINNED_PATH = "/pinned/repo"
OBS_PATH = "/obs/alpha"
TASK_PAYLOAD_KEYS = set(TASK_FIELDS) | {
    "current_activity", "material_fingerprint", "stale"
}
ACTIVITY_PAYLOAD_KEYS = set(ACTIVITY_FIELDS)
ARTIFACT_PAYLOAD_KEYS = set(ARTIFACT_FIELDS)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_root = Path(tempfile.mkdtemp())
        self.database = support.create_state_db(self.state_root)
        self.config = HubConfig(
            state_root=self.state_root,
            port=7343,
            stale_after_minutes=60,
            pinned_repositories=(
                PinnedRepository(path=PINNED_PATH, name="Pinned"),
            ),
        )

    def _connect(self):
        import sqlite3

        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def test_task_payload_keys_are_exact_allowlist(self) -> None:
        support.insert_task(self.database, repository=OBS_PATH)
        connection = self._connect()
        try:
            payload = summary_payload(connection, self.config, NOW)
        finally:
            connection.close()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["material_fingerprint_version"],
            MATERIAL_FINGERPRINT_VERSION,
        )
        self.assertEqual(set(payload["tasks"][0]), TASK_PAYLOAD_KEYS)
        self.assertNotIn("generated_at", payload)

    def test_attention_rules(self) -> None:
        blocked = support.insert_task(
            self.database,
            id="blocked-task",
            label="Blocked",
            repository=OBS_PATH,
            blocker="waiting on review",
            updated_at="2026-08-02T18:30:00Z",
        )
        stale = support.insert_task(
            self.database,
            id="stale-task",
            label="Stale",
            repository=OBS_PATH,
            blocker="",
            updated_at="2026-08-02T17:00:00Z",
        )
        support.insert_task(
            self.database,
            id="completed-noise",
            label="Done",
            repository=OBS_PATH,
            status="completed",
            blocker="ignored blocker",
            updated_at="2026-08-02T16:00:00Z",
        )
        support.insert_task(
            self.database,
            id="fresh-task",
            label="Fresh",
            repository=OBS_PATH,
            blocker="",
            updated_at="2026-08-02T18:45:00Z",
        )

        connection = self._connect()
        try:
            payload = summary_payload(connection, self.config, NOW)
        finally:
            connection.close()

        attention = {
            entry["task_id"]: entry for entry in payload["attention"]
        }
        self.assertEqual(set(attention), {blocked["id"], stale["id"]})
        self.assertEqual(attention[blocked["id"]]["reasons"], ["blocker"])
        self.assertEqual(attention[stale["id"]]["reasons"], ["stale"])
        self.assertEqual(
            set(attention[blocked["id"]]),
            {
                "task_id",
                "short_id",
                "label",
                "repository",
                "reasons",
                "blocker",
                "next_action",
                "updated_at",
            },
        )

    def test_repositories_aggregation_and_pinned_zero_tasks(self) -> None:
        support.insert_task(
            self.database,
            id="alpha-active",
            repository=OBS_PATH,
            status="active",
            updated_at="2026-08-02T18:50:00Z",
        )
        support.insert_task(
            self.database,
            id="alpha-done",
            repository=OBS_PATH,
            status="completed",
            updated_at="2026-08-02T18:40:00Z",
        )

        connection = self._connect()
        try:
            payload = summary_payload(connection, self.config, NOW)
        finally:
            connection.close()

        by_path = {entry["path"]: entry for entry in payload["repositories"]}
        self.assertEqual(
            by_path[OBS_PATH],
            {
                "path": OBS_PATH,
                "name": "alpha",
                "pinned": False,
                "observed": True,
                "active_tasks": 1,
                "completed_tasks": 1,
            },
        )
        self.assertEqual(
            by_path[PINNED_PATH],
            {
                "path": PINNED_PATH,
                "name": "Pinned",
                "pinned": True,
                "observed": False,
                "active_tasks": 0,
                "completed_tasks": 0,
            },
        )
        names_paths = [
            (entry["name"], entry["path"]) for entry in payload["repositories"]
        ]
        self.assertEqual(names_paths, sorted(names_paths))

    def test_task_without_repository_gets_a_synthetic_observed_group(self) -> None:
        support.insert_task(
            self.database,
            id="no-repository",
            label="Direct task",
            repository="",
            status="active",
            updated_at="2026-08-02T18:50:00Z",
        )

        connection = self._connect()
        try:
            payload = summary_payload(connection, self.config, NOW)
        finally:
            connection.close()

        by_path = {entry["path"]: entry for entry in payload["repositories"]}
        self.assertEqual(
            by_path[""],
            {
                "path": "",
                "name": "No repository",
                "pinned": False,
                "observed": True,
                "active_tasks": 1,
                "completed_tasks": 0,
            },
        )

    def test_tasks_payload_status_filter_and_ordering(self) -> None:
        older = support.insert_task(
            self.database,
            id="completed-older",
            repository=OBS_PATH,
            status="completed",
            updated_at="2026-08-02T17:00:00Z",
        )
        newer = support.insert_task(
            self.database,
            id="completed-newer",
            repository=OBS_PATH,
            status="completed",
            updated_at="2026-08-02T18:00:00Z",
        )
        support.insert_task(
            self.database,
            id="still-active",
            repository=OBS_PATH,
            status="active",
            updated_at="2026-08-02T18:30:00Z",
        )

        connection = self._connect()
        try:
            payload = tasks_payload(
                connection, self.config, NOW, status="completed"
            )
        finally:
            connection.close()

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["material_fingerprint_version"],
            MATERIAL_FINGERPRINT_VERSION,
        )
        ids = [task["id"] for task in payload["tasks"]]
        self.assertEqual(ids, [newer["id"], older["id"]])
        self.assertTrue(all(task["status"] == "completed" for task in payload["tasks"]))
        self.assertNotIn("generated_at", payload)

    def test_task_detail_lists_filesystem_artifacts(self) -> None:
        worktree = support.make_worktree(self.state_root, linked=True)
        task = support.insert_task(
            self.database, repository=OBS_PATH, worktree=str(worktree)
        )
        support.write_artifact(worktree, "01-repository-context.md")
        support.write_artifact(worktree, "02-plan-overview.md")
        support.write_artifact(worktree, "03-plan-phase-p2.md")
        support.write_artifact(worktree, "notes.md")  # not an artifact name
        support.insert_activity(
            self.database,
            task["id"],
            agent_id="agent-z",
            capability="implementation",
            updated_at="2026-08-02T18:05:00Z",
        )
        support.insert_activity(
            self.database,
            task["id"],
            agent_id="agent-a",
            capability="review",
            updated_at="2026-08-02T18:15:00Z",
        )

        connection = self._connect()
        try:
            payload = task_detail_payload(
                connection, self.config, NOW, task["id"]
            )
            unknown = task_detail_payload(
                connection, self.config, NOW, "absent-id"
            )
        finally:
            connection.close()

        self.assertIsNone(unknown)
        assert payload is not None
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["material_fingerprint_version"],
            MATERIAL_FINGERPRINT_VERSION,
        )
        self.assertEqual(set(payload["task"]), TASK_PAYLOAD_KEYS)
        self.assertNotIn("generated_at", payload)

        self.assertEqual(
            [activity["agent_id"] for activity in payload["activities"]],
            ["agent-a", "agent-z"],
        )
        self.assertEqual(
            [activity["agent_id"] for activity in payload["task"]["current_activity"]],
            ["agent-a", "agent-z"],
        )
        for activity in payload["activities"]:
            self.assertEqual(set(activity), ACTIVITY_PAYLOAD_KEYS)

        self.assertEqual(
            [artifact["id"] for artifact in payload["artifacts"]],
            [
                "03-plan-phase-p2.md",
                "02-plan-overview.md",
                "01-repository-context.md",
            ],
        )
        by_id = {artifact["id"]: artifact for artifact in payload["artifacts"]}
        for artifact in payload["artifacts"]:
            self.assertEqual(set(artifact), ARTIFACT_PAYLOAD_KEYS)
            self.assertNotIn("path", artifact)
        self.assertEqual(by_id["03-plan-phase-p2.md"]["kind"], "plan-phase")
        self.assertEqual(by_id["03-plan-phase-p2.md"]["phase"], 2)
        self.assertEqual(by_id["02-plan-overview.md"]["kind"], "plan-overview")
        self.assertEqual(by_id["02-plan-overview.md"]["phase"], 0)

    def test_prepared_and_coordination_rows_join_by_shared_uuid(self) -> None:
        control_database = support.create_control_db(self.state_root)
        prepared = support.insert_prepared_task(
            control_database,
            id="11111111-1111-4111-8111-111111111111",
            short_id="A1",
            title="Prepared A1",
        )
        support.insert_task(
            self.database,
            id=prepared["id"],
            label="Active A1",
            repository=OBS_PATH,
            status="active",
            stage="implementation",
        )
        ready_only = support.insert_prepared_task(
            control_database,
            id="22222222-2222-4222-8222-222222222222",
            short_id="A2",
            title="Prepared A2",
            rank=2,
            idempotency_key="prepared-a2",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination,
                self.config,
                NOW,
                control_connection=control,
            )
        finally:
            coordination.close()
            control.close()
        by_id = {task["id"]: task for task in payload["tasks"]}
        self.assertEqual(len(by_id), 2)
        self.assertEqual(by_id[prepared["id"]]["short_id"], "A1")
        self.assertEqual(by_id[prepared["id"]]["label"], "Prepared A1")
        self.assertEqual(by_id[prepared["id"]]["stage"], "implementation")
        self.assertEqual(by_id[ready_only["id"]]["short_id"], "A2")
        self.assertEqual(by_id[ready_only["id"]]["status"], "ready")
        self.assertEqual(by_id[ready_only["id"]]["worktree"], "")

    def test_direct_and_mismatched_tasks_never_receive_control_short_ids(self) -> None:
        control_database = support.create_control_db(self.state_root)
        prepared = support.insert_prepared_task(
            control_database,
            id="11111111-1111-4111-8111-111111111111",
            short_id="A1",
            title="Prepared card",
        )
        direct_one = support.insert_task(
            self.database,
            id="22222222-2222-4222-8222-222222222222",
            label="Direct task one",
            repository=OBS_PATH,
            status="active",
            stage="planning",
        )
        direct_two = support.insert_task(
            self.database,
            id="33333333-3333-4333-8333-333333333333",
            label="Direct task two",
            repository="/Users/example/second-repository",
            status="active",
            stage="implementation",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination,
                self.config,
                NOW,
                control_connection=control,
            )
        finally:
            coordination.close()
            control.close()
        by_id = {task["id"]: task for task in payload["tasks"]}
        self.assertEqual(by_id[prepared["id"]]["short_id"], "A1")
        for direct in (direct_one, direct_two):
            self.assertIsNone(by_id[direct["id"]]["short_id"])
            self.assertEqual(by_id[direct["id"]]["label"], direct["label"])

    def test_prepared_task_fingerprint_ignores_timestamp_only_changes(self) -> None:
        control_database = support.create_control_db(self.state_root)
        prepared = support.insert_prepared_task(
            control_database,
            id="22222222-2222-4222-8222-222222222222",
            short_id="A2",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            before = summary_payload(
                coordination,
                self.config,
                NOW,
                control_connection=control,
            )
            control.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                ("2099-01-01T00:00:00Z", prepared["id"]),
            )
            control.commit()
            after = summary_payload(
                coordination,
                self.config,
                NOW,
                control_connection=control,
            )
        finally:
            coordination.close()
            control.close()
        self.assertEqual(
            before["tasks"][0]["material_fingerprint"],
            after["tasks"][0]["material_fingerprint"],
        )

    def test_prepared_task_detail_accepts_short_id(self) -> None:
        control_database = support.create_control_db(self.state_root)
        support.insert_prepared_task(control_database, short_id="A1")
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = task_detail_payload(
                coordination,
                self.config,
                NOW,
                "a1",
                control_connection=control,
            )
        finally:
            coordination.close()
            control.close()
        assert payload is not None
        self.assertEqual(payload["task"]["short_id"], "A1")
        self.assertEqual(payload["activities"], [])
        self.assertEqual(payload["artifacts"], [])

    def test_control_v3_projects_empty_relation_fields(self) -> None:
        control_database = support.create_control_v3_db(self.state_root)
        support.insert_prepared_task(control_database, short_id="A1")
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination, self.config, NOW, control_connection=control
            )
        finally:
            coordination.close()
            control.close()
        task = payload["tasks"][0]
        self.assertIsNone(task["initiative"])
        self.assertEqual(task["blocked_by"], [])
        self.assertEqual(task["parallel_with"], [])

    def test_v4_initiative_dependencies_and_parallelism_are_projected(self) -> None:
        control_database = support.create_control_db(self.state_root)
        connection = __import__("sqlite3").connect(control_database)
        try:
            initiative_id = "initiative-1"
            connection.execute(
                "INSERT INTO task_initiatives VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    initiative_id, "Checkout initiative", "Two delivery lanes",
                    "source-task", "initiative-key", "d" * 64,
                    "2026-08-02T18:00:00Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        backend = support.insert_prepared_task(
            control_database,
            id="source-task",
            short_id="A1",
            title="Backend",
            initiative_id=initiative_id,
        )
        frontend = support.insert_prepared_task(
            control_database,
            id="frontend-task",
            short_id="A2",
            title="Frontend",
            rank=2,
            idempotency_key="frontend-key",
            initiative_id=initiative_id,
        )
        docs = support.insert_prepared_task(
            control_database,
            id="docs-task",
            short_id="A3",
            title="Docs",
            rank=3,
            idempotency_key="docs-key",
            initiative_id=initiative_id,
        )
        connection = __import__("sqlite3").connect(control_database)
        try:
            connection.execute(
                "INSERT INTO task_dependencies VALUES (?, ?, 'completed', ?, ?)",
                (frontend["id"], backend["id"], "Requires API completion", "2026-08-02T18:00:00Z"),
            )
            connection.commit()
            connection.row_factory = __import__("sqlite3").Row
            coordination = self._connect()
            try:
                payload = summary_payload(
                    coordination, self.config, NOW, control_connection=connection
                )
            finally:
                coordination.close()
        finally:
            connection.close()
        by_id = {task["id"]: task for task in payload["tasks"]}
        self.assertEqual(by_id[frontend["id"]]["initiative"]["title"], "Checkout initiative")
        self.assertEqual(by_id[frontend["id"]]["blocked_by"][0]["short_id"], "A1")
        self.assertIn("Blocked by A1", by_id[frontend["id"]]["blocker"])
        self.assertEqual(
            [item["short_id"] for item in by_id[docs["id"]]["parallel_with"]],
            ["A1", "A2"],
        )

    def test_archived_prepared_task_is_not_active_or_stale(self) -> None:
        control_database = support.create_control_db(self.state_root)
        prepared = support.insert_prepared_task(
            control_database,
            disposition="archived",
            updated_at="2026-08-02T10:00:00Z",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination, self.config, NOW, control_connection=control
            )
        finally:
            coordination.close()
            control.close()
        task = next(item for item in payload["tasks"] if item["id"] == prepared["id"])
        self.assertEqual(task["status"], "archived")
        self.assertFalse(task["stale"])
        self.assertNotIn(prepared["id"], {item["task_id"] for item in payload["attention"]})

    def test_v7_hides_trash_and_marks_safe_stop_attention(self) -> None:
        control_database = support.create_control_db(self.state_root)
        adopted = support.insert_prepared_task(
            control_database,
            id="33333333-3333-4333-8333-333333333333",
            short_id="A3",
            preparation_status="adopted",
            adopted_thread_id="11111111-1111-4111-8111-111111111111",
            adopted_harness="codex",
            adopted_at="2026-08-02T18:01:00Z",
            stop_requested_at="2026-08-02T18:02:00Z",
        )
        support.insert_prepared_task(
            control_database,
            id="44444444-4444-4444-8444-444444444444",
            short_id="A4",
            rank=2,
            idempotency_key="trashed-a4",
            disposition="trashed",
            trashed_at="2026-08-02T18:03:00Z",
            disposition_before_trash="open",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination, self.config, NOW, control_connection=control
            )
        finally:
            coordination.close()
            control.close()
        self.assertEqual([task["id"] for task in payload["tasks"]], [adopted["id"]])
        self.assertTrue(payload["tasks"][0]["stop_requested_at"])
        self.assertEqual(payload["attention"][0]["reasons"], ["stop-requested"])

    def test_cancelled_card_overrides_stale_coordinator_progress_as_inactive(self) -> None:
        control_database = support.create_control_db(self.state_root)
        cancelled = support.insert_prepared_task(
            control_database,
            id="55555555-5555-4555-8555-555555555555",
            short_id="A5",
            preparation_status="cancelled",
            previous_thread_id="11111111-1111-4111-8111-111111111111",
            previous_harness="codex",
            cancelled_at="2026-08-02T17:00:00Z",
            updated_at="2026-08-02T10:00:00Z",
        )
        support.insert_task(
            self.database,
            id=cancelled["id"],
            status="active",
            stage="implementation",
            updated_at="2026-08-02T10:00:00Z",
        )
        coordination = self._connect()
        control = __import__("sqlite3").connect(control_database)
        control.row_factory = __import__("sqlite3").Row
        try:
            payload = summary_payload(
                coordination, self.config, NOW, control_connection=control
            )
        finally:
            coordination.close()
            control.close()
        task = payload["tasks"][0]
        self.assertEqual(task["status"], "cancelled")
        self.assertFalse(task["stale"])
        self.assertEqual(payload["attention"], [])

    def test_both_v5_shapes_are_read_by_columns(self) -> None:
        for shape in ("main", "o1"):
            state_root = self.state_root / shape
            state_root.mkdir()
            control_database = support.create_control_db(state_root)
            connection = __import__("sqlite3").connect(control_database)
            try:
                drop = (
                    ("disposition_before_trash", "trashed_at", "cancelled_at", "stop_requested_at")
                    if shape == "main"
                    else ("previous_harness", "adopted_harness")
                )
                for column in drop:
                    connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
                connection.execute("PRAGMA user_version = 5")
                connection.commit()
            finally:
                connection.close()
            inserted = support.insert_prepared_task(
                control_database,
                short_id="A1",
                stop_requested_at=(
                    "2026-08-02T18:02:00Z" if shape == "o1" else None
                ),
            )
            coordination = self._connect()
            control = __import__("sqlite3").connect(control_database)
            control.row_factory = __import__("sqlite3").Row
            try:
                payload = summary_payload(
                    coordination, self.config, NOW, control_connection=control
                )
            finally:
                coordination.close()
                control.close()
            self.assertEqual(payload["tasks"][0]["id"], inserted["id"])
            self.assertEqual(
                bool(payload["tasks"][0]["stop_requested_at"]),
                shape == "o1",
            )

    def test_task_detail_without_artifacts_directory(self) -> None:
        task = support.insert_task(
            self.database, repository=OBS_PATH, worktree="/absent/worktree"
        )
        connection = self._connect()
        try:
            payload = task_detail_payload(
                connection, self.config, NOW, task["id"]
            )
        finally:
            connection.close()
        assert payload is not None
        self.assertEqual(payload["artifacts"], [])

    def test_stale_flag_uses_threshold(self) -> None:
        support.insert_task(
            self.database,
            id="barely-fresh",
            repository=OBS_PATH,
            updated_at="2026-08-02T18:00:00Z",
        )
        support.insert_task(
            self.database,
            id="just-stale",
            repository=OBS_PATH,
            updated_at="2026-08-02T17:59:00Z",
        )
        connection = self._connect()
        try:
            payload = tasks_payload(connection, self.config, NOW)
        finally:
            connection.close()
        by_id = {task["id"]: task for task in payload["tasks"]}
        self.assertFalse(by_id["barely-fresh"]["stale"])
        self.assertTrue(by_id["just-stale"]["stale"])
        self.assertEqual(
            NOW - datetime.fromisoformat("2026-08-02T18:00:00+00:00"),
            timedelta(minutes=60),
        )


if __name__ == "__main__":
    unittest.main()
