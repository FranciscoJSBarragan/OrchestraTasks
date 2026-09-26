#!/usr/bin/env python3
"""Maintain fail-soft Orchestra task and activity snapshots.

Artifact content lives directly in each task's worktree-local
`.orchestra/artifacts` directory; the filesystem is the only locator and this
helper never tracks it. Legacy tasks may retain their prior Git-private path.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
from typing import Any
import uuid

from git_identity import SHA_PATTERN, git_repository_identity


SCHEMA_VERSION = 1
TIERS = ("luna", "standard", "critical")
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        repository TEXT NOT NULL,
        worktree TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_revision TEXT NOT NULL,
        head_revision TEXT NOT NULL,
        tier TEXT NOT NULL,
        stage TEXT NOT NULL,
        status TEXT NOT NULL,
        summary TEXT NOT NULL,
        blocker TEXT NOT NULL,
        next_action TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE activities (
        task_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        state TEXT NOT NULL,
        summary TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (task_id, agent_id, capability),
        FOREIGN KEY (task_id) REFERENCES tasks(id)
    )
    """,
    "CREATE INDEX activities_task_id ON activities(task_id)",
)


class CoordinationInvalid(Exception):
    """The requested coordination operation is invalid."""


class CoordinationUnavailable(Exception):
    """Coordination storage or a referenced runtime resource is unavailable."""


class JsonArgumentParser(argparse.ArgumentParser):
    """Emit the same compact JSON error shape as runtime validation."""

    def error(self, message: str) -> None:
        payload = {"status": "invalid", "reason": _compact(message, limit=500)}
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compact(value: str, *, limit: int = 2000) -> str:
    return " ".join(value.split())[:limit]


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise CoordinationUnavailable(f"cannot run {command[0]}: {error}") from error


def _git_root(path: Path) -> Path:
    resolved = path.resolve()
    result = _run(["git", "rev-parse", "--show-toplevel"], cwd=resolved)
    if result.returncode:
        raise CoordinationInvalid(f"not a Git worktree: {resolved}")
    root = Path(result.stdout.strip()).resolve()
    if root != resolved:
        raise CoordinationInvalid(f"path is not the Git worktree root: {resolved}")
    return root


def _git_value(worktree: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=worktree)
    value = result.stdout.strip()
    if result.returncode or not value:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise CoordinationInvalid(f"cannot inspect Git worktree: {_compact(detail)}")
    return value


def _head(worktree: Path) -> str:
    value = _git_value(worktree, "rev-parse", "--verify", "HEAD^{commit}")
    if not SHA_PATTERN.fullmatch(value):
        raise CoordinationInvalid("Git returned an invalid HEAD revision")
    return value


def _branch(worktree: Path) -> str:
    return _git_value(worktree, "symbolic-ref", "--short", "HEAD")


def _common_git_dir(worktree: Path) -> Path:
    value = Path(_git_value(worktree, "rev-parse", "--git-common-dir"))
    return (worktree / value).resolve() if not value.is_absolute() else value.resolve()


def _same_repository(first: Path, second: Path) -> bool:
    return _common_git_dir(first) == _common_git_dir(second)


