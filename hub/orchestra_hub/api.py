"""Allowlisted Hub API payloads for prepared and adopted Orchestra tasks."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping

from orchestra_hub.artifacts import ARTIFACT_FIELDS, artifact_entries
from orchestra_hub.config import HubConfig, PinnedRepository
from orchestra_hub.fingerprint import (
    MATERIAL_FINGERPRINT_VERSION,
    material_fingerprint,
)

TASK_FIELDS = (
    "id", "short_id", "label", "repository", "worktree", "branch",
    "base_revision", "head_revision", "tier", "stage", "status", "summary",
    "blocker", "next_action", "initiative", "blocked_by", "parallel_with",
    "preparation_status", "disposition", "stop_requested_at",
    "created_at", "updated_at",
)
ACTIVITY_FIELDS = ("agent_id", "capability", "state", "summary", "updated_at")
INACTIVE_STATUSES = frozenset({"archived", "cancelled", "completed"})

__all__ = [
    "ACTIVITY_FIELDS",
    "ARTIFACT_FIELDS",
    "TASK_FIELDS",
    "attention_entries",
    "repository_entries",
    "summary_payload",
    "task_detail_payload",
    "task_summary",
    "tasks_payload",
]


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def task_summary(
    row: Mapping[str, object],
    *,
    now: datetime,
    stale_after: timedelta,
) -> dict:
    task = {
        field: row[field] if field in row.keys() else None
        for field in TASK_FIELDS
    }
    task["material_fingerprint"] = material_fingerprint(task)
    task["current_activity"] = list(
        row["current_activity"] if "current_activity" in row.keys() else []
    )
    updated_at = parse_timestamp(str(task["updated_at"]))
    task["stale"] = task["status"] not in INACTIVE_STATUSES and now - updated_at > stale_after
    return task


def attention_entries(tasks: list[dict]) -> list[dict]:
    entries: list[dict] = []
    for task in tasks:
        if task["status"] in INACTIVE_STATUSES:
            continue
        reasons: list[str] = []
        if task["blocker"]:
            reasons.append("blocker")
        if task["stop_requested_at"]:
            reasons.append("stop-requested")
        if task["stale"]:
            reasons.append("stale")
        if not reasons:
            continue
        entries.append(
            {
                "task_id": task["id"],
                "short_id": task["short_id"],
                "label": task["label"],
                "repository": task["repository"],
                "reasons": reasons,
                "blocker": task["blocker"],
                "next_action": task["next_action"],
                "updated_at": task["updated_at"],
            }
        )
    return entries


def repository_entries(
    tasks: list[dict],
    pinned: tuple[PinnedRepository, ...],
) -> list[dict]:
    repos: dict[str, dict] = {}
    for entry in pinned:
        repos[entry.path] = {
            "path": entry.path,
            "name": entry.name,
            "pinned": True,
            "observed": False,
            "active_tasks": 0,
            "completed_tasks": 0,
        }
    for task in tasks:
        path = str(task["repository"] or "")
        current = repos.get(path)
        if current is None:
            current = {
                "path": path,
                "name": Path(path).name if path else "No repository",
                "pinned": False,
                "observed": False,
                "active_tasks": 0,
                "completed_tasks": 0,
            }
            repos[path] = current
        current["observed"] = True
        if task["status"] in INACTIVE_STATUSES:
            current["completed_tasks"] += 1
        else:
            current["active_tasks"] += 1
    return sorted(repos.values(), key=lambda item: (item["name"], item["path"]))


def _stale_after(config: HubConfig) -> timedelta:
    return timedelta(minutes=config.stale_after_minutes)


def _control_rows(connection: sqlite3.Connection | None) -> list[dict]:
    if connection is None:
        return []
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    task_columns = {
        str(item[1]) for item in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    stop_projection = (
        "t.stop_requested_at" if "stop_requested_at" in task_columns
        else "NULL AS stop_requested_at"
    )
    if version >= 4:
        rows = connection.execute(
            f"""
            SELECT t.id, t.short_id, t.title, t.repository, t.preparation_status,
                   t.disposition, t.prepared_revision, t.adopted_revision,
                   t.initiative_id, i.title AS initiative_title, i.brief AS initiative_brief,
                   {stop_projection},
                   t.created_at, t.updated_at
            FROM tasks AS t
            LEFT JOIN task_initiatives AS i ON i.id = t.initiative_id
            WHERE t.short_id IS NOT NULL AND t.disposition != 'trashed'
            ORDER BY t.updated_at DESC, t.id
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id, short_id, title, repository, preparation_status, disposition,
                   prepared_revision, adopted_revision, NULL AS stop_requested_at,
                   created_at, updated_at
            FROM tasks
            WHERE short_id IS NOT NULL AND disposition != 'trashed'
            ORDER BY updated_at DESC, id
            """
        ).fetchall()
    dependency_views: dict[str, list[dict]] = {}
    parallel_views: dict[str, list[dict]] = {}
    if version >= 4:
        task_rows = {
            str(item["id"]): dict(item)
            for item in connection.execute(
                """
                SELECT id, short_id, title, initiative_id, preparation_status,
                       delivered_at, delivery_revision, rank, created_at
                FROM tasks WHERE short_id IS NOT NULL
                """
            ).fetchall()
        }
        edges: dict[str, set[str]] = {task_id: set() for task_id in task_rows}
        for dependency in connection.execute(
            """
            SELECT task_id, blocked_by_task_id, condition, reason
            FROM task_dependencies
            ORDER BY created_at, task_id, blocked_by_task_id
            """
        ).fetchall():
            dependent_id = str(dependency["task_id"])
            predecessor_id = str(dependency["blocked_by_task_id"])
            predecessor = task_rows[predecessor_id]
            condition = str(dependency["condition"])
            satisfied = predecessor["preparation_status"] == "completed"
            if condition == "delivered":
                satisfied = bool(
                    satisfied and predecessor["delivered_at"] and predecessor["delivery_revision"]
                )
            dependency_views.setdefault(dependent_id, []).append(
                {
                    "task_id": predecessor_id,
                    "short_id": predecessor["short_id"],
                    "title": predecessor["title"],
                    "condition": condition,
                    "reason": dependency["reason"],
                    "satisfied": satisfied,
                    "state": "satisfied" if satisfied else f"waiting-for-{condition}",
                }
            )
            edges.setdefault(predecessor_id, set()).add(dependent_id)

        def reaches(start: str, target: str) -> bool:
            pending = list(edges.get(start, ()))
            seen: set[str] = set()
            while pending:
                candidate = pending.pop()
                if candidate == target:
                    return True
                if candidate not in seen:
                    seen.add(candidate)
                    pending.extend(edges.get(candidate, ()))
            return False

        by_initiative: dict[str, list[dict]] = {}
        for item in task_rows.values():
            if item["initiative_id"]:
                by_initiative.setdefault(str(item["initiative_id"]), []).append(item)
        for initiative_tasks in by_initiative.values():
            initiative_tasks.sort(key=lambda item: (item["rank"], item["created_at"], item["id"]))
            for item in initiative_tasks:
                task_id = str(item["id"])
                parallel_views[task_id] = [
                    {
                        "task_id": other["id"],
                        "short_id": other["short_id"],
                        "title": other["title"],
                        "preparation_status": other["preparation_status"],
                    }
                    for other in initiative_tasks
                    if other["id"] != task_id
                    and not reaches(task_id, str(other["id"]))
                    and not reaches(str(other["id"]), task_id)
                ]
    result = []
    for row in rows:
        status = str(row["preparation_status"])
        disposition = str(row["disposition"])
        visible_status = "archived" if disposition == "archived" else status
        next_action = {
            "draft": "Complete repository context and confirm the specification",
            "ready": f"Open a native host chat and ask it to start {row['short_id']} with Orchestra",
            "adopted": "Continue in the adopting native host chat",
            "cancelled": "Reopen the task when you are ready to continue",
            "completed": "",
        }.get(status, "")
        blocked_by = dependency_views.get(str(row["id"]), [])
        unsatisfied = [item for item in blocked_by if not item["satisfied"]]
        blocker = ""
        if unsatisfied:
            blocker = "Blocked by " + ", ".join(
                f"{item['short_id']} ({item['condition']})" for item in unsatisfied
            )
            next_action = "Satisfy the listed task dependencies before adoption"
        initiative = None
        if version >= 4 and row["initiative_id"]:
            initiative = {
                "id": row["initiative_id"],
                "title": row["initiative_title"],
                "brief": row["initiative_brief"],
            }
        result.append(
            {
                "id": row["id"],
                "short_id": row["short_id"],
                "label": row["title"],
                "repository": row["repository"] or "",
                "worktree": "",
                "branch": "",
                "base_revision": row["prepared_revision"] or "",
                "head_revision": row["adopted_revision"] or row["prepared_revision"] or "",
                "tier": "",
                "stage": "preparation" if status in ("draft", "ready") else status,
                "status": visible_status,
                "summary": "Prepared specification confirmed" if status == "ready" else "",
                "blocker": blocker,
                "next_action": next_action,
                "initiative": initiative,
                "blocked_by": blocked_by,
                "parallel_with": parallel_views.get(str(row["id"]), []),
                "preparation_status": status,
                "disposition": disposition,
                "stop_requested_at": row["stop_requested_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "current_activity": [],
            }
        )
    return result


def _coordination_rows(connection: sqlite3.Connection | None) -> dict[str, dict]:
    if connection is None:
        return {}
    tasks = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM tasks").fetchall()}
    for task in tasks.values():
        task["current_activity"] = []
    for activity in connection.execute(
        """
        SELECT * FROM activities
        ORDER BY updated_at DESC, agent_id, capability
        """
    ).fetchall():
        task = tasks.get(activity["task_id"])
        if task is not None:
            task["current_activity"].append(
                {field: activity[field] for field in ACTIVITY_FIELDS}
            )
    return tasks


def _merged_rows(
    coordination: sqlite3.Connection | None,
    control: sqlite3.Connection | None,
) -> list[dict]:
    coordinator = _coordination_rows(coordination)
    rows: dict[str, dict] = {}
    for prepared in _control_rows(control):
        active = coordinator.pop(str(prepared["id"]), None)
        if active is None:
            rows[str(prepared["id"])] = prepared
            continue
        active["short_id"] = prepared["short_id"]
        # Task Control owns the confirmed user-facing title for adopted cards.
        # The coordinator label may be a branch-oriented implementation slug.
        active["label"] = prepared["label"]
        active["initiative"] = prepared["initiative"]
        active["blocked_by"] = prepared["blocked_by"]
        active["parallel_with"] = prepared["parallel_with"]
        active["preparation_status"] = prepared["preparation_status"]
        active["disposition"] = prepared["disposition"]
        active["stop_requested_at"] = prepared["stop_requested_at"]
        if prepared["status"] in INACTIVE_STATUSES:
            active["status"] = prepared["status"]
            active["stage"] = prepared["stage"]
        if prepared["blocker"]:
            active["blocker"] = prepared["blocker"]
            active["next_action"] = prepared["next_action"]
        rows[str(active["id"])] = active
    for task_id, active in coordinator.items():
        active["short_id"] = None
        active["initiative"] = None
        active["blocked_by"] = []
        active["parallel_with"] = []
        active["preparation_status"] = active.get("status", "")
        active["disposition"] = "open"
        active["stop_requested_at"] = None
        rows[task_id] = active
    return sorted(rows.values(), key=lambda row: (str(row["updated_at"]), str(row["id"])), reverse=True)


def _tasks(
    coordination: sqlite3.Connection | None,
    control: sqlite3.Connection | None,
    *,
    now: datetime,
    stale_after: timedelta,
    status: str | None = None,
) -> list[dict]:
    rows = _merged_rows(coordination, control)
    if status is not None:
        rows = [row for row in rows if row["status"] == status]
    return [task_summary(row, now=now, stale_after=stale_after) for row in rows]


def summary_payload(
    connection: sqlite3.Connection | None,
    config: HubConfig,
    now: datetime,
    control_connection: sqlite3.Connection | None = None,
) -> dict:
    stale_after = _stale_after(config)
    tasks = _tasks(connection, control_connection, now=now, stale_after=stale_after)
    return {
        "status": "ok",
        "material_fingerprint_version": MATERIAL_FINGERPRINT_VERSION,
        "repositories": repository_entries(tasks, config.pinned_repositories),
        "tasks": tasks,
        "attention": attention_entries(tasks),
    }


def tasks_payload(
    connection: sqlite3.Connection | None,
    config: HubConfig,
    now: datetime,
    *,
    status: str | None = None,
    control_connection: sqlite3.Connection | None = None,
) -> dict:
    tasks = _tasks(
        connection,
        control_connection,
        now=now,
        stale_after=_stale_after(config),
        status=status,
    )
    return {
        "status": "ok",
        "material_fingerprint_version": MATERIAL_FINGERPRINT_VERSION,
        "tasks": tasks,
    }


def task_detail_payload(
    connection: sqlite3.Connection | None,
    config: HubConfig,
    now: datetime,
    task_id: str,
    control_connection: sqlite3.Connection | None = None,
) -> dict | None:
    candidates = _merged_rows(connection, control_connection)
    matches = [
        row for row in candidates
        if row["id"] == task_id or str(row.get("short_id") or "").lower() == task_id.lower()
    ]
    if not matches:
        return None
    task = task_summary(matches[0], now=now, stale_after=_stale_after(config))
    activities = []
    if connection is not None and task["worktree"]:
        activities = [
            {field: activity[field] for field in ACTIVITY_FIELDS}
            for activity in connection.execute(
                """
                SELECT * FROM activities WHERE task_id = ?
                ORDER BY updated_at DESC, agent_id, capability
                """,
                (task["id"],),
            ).fetchall()
        ]
    return {
        "status": "ok",
        "material_fingerprint_version": MATERIAL_FINGERPRINT_VERSION,
        "task": task,
        "activities": activities,
        "artifacts": artifact_entries(str(task["worktree"])) if task["worktree"] else [],
    }
