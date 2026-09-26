"""Behavioral tests for fail-soft Orchestra task coordination."""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/coordination.py"
sys.path.insert(0, str(HELPER.parent))
_COORDINATION_SPEC = importlib.util.spec_from_file_location(
    "orchestra_coordination_tests", HELPER
)
assert _COORDINATION_SPEC is not None and _COORDINATION_SPEC.loader is not None
coordination = importlib.util.module_from_spec(_COORDINATION_SPEC)
_COORDINATION_SPEC.loader.exec_module(coordination)


class StateFilePermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "state.sqlite3"

    def state_files(self) -> tuple[Path, Path, Path]:
        return (
            self.database,
            self.database.with_name(self.database.name + "-wal"),
            self.database.with_name(self.database.name + "-shm"),
        )

    def test_secure_state_files_do_not_call_chmod(self) -> None:
        for path in self.state_files():
            path.write_bytes(b"")
            os.chmod(path, 0o600)

        with mock.patch.object(coordination.os, "chmod") as chmod:
            coordination._restrict_state_files(self.database)

        chmod.assert_not_called()

    def test_insecure_regular_state_files_are_corrected_to_0600(self) -> None:
        for path in self.state_files():
            path.write_bytes(b"")
            os.chmod(path, 0o640)

        coordination._restrict_state_files(self.database)

        for path in self.state_files():
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)

    def test_symlink_state_file_remains_rejected(self) -> None:
        target = Path(self.temporary_directory.name) / "target"
        target.write_bytes(b"")
        self.database.symlink_to(target)

        with self.assertRaises(coordination.CoordinationUnavailable):
            coordination._restrict_state_files(self.database)

    def test_non_regular_state_file_remains_rejected(self) -> None:
        self.database.mkdir()

        with self.assertRaises(coordination.CoordinationUnavailable):
            coordination._restrict_state_files(self.database)


class CoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.state_root = root / "state"
        self.repository = root / "repository"
        self.task_one = root / "task-one"
        self.task_two = root / "task-two"
        self.repository.mkdir()
        self.git(self.repository, "init", "-q", "-b", "main")
        self.git(self.repository, "config", "user.name", "Orchestra Test")
        self.git(self.repository, "config", "user.email", "orchestra@example.invalid")
        (self.repository / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git(self.repository, "add", "seed.txt")
        self.git(self.repository, "commit", "-q", "-m", "seed")
        self.head = self.git(self.repository, "rev-parse", "HEAD").stdout.strip()
        self.git(
            self.repository,
            "worktree",
            "add",
            "-q",
            "-b",
            "orchestra/task-one",
            str(self.task_one),
            self.head,
        )
        self.git(
            self.repository,
            "worktree",
            "add",
            "-q",
            "-b",
            "orchestra/task-two",
            str(self.task_two),
            self.head,
        )

    def git(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def command(self, *args: str) -> list[str]:
        return [
            sys.executable,
            str(HELPER),
            "--state-root",
            str(self.state_root),
            *args,
        ]

    def run_cli(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            self.command(*args),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(result.stdout + result.stderr)
        return result, payload

    def create_task(
        self,
        worktree: Path,
        label: str = "Task",
        tier: str = "standard",
        task_id: str | None = None,
    ) -> dict:
        arguments = [
            "task",
            "create",
            "--repository",
            str(self.repository),
            "--worktree",
            str(worktree),
            "--base-revision",
            self.head,
            "--tier",
            tier,
            "--label",
            label,
        ]
        if task_id is not None:
            arguments.extend(("--task-id", task_id))
        result, payload = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["status"], "ok")
        return payload

    def test_create_accepts_prepared_task_uuid_and_rejects_identity_collision(self) -> None:
        prepared_uuid = "11111111-1111-4111-8111-111111111111"
        first = self.create_task(self.task_one, task_id=prepared_uuid)
        repeated = self.create_task(self.task_one, task_id=prepared_uuid)
        self.assertTrue(first["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(first["task"]["id"], prepared_uuid)

        result, collision = self.run_cli(
            "task",
            "create",
            "--task-id",
            prepared_uuid,
            "--repository",
            str(self.repository),
            "--worktree",
            str(self.task_two),
            "--base-revision",
            self.head,
            "--tier",
            "standard",
            "--label",
            "Collision",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(collision["status"], "invalid")
        self.assertIn("task id already belongs", collision["reason"])

    def test_bare_primary_falls_back_to_current_linked_checkout(self) -> None:
        bare = self.repository.parent / "bare.git"
        linked = self.repository.parent / "bare-linked"
        result = subprocess.run(
            ["git", "clone", "--bare", str(self.repository), str(bare)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run(
            ["git", "--git-dir", str(bare), "worktree", "add", str(linked), "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        identity = coordination.git_repository_identity(linked)

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.checkout_root, linked.resolve())
        self.assertEqual(identity.repository_root, linked.resolve())
        self.assertEqual(identity.common_dir, bare.resolve())

    def test_explicit_uuid_cannot_adopt_an_existing_worktree_snapshot(self) -> None:
        existing = self.create_task(self.task_one)
        result, collision = self.run_cli(
            "task",
            "create",
            "--task-id",
            "22222222-2222-4222-8222-222222222222",
            "--repository",
            str(self.repository),
            "--worktree",
            str(self.task_one),
            "--base-revision",
            self.head,
            "--tier",
            "standard",
            "--label",
            "Different UUID",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(collision["status"], "invalid")
        self.assertIn("different task id", collision["reason"])
        self.assertNotEqual(existing["task"]["id"], "22222222-2222-4222-8222-222222222222")

    def test_luna_tier_round_trips_without_schema_change(self) -> None:
        created = self.create_task(self.task_one, tier="luna")
        self.assertEqual(created["task"]["tier"], "luna")

        result, updated = self.run_cli(
            "task",
            "update",
            "--task",
            created["task"]["id"],
            "--tier",
            "standard",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(updated["task"]["tier"], "standard")

        result, restored = self.run_cli(
            "task",
            "update",
            "--task",
            created["task"]["id"],
            "--tier",
            "luna",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(restored["task"]["tier"], "luna")

    def test_tasks_are_idempotent_queryable_and_freely_updatable(self) -> None:
        first = self.create_task(self.task_one, "First")
        second = self.create_task(self.task_two, "Second")
        repeated = self.create_task(self.task_one, "Ignored duplicate")

        self.assertTrue(first["created"])
        self.assertTrue(second["created"])
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["task"]["id"], first["task"]["id"])

        task_id = first["task"]["id"]
        result, updated = self.run_cli(
            "task",
            "update",
            "--task",
            task_id,
            "--tier",
            "critical",
            "--stage",
            "custom-stage-without-transition",
            "--status",
            "paused-for-observation",
            "--summary",
            "  concise   material update ",
            "--blocker",
            "",
            "--next-action",
            "Resume directly",
            "--head-revision",
            self.head,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(updated["task"]["tier"], "critical")
        self.assertEqual(updated["task"]["stage"], "custom-stage-without-transition")
        self.assertEqual(updated["task"]["status"], "paused-for-observation")
        self.assertEqual(updated["task"]["summary"], "concise material update")

        result, listed = self.run_cli("task", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({task["id"] for task in listed["tasks"]}, {
            first["task"]["id"],
            second["task"]["id"],
        })

        result, filtered = self.run_cli(
            "task", "list", "--status", "paused-for-observation"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([task["id"] for task in filtered["tasks"]], [task_id])

    def test_linked_checkout_resolves_primary_repository_and_preserves_legacy_retry(self) -> None:
        identity = coordination.git_repository_identity(self.task_one)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity.checkout_root, self.task_one.resolve())
        self.assertEqual(identity.repository_root, self.repository.resolve())
        self.assertNotEqual(identity.checkout_root, identity.repository_root)

        result, created = self.run_cli(
            "task", "create",
            "--repository", str(self.task_one),
            "--worktree", str(self.task_one),
            "--base-revision", self.head,
            "--tier", "standard",
            "--label", "Linked checkout",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(created["task"]["repository"], str(self.repository.resolve()))
        self.assertEqual(created["task"]["worktree"], str(self.task_one.resolve()))

        database = self.state_root / "state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE tasks SET repository = ? WHERE id = ?",
                (str(self.task_one.resolve()), created["task"]["id"]),
            )

        result, repeated = self.run_cli(
            "task", "create",
            "--repository", str(self.repository),
            "--worktree", str(self.task_one),
            "--base-revision", self.head,
            "--tier", "standard",
            "--label", "Ignored retry",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["task"]["repository"], str(self.task_one.resolve()))

    def test_localized_visible_text_round_trips_with_canonical_labels(self) -> None:
        task_id = self.create_task(self.task_one)["task"]["id"]
        result, updated = self.run_cli(
            "task",
            "update",
            "--task",
            task_id,
            "--tier",
            "standard",
            "--stage",
            "implementation",
            "--status",
            "active",
            "--summary",
            "Fase 2 en progreso — revisión de integración",
            "--blocker",
            "Esperando `git status` en /ruta/técnica",
            "--next-action",
            "Continuar con la verificación",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        task = updated["task"]
        self.assertEqual(task["tier"], "standard")
        self.assertEqual(task["stage"], "implementation")
        self.assertEqual(task["status"], "active")
        self.assertEqual(
            task["summary"],
            "Fase 2 en progreso — revisión de integración",
        )
        self.assertEqual(task["blocker"], "Esperando `git status` en /ruta/técnica")
        self.assertEqual(task["next_action"], "Continuar con la verificación")

        result, activity = self.run_cli(
            "activity",
            "set",
            "--task",
            task_id,
            "--agent",
            "verifier-1",
            "--capability",
            "runtime_verification",
            "--state",
            "running",
            "--summary",
            "Verificación técnica en progreso",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(activity["activity"]["capability"], "runtime_verification")
        self.assertEqual(activity["activity"]["state"], "running")
        self.assertEqual(
            activity["activity"]["summary"],
            "Verificación técnica en progreso",
        )

    def test_completed_task_does_not_reserve_a_reused_worktree_path(self) -> None:
        first = self.create_task(self.task_one)
        result, _ = self.run_cli(
            "task",
            "update",
            "--task",
            first["task"]["id"],
            "--status",
            "completed",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        second = self.create_task(self.task_one)

        self.assertTrue(second["created"])
        self.assertNotEqual(second["task"]["id"], first["task"]["id"])
        result, listed = self.run_cli("task", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(listed["tasks"]), 2)

    def test_concurrent_activity_updates_keep_each_agent(self) -> None:
        task_id = self.create_task(self.task_one)["task"]["id"]
        database = self.state_root / "state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE tasks SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?",
                (task_id,),
            )
        processes = [
            subprocess.Popen(
                self.command(
                    "activity",
                    "set",
                    "--task",
                    task_id,
                    "--agent",
                    f"agent-{index}",
                    "--capability",
                    "runtime_verification",
                    "--state",
                    "running",
                    "--summary",
                    f"check {index}",
                ),
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(8)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(json.loads(stdout)["status"], "ok")

        result, shown = self.run_cli("task", "show", "--task", task_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(shown["activities"]), 8)
        self.assertEqual(
            {activity["agent_id"] for activity in shown["activities"]},
            {f"agent-{index}" for index in range(8)},
        )
        self.assertNotEqual(shown["task"]["updated_at"], "2000-01-01T00:00:00Z")

        result, cleared = self.run_cli(
            "activity",
            "clear",
            "--task",
            task_id,
            "--agent",
            "agent-0",
            "--capability",
            "runtime_verification",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(cleared["cleared"])

    def test_concurrent_task_creation_uses_one_active_snapshot(self) -> None:
        processes = [
            subprocess.Popen(
                self.command(
                    "task",
                    "create",
                    "--repository",
                    str(self.repository),
                    "--worktree",
                    str(self.task_one),
                    "--base-revision",
                    self.head,
                    "--tier",
                    "standard",
                    "--label",
                    f"Concurrent {index}",
                ),
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(8)
        ]
        payloads = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            payloads.append(json.loads(stdout))

        self.assertEqual(sum(payload["created"] for payload in payloads), 1)
        self.assertEqual(
            len({payload["task"]["id"] for payload in payloads}),
            1,
        )
        result, listed = self.run_cli("task", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(listed["tasks"]), 1)

    def test_rejects_unrelated_worktrees(self) -> None:
        unrelated = Path(self.temporary_directory.name) / "unrelated"
        unrelated.mkdir()
        self.git(unrelated, "init", "-q", "-b", "main")
        self.git(unrelated, "config", "user.name", "Orchestra Test")
        self.git(unrelated, "config", "user.email", "orchestra@example.invalid")
        (unrelated / "file.txt").write_text("unrelated\n", encoding="utf-8")
        self.git(unrelated, "add", "file.txt")
        self.git(unrelated, "commit", "-q", "-m", "unrelated")
        result, payload = self.run_cli(
            "task",
            "create",
            "--repository",
            str(self.repository),
            "--worktree",
            str(unrelated),
            "--base-revision",
            self.head,
            "--tier",
            "standard",
            "--label",
            "Invalid",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "invalid")

    def test_accepts_hybrid_task_in_repository_checkout(self) -> None:
        self.git(self.repository, "switch", "-c", "orchestra/hybrid-task")

        result, payload = self.run_cli(
            "task",
            "create",
            "--repository",
            str(self.repository),
            "--worktree",
            str(self.repository),
            "--base-revision",
            self.head,
            "--tier",
            "standard",
            "--label",
            "Hybrid task",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(payload["created"])
        self.assertEqual(payload["task"]["worktree"], str(self.repository.resolve()))

    def test_removed_worktree_keeps_task_metadata(self) -> None:
        task_id = self.create_task(self.task_one)["task"]["id"]
        self.git(self.repository, "worktree", "remove", str(self.task_one))

        result, shown = self.run_cli("task", "show", "--task", task_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(shown["task"]["worktree"], str(self.task_one.resolve()))

    def test_legacy_artifacts_table_is_tolerated(self) -> None:
        task_id = self.create_task(self.task_one)["task"]["id"]
        database = self.state_root / "state.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, task_id TEXT)"
        )
        connection.commit()
        connection.close()

        result, listed = self.run_cli("task", "list")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(listed["status"], "ok")
        self.assertEqual(len(listed["tasks"]), 1)

        result, shown = self.run_cli("task", "show", "--task", task_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("artifacts", shown)

    def test_damaged_database_is_reported_without_replacement(self) -> None:
        self.state_root.mkdir()
        database = self.state_root / "state.sqlite3"
        database.write_bytes(b"not a sqlite database")
        before = database.read_bytes()

        result, payload = self.run_cli("task", "list")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(database.read_bytes(), before)

    def test_empty_existing_database_is_safely_initialized(self) -> None:
        self.state_root.mkdir()
        database = self.state_root / "state.sqlite3"
        database.touch()

        result, payload = self.run_cli("task", "list")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload, {"status": "ok", "tasks": []})
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(database) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                1,
            )

    def test_partial_version_zero_database_is_not_reconstructed(self) -> None:
        self.state_root.mkdir()
        database = self.state_root / "state.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE interrupted (id TEXT PRIMARY KEY)")
        before = database.read_bytes()

        result, payload = self.run_cli("task", "list")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("schema", payload["reason"])
        self.assertEqual(database.read_bytes(), before)

    def test_concurrent_empty_database_initialization_converges(self) -> None:
        processes = [
            subprocess.Popen(
                self.command("task", "list"),
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(json.loads(stdout), {"status": "ok", "tasks": []})
        database = self.state_root / "state.sqlite3"
        self.assertEqual(database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state_root.stat().st_mode & 0o777, 0o700)

    def test_revision_lengths_are_exact_and_states_remain_descriptive(self) -> None:
        task_id = self.create_task(self.task_one)["task"]["id"]
        for revision in ("a" * 41, "b" * 63):
            result, payload = self.run_cli(
                "task",
                "update",
                "--task",
                task_id,
                "--head-revision",
                revision,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(payload["status"], "invalid")

        for revision in (self.head, "c" * 64):
            result, payload = self.run_cli(
                "task",
                "update",
                "--task",
                task_id,
                "--head-revision",
                revision,
                "--stage",
                "free-form-stage",
                "--status",
                "paused-for-observation",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(payload["task"]["head_revision"], revision)
            self.assertEqual(payload["task"]["stage"], "free-form-stage")
            self.assertEqual(payload["task"]["status"], "paused-for-observation")

    def test_state_root_symlink_is_unavailable(self) -> None:
        target = Path(self.temporary_directory.name) / "real-state"
        target.mkdir()
        self.state_root.symlink_to(target, target_is_directory=True)

        result, payload = self.run_cli("task", "list")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "unavailable")

    def test_cli_validation_errors_are_compact_json(self) -> None:
        result = subprocess.run(
            self.command("task", "show"),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid")
        self.assertNotIn("blocked", payload)


if __name__ == "__main__":
    unittest.main()