def _state_directory(explicit: Path | None) -> Path:
    if explicit is not None:
        raw = explicit
    else:
        home = os.environ.get("HOME")
        if not home:
            raise CoordinationUnavailable("HOME is unavailable")
        raw = Path(home) / ".orchestra"
    absolute = Path(os.path.abspath(raw))
    if not absolute.exists():
        try:
            absolute.mkdir(parents=True, mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise CoordinationUnavailable(f"cannot create state root: {error}") from error
    if absolute.is_symlink() or not absolute.is_dir():
        raise CoordinationUnavailable(f"state root is not a regular directory: {absolute}")
    return absolute


def _state_objects(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
    ]


def _restrict_state_files(database: Path) -> None:
    for path in (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    ):
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise CoordinationUnavailable(
                f"cannot inspect state database file: {error}"
            ) from error
        if not stat.S_ISREG(mode):
            raise CoordinationUnavailable(f"state database file is unsafe: {path}")
        if stat.S_IMODE(mode) == 0o600:
            continue
        try:
            os.chmod(path, 0o600)
        except FileNotFoundError:
            # A WAL sidecar may be checkpointed away by a concurrent process.
            continue
        except OSError as error:
            raise CoordinationUnavailable(
                f"cannot restrict state database permissions: {error}"
            ) from error


def _connect(state_root: Path | None) -> sqlite3.Connection:
    root = _state_directory(state_root)
    database = root / "state.sqlite3"
    if database.is_symlink():
        raise CoordinationUnavailable(f"state database is a symlink: {database}")
    if database.exists() and not database.is_file():
        raise CoordinationUnavailable(f"state database is not a regular file: {database}")
    if not database.exists():
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(database, flags, 0o600)
        except FileExistsError:
            pass
        except OSError as error:
            raise CoordinationUnavailable(
                f"cannot create coordination database: {error}"
            ) from error
        else:
            os.close(descriptor)
    if database.is_symlink() or not database.is_file():
        raise CoordinationUnavailable(f"state database is unsafe: {database}")
    _restrict_state_files(database)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = _state_objects(connection)
        if version == 0 and not objects:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            connection.rollback()
            raise CoordinationUnavailable(
                f"unsupported coordination schema version: {version}"
            )
        connection.commit()
        connection.execute("PRAGMA journal_mode = WAL")
        _restrict_state_files(database)
        return connection
    except CoordinationUnavailable:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.rollback()
            connection.close()
        raise CoordinationUnavailable(f"coordination database is unavailable: {error}") from error


def _task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise CoordinationInvalid(f"unknown task: {task_id}")
    return row


def _task_payload(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_task(
    connection: sqlite3.Connection,
    *,
    task_id: str | None,
    repository: Path,
    worktree: Path,
    base_revision: str,
    tier: str,
    label: str,
    stage: str,
    status: str,
) -> dict[str, Any]:
    if not SHA_PATTERN.fullmatch(base_revision):
        raise CoordinationInvalid("base revision must be a full Git object name")
    provided_repository_root = _git_root(repository)
    worktree_root = _git_root(worktree)
    if not _same_repository(provided_repository_root, worktree_root):
        raise CoordinationInvalid("repository and task worktree do not share a Git repository")
    repository_identity = git_repository_identity(provided_repository_root)
    if repository_identity is None:
        raise CoordinationInvalid("cannot resolve the primary repository worktree")
    repository_root = repository_identity.repository_root
    branch = _branch(worktree_root)
    if not branch.startswith("orchestra/"):
        raise CoordinationInvalid("task worktree branch must use the orchestra/ namespace")
    head_revision = _head(worktree_root)
    resolved_base = _git_value(
        worktree_root, "rev-parse", "--verify", f"{base_revision}^{{commit}}"
    )
    if resolved_base != base_revision:
        raise CoordinationInvalid("base revision does not identify a full commit")
    if not _compact(label) or not _compact(stage) or not _compact(status):
        raise CoordinationInvalid("label, stage, and status must be nonempty")
    timestamp = _now()
    explicit_task_id = task_id is not None
    task_id = task_id.strip() if task_id else str(uuid.uuid4())
    try:
        task_id = str(uuid.UUID(task_id))
    except ValueError as error:
        raise CoordinationInvalid("task id must be a UUID") from error
    payload = {
        "id": task_id,
        "label": _compact(label),
        "repository": str(repository_root),
        "worktree": str(worktree_root),
        "branch": branch,
        "base_revision": base_revision,
        "head_revision": head_revision,
        "tier": tier,
        "stage": _compact(stage),
        "status": _compact(status),
        "summary": "",
        "blocker": "",
        "next_action": "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    connection.execute("BEGIN IMMEDIATE")
    try:
        by_id = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if by_id is not None:
            if (
                by_id["worktree"] != str(worktree_root)
                or by_id["branch"] != branch
                or by_id["base_revision"] != base_revision
            ):
                raise CoordinationInvalid(
                    "task id already belongs to a different coordination snapshot"
                )
            connection.commit()
            return {
                "status": "ok",
                "created": False,
                "task": _task_payload(by_id),
            }
        existing = connection.execute(
            """
            SELECT * FROM tasks
            WHERE worktree = ? AND status != 'completed'
            ORDER BY updated_at DESC, id
            LIMIT 1
            """,
            (str(worktree_root),),
        ).fetchone()
        if existing is not None:
            if explicit_task_id and existing["id"] != task_id:
                raise CoordinationInvalid(
                    "worktree already belongs to a different task id"
                )
            if existing["branch"] != branch:
                raise CoordinationInvalid(
                    "worktree already belongs to a different active task snapshot"
                )
            connection.commit()
            return {
                "status": "ok",
                "created": False,
                "task": _task_payload(existing),
            }
        connection.execute(
            """
            INSERT INTO tasks (
                id, label, repository, worktree, branch, base_revision,
                head_revision, tier, stage, status, summary, blocker,
                next_action, created_at, updated_at
            ) VALUES (
                :id, :label, :repository, :worktree, :branch, :base_revision,
                :head_revision, :tier, :stage, :status, :summary, :blocker,
                :next_action, :created_at, :updated_at
            )
            """,
            payload,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {"status": "ok", "created": True, "task": payload}


def list_tasks(connection: sqlite3.Connection, *, status: str | None) -> dict[str, Any]:
    if status is None:
        rows = connection.execute(
            "SELECT * FROM tasks ORDER BY updated_at DESC, id"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY updated_at DESC, id",
            (status,),
        ).fetchall()
    return {"status": "ok", "tasks": [_task_payload(row) for row in rows]}


def show_task(connection: sqlite3.Connection, *, task_id: str) -> dict[str, Any]:
    row = _task(connection, task_id)
    activities = [
        dict(activity)
        for activity in connection.execute(
            """
            SELECT * FROM activities
            WHERE task_id = ?
            ORDER BY updated_at DESC, agent_id, capability
            """,
            (task_id,),
        ).fetchall()
    ]
    return {
        "status": "ok",
        "task": _task_payload(row),
        "activities": activities,
    }


def update_task(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    updates: dict[str, str | None],
) -> dict[str, Any]:
    _task(connection, task_id)
    values = {key: value for key, value in updates.items() if value is not None}
    if not values:
        raise CoordinationInvalid("task update requires at least one field")
    if "head_revision" in values and not SHA_PATTERN.fullmatch(values["head_revision"] or ""):
        raise CoordinationInvalid("head revision must be a full Git object name")
    for key in ("stage", "status", "summary", "blocker", "next_action"):
        if key in values:
            values[key] = _compact(values[key] or "")
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key} = :{key}" for key in values)
    values["task_id"] = task_id
    with connection:
        connection.execute(
            f"UPDATE tasks SET {assignments} WHERE id = :task_id",
            values,
        )
    return {"status": "ok", "task": _task_payload(_task(connection, task_id))}


def set_activity(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    agent_id: str,
    capability: str,
    state: str,
    summary: str,
) -> dict[str, Any]:
    _task(connection, task_id)
    payload = {
        "task_id": task_id,
        "agent_id": _compact(agent_id, limit=200),
        "capability": _compact(capability, limit=200),
        "state": _compact(state, limit=200),
        "summary": _compact(summary),
        "updated_at": _now(),
    }
    if not payload["agent_id"] or not payload["capability"] or not payload["state"]:
        raise CoordinationInvalid("agent, capability, and state must be nonempty")
    with connection:
        connection.execute(
            """
            INSERT INTO activities (
                task_id, agent_id, capability, state, summary, updated_at
            ) VALUES (
                :task_id, :agent_id, :capability, :state, :summary, :updated_at
            )
            ON CONFLICT(task_id, agent_id, capability) DO UPDATE SET
                state = excluded.state,
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        connection.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ?",
            (payload["updated_at"], task_id),
        )
    return {"status": "ok", "activity": payload}


def clear_activity(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    agent_id: str,
    capability: str,
) -> dict[str, Any]:
    _task(connection, task_id)
    with connection:
        cursor = connection.execute(
            """
            DELETE FROM activities
            WHERE task_id = ? AND agent_id = ? AND capability = ?
            """,
            (task_id, agent_id, capability),
        )
    return {"status": "ok", "cleared": bool(cursor.rowcount)}


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path)
    resources = parser.add_subparsers(dest="resource", required=True)

    task = resources.add_parser("task")
    task_commands = task.add_subparsers(dest="action", required=True)
    task_create = task_commands.add_parser("create")
    task_create.add_argument("--task-id")
    task_create.add_argument("--repository", type=Path, required=True)
    task_create.add_argument("--worktree", type=Path, required=True)
    task_create.add_argument("--base-revision", required=True)
    task_create.add_argument("--tier", choices=TIERS, required=True)
    task_create.add_argument("--label", required=True)
    task_create.add_argument("--stage", default="context")
    task_create.add_argument("--status", default="active")
    task_list = task_commands.add_parser("list")
    task_list.add_argument("--status")
    task_show = task_commands.add_parser("show")
    task_show.add_argument("--task", required=True)
    task_update = task_commands.add_parser("update")
    task_update.add_argument("--task", required=True)
    task_update.add_argument("--tier", choices=TIERS)
    task_update.add_argument("--stage")
    task_update.add_argument("--status")
    task_update.add_argument("--summary")
    task_update.add_argument("--blocker")
    task_update.add_argument("--next-action")
    task_update.add_argument("--head-revision")

    activity = resources.add_parser("activity")
    activity_commands = activity.add_subparsers(dest="action", required=True)
    activity_set = activity_commands.add_parser("set")
    activity_set.add_argument("--task", required=True)
    activity_set.add_argument("--agent", required=True)
    activity_set.add_argument("--capability", required=True)
    activity_set.add_argument("--state", required=True)
    activity_set.add_argument("--summary", default="")
    activity_clear = activity_commands.add_parser("clear")
    activity_clear.add_argument("--task", required=True)
    activity_clear.add_argument("--agent", required=True)
    activity_clear.add_argument("--capability", required=True)
    return parser


def _dispatch(connection: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    if args.resource == "task":
        if args.action == "create":
            return create_task(
                connection,
                task_id=args.task_id,
                repository=args.repository,
                worktree=args.worktree,
                base_revision=args.base_revision,
                tier=args.tier,
                label=args.label,
                stage=args.stage,
                status=args.status,
            )
        if args.action == "list":
            return list_tasks(connection, status=args.status)
        if args.action == "show":
            return show_task(connection, task_id=args.task)
        if args.action == "update":
            return update_task(
                connection,
                task_id=args.task,
                updates={
                    "tier": args.tier,
                    "stage": args.stage,
                    "status": args.status,
                    "summary": args.summary,
                    "blocker": args.blocker,
                    "next_action": args.next_action,
                    "head_revision": args.head_revision,
                },
            )
    if args.resource == "activity":
        if args.action == "set":
            return set_activity(
                connection,
                task_id=args.task,
                agent_id=args.agent,
                capability=args.capability,
                state=args.state,
                summary=args.summary,
            )
        if args.action == "clear":
            return clear_activity(
                connection,
                task_id=args.task,
                agent_id=args.agent,
                capability=args.capability,
            )
    raise CoordinationInvalid("unsupported coordination command")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(args.state_root)
        payload = _dispatch(connection, args)
    except CoordinationInvalid as error:
        payload = {"status": "invalid", "reason": _compact(str(error), limit=500)}
    except (CoordinationUnavailable, sqlite3.Error, OSError) as error:
        payload = {"status": "unavailable", "reason": _compact(str(error), limit=500)}
    finally:
        if connection is not None:
            connection.close()
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
