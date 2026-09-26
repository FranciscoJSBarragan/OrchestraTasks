"""Server-rendered panel escaping and section tests (SPEC §9, §11 / PLAN Task 7)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.api import summary_payload  # noqa: E402
from orchestra_hub.config import HubConfig, PinnedRepository  # noqa: E402
from orchestra_hub.panel import render_degraded, render_panel  # noqa: E402


NOW = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
REPO_PATH = "/obs/alpha"


class PanelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_root = Path(tempfile.mkdtemp())
        self.database = support.create_state_db(self.state_root)
        self.config = HubConfig(
            state_root=self.state_root,
            port=7343,
            stale_after_minutes=60,
            pinned_repositories=(
                PinnedRepository(path="/pinned/repo", name="Pinned"),
            ),
        )

    def _summary(self) -> dict:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            return summary_payload(connection, self.config, NOW)
        finally:
            connection.close()

    def test_hostile_payloads_are_escaped(self) -> None:
        support.insert_task(
            self.database,
            label="<script>alert(1)</script>",
            summary="<img src=x onerror=alert(2)>",
            blocker='"><svg onload=alert(3)>',
            next_action='<b onmouseover=alert(4)>go</b>',
            repository=REPO_PATH,
            updated_at="2026-08-02T18:48:00Z",
        )
        html = render_panel(self._summary(), NOW)

        for raw in (
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(2)>",
            '"><svg onload=alert(3)>',
            "<b onmouseover=alert(4)>go</b>",
        ):
            self.assertNotIn(raw, html)

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_required_sections_age_and_refresh(self) -> None:
        support.insert_task(
            self.database,
            label="Blocked task",
            repository=REPO_PATH,
            blocker="waiting on review",
            updated_at="2026-08-02T18:48:00Z",
        )
        html = render_panel(self._summary(), NOW)

        self.assertIn("Possible attention", html)
        self.assertIn("Repositories", html)
        self.assertIn("12 min ago", html)
        self.assertIn('http-equiv="refresh"', html)

    def test_compact_rows_group_by_repository_and_keep_operational_details(self) -> None:
        task = {
            "id": "task-1",
            "short_id": "A1",
            "label": "Fallback de variante",
            "repository": "/repos/NeniTPV",
            "worktree": "/worktrees/a-very-long-worktree-name",
            "branch": "orchestra/fallback",
            "stage": "implementation",
            "status": "active",
            "summary": "Working",
            "blocker": "Waiting on review",
            "next_action": "Review",
            "initiative": {"title": "Checkout"},
            "blocked_by": [],
            "parallel_with": [],
            "current_activity": [],
            "updated_at": "2026-08-02T18:48:00Z",
            "stale": False,
            "stop_requested_at": None,
        }
        direct = dict(task)
        direct.update({
            "id": "direct-1",
            "short_id": None,
            "label": "Direct task",
            "worktree": "",
            "branch": "",
            "blocker": "",
            "initiative": None,
        })
        summary = {
            "repositories": [{
                "path": "/repos/NeniTPV", "name": "NeniTPV", "pinned": True,
                "observed": True, "active_tasks": 2, "completed_tasks": 0,
            }],
            "tasks": [task, direct],
            "attention": [],
        }

        markup = render_panel(summary, NOW)

        self.assertIn("<h3>NeniTPV</h3>", markup)
        self.assertIn("[a-very-…ree-name]", markup)
        self.assertIn("A1", markup)
        self.assertIn("Fallback de variante", markup)
        self.assertIn("[Checkout]", markup)
        self.assertIn("Direct task", markup)
        self.assertNotIn("—", markup)
        self.assertIn('<details class="task">', markup)
        self.assertIn("orchestra/fallback", markup)
        self.assertIn("/worktrees/a-very-long-worktree-name", markup)
        self.assertIn("status-red", markup)
        self.assertIn('aria-label="Blocked"', markup)

    def test_task_without_repository_is_rendered_in_synthetic_group(self) -> None:
        support.insert_task(
            self.database,
            id="direct-task",
            label="Direct task without repository",
            repository="",
            updated_at="2026-08-02T18:48:00Z",
        )

        markup = render_panel(self._summary(), NOW)

        self.assertIn("<h2>Repositories</h2>", markup)
        self.assertIn("<h3>No repository</h3>", markup)
        self.assertIn("Direct task without repository", markup)
        self.assertNotIn('<p class="repository-path"></p>', markup)

    def test_render_degraded_standalone(self) -> None:
        html = render_degraded("missing", "database not found")
        self.assertIn("degraded", html.lower())
        self.assertIn("missing", html)
