"""Scenario preview harness: renders TUI screenshots against a fake Hub.

Usage (from hub/tui, venv python):  .venv/bin/python preview.py
Writes one SVG per scenario into hub/tui/screenshots/ (git-ignored).
The fake server is stdlib-only and mimics the real API shape (SPEC.md §7).
"""
from __future__ import annotations

import asyncio
import http.server
import json
import threading
from pathlib import Path

from orchestra_hub_tui.app import HubTuiApp
from orchestra_hub_tui.client import HubClient

SCREENSHOTS = Path(__file__).resolve().parent / "screenshots"
SIZE = (120, 40)


def _task(**overrides: object) -> dict:
    task = {
        "id": "task-default",
        "label": "Task",
        "repository": "/Users/me/Code/Orchestra",
        "worktree": "/Users/me/.orchestra/worktrees/Orchestra/task",
        "branch": "orchestra/task",
        "base_revision": "a" * 40,
        "head_revision": "b" * 40,
        "tier": "standard",
        "stage": "implementation",
        "status": "active",
        "summary": "Working on the approved plan.",
        "blocker": "",
        "next_action": "Continue implementation",
        "created_at": "2026-08-03T15:00:00Z",
        "updated_at": "2026-08-03T18:55:00Z",
        "material_fingerprint": "sha256:" + "0" * 64,
        "stale": False,
    }
    task.update(overrides)
    return task


def _repo(path: str, name: str, active: int, completed: int,
          pinned: bool = False) -> dict:
    return {"path": path, "name": name, "pinned": pinned,
            "observed": not pinned or active + completed > 0,
            "active_tasks": active, "completed_tasks": completed}


def _summary(repositories: list[dict], tasks: list[dict],
             attention: list[dict] | None = None) -> dict:
    return {"status": "ok", "material_fingerprint_version": 1,
            "repositories": repositories, "tasks": tasks,
            "attention": attention or []}


def _detail(task: dict, activities: list[dict],
            artifacts: list[dict]) -> dict:
    return {"status": "ok", "material_fingerprint_version": 1,
            "task": task, "activities": activities, "artifacts": artifacts}


def _activity(agent: str, capability: str, state: str, summary: str,
              updated: str = "2026-08-03T18:50:00Z") -> dict:
    return {"agent_id": agent, "capability": capability, "state": state,
            "summary": summary, "updated_at": updated}


def _artifact(ordinal: int, kind: str, phase: int) -> dict:
    suffix = f"-p{phase}" if phase else ""
    return {"id": f"{ordinal:02d}-{kind}{suffix}.md", "kind": kind,
            "phase": phase, "created_at": "2026-08-03T17:00:00Z"}


ORCHESTRA = "/Users/me/Code/Orchestra"
NENITPV = "/Users/me/Code/NeniTPV"

_IMPL_TASK = _task(
    id="hub-clients-v2", label="Hub clients v2",
    stage="implementation", summary="Phase 2: menu bar app in progress.",
    next_action="Wait for implementation owner",
)
_IMPL_DETAIL = _detail(
    _IMPL_TASK,
    [
        _activity("worker-1", "implementation", "working",
                  "Building the status item controller"),
        _activity("reviewer-1", "review", "idle",
                  "Waiting for phase handoff", "2026-08-03T17:20:00Z"),
        _activity("verifier-1", "verification", "idle",
                  "Waiting for a stable revision", "2026-08-03T17:20:00Z"),
    ],
    [
        _artifact(1, "plan-overview", 0),
        _artifact(2, "plan-phase", 1),
        _artifact(3, "plan-phase", 2),
        _artifact(4, "implementation-report", 1),
        _artifact(5, "implementation-review", 1),
    ],
)

_BLOCKED_TASK = _task(
    id="ticket-drawer", label="Configurable drawer ticket",
    repository=NENITPV, stage="planning", tier="critical",
    blocker="Waiting for user: approve the plan for phase 2",
    next_action="User decision required",
    summary="Plan drafted; needs approval before implementation.",
)
_BLOCKED_DETAIL = _detail(
    _BLOCKED_TASK,
    [_activity("planner-1", "planning", "blocked",
               "Plan published; awaiting approval")],
    [_artifact(1, "plan-overview", 0),
     _artifact(2, "plan-phase", 1)],
)

