"""Pure presentation helpers for the TUI (no textual import)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import posixpath

TASK_ROW_FIELDS = (
    "label", "tier", "stage", "status", "branch", "worktree",
    "summary", "blocker", "next_action", "initiative", "blocked_by", "parallel_with",
    "created_at", "updated_at",
    "stale",
)


@dataclass(frozen=True)
class RepoNode:
    name: str
    path: str
    active: int
    completed: int
    tasks: tuple[dict, ...]


def compact_middle(value: str, limit: int = 16) -> str:
    if len(value) <= limit:
        return value
    left = (limit - 1) // 2
    right = limit - left - 1
    return value[:left] + "…" + value[-right:]


def worktree_name(task: dict) -> str:
    repository = str(task.get("repository") or "")
    worktree = str(task.get("worktree") or "")
    if not worktree or (
        repository and posixpath.normpath(worktree) == posixpath.normpath(repository)
    ):
        return ""
    return compact_middle(posixpath.basename(posixpath.normpath(worktree)))


def status_tone(task: dict) -> str:
    if str(task.get("blocker") or ""):
        return "red"
    if task.get("stop_requested_at"):
        return "orange"
    if task.get("stale") is True:
        return "yellow"
    status = str(task.get("status") or task.get("preparation_status") or "")
    if status in {"active", "adopted"}:
        return "blue"
    if status == "ready":
        return "green"
    if status in {"draft"}:
        return "yellow"
    if status in {"cancelled", "completed", "archived", "trashed"}:
        return "muted"
    return "yellow"


def build_tree(summary: dict) -> list[RepoNode]:
    tasks_by_repo: dict[str, list[dict]] = {}
    for task in summary.get("tasks", ()):
        tasks_by_repo.setdefault(str(task.get("repository", "")), []).append(task)
    nodes: list[RepoNode] = []
    seen: set[str] = set()
    for repo in summary.get("repositories", ()):
        path = str(repo.get("path", ""))
        seen.add(path)
        nodes.append(_node(
            name=str(repo.get("name", "")) or path,
            path=path,
            tasks=tasks_by_repo.get(path, []),
        ))
    for path, tasks in tasks_by_repo.items():
        if path not in seen:
            nodes.append(_node(name=path, path=path, tasks=tasks))
    return nodes


def _node(*, name: str, path: str, tasks: list[dict]) -> RepoNode:
    active = [task for task in tasks if task.get("status") not in {"completed", "archived"}]
    completed = [task for task in tasks if task.get("status") in {"completed", "archived"}]
    return RepoNode(
        name=name,
        path=path,
        active=len(active),
        completed=len(completed),
        tasks=tuple(
            sorted(
                active + completed,
                key=lambda task: (
                    str((task.get("initiative") or {}).get("title") or "~ Independent tasks"),
                    str(task.get("short_id") or ""),
                ),
            )
        ),
    )


def task_rows(task: dict) -> list[tuple[str, str]]:
    rows = []
    for field in TASK_ROW_FIELDS:
        value = task.get(field, "")
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif field == "initiative" and isinstance(value, dict):
            value = value.get("title", "")
        elif field in {"blocked_by", "parallel_with"} and isinstance(value, list):
            value = ", ".join(
                str(item.get("short_id", ""))
                for item in value
                if field == "parallel_with" or not item.get("satisfied")
            )
        rows.append((field, str(value)))
    return rows


def phase_progress(artifacts: list[dict] | tuple) -> tuple[int, int] | None:
    """Best-effort (current, total) phase derived from published documents.

    Total = number of plan-phase documents. Current = highest phase with a
    published non-plan document, else the first planned phase. Returns None
    when the plan has no per-phase documents (nothing to derive).
    """
    planned = {
        int(artifact.get("phase", 0))
        for artifact in artifacts
        if artifact.get("kind") == "plan-phase"
    }
    if not planned:
        return None
    worked = {
        int(artifact.get("phase", 0))
        for artifact in artifacts
        if artifact.get("kind") not in ("plan-phase", "plan-overview")
    }
    current = max(worked & planned) if worked & planned else min(planned)
    return current, len(planned)


def snapshot_age(updated_at: str, now: datetime) -> str:
    try:
        moment = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return updated_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"
