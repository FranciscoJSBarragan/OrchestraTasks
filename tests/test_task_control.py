"""Tests for the durable prepared-task Kanban."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CONTROL_ROOT = ROOT / "control"
sys.path.insert(0, str(CONTROL_ROOT))

from orchestra_control.db import SCHEMA_VERSION, StorageError  # noqa: E402
from orchestra_control.service import (  # noqa: E402
    ControlError,
    ControlService,
    host_thread_from_env,
    sequence_to_short_id,
    validate_thread_id,
)
from orchestra_control.mcp import TOOLS, cli_arguments  # noqa: E402


HELPER = ROOT / "scripts/task_control.py"
MCP_HELPER = ROOT / "scripts/task_mcp.py"
THREAD_ONE = "11111111-1111-4111-8111-111111111111"
THREAD_TWO = "22222222-2222-4222-8222-222222222222"


class TaskControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_root = self.root / "state"
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Orchestra Test")
        self.git("config", "user.email", "orchestra@example.invalid")
        (self.repository / "seed.txt").write_text("seed\n", encoding="utf-8")
        self.git("add", "seed.txt")
        self.git("commit", "-q", "-m", "seed")
        self.revision = self.git("rev-parse", "HEAD").stdout.strip()
        self.common_dir = self.git(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        ).stdout.strip()
        self.service = ControlService(self.state_root)

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args], cwd=self.repository, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def create_task(self, key: str = "capture-1") -> dict[str, object]:
        return self.service.create_task(
            title="Durable task",
            brief="Inspect the repository and prepare a candidate specification.",
            source_harness="test",
            repository=str(self.repository),
            idempotency_key=key,
        )

    def prepare(self, task: str) -> dict[str, object]:
        return self.service.prepare_task(
            task_ref=task,
            repository=str(self.repository),
            prepared_revision=self.revision,
            repository_common_dir=self.common_dir,
            repository_context="# Repository context\n\nObserved source.\n",
            specification="# Specification\n\nConfirmed result.\n",
            confirmed=True,
        )

    def cli(
        self,
        *args: str,
        thread: str | None = None,
        cursor_thread: str | None = None,
        grok_thread: str | None = None,
        devin_thread: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict]:
        environment = os.environ.copy()
        for key in (
            "CODEX_THREAD_ID",
            "CURSOR_CONVERSATION_ID",
            "CURSOR_THREAD_ID",
            "ORCHESTRA_HOST_THREAD_ID",
            "GROK_SESSION_ID",
            "ORCHESTRA_DEVIN_THREAD_ID",
        ):
            environment.pop(key, None)
        if thread is not None:
            environment["CODEX_THREAD_ID"] = thread
        if cursor_thread is not None:
            environment["ORCHESTRA_HOST_THREAD_ID"] = cursor_thread
        if grok_thread is not None:
            environment["GROK_SESSION_ID"] = grok_thread
        if devin_thread is not None:
            environment["ORCHESTRA_DEVIN_THREAD_ID"] = devin_thread
        result = subprocess.run(
            [sys.executable, str(HELPER), "--state-root", str(self.state_root), *args],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(result.stdout + result.stderr)
        return result, payload

    def test_short_id_sequence_matches_approved_format(self) -> None:
        expected = {
            1: "A1",
            99: "A99",
            100: "B1",
            2574: "Z99",
            2575: "AA1",
            2674: "AB1",
        }
        for sequence, short_id in expected.items():
            self.assertEqual(sequence_to_short_id(sequence), short_id)

    def test_cli_package_import_resolves_shared_git_helper(self) -> None:
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(CONTROL_ROOT)!r}); "
            "import orchestra_control.cli"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_capture_is_idempotent_allocates_human_id_and_is_private(self) -> None:
        first = self.create_task()
        second = self.create_task()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["short_id"], "A1")
        self.assertEqual(first["preparation_status"], "draft")
        self.assertEqual(self.create_task("capture-2")["short_id"], "A2")
        self.assertEqual(stat.S_IMODE(self.state_root.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.state_root / "control.sqlite3").stat().st_mode), 0o600
        )

    def test_lookup_is_case_insensitive_and_uuid_remains_identity(self) -> None:
        task = self.create_task()
        self.assertEqual(self.service.get_task("a1")["id"], task["id"])
        self.assertEqual(self.service.get_task(str(task["id"]))["short_id"], "A1")

    def test_concurrent_capture_allocates_unique_monotonic_ids(self) -> None:
        commands = [
            [
                sys.executable,
                str(HELPER),
                "--state-root",
                str(self.state_root),
                "task",
                "create",
                "--title",
                f"Task {index}",
                "--brief",
                "Prepared task",
                "--source-harness",
                "test",
                "--idempotency-key",
                f"concurrent-{index}",
            ]
            for index in range(12)
        ]
        processes = [
            subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for command in commands
        ]
        rows = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            rows.append(json.loads(stdout)["task"])
        self.assertEqual(len({row["short_id"] for row in rows}), 12)
        self.assertEqual(
            {row["short_id"] for row in rows},
            {f"A{index}" for index in range(1, 13)},
        )

    def test_prepare_requires_confirmation_and_writes_private_documents(self) -> None:
        task = self.create_task()
        with self.assertRaises(ControlError):
            self.service.prepare_task(
                task_ref="A1",
                repository=str(self.repository),
                prepared_revision=self.revision,
                repository_context="context",
                specification="specification",
                confirmed=False,
            )
        ready = self.prepare("A1")
        self.assertEqual(ready["preparation_status"], "ready")
        for name in ("repository_context", "specification", "marker"):
            path = Path(ready["documents"][name])
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        marker = json.loads(Path(ready["documents"]["marker"]).read_text())
        self.assertEqual(marker["id"], task["id"])
        self.assertEqual(marker["short_id"], "A1")

    def test_state_check_and_mutation_are_serialized_against_adoption(self) -> None:
        self.create_task()
        self.prepare("A1")
        archive_read = threading.Event()
        release_archive = threading.Event()
        adoption_started = threading.Event()
        adoption_finished = threading.Event()
        outcomes: dict[str, object] = {}
        original_task_row = self.service._task_row

        def gated_task_row(connection: sqlite3.Connection, task_ref: str) -> sqlite3.Row:
            row = original_task_row(connection, task_ref)
            if threading.current_thread().name == "archive-worker":
                archive_read.set()
                if not release_archive.wait(5):
                    raise RuntimeError("archive test gate timed out")
            return row

        self.service._task_row = gated_task_row  # type: ignore[method-assign]

        def archive() -> None:
            try:
                outcomes["archive"] = self.service.set_archived("A1", True)
            except Exception as error:  # pragma: no cover - asserted below
                outcomes["archive_error"] = error

        def adopt() -> None:
            adoption_started.set()
            try:
                outcomes["adopt"] = ControlService(self.state_root).adopt_task(
                    task_ref="A1",
                    thread_id=THREAD_ONE,
                    repository=str(self.repository),
                    current_revision=self.revision,
                    repository_common_dir=self.common_dir,
                )
            except Exception as error:
                outcomes["adopt_error"] = error
            finally:
                adoption_finished.set()

        archive_thread = threading.Thread(target=archive, name="archive-worker")
        adopt_thread = threading.Thread(target=adopt, name="adopt-worker")
        archive_thread.start()
        self.assertTrue(archive_read.wait(5))
        adopt_thread.start()
        self.assertTrue(adoption_started.wait(5))
        time.sleep(0.1)
        self.assertFalse(adoption_finished.is_set())
        release_archive.set()
        archive_thread.join(5)
        adopt_thread.join(5)
        self.assertFalse(archive_thread.is_alive())
        self.assertFalse(adopt_thread.is_alive())
        self.assertNotIn("archive_error", outcomes)
        self.assertIsInstance(outcomes.get("adopt_error"), ControlError)
        current = self.service.get_task("A1")
        self.assertEqual(current["disposition"], "archived")
        self.assertEqual(current["preparation_status"], "ready")

    def test_adoption_requires_native_thread_and_reports_context_delta(self) -> None:
        self.create_task()
        self.prepare("A1")
        with self.assertRaises(ControlError) as missing:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=None,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        self.assertEqual(missing.exception.status, "blocked")
        adopted = self.service.adopt_task(
            task_ref="a1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.assertEqual(adopted["preparation_status"], "adopted")
        self.assertEqual(adopted["context_action"], "use_prepared")
        self.git("commit", "--allow-empty", "-q", "-m", "new revision")
        new_revision = self.git("rev-parse", "HEAD").stdout.strip()
        resumed = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=new_revision,
        )
        self.assertEqual(resumed["context_action"], "repository_context_delta")

    def test_cursor_adoption_blocks_without_host_identity_and_accepts_host_id(
        self,
    ) -> None:
        self.create_task()
        self.prepare("A1")
        with self.assertRaises(ControlError) as missing:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=None,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness="cursor",
            )
        self.assertEqual(missing.exception.status, "blocked")
        self.assertIn("Cursor host conversation identity", missing.exception.reason)
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id="cursor-conversation-abc",
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="cursor",
        )
        self.assertEqual(adopted["preparation_status"], "adopted")
        self.assertEqual(adopted["adopted_thread_id"], "cursor-conversation-abc")

    def test_host_thread_from_env_reads_codex_or_cursor_identity(self) -> None:
        self.assertEqual(host_thread_from_env({}), (None, None))
        self.assertEqual(
            host_thread_from_env({"CODEX_THREAD_ID": THREAD_ONE}),
            ("codex", THREAD_ONE),
        )
        self.assertEqual(
            host_thread_from_env({"ORCHESTRA_HOST_THREAD_ID": "cursor-thread-1"}),
            ("cursor", "cursor-thread-1"),
        )
        self.assertEqual(
            host_thread_from_env({"GROK_SESSION_ID": THREAD_TWO}),
            ("grok", THREAD_TWO),
        )
        for other in ("GROK_SESSION_ID", "ORCHESTRA_HOST_THREAD_ID", "ORCHESTRA_DEVIN_THREAD_ID"):
            with self.subTest(other=other):
                with self.assertRaises(ControlError) as ambiguous:
                    host_thread_from_env({"CODEX_THREAD_ID": THREAD_ONE, other: THREAD_TWO})
                self.assertEqual(ambiguous.exception.status, "blocked")
        self.assertEqual(
            host_thread_from_env({"CURSOR_CONVERSATION_ID": "undocumented"}),
            (None, None),
        )
        self.assertEqual(
            host_thread_from_env({"CURSOR_THREAD_ID": "undocumented"}),
            (None, None),
        )
        with self.assertRaises(ControlError) as missing:
            validate_thread_id(None, None)
        self.assertEqual(missing.exception.status, "blocked")
        self.assertIn("host conversation identity is missing", missing.exception.reason)

    def test_cli_ambiguous_host_identity_cannot_adopt(self) -> None:
        self.create_task()
        self.prepare("A1")
        result, payload = self.cli(
            "task", "adopt", "--task", "A1", "--repository", str(self.repository),
            thread=THREAD_ONE, devin_thread="devin-owner",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "blocked")
        self.assertIsNone(self.service.get_task("A1")["adopted_harness"])
        with self.assertRaises(ControlError) as grok_missing:
            validate_thread_id("not-a-uuid", "grok")
        self.assertEqual(grok_missing.exception.status, "blocked")
        self.assertIn("Grok host session identity", grok_missing.exception.reason)

    def test_adoption_rejects_tampered_prepared_documents(self) -> None:
        self.create_task()
        ready = self.prepare("A1")
        Path(ready["documents"]["specification"]).write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(ControlError) as error:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        self.assertEqual(error.exception.status, "blocked")
        self.assertIn("digest", error.exception.reason)

    def test_adoption_rejects_repository_alias_and_symlinked_document(self) -> None:
        self.create_task()
        ready = self.prepare("A1")
        alias = self.root / "repository-alias"
        alias.symlink_to(self.repository, target_is_directory=True)
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(alias),
            current_revision=self.revision,
        )
        self.assertEqual(adopted["adopted_thread_id"], THREAD_ONE)
        self.assertEqual(adopted["adopted_harness"], "codex")
        self.service.transfer_task(
            task_ref="A1", thread_id=THREAD_ONE, stable_checkpoint=True
        )
        specification = Path(ready["documents"]["specification"])
        original = specification.read_text(encoding="utf-8")
        specification.unlink()
        target = self.root / "specification-target.md"
        target.write_text(original, encoding="utf-8")
        specification.symlink_to(target)
        with self.assertRaises(ControlError) as error:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        self.assertEqual(error.exception.status, "blocked")
        self.assertIn("unsafe", error.exception.reason)

    def test_capture_and_adoption_share_identity_across_linked_worktrees(self) -> None:
        linked = self.root / "N1"
        self.git("worktree", "add", "-q", "-b", "orchestra/n1", str(linked), self.revision)
        result, payload = self.cli(
            "task", "create",
            "--title", "Linked task",
            "--brief", "Prepare from a linked checkout.",
            "--repository", str(linked),
            "--source-harness", "test",
            "--idempotency-key", "linked-capture",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload["task"]["repository"], str(self.repository.resolve()))

        self.prepare("A1")
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(linked),
            current_revision=self.revision,
            repository_common_dir=self.common_dir,
        )
        self.assertEqual(adopted["adopted_thread_id"], THREAD_ONE)

        other = self.root / "other-repository"
        other.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=other, check=True)
        other_common_dir = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=other, capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.service.transfer_task(
            task_ref="A1", thread_id=THREAD_ONE, stable_checkpoint=True
        )
        with self.assertRaises(ControlError) as error:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_TWO,
                repository=str(other),
                current_revision=self.revision,
                repository_common_dir=other_common_dir,
            )
        self.assertEqual(error.exception.status, "invalid")
        self.assertIn("different Git repository", error.exception.reason)

    def test_cli_lifecycle_normalizes_legacy_linked_repository(self) -> None:
        linked_a = self.root / "legacy-a"
        linked_b = self.root / "legacy-b"
        self.git(
            "worktree", "add", "-q", "-b", "orchestra/legacy-a",
            str(linked_a), self.revision,
        )
        self.git(
            "worktree", "add", "-q", "-b", "orchestra/legacy-b",
            str(linked_b), self.revision,
        )
        self.create_task()
        self.prepare("A1")
        database = self.state_root / "control.sqlite3"

        def restore_legacy_identity() -> None:
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE tasks SET repository = ?, repository_common_dir = NULL "
                    "WHERE short_id = 'A1'",
                    (str(linked_a.resolve()),),
                )

        restore_legacy_identity()
        result, adopted = self.cli(
            "task", "adopt", "--task", "A1", "--repository", str(linked_b),
            thread=THREAD_ONE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(adopted["task"]["repository"], str(self.repository.resolve()))
        self.assertEqual(adopted["task"]["repository_common_dir"], self.common_dir)

        restore_legacy_identity()
        result, reclaimed = self.cli(
            "task", "reclaim", "--task", "A1", "--repository", str(linked_b),
            "--authorized", thread=THREAD_TWO,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(reclaimed["task"]["repository"], str(self.repository.resolve()))
        self.assertEqual(reclaimed["task"]["repository_common_dir"], self.common_dir)

        restore_legacy_identity()
        result, finished = self.cli(
            "task", "finish", "--task", "A1", "--repository", str(linked_b),
            "--task-revision", self.revision, thread=THREAD_TWO,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(finished["task"]["repository"], str(self.repository.resolve()))
        self.assertEqual(finished["task"]["repository_common_dir"], self.common_dir)

        restore_legacy_identity()
        result, delivered = self.cli(
            "task", "record-delivery", "--task", "A1",
            "--repository", str(linked_b),
            "--task-revision", self.revision,
            "--delivery-revision", self.revision,
            "--kind", "local-integration", thread=THREAD_TWO,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(delivered["task"]["repository"], str(self.repository.resolve()))
        self.assertEqual(delivered["task"]["repository_common_dir"], self.common_dir)

    def test_only_one_chat_adopts_and_transfer_requires_stable_checkpoint(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        with self.assertRaises(ControlError) as busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        self.assertEqual(busy.exception.status, "busy")
        with self.assertRaises(ControlError):
            self.service.transfer_task(
                task_ref="A1", thread_id=THREAD_ONE, stable_checkpoint=False
            )
        transferred = self.service.transfer_task(
            task_ref="A1", thread_id=THREAD_ONE, stable_checkpoint=True
        )
        self.assertEqual(transferred["preparation_status"], "ready")
        self.assertEqual(transferred["transfer_generation"], 1)
        with self.assertRaises(ControlError):
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        resumed = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.assertTrue(resumed["resume_existing_checkout"])
        self.assertEqual(resumed["id"], transferred["id"])
        self.assertEqual(resumed["short_id"], "A1")
        self.assertEqual(resumed["title"], transferred["title"])

    def test_reclaim_takes_over_another_host_chat(self) -> None:
        self.create_task()
        self.prepare("A1")
        with self.assertRaises(ControlError) as not_adopted:
            self.service.reclaim_task(
                task_ref="A1",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
                authorized=True,
            )
        self.assertEqual(not_adopted.exception.status, "invalid")
        self.assertIn("owned by another chat", not_adopted.exception.reason)
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.assertEqual(adopted["adopted_thread_id"], THREAD_ONE)
        with self.assertRaises(ControlError) as busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id="cursor-conversation-abc",
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness="cursor",
            )
        self.assertEqual(busy.exception.status, "busy")
        with self.assertRaises(ControlError) as unauthorized:
            self.service.reclaim_task(
                task_ref="A1",
                thread_id="cursor-conversation-abc",
                repository=str(self.repository),
                current_revision=self.revision,
                authorized=False,
                source_harness="cursor",
            )
        self.assertEqual(unauthorized.exception.status, "invalid")
        self.assertIn("authorization", unauthorized.exception.reason)
        with self.assertRaises(ControlError) as missing:
            self.service.reclaim_task(
                task_ref="A1",
                thread_id=None,
                repository=str(self.repository),
                current_revision=self.revision,
                authorized=True,
                source_harness="cursor",
            )
        self.assertEqual(missing.exception.status, "blocked")
        self.assertIn("Cursor host conversation identity", missing.exception.reason)
        with self.assertRaises(ControlError) as same_thread:
            self.service.reclaim_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
                authorized=True,
            )
        self.assertEqual(same_thread.exception.status, "invalid")
        self.assertIn("continue instead of reclaim", same_thread.exception.reason)
        with self.assertRaises(ControlError) as foreign_transfer:
            self.service.transfer_task(
                task_ref="A1",
                thread_id="cursor-conversation-abc",
                stable_checkpoint=True,
                source_harness="cursor",
            )
        self.assertEqual(foreign_transfer.exception.status, "invalid")
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id="cursor-conversation-abc",
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="cursor",
        )
        self.assertEqual(reclaimed["preparation_status"], "adopted")
        self.assertEqual(reclaimed["adopted_thread_id"], "cursor-conversation-abc")
        self.assertEqual(reclaimed["adopted_harness"], "cursor")
        self.assertEqual(reclaimed["previous_thread_id"], THREAD_ONE)
        self.assertEqual(reclaimed["previous_harness"], "codex")
        self.assertEqual(reclaimed["transfer_generation"], 1)
        self.assertTrue(reclaimed["resume_existing_checkout"])
        with self.assertRaises(ControlError) as still_busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
            )
        self.assertEqual(still_busy.exception.status, "busy")
        with self.assertRaises(ControlError) as same_new_owner:
            self.service.reclaim_task(
                task_ref="A1",
                thread_id="cursor-conversation-abc",
                repository=str(self.repository),
                current_revision=self.revision,
                authorized=True,
                source_harness="cursor",
            )
        self.assertEqual(same_new_owner.exception.status, "invalid")
        result, missing_cli = self.cli(
            "task",
            "reclaim",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            "--authorized",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(missing_cli["status"], "blocked")
        result, cli_reclaim = self.cli(
            "task",
            "reclaim",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            "--authorized",
            cursor_thread="cursor-conversation-abc",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(cli_reclaim["status"], "invalid")
        result, without_flag = self.cli(
            "task",
            "reclaim",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            cursor_thread="cursor-other",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(without_flag["status"], "invalid")
        result, cli_ok = self.cli(
            "task",
            "reclaim",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            "--authorized",
            cursor_thread="cursor-other",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(cli_ok["task"]["adopted_thread_id"], "cursor-other")
        self.assertEqual(cli_ok["task"]["previous_thread_id"], "cursor-conversation-abc")
        self.assertTrue(cli_ok["task"]["resume_existing_checkout"])

    def test_finish_and_archive_are_owner_safe(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        with self.assertRaises(ControlError):
            self.service.set_archived("A1", True)
        with self.assertRaises(ControlError):
            self.service.finish_task(task_ref="A1", thread_id=THREAD_TWO)
        completed = self.service.finish_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            terminal_revision=self.revision,
            repository_common_dir=self.common_dir,
        )
        self.assertEqual(completed["preparation_status"], "completed")
        self.assertEqual(self.service.set_archived("a1", True)["disposition"], "archived")

    def test_draft_update_preserves_or_clears_repository_explicitly(self) -> None:
        self.create_task()
        preserved = self.service.update_draft(
            task_ref="A1",
            title="Edited task",
            brief="A clearer bounded brief.",
        )
        self.assertEqual(preserved["repository"], str(self.repository.resolve()))
        self.assertEqual(preserved["brief_revision"], 2)
        result, payload = self.cli(
            "task", "update", "--task", "A1", "--title", "CLI edit",
            "--brief", "Keep the repository because the flag is omitted.",
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["task"]["repository"], str(self.repository.resolve()))
        result, payload = self.cli(
            "task", "update", "--task", "A1", "--title", "No repository",
            "--brief", "Clear the repository explicitly.", "--repository", "",
        )
        self.assertEqual(result.returncode, 0, payload)
        self.assertIsNone(payload["task"]["repository"])

    def test_archive_trash_restore_and_strict_purge(self) -> None:
        first = self.create_task()
        archived = self.service.set_archived("A1", True)
        self.assertEqual(archived["disposition"], "archived")
        trashed = self.service.set_trashed("A1", True)
        self.assertEqual(trashed["disposition_before_trash"], "archived")
        self.assertNotIn("A1", [item["short_id"] for item in self.service.list_tasks(True)])
        self.assertIn(
            "A1",
            [item["short_id"] for item in self.service.list_tasks(True, True)],
        )
        restored = self.service.set_trashed("A1", False)
        self.assertEqual(restored["disposition"], "archived")
        self.service.set_archived("A1", False)
        self.service.set_trashed("A1", True)
        with self.assertRaises(ControlError) as mismatch:
            self.service.purge_task("A1", "A2")
        self.assertEqual(mismatch.exception.status, "invalid")
        purged = self.service.purge_task("A1", "a1")
        self.assertTrue(purged["purged"])
        with self.assertRaises(ControlError):
            self.service.get_task("A1")
        second = self.create_task("capture-2")
        self.assertNotEqual(second["short_id"], first["short_id"])

    def test_purge_reports_quarantined_document_cleanup_failure(self) -> None:
        self.create_task()
        documents = self.state_root / "tasks" / "A1"
        documents.mkdir(parents=True, mode=0o700)
        (documents / "stale.txt").write_text("stale private data\n", encoding="utf-8")
        self.service.set_trashed("A1", True)
        with mock.patch(
            "orchestra_control.service.shutil.rmtree",
            side_effect=OSError("simulated cleanup failure"),
        ):
            purged = self.service.purge_task("A1", "A1")
        self.assertTrue(purged["purged"])
        self.assertIn("quarantined documents remain", purged["warning"])
        quarantine = self.state_root / ".purge-quarantine"
        self.assertEqual(len(list(quarantine.iterdir())), 1)
        with self.assertRaises(ControlError):
            self.service.get_task("A1")

    def test_purge_storage_failure_stays_inside_json_contract(self) -> None:
        unsafe_root = self.root / "unsafe-state"
        unsafe_root.write_text("not a directory\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--state-root",
                str(unsafe_root),
                "task",
                "purge",
                "--task",
                "A1",
                "--confirm",
                "A1",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "unavailable")

    def test_purge_translates_storage_failure_during_document_resolution(self) -> None:
        self.create_task()
        self.service.set_trashed("A1", True)
        with mock.patch(
            "orchestra_control.service.resolve_state_root",
            side_effect=[self.state_root, StorageError("simulated state-root race")],
        ):
            with self.assertRaises(ControlError) as unavailable:
                self.service.purge_task("A1", "A1")
        self.assertEqual(unavailable.exception.status, "unavailable")
        self.assertIn("simulated state-root race", unavailable.exception.reason)
        self.assertEqual(self.service.get_task("A1")["disposition"], "trashed")

    def test_purge_rejects_evidence(self) -> None:
        self.create_task()
        self.service.add_note(
            task_id="A1",
            body="Keep this evidence.",
            source_harness="test",
            source_reference="",
            idempotency_key="note-keep",
        )
        self.service.set_trashed("A1", True)
        with self.assertRaises(ControlError) as evidence:
            self.service.purge_task("A1", "A1")
        self.assertEqual(evidence.exception.status, "busy")
        self.assertIn("notes", evidence.exception.reason)

    def test_safe_stop_matrix_is_owner_namespaced_and_reopen_resumes(self) -> None:
        identities = (
            ("codex", THREAD_ONE),
            ("cursor", "cursor-conversation-abc"),
            ("grok", THREAD_TWO),
        )
        for index, (harness, thread) in enumerate(identities, start=1):
            task = self.create_task(f"safe-stop-{index}")
            short_id = str(task["short_id"])
            self.prepare(short_id)
            self.service.adopt_task(
                task_ref=short_id,
                thread_id=thread,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness=harness,
            )
            requested = self.service.request_stop(short_id)
            self.assertTrue(requested["stop_requested_at"])
            with self.assertRaises(ControlError) as transfer:
                self.service.transfer_task(
                    task_ref=short_id,
                    thread_id=thread,
                    stable_checkpoint=True,
                    source_harness=harness,
                )
            self.assertEqual(transfer.exception.status, "blocked")
            with self.assertRaises(ControlError) as finish:
                self.service.finish_task(
                    task_ref=short_id,
                    thread_id=thread,
                    repository=str(self.repository),
                    terminal_revision=self.revision,
                    repository_common_dir=self.common_dir,
                    source_harness=harness,
                )
            self.assertEqual(finish.exception.status, "blocked")
            cancelled = self.service.acknowledge_stop(
                short_id,
                thread,
                source_harness=harness,
            )
            self.assertEqual(cancelled["preparation_status"], "cancelled")
            self.assertEqual(cancelled["previous_harness"], harness)
            reopened = self.service.reopen_cancelled(short_id)
            self.assertEqual(reopened["preparation_status"], "ready")
            resumed = self.service.adopt_task(
                task_ref=short_id,
                thread_id=thread,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness=harness,
            )
            self.assertTrue(resumed["resume_existing_checkout"])
            self.assertEqual(resumed["adopted_harness"], harness)

    def test_cancelled_or_reopened_execution_history_cannot_be_reprepared(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.service.request_stop("A1")
        self.service.acknowledge_stop("A1", THREAD_ONE)
        with self.assertRaises(ControlError) as cancelled:
            self.prepare("A1")
        self.assertIn("reopened and resumed", cancelled.exception.reason)
        self.service.reopen_cancelled("A1")
        with self.assertRaises(ControlError) as reopened:
            self.prepare("A1")
        self.assertIn("existing checkout and plan", reopened.exception.reason)

    def test_cli_sensitive_operations_are_not_exposed_by_mcp(self) -> None:
        for forbidden in (
            "task_update", "task_trash", "task_restore_trash", "task_purge",
            "task_request_stop", "task_withdraw_stop", "task_acknowledge_stop",
            "task_reopen", "task_adopt", "task_transfer", "task_reclaim",
        ):
            self.assertNotIn(forbidden, TOOLS)

    def manifest(self, condition: str | None = None) -> dict:
        dependencies = []
        if condition:
            dependencies.append(
                {
                    "task": "frontend",
                    "blocked_by": "backend",
                    "condition": condition,
                    "reason": "Requires the integrated API contract",
                }
            )
        return {
            "initiative": {
                "title": "Checkout improvements",
                "brief": "Deliver backend and frontend changes with independent acceptance.",
            },
            "cards": [
                {
                    "key": "backend",
                    "title": "Backend contract",
                    "brief": "Implement and verify the API contract.",
                    "repository": str(self.repository),
                },
                {
                    "key": "frontend",
                    "title": "Frontend flow",
                    "brief": "Consume and verify the API contract.",
                    "repository": str(self.repository),
                },
            ],
            "dependencies": dependencies,
        }

    def decompose(self, condition: str | None = None) -> dict:
        return self.service.decompose_task(
            task_ref="A1",
            manifest=self.manifest(condition),
            confirmed=True,
            idempotency_key=f"decompose-{condition or 'parallel'}",
        )

    def test_decomposition_requires_confirmation_and_is_atomic_and_idempotent(self) -> None:
        source = self.create_task()
        with self.assertRaises(ControlError):
            self.service.decompose_task(
                task_ref="A1",
                manifest=self.manifest(),
                confirmed=False,
                idempotency_key="not-confirmed",
            )
        self.assertEqual([task["short_id"] for task in self.service.list_tasks()], ["A1"])
        invalid = self.manifest()
        invalid["dependencies"] = [
            {
                "task": "backend",
                "blocked_by": "frontend",
                "condition": "completed",
                "reason": "one",
            },
            {
                "task": "frontend",
                "blocked_by": "backend",
                "condition": "completed",
                "reason": "two",
            },
        ]
        with self.assertRaises(ControlError) as cycle:
            self.service.decompose_task(
                task_ref="A1", manifest=invalid, confirmed=True, idempotency_key="cycle"
            )
        self.assertIn("cycle", cycle.exception.reason)
        created = self.decompose()
        repeated = self.decompose()
        self.assertEqual(created["key_map"], {"backend": "A1", "frontend": "A2"})
        self.assertEqual(repeated["key_map"], created["key_map"])
        self.assertEqual(created["cards"][0]["id"], source["id"])
        self.assertEqual(created["cards"][0]["brief_revision"], 2)
        self.assertEqual(
            [item["short_id"] for item in created["cards"][0]["parallel_with"]], ["A2"]
        )
        self.assertEqual(self.create_task("after-decompose")["short_id"], "A3")

    def test_more_than_three_cards_requires_individual_reasons(self) -> None:
        self.create_task()
        manifest = self.manifest()
        manifest["cards"].extend(
            [
                {
                    "key": "docs",
                    "title": "Documentation",
                    "brief": "Document the contract.",
                    "repository": str(self.repository),
                },
                {
                    "key": "ops",
                    "title": "Operations",
                    "brief": "Verify operational acceptance.",
                    "repository": str(self.repository),
                },
            ]
        )
        with self.assertRaises(ControlError) as error:
            self.service.decompose_task(
                task_ref="A1", manifest=manifest, confirmed=True, idempotency_key="too-many"
            )
        self.assertIn("decomposition_reason", error.exception.reason)

    def test_completed_dependency_blocks_adoption_until_finish(self) -> None:
        self.create_task()
        self.decompose("completed")
        self.prepare("A1")
        self.prepare("A2")
        with self.assertRaises(ControlError) as blocked:
            self.service.adopt_task(
                task_ref="A2",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
                repository_common_dir=self.common_dir,
                ancestor_contains=lambda _revision: True,
            )
        self.assertEqual(blocked.exception.status, "blocked")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.service.finish_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            terminal_revision=self.revision,
            repository_common_dir=self.common_dir,
        )
        adopted = self.service.adopt_task(
            task_ref="A2",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            repository_common_dir=self.common_dir,
            ancestor_contains=lambda _revision: True,
        )
        self.assertEqual(adopted["preparation_status"], "adopted")

    def test_delivered_dependency_requires_evidence_and_checkout_containment(self) -> None:
        self.create_task()
        self.decompose("delivered")
        self.prepare("A1")
        self.prepare("A2")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.service.finish_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            terminal_revision=self.revision,
            repository_common_dir=self.common_dir,
        )
        with self.assertRaises(ControlError):
            self.service.adopt_task(
                task_ref="A2",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
                repository_common_dir=self.common_dir,
                ancestor_contains=lambda _revision: True,
            )
        delivered = self.service.record_delivery(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            task_revision=self.revision,
            delivery_revision=self.revision,
            kind="local-integration",
            repository_common_dir=self.common_dir,
        )
        self.assertEqual(delivered["delivery_revision"], self.revision)
        with self.assertRaises(ControlError) as stale:
            self.service.adopt_task(
                task_ref="A2",
                thread_id=THREAD_TWO,
                repository=str(self.repository),
                current_revision=self.revision,
                repository_common_dir=self.common_dir,
                ancestor_contains=lambda _revision: False,
            )
        self.assertIn("checkout-update-required", stale.exception.reason)
        adopted = self.service.adopt_task(
            task_ref="A2",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            repository_common_dir=self.common_dir,
            ancestor_contains=lambda revision: revision == self.revision,
        )
        self.assertEqual(adopted["preparation_status"], "adopted")

    def test_cli_adopt_uses_codex_thread_id_and_never_creates_checkout(self) -> None:
        self.create_task()
        self.prepare("A1")
        before = self.git("worktree", "list", "--porcelain").stdout
        result, missing = self.cli("task", "adopt", "--task", "A1", "--repository", str(self.repository))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(missing["status"], "blocked")
        result, adopted = self.cli(
            "task", "adopt", "--task", "a1", "--repository", str(self.repository), thread=THREAD_ONE
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(adopted["task"]["adopted_thread_id"], THREAD_ONE)
        self.assertEqual(before, self.git("worktree", "list", "--porcelain").stdout)

    def test_mcp_exposes_preparation_but_not_execution_or_ownership(self) -> None:
        process = subprocess.run(
            [sys.executable, str(MCP_HELPER)],
            cwd=ROOT,
            input="\n".join(
                (
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                )
            ) + "\n",
            capture_output=True,
            text=True,
            check=True,
        )
        messages = [json.loads(line) for line in process.stdout.splitlines()]
        names = {tool["name"] for tool in messages[1]["result"]["tools"]}
        self.assertIn("task_prepare", names)
        self.assertIn("task_decompose", names)
        for forbidden in (
            "task_adopt",
            "task_transfer",
            "task_reclaim",
            "task_finish",
            "task_record_delivery",
            "run_start",
            "run_respond",
            "run_reopen",
            "run_reconcile",
            "run_interactions",
            "run_resolve",
        ):
            self.assertNotIn(forbidden, names)

    def test_mcp_decomposition_uses_structured_arrays(self) -> None:
        definition = TOOLS["task_decompose"]["inputSchema"]
        self.assertEqual(definition["properties"]["cards"]["type"], "array")
        self.assertEqual(definition["properties"]["dependencies"]["type"], "array")
        arguments = {
            "task": "A1",
            "initiative": self.manifest()["initiative"],
            "cards": self.manifest()["cards"],
            "dependencies": [],
            "confirmed": True,
            "idempotency_key": "mcp-decompose",
        }
        command = cli_arguments("task_decompose", arguments)
        self.assertEqual(command[:2], ["task", "decompose"])
        encoded = command[command.index("--manifest-json") + 1]
        self.assertEqual(json.loads(encoded)["cards"], arguments["cards"])

    def test_schema_v2_migration_preserves_legacy_rows_without_short_ids(self) -> None:
        self.state_root.mkdir(mode=0o700)
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, brief TEXT NOT NULL,
                    brief_revision INTEGER NOT NULL DEFAULT 1,
                    source_harness TEXT NOT NULL, source_conversation TEXT NOT NULL,
                    source_message TEXT NOT NULL, repository TEXT, rank INTEGER NOT NULL,
                    disposition TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE task_notes (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    body TEXT NOT NULL, source_harness TEXT NOT NULL,
                    source_reference TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    repository TEXT NOT NULL, base_revision TEXT NOT NULL,
                    delivery TEXT NOT NULL, status TEXT NOT NULL, thread_uuid TEXT,
                    active_turn_id TEXT, last_turn_id TEXT, last_result_kind TEXT,
                    last_result_json TEXT, retained_resources_json TEXT NOT NULL DEFAULT '[]',
                    cancel_requested_at TEXT, cancelled_at TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE turns (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    idempotency_key TEXT NOT NULL UNIQUE, input_digest TEXT NOT NULL,
                    input_text TEXT NOT NULL, response_to TEXT, status TEXT NOT NULL,
                    turn_uuid TEXT, result_json TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE interactions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    turn_record_id TEXT NOT NULL REFERENCES turns(id), thread_uuid TEXT NOT NULL,
                    turn_uuid TEXT NOT NULL, item_id TEXT, method TEXT NOT NULL,
                    params_json TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                    response_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                PRAGMA user_version = 2;
                """
            )
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, 1, ?, '', '', NULL, 1, 'open', ?, ?, ?)",
                ("legacy-uuid", "Legacy", "Old task", "test", "legacy-key", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.commit()
        finally:
            connection.close()
        os.chmod(database, 0o600)
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        legacy = self.service.get_task("legacy-uuid")
        self.assertIsNone(legacy["short_id"])
        self.assertEqual(legacy["preparation_status"], "legacy")
        self.assertEqual(self.create_task()["short_id"], "A1")
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        finally:
            connection.close()

    def test_schema_v3_migration_preserves_prepared_cards(self) -> None:
        created = self.create_task()
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("DROP TABLE task_dependencies")
            connection.execute("DROP INDEX tasks_initiative")
            for column in (
                "disposition_before_trash",
                "trashed_at",
                "cancelled_at",
                "stop_requested_at",
                "previous_harness",
                "adopted_harness",
                "delivered_at",
                "delivery_kind",
                "delivery_revision",
                "delivered_task_revision",
                "completed_revision",
                "repository_common_dir",
                "decomposition_reason",
                "initiative_id",
            ):
                connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
            connection.execute("DROP TABLE task_initiatives")
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        finally:
            connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["id"], created["id"])
        self.assertIsNone(migrated["initiative_id"])
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
        finally:
            connection.close()

    def test_schema_v4_migration_namespaces_historical_owners_as_codex(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.service.reclaim_task(
            task_ref="A1",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
        )
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            for column in (
                "disposition_before_trash",
                "trashed_at",
                "cancelled_at",
                "stop_requested_at",
            ):
                connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
            connection.execute("ALTER TABLE tasks DROP COLUMN previous_harness")
            connection.execute("ALTER TABLE tasks DROP COLUMN adopted_harness")
            connection.execute("PRAGMA user_version = 4")
            connection.commit()
        finally:
            connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["adopted_thread_id"], THREAD_TWO)
        self.assertEqual(migrated["adopted_harness"], "codex")
        self.assertEqual(migrated["previous_thread_id"], THREAD_ONE)
        self.assertEqual(migrated["previous_harness"], "codex")

    def test_equal_textual_ids_from_different_hosts_are_distinct_owners(self) -> None:
        self.create_task()
        self.prepare("A1")
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="cursor",
        )
        self.assertEqual(adopted["adopted_harness"], "cursor")
        with self.assertRaises(ControlError) as busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness="codex",
            )
        self.assertEqual(busy.exception.status, "busy")
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="codex",
        )
        self.assertEqual(reclaimed["adopted_harness"], "codex")
        self.assertEqual(reclaimed["previous_harness"], "cursor")

    def test_grok_session_identity_is_a_distinct_owner_namespace(self) -> None:
        self.create_task()
        self.prepare("A1")
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="grok",
        )
        self.assertEqual(adopted["adopted_harness"], "grok")
        self.assertEqual(adopted["adopted_thread_id"], THREAD_ONE)
        with self.assertRaises(ControlError) as busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness="codex",
            )
        self.assertEqual(busy.exception.status, "busy")
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="cursor",
        )
        self.assertEqual(reclaimed["adopted_harness"], "cursor")
        self.assertEqual(reclaimed["previous_harness"], "grok")
        result, cli_adopted = self.cli(
            "task",
            "adopt",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            grok_thread=THREAD_TWO,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(cli_adopted["status"], "busy")

    def test_devin_session_identity_is_a_distinct_owner_namespace(self) -> None:
        self.create_task()
        self.prepare("A1")
        adopted = self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="devin",
        )
        self.assertEqual(adopted["adopted_harness"], "devin")
        self.assertEqual(adopted["adopted_thread_id"], THREAD_ONE)
        with self.assertRaises(ControlError) as busy:
            self.service.adopt_task(
                task_ref="A1",
                thread_id=THREAD_ONE,
                repository=str(self.repository),
                current_revision=self.revision,
                source_harness="codex",
            )
        self.assertEqual(busy.exception.status, "busy")
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="cursor",
        )
        self.assertEqual(reclaimed["adopted_harness"], "cursor")
        self.assertEqual(reclaimed["previous_harness"], "devin")
        result, cli_adopted = self.cli(
            "task",
            "adopt",
            "--task",
            "A1",
            "--repository",
            str(self.repository),
            devin_thread=THREAD_TWO,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(cli_adopted["status"], "busy")
        self.assertEqual(
            host_thread_from_env({"ORCHESTRA_DEVIN_THREAD_ID": "devin-thread-1"}),
            ("devin", "devin-thread-1"),
        )
        with self.assertRaises(ControlError) as devin_missing:
            validate_thread_id("not-a-valid-id?", "devin")
        self.assertEqual(devin_missing.exception.status, "blocked")
        self.assertIn("Devin host session identity", devin_missing.exception.reason)

    def test_schema_v5_migration_widens_owner_harness_to_grok(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="cursor",
        )
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE tasks RENAME TO tasks_old")
            connection.execute(
                """
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    short_id TEXT COLLATE NOCASE UNIQUE,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    brief_revision INTEGER NOT NULL DEFAULT 1,
                    source_harness TEXT NOT NULL,
                    source_conversation TEXT NOT NULL,
                    source_message TEXT NOT NULL,
                    repository TEXT,
                    rank INTEGER NOT NULL,
                    disposition TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    preparation_status TEXT NOT NULL DEFAULT 'draft',
                    prepared_revision TEXT,
                    repository_context_digest TEXT,
                    specification_digest TEXT,
                    specification_confirmed_at TEXT,
                    adopted_thread_id TEXT,
                    adopted_harness TEXT CHECK (
                        adopted_harness IS NULL OR adopted_harness IN ('codex', 'cursor')
                    ),
                    adopted_revision TEXT,
                    adopted_at TEXT,
                    previous_thread_id TEXT,
                    previous_harness TEXT CHECK (
                        previous_harness IS NULL OR previous_harness IN ('codex', 'cursor')
                    ),
                    transfer_generation INTEGER NOT NULL DEFAULT 0,
                    transfer_requested_at TEXT,
                    completed_at TEXT,
                    initiative_id TEXT,
                    decomposition_reason TEXT,
                    repository_common_dir TEXT,
                    completed_revision TEXT,
                    delivered_task_revision TEXT,
                    delivery_revision TEXT,
                    delivery_kind TEXT,
                    delivered_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            ]
            quoted = ", ".join(f'"{column}"' for column in columns)
            connection.execute(
                f"INSERT INTO tasks ({quoted}) SELECT {quoted} FROM tasks_old"
            )
            connection.execute("DROP TABLE tasks_old")
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
        finally:
            connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["adopted_harness"], "cursor")
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id=THREAD_TWO,
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="grok",
        )
        self.assertEqual(reclaimed["adopted_harness"], "grok")
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn("'grok'", sql)

    def test_schema_v5_o1_migration_preserves_lifecycle_and_backfills_owner(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
        )
        self.service.request_stop("A1")
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("ALTER TABLE tasks DROP COLUMN previous_harness")
            connection.execute("ALTER TABLE tasks DROP COLUMN adopted_harness")
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
        finally:
            connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["adopted_harness"], "codex")
        self.assertEqual(migrated["adopted_thread_id"], THREAD_ONE)
        self.assertTrue(migrated["stop_requested_at"])
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        finally:
            connection.close()

    def test_schema_v6_migration_preserves_grok_owner(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id=THREAD_ONE,
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="grok",
        )
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            for column in (
                "disposition_before_trash",
                "trashed_at",
                "cancelled_at",
                "stop_requested_at",
            ):
                connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
            connection.execute("PRAGMA user_version = 6")
            connection.commit()
        finally:
            connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["adopted_harness"], "grok")
        self.assertIsNone(migrated["stop_requested_at"])

    def test_schema_v7_migration_widens_owner_harness_to_devin(self) -> None:
        self.create_task()
        self.prepare("A1")
        self.service.adopt_task(
            task_ref="A1",
            thread_id="cursor-conversation-abc",
            repository=str(self.repository),
            current_revision=self.revision,
            source_harness="cursor",
        )
        self.create_task("capture-2")
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE tasks RENAME TO tasks_old")
            connection.execute(
                """
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    short_id TEXT COLLATE NOCASE UNIQUE,
                    title TEXT NOT NULL,
                    brief TEXT NOT NULL,
                    brief_revision INTEGER NOT NULL DEFAULT 1,
                    source_harness TEXT NOT NULL,
                    source_conversation TEXT NOT NULL,
                    source_message TEXT NOT NULL,
                    repository TEXT,
                    rank INTEGER NOT NULL,
                    disposition TEXT NOT NULL CHECK (disposition IN ('open', 'archived', 'trashed')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    preparation_status TEXT NOT NULL DEFAULT 'draft'
                        CHECK (preparation_status IN ('legacy', 'draft', 'ready', 'adopted', 'cancelled', 'completed')),
                    prepared_revision TEXT,
                    repository_context_digest TEXT,
                    specification_digest TEXT,
                    specification_confirmed_at TEXT,
                    adopted_thread_id TEXT,
                    adopted_harness TEXT CHECK (
                        adopted_harness IS NULL OR adopted_harness IN ('codex', 'cursor', 'grok')
                    ),
                    adopted_revision TEXT,
                    adopted_at TEXT,
                    previous_thread_id TEXT,
                    previous_harness TEXT CHECK (
                        previous_harness IS NULL OR previous_harness IN ('codex', 'cursor', 'grok')
                    ),
                    transfer_generation INTEGER NOT NULL DEFAULT 0,
                    transfer_requested_at TEXT,
                    completed_at TEXT,
                    initiative_id TEXT REFERENCES task_initiatives(id),
                    decomposition_reason TEXT,
                    repository_common_dir TEXT,
                    completed_revision TEXT,
                    delivered_task_revision TEXT,
                    delivery_revision TEXT,
                    delivery_kind TEXT CHECK (
                        delivery_kind IS NULL OR delivery_kind IN ('local-integration', 'pr-merge')
                    ),
                    delivered_at TEXT,
                    stop_requested_at TEXT,
                    cancelled_at TEXT,
                    trashed_at TEXT,
                    disposition_before_trash TEXT CHECK (
                        disposition_before_trash IS NULL OR disposition_before_trash IN ('open', 'archived')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            ]
            quoted = ", ".join(f'"{column}"' for column in columns)
            connection.execute(
                f"INSERT INTO tasks ({quoted}) SELECT {quoted} FROM tasks_old"
            )
            connection.execute("DROP TABLE tasks_old")
            connection.execute("PRAGMA user_version = 7")
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(database)
        before = tuple(connection.iterdump())
        connection.close()
        result, payload = self.cli("task", "list")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("storage migrate", payload["reason"])
        connection = sqlite3.connect(database)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
        self.assertEqual(tuple(connection.iterdump()), before)
        connection.close()
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        result, payload = self.cli("storage", "migrate")
        self.assertEqual(result.returncode, 0, payload)
        migrated = self.service.get_task("A1")
        self.assertEqual(migrated["adopted_harness"], "cursor")
        self.assertEqual(migrated["adopted_thread_id"], "cursor-conversation-abc")
        self.assertIsNone(self.service.get_task("A2")["adopted_harness"])
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertIn("'devin'", sql)
        reclaimed = self.service.reclaim_task(
            task_ref="A1",
            thread_id="devin-session-abc",
            repository=str(self.repository),
            current_revision=self.revision,
            authorized=True,
            source_harness="devin",
        )
        self.assertEqual(reclaimed["adopted_harness"], "devin")
        self.assertEqual(reclaimed["previous_harness"], "cursor")
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "INSERT INTO tasks (id, short_id, title, brief, brief_revision, "
                "source_harness, source_conversation, source_message, repository, "
                "rank, disposition, idempotency_key, preparation_status, "
                "transfer_generation, created_at, updated_at, adopted_thread_id, "
                "adopted_harness) VALUES (?, ?, ?, ?, 1, ?, '', '', NULL, 1, 'open', "
                "?, 'draft', 0, ?, ?, ?, 'devin')",
                (
                    "devin-uuid",
                    "A3",
                    "Devin task",
                    "brief",
                    "test",
                    "devin-key",
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    "devin-thread",
                ),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO tasks (id, short_id, title, brief, brief_revision, "
                    "source_harness, source_conversation, source_message, repository, "
                    "rank, disposition, idempotency_key, preparation_status, "
                    "transfer_generation, created_at, updated_at, adopted_thread_id, "
                    "adopted_harness) VALUES (?, ?, ?, ?, 1, ?, '', '', NULL, 1, 'open', "
                    "?, 'draft', 0, ?, ?, ?, 'bogus')",
                    (
                        "bogus-uuid",
                        "A4",
                        "Bogus task",
                        "brief",
                        "test",
                        "bogus-key",
                        "2026-01-01T00:00:00Z",
                        "2026-01-01T00:00:00Z",
                        "bogus-thread",
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    def test_unknown_v5_shape_rolls_back_without_changes(self) -> None:
        self.create_task()
        database = self.state_root / "control.sqlite3"
        connection = sqlite3.connect(database)
        try:
            for column in (
                "disposition_before_trash",
                "trashed_at",
                "cancelled_at",
                "stop_requested_at",
            ):
                connection.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
            connection.execute("ALTER TABLE tasks ADD COLUMN unknown_future_field TEXT")
            connection.execute("PRAGMA user_version = 5")
            connection.commit()
            before_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()[0]
            before_row = connection.execute(
                "SELECT id, short_id, title FROM tasks"
            ).fetchall()
        finally:
            connection.close()
        with self.assertRaises(ControlError) as error:
            self.service.get_task("A1")
        self.assertIn("unsupported control schema v5 shape", error.exception.reason)
        connection = sqlite3.connect(database)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
                ).fetchone()[0],
                before_sql,
            )
            self.assertEqual(
                connection.execute("SELECT id, short_id, title FROM tasks").fetchall(),
                before_row,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