_STALE_TASK = _task(
    id="cash-method", label="Cash payment method",
    repository=NENITPV, stage="verification", stale=True,
    updated_at="2026-08-03T12:00:00Z",
    summary="Verification running on phase 3.",
    next_action="Check verifier output",
)
_STALE_DETAIL = _detail(
    _STALE_TASK,
    [_activity("verifier-1", "verification", "working",
               "Running the full suite", "2026-08-03T12:00:00Z")],
    [_artifact(1, "plan-overview", 0),
     _artifact(2, "implementation-report", 3)],
)

_DONE_TASK = _task(
    id="hub-mvp", label="Orchestra Hub MVP", status="completed",
    stage="acceptance", summary="Integrated and operational.",
    next_action="", updated_at="2026-08-03T14:00:00Z",
)


def _attention(task: dict, reasons: list[str]) -> dict:
    return {"task_id": task["id"], "label": task["label"],
            "repository": task["repository"], "reasons": reasons,
            "blocker": task["blocker"], "next_action": task["next_action"],
            "updated_at": task["updated_at"]}


SCENARIOS: dict[str, dict] = {
    "idle": {
        "summary": _summary(
            [_repo(ORCHESTRA, "Orchestra", 0, 1),
             _repo(NENITPV, "NeniTPV", 0, 0, pinned=True)],
            [_DONE_TASK],
        ),
        "details": {"hub-mvp": _detail(_DONE_TASK, [], [])},
    },
    "active": {
        "summary": _summary(
            [_repo(NENITPV, "NeniTPV", 1, 0),
             _repo(ORCHESTRA, "Orchestra", 2, 1)],
            [_IMPL_TASK,
             _task(id="ctx-refactor", label="Context skill refactor",
                   stage="repository_context",
                   summary="Focused context pass running.",
                   next_action="Wait for analyst"),
             _task(id="pos-receipts", label="POS receipt export",
                   repository=NENITPV, stage="review",
                   summary="Independent review in progress.",
                   next_action="Wait for reviewer"),
             _DONE_TASK],
        ),
        "details": {"hub-clients-v2": _IMPL_DETAIL},
    },
    "blocker": {
        "summary": _summary(
            [_repo(NENITPV, "NeniTPV", 1, 0)],
            [_BLOCKED_TASK],
            [_attention(_BLOCKED_TASK, ["blocker"])],
        ),
        "details": {"ticket-drawer": _BLOCKED_DETAIL},
    },
    "stale": {
        "summary": _summary(
            [_repo(NENITPV, "NeniTPV", 1, 0)],
            [_STALE_TASK],
            [_attention(_STALE_TASK, ["stale"])],
        ),
        "details": {"cash-method": _STALE_DETAIL},
    },
    "mixed": {
        "summary": _summary(
            [_repo(NENITPV, "NeniTPV", 2, 1),
             _repo(ORCHESTRA, "Orchestra", 1, 1)],
            [_IMPL_TASK, _BLOCKED_TASK, _STALE_TASK, _DONE_TASK],
            [_attention(_BLOCKED_TASK, ["blocker"]),
             _attention(_STALE_TASK, ["stale"])],
        ),
        "details": {"hub-clients-v2": _IMPL_DETAIL,
                    "ticket-drawer": _BLOCKED_DETAIL,
                    "cash-method": _STALE_DETAIL},
    },
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        scenario = self.server.scenario
        if self.path == "/v1/summary":
            payload = scenario["summary"]
        elif self.path.startswith("/v1/tasks/"):
            payload = scenario["details"].get(self.path.rsplit("/", 1)[1])
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


async def _snap(name: str, base_url: str) -> None:
    app = HubTuiApp(client=HubClient(base_url))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(1.5)
        tree = app.query_one("#repos")
        for branch in tree.root.children:
            if branch.children:
                app.on_tree_node_selected(
                    type("E", (), {"node": branch.children[0]})()
                )
                break
        await pilot.pause(1.5)
        app.save_screenshot(str(SCREENSHOTS / f"{name}.svg"))
        await pilot.press("q")


def main() -> None:
    SCREENSHOTS.mkdir(exist_ok=True)
    for name, scenario in SCENARIOS.items():
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.scenario = scenario
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            asyncio.run(_snap(name, f"http://127.0.0.1:{port}"))
        finally:
            server.shutdown()
            server.server_close()
        print(f"screenshots/{name}.svg")
    # Unreachable: point at a port that is closed.
    probe = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    closed = probe.server_address[1]
    probe.server_close()
    asyncio.run(_snap("unreachable", f"http://127.0.0.1:{closed}"))
    print("screenshots/unreachable.svg")


if __name__ == "__main__":
    main()
