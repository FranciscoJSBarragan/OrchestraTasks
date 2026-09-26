"""Shared fixtures: real coordination-schema database for Hub tests."""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "control"))
sys.path.insert(0, str(REPO_ROOT / "hub"))

import coordination  # noqa: E402

from orchestra_control import db as control_db  # noqa: E402

from orchestra_hub import artifacts  # noqa: E402

HEX40 = "a" * 40


def create_state_db(state_root: Path) -> Path:
    state_root.mkdir(parents=True, exist_ok=True)
    database = state_root / "state.sqlite3"
    connection = sqlite3.connect(database)
    try:
        for statement in coordination.SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {coordination.SCHEMA_VERSION}")
        connection.commit()
        connection.execute("PRAGMA journal_mode = WAL")
    finally:
        connection.close()
    return database


def create_control_db(state_root: Path) -> Path:
    database = state_root / "control.sqlite3"
    connection = sqlite3.connect(database)
    try:
        for statement in control_db.SCHEMA:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {control_db.SCHEMA_VERSION}")
        connection.commit()
    finally:
        connection.close()
    return database


def create_control_v3_db(state_root: Path) -> Path:
    database = state_root / "control-v3.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, short_id TEXT, title TEXT NOT NULL,
                brief TEXT NOT NULL, brief_revision INTEGER NOT NULL,
                source_harness TEXT NOT NULL, source_conversation TEXT NOT NULL,
                source_message TEXT NOT NULL, repository TEXT, rank INTEGER NOT NULL,
                disposition TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                preparation_status TEXT NOT NULL, prepared_revision TEXT,
                repository_context_digest TEXT, specification_digest TEXT,
                specification_confirmed_at TEXT, adopted_thread_id TEXT,
                adopted_revision TEXT, adopted_at TEXT, previous_thread_id TEXT,
                transfer_generation INTEGER NOT NULL, transfer_requested_at TEXT,
                completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            PRAGMA user_version = 3;
            """
        )
        connection.commit()
    finally:
        connection.close()
    return database


def insert_prepared_task(database: Path, **overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()), "short_id": "A1", "title": "Prepared task",
        "brief": "Prepared brief", "brief_revision": 1,
        "source_harness": "test", "source_conversation": "",
        "source_message": "", "repository": "/tmp/repo", "rank": 1,
        "disposition": "open", "idempotency_key": str(uuid.uuid4()),
        "preparation_status": "ready", "prepared_revision": HEX40,
        "repository_context_digest": "b" * 64, "specification_digest": "c" * 64,
        "specification_confirmed_at": "2026-08-02T18:00:00Z",
        "adopted_thread_id": None, "adopted_harness": None,
        "adopted_revision": None, "adopted_at": None,
        "previous_thread_id": None, "previous_harness": None,
        "transfer_generation": 0, "transfer_requested_at": None,
        "completed_at": None, "initiative_id": None,
        "decomposition_reason": None, "repository_common_dir": None,
        "completed_revision": None, "delivered_task_revision": None,
        "delivery_revision": None, "delivery_kind": None, "delivered_at": None,
        "stop_requested_at": None, "cancelled_at": None, "trashed_at": None,
        "disposition_before_trash": None,
        "created_at": "2026-08-02T18:00:00Z",
        "updated_at": "2026-08-02T18:00:00Z",
    }
    row.update(overrides)
    connection = sqlite3.connect(database)
    try:
        table_columns = {
            item[1] for item in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        columns = [
            "id", "short_id", "title", "brief", "brief_revision", "source_harness",
            "source_conversation", "source_message", "repository", "rank",
            "disposition", "idempotency_key", "preparation_status",
            "prepared_revision", "repository_context_digest", "specification_digest",
            "specification_confirmed_at", "adopted_thread_id", "adopted_revision",
            "adopted_at", "previous_thread_id", "transfer_generation",
            "transfer_requested_at", "completed_at",
        ]
        if "initiative_id" in table_columns:
            columns.extend(
                [
                    "initiative_id", "decomposition_reason", "repository_common_dir",
                    "completed_revision", "delivered_task_revision", "delivery_revision",
                    "delivery_kind", "delivered_at",
                ]
            )
        if "adopted_harness" in table_columns:
            columns.extend(["adopted_harness", "previous_harness"])
        if "stop_requested_at" in table_columns:
            columns.extend(
                [
                    "stop_requested_at", "cancelled_at", "trashed_at",
                    "disposition_before_trash",
                ]
            )
        columns.extend(["created_at", "updated_at"])
        connection.execute(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES "
            f"({', '.join(':' + column for column in columns)})",
            row,
        )
        connection.commit()
    finally:
        connection.close()
    return row


def task_row(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()), "label": "Sample task",
        "repository": "/tmp/repo", "worktree": "/tmp/worktrees/repo/sample",
        "branch": "orchestra/sample", "base_revision": HEX40,
        "head_revision": HEX40, "tier": "standard",
        "stage": "implementation", "status": "active", "summary": "Working",
        "blocker": "", "next_action": "Continue",
        "initiative": None, "blocked_by": [], "parallel_with": [],
        "preparation_status": "adopted", "disposition": "open",
        "stop_requested_at": None,
        "created_at": "2026-08-02T18:00:00Z",
        "updated_at": "2026-08-02T18:00:00Z",
    }
    row.update(overrides)
    return row


def insert_task(database: Path, **overrides) -> dict:
    row = task_row(**overrides)
    connection = sqlite3.connect(database)
    try:
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
            row,
        )
        connection.commit()
    finally:
        connection.close()
    return row


def insert_activity(database: Path, task_id: str, **overrides) -> dict:
    row = {
        "task_id": task_id,
        "agent_id": "agent-1",
        "capability": "implementation",
        "state": "running",
        "summary": "Implementing",
        "updated_at": "2026-08-02T18:00:00Z",
    }
    row.update(overrides)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """
            INSERT INTO activities (
                task_id, agent_id, capability, state, summary, updated_at
            ) VALUES (
                :task_id, :agent_id, :capability, :state, :summary, :updated_at
            )
            """,
            row,
        )
        connection.commit()
    finally:
        connection.close()
    return row


def make_worktree(root: Path, *, linked: bool = False) -> Path:
    """Create a primary- or linked-worktree-shaped directory."""
    worktree = root / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    if linked:
        gitdir = root / "repo" / ".git" / "worktrees" / "worktree"
        gitdir.mkdir(parents=True, exist_ok=True)
        (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    else:
        (worktree / ".git").mkdir(exist_ok=True)
    return worktree


def write_artifact(worktree: Path, name: str, body: str = "report\n") -> Path:
    """Publish an artifact file the way an Orchestra agent does."""
    state = worktree / ".orchestra"
    if not state.exists():
        state.mkdir()
        (state / ".gitignore").write_text(
            artifacts.STATE_MARKER_CONTENT,
            encoding="utf-8",
        )
    directory = artifacts.artifacts_directory(worktree)
    assert directory is not None
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path
