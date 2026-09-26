"""Tests for the TUI view-model (pure functions, no textual import)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

import support  # noqa: F401  (sys.path setup)
from orchestra_hub_tui.viewmodel import (
    build_tree,
    compact_middle,
    phase_progress,
    snapshot_age,
    status_tone,
    task_rows,
    worktree_name,
)


def _artifact(kind: str, phase: int) -> dict:
    suffix = f"-p{phase}" if phase else ""
    return {"id": f"01-{kind}{suffix}.md", "kind": kind, "phase": phase,
            "created_at": "2026-08-03T17:00:00Z"}


class PhaseProgressTest(unittest.TestCase):
    def test_no_plan_phases_returns_none(self) -> None:
        self.assertIsNone(phase_progress([_artifact("plan-overview", 0)]))
        self.assertIsNone(phase_progress([]))

    def test_planned_without_work_is_phase_one(self) -> None:
        artifacts = [_artifact("plan-phase", 1), _artifact("plan-phase", 2)]
        self.assertEqual(phase_progress(artifacts), (1, 2))

    def test_work_artifacts_advance_current_phase(self) -> None:
        artifacts = [
            _artifact("plan-overview", 0),
            _artifact("plan-phase", 1),
            _artifact("plan-phase", 2),
            _artifact("plan-phase", 3),
            _artifact("implementation-report", 1),
            _artifact("review-report", 1),
            _artifact("implementation-report", 2),
        ]
        self.assertEqual(phase_progress(artifacts), (2, 3))

    def test_work_outside_the_plan_does_not_advance_progress(self) -> None:
        artifacts = [
            _artifact("plan-phase", 1),
            _artifact("plan-phase", 2),
            _artifact("implementation-report", 5),
        ]
        self.assertEqual(phase_progress(artifacts), (1, 2))


def _task(**overrides: object) -> dict:
    task = {
        "id": "task-1",
        "label": "Task One",
        "repository": "/repos/alpha",
        "worktree": "/wt/alpha-task",
        "branch": "orchestra/task-one",
        "base_revision": "a" * 40,
        "head_revision": "b" * 40,
        "tier": "standard",
        "stage": "implementation",
        "status": "active",
        "summary": "working",
        "blocker": "",
        "next_action": "continue",
        "created_at": "2026-08-03T10:00:00Z",
        "updated_at": "2026-08-03T11:00:00Z",
        "material_fingerprint": "sha256:" + "0" * 64,
        "stale": False,
    }
    task.update(overrides)
    return task


def _summary() -> dict:
    return {
        "status": "ok",
        "material_fingerprint_version": 1,
        "repositories": [
            {"path": "/repos/alpha", "name": "Alpha", "pinned": False,
             "observed": True, "active_tasks": 1, "completed_tasks": 1},
            {"path": "/repos/pinned", "name": "Pinned", "pinned": True,
             "observed": False, "active_tasks": 0, "completed_tasks": 0},
        ],
        "tasks": [
            _task(id="done-1", label="Done", status="completed",
                  repository="/repos/alpha"),
            _task(id="task-1", repository="/repos/alpha"),
        ],
        "attention": [],
    }


class BuildTreeTest(unittest.TestCase):
    def test_repositories_preserved_including_pinned_empty(self) -> None:
        tree = build_tree(_summary())
        self.assertEqual([node.name for node in tree], ["Alpha", "Pinned"])
        self.assertEqual(tree[1].tasks, ())
        self.assertEqual(tree[1].active, 0)
        self.assertEqual(tree[1].completed, 0)

    def test_tasks_grouped_and_active_first(self) -> None:
        tree = build_tree(_summary())
        alpha = tree[0]
        self.assertEqual(alpha.active, 1)
        self.assertEqual(alpha.completed, 1)
        self.assertEqual([task["id"] for task in alpha.tasks],
                         ["task-1", "done-1"])

    def test_task_with_unlisted_repository_still_appears(self) -> None:
        summary = _summary()
        summary["tasks"].append(_task(id="task-2", repository="/repos/other"))
        tree = build_tree(summary)
        names = [node.path for node in tree]
        self.assertIn("/repos/other", names)

    def test_task_without_repository_uses_catalog_group_name(self) -> None:
        summary = _summary()
        summary["repositories"].append({
            "path": "", "name": "No repository", "pinned": False,
            "observed": True, "active_tasks": 1, "completed_tasks": 0,
        })
        summary["tasks"].append(_task(id="direct", repository="", worktree=""))

        tree = build_tree(summary)

        node = next(item for item in tree if item.path == "")
        self.assertEqual(node.name, "No repository")
        self.assertEqual([task["id"] for task in node.tasks], ["direct"])


class TaskRowsTest(unittest.TestCase):
    def test_rows_are_ordered_and_stringified(self) -> None:
        rows = task_rows(_task(stale=True))
        self.assertEqual(rows[0], ("label", "Task One"))
        keys = [key for key, _ in rows]
        self.assertEqual(keys, [
            "label", "tier", "stage", "status", "branch", "worktree",
            "summary", "blocker", "next_action", "initiative", "blocked_by",
            "parallel_with", "created_at",
            "updated_at", "stale",
        ])
        self.assertEqual(dict(rows)["stale"], "true")

    def test_missing_field_renders_empty(self) -> None:
        task = _task()
        del task["summary"]
        self.assertEqual(dict(task_rows(task))["summary"], "")


class CompactTaskPresentationTest(unittest.TestCase):
    def test_worktree_is_hidden_for_primary_checkout(self) -> None:
        self.assertEqual(
            worktree_name(_task(repository="/repos/NeniTPV", worktree="/repos/NeniTPV")),
            "",
        )

    def test_worktree_name_is_middle_truncated_to_sixteen_characters(self) -> None:
        name = worktree_name(_task(worktree="/worktrees/a-very-long-worktree-name"))
        self.assertEqual(name, "a-very-…ree-name")
        self.assertEqual(len(name), 16)
        self.assertEqual(compact_middle("N1"), "N1")

    def test_status_priority_matches_shared_presentation(self) -> None:
        self.assertEqual(status_tone(_task(blocker="blocked", stop_requested_at="now")), "red")
        self.assertEqual(status_tone(_task(stop_requested_at="now", stale=True)), "orange")
        self.assertEqual(status_tone(_task(stale=True)), "yellow")
        self.assertEqual(status_tone(_task(status="active")), "blue")
        self.assertEqual(status_tone(_task(status="ready")), "green")
        self.assertEqual(status_tone(_task(status="completed")), "muted")


class SnapshotAgeTest(unittest.TestCase):
    NOW = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)

    def test_minutes(self) -> None:
        self.assertEqual(
            snapshot_age("2026-08-03T11:57:00Z", self.NOW), "3 min ago"
        )

    def test_hours(self) -> None:
        self.assertEqual(
            snapshot_age("2026-08-03T10:00:00Z", self.NOW), "2 h ago"
        )

    def test_days(self) -> None:
        self.assertEqual(
            snapshot_age("2026-08-01T12:00:00Z", self.NOW), "2 d ago"
        )

    def test_just_now(self) -> None:
        self.assertEqual(
            snapshot_age("2026-08-03T11:59:40Z", self.NOW), "just now"
        )

    def test_unparsable_returns_raw(self) -> None:
        self.assertEqual(snapshot_age("not-a-time", self.NOW), "not-a-time")


if __name__ == "__main__":
    unittest.main()
