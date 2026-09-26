"""Domain operations for the durable prepared-task Kanban."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
from typing import Any, Callable, Iterator
import uuid

from .db import StorageError, connect, state_root as resolve_state_root


SHORT_ID_PATTERN = re.compile(r"^([A-Z]+)([1-9][0-9]?)$", re.IGNORECASE)
CODEX_THREAD_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
CURSOR_THREAD_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
CURSOR_THREAD_ENV = "ORCHESTRA_HOST_THREAD_ID"
GROK_THREAD_ENV = "GROK_SESSION_ID"
DEVIN_THREAD_ENV = "ORCHESTRA_DEVIN_THREAD_ID"
OWNER_HARNESSES = frozenset({"codex", "cursor", "grok", "devin"})
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)
CARD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DEPENDENCY_CONDITIONS = {"completed", "delivered"}
DELIVERY_KINDS = {"local-integration", "pr-merge"}
REPOSITORY_UNSET = object()


class ControlError(Exception):
    """A validated user or runtime control error."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compact(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def bounded(value: str, limit: int) -> str:
    return value.strip()[:limit]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sequence_to_short_id(sequence: int) -> str:
    if sequence < 1:
        raise ValueError("short task sequence must be positive")
    letter_sequence = (sequence - 1) // 99 + 1
    number = (sequence - 1) % 99 + 1
    letters = ""
    while letter_sequence:
        letter_sequence, remainder = divmod(letter_sequence - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{number}"


def normalize_short_id(value: str) -> str:
    normalized = value.strip().upper()
    if not SHORT_ID_PATTERN.fullmatch(normalized):
        raise ControlError("invalid", f"invalid short task ID: {value}")
    return normalized


def host_thread_from_env(environ: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    env = os.environ if environ is None else environ
    identities = [
        (harness, env[key])
        for harness, key in (
            ("codex", "CODEX_THREAD_ID"), ("grok", GROK_THREAD_ENV),
            ("cursor", CURSOR_THREAD_ENV), ("devin", DEVIN_THREAD_ENV),
        )
        if env.get(key)
    ]
    if len(identities) > 1:
        raise ControlError(
            "blocked",
            "multiple host conversation identities are present; use the owning native "
            "chat's environment without inherited identities from another host",
        )
    return identities[0] if identities else (None, None)


def validate_thread_id(value: str | None, harness: str | None = "codex") -> str:
    if harness == "cursor":
        if not value or not CURSOR_THREAD_PATTERN.fullmatch(value.strip()):
            raise ControlError(
                "blocked",
                "Cursor host conversation identity is missing or invalid",
            )
        return value.strip()
    if harness == "grok":
        if not value or not CODEX_THREAD_PATTERN.fullmatch(value.strip()):
            raise ControlError(
                "blocked",
                "Grok host session identity is missing or invalid",
            )
        return value.strip().lower()
    if harness == "devin":
        if not value or not CURSOR_THREAD_PATTERN.fullmatch(value.strip()):
            raise ControlError(
                "blocked",
                "Devin host session identity is missing or invalid",
            )
        return value.strip()
    if harness != "codex":
        raise ControlError("blocked", "host conversation identity is missing")
    if not value or not CODEX_THREAD_PATTERN.fullmatch(value.strip()):
        raise ControlError("blocked", "CODEX_THREAD_ID is missing or invalid")
    return value.strip().lower()


def owner_identity(task: dict[str, Any], prefix: str = "adopted") -> tuple[str, str] | None:
    """Return one namespaced task owner, or no owner when the slot is empty."""
    thread = task.get(f"{prefix}_thread_id")
    harness = task.get(f"{prefix}_harness")
    if not thread:
        return None
    if harness not in OWNER_HARNESSES:
        raise ControlError("blocked", f"{prefix} task owner has no valid host namespace")
    return str(harness), str(thread)


def validate_revision(value: str) -> str:
    normalized = value.strip().lower()
    if not REVISION_PATTERN.fullmatch(normalized):
        raise ControlError("invalid", "revision must be a full Git object name")
    return normalized


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in (
        "last_result_json",
        "retained_resources_json",
        "result_json",
        "params_json",
        "response_json",
    ):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key.removesuffix("_json")] = json.loads(value)
            except json.JSONDecodeError:
                result[key.removesuffix("_json")] = None
            del result[key]
    return result


class ControlService:
    def __init__(self, state_root: Path | None = None) -> None:
        self.state_root = state_root

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect(self.state_root)
        except StorageError as error:
            raise ControlError("unavailable", str(error)) from error
        try:
            with connection:
                yield connection
        except ControlError:
            raise
        except sqlite3.Error as error:
            raise ControlError("unavailable", f"control database is unavailable: {error}") from error
        finally:
            connection.close()

    def _task_row(self, connection: sqlite3.Connection, task_ref: str) -> sqlite3.Row:
        reference = task_ref.strip()
        if SHORT_ID_PATTERN.fullmatch(reference):
            row = connection.execute(
                "SELECT * FROM tasks WHERE short_id = ? COLLATE NOCASE",
                (reference.upper(),),
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (reference,)).fetchone()
        if row is None:
            raise ControlError("invalid", f"unknown task: {task_ref}")
        return row

    def _documents_directory(self, short_id: str, *, create: bool) -> Path:
        try:
            root = resolve_state_root(self.state_root)
        except StorageError as error:
            raise ControlError("unavailable", str(error)) from error
        tasks_root = root / "tasks"
        directory = tasks_root / normalize_short_id(short_id)
        if not create:
            return directory
        for candidate in (tasks_root, directory):
            try:
                candidate.mkdir(mode=0o700, exist_ok=True)
            except OSError as error:
                raise ControlError("unavailable", f"cannot create task documents: {error}") from error
            try:
                mode = os.lstat(candidate).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ControlError("unavailable", f"unsafe task documents directory: {candidate}")
                if stat.S_IMODE(mode) != 0o700:
                    os.chmod(candidate, 0o700)
            except OSError as error:
                raise ControlError("unavailable", f"cannot secure task documents: {error}") from error
        return directory

    def _write_private(self, path: Path, body: str) -> None:
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ControlError("unavailable", f"unsafe task document: {path}")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError as error:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise ControlError("unavailable", f"cannot write task document: {error}") from error

    def _verify_prepared_documents(self, task: dict[str, Any]) -> None:
        documents = self._document_payload(task)
        expected = {
            "repository_context": task.get("repository_context_digest"),
            "specification": task.get("specification_digest"),
        }
        for name, expected_digest in expected.items():
            path = Path(documents[name])
            try:
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    raise OSError("not a regular private file")
                if stat.S_IMODE(mode) != 0o600:
                    raise OSError("permissions are not 0600")
                observed = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise ControlError("blocked", f"prepared {name} is unavailable or unsafe: {error}") from error
            if not expected_digest or observed != expected_digest:
                raise ControlError("blocked", f"prepared {name} digest does not match the task marker")
        marker_path = Path(documents["marker"])
        try:
            mode = os.lstat(marker_path).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise OSError("not a regular private file")
            if stat.S_IMODE(mode) != 0o600:
                raise OSError("permissions are not 0600")
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ControlError("blocked", f"prepared task marker is unavailable or invalid: {error}") from error
        required = {
            "id": task["id"],
            "short_id": task["short_id"],
            "prepared_revision": task["prepared_revision"],
            "repository_context_digest": task["repository_context_digest"],
            "specification_digest": task["specification_digest"],
            "specification_confirmed_at": task["specification_confirmed_at"],
        }
        if any(marker.get(key) != value for key, value in required.items()):
            raise ControlError("blocked", "prepared task marker does not match the database record")

    def _document_payload(self, task: dict[str, Any]) -> dict[str, Any]:
        short_id = task.get("short_id")
        if not short_id:
            return {}
        directory = self._documents_directory(str(short_id), create=False)
        return {
            "directory": str(directory),
            "repository_context": str(directory / "repository-context.md"),
            "specification": str(directory / "specification.md"),
            "marker": str(directory / "task.json"),
        }

    def _decorate(self, task: dict[str, Any]) -> dict[str, Any]:
        task["documents"] = self._document_payload(task) if task.get("short_id") else {}
        status = task.get("preparation_status")
        disposition = task.get("disposition")
        has_relations = bool(task.get("initiative_id"))
        task["capabilities"] = {
            "edit": disposition == "open" and status == "draft" and not has_relations,
            "note": disposition != "trashed",
            "archive": disposition == "open" and status != "adopted",
            "restore_archive": disposition == "archived",
            "trash": disposition != "trashed" and status != "adopted",
            "restore_trash": disposition == "trashed",
            "request_stop": (
                disposition == "open"
                and status == "adopted"
                and not task.get("stop_requested_at")
            ),
            "withdraw_stop": (
                disposition == "open"
                and status == "adopted"
                and bool(task.get("stop_requested_at"))
            ),
            "reopen": disposition == "open" and status == "cancelled",
            "purge": False,
        }
        return task

    def _purge_blockers(
        self, connection: sqlite3.Connection, task: dict[str, Any]
    ) -> list[str]:
        blockers: list[str] = []
        if task.get("disposition") != "trashed":
            blockers.append("task is not in trash")
        if task.get("preparation_status") != "draft":
            blockers.append("only an unprepared draft can be purged")
        evidence_fields = (
            "prepared_revision",
            "repository_context_digest",
            "specification_digest",
            "specification_confirmed_at",
            "adopted_thread_id",
            "adopted_harness",
            "adopted_revision",
            "adopted_at",
            "previous_thread_id",
            "previous_harness",
            "initiative_id",
            "completed_at",
            "completed_revision",
            "delivered_task_revision",
            "delivery_revision",
            "delivery_kind",
            "delivered_at",
            "stop_requested_at",
            "cancelled_at",
        )
        if any(task.get(field) for field in evidence_fields):
            blockers.append(
                "task has preparation, ownership, initiative, completion, or delivery evidence"
            )
        relations = connection.execute(
            """
            SELECT
                EXISTS(SELECT 1 FROM task_dependencies WHERE task_id = ? OR blocked_by_task_id = ?),
                EXISTS(SELECT 1 FROM task_notes WHERE task_id = ?),
                EXISTS(SELECT 1 FROM runs WHERE task_id = ?)
            """,
            (task["id"], task["id"], task["id"], task["id"]),
        ).fetchone()
        if relations[0]:
            blockers.append("task has dependency evidence")
        if relations[1]:
            blockers.append("task has notes")
        if relations[2]:
            blockers.append("task has legacy run evidence")
        return blockers

    def _initiative_payload(
        self, connection: sqlite3.Connection, initiative_id: str | None
    ) -> dict[str, Any] | None:
        if not initiative_id:
            return None
        row = connection.execute(
            "SELECT id, title, brief, source_task_id, created_at FROM task_initiatives WHERE id = ?",
            (initiative_id,),
        ).fetchone()
        return row_dict(row) if row is not None else None

    def _dependency_satisfied(self, predecessor: dict[str, Any], condition: str) -> bool:
        if condition == "completed":
            return predecessor.get("preparation_status") == "completed"
        return bool(
            predecessor.get("preparation_status") == "completed"
            and predecessor.get("delivered_at")
            and predecessor.get("delivery_revision")
            and predecessor.get("delivered_task_revision")
        )

    def _dependency_views(
        self,
        connection: sqlite3.Connection,
        task: dict[str, Any],
        *,
        current_common_dir: str | None = None,
        ancestor_contains: Callable[[str], bool] | None = None,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT d.condition, d.reason,
                predecessor.id AS predecessor_id,
                predecessor.short_id AS predecessor_short_id,
                predecessor.title AS predecessor_title,
                predecessor.preparation_status AS predecessor_status,
                predecessor.repository_common_dir AS predecessor_common_dir,
                predecessor.completed_revision AS predecessor_completed_revision,
                predecessor.delivered_task_revision,
                predecessor.delivery_revision,
                predecessor.delivery_kind,
                predecessor.delivered_at
            FROM task_dependencies AS d
            JOIN tasks AS predecessor ON predecessor.id = d.blocked_by_task_id
            WHERE d.task_id = ?
            ORDER BY predecessor.rank, predecessor.created_at, predecessor.id
            """,
            (task["id"],),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            predecessor = row_dict(row)
            condition = str(predecessor["condition"])
            satisfied = self._dependency_satisfied(
                {
                    "preparation_status": predecessor["predecessor_status"],
                    "delivered_at": predecessor["delivered_at"],
                    "delivery_revision": predecessor["delivery_revision"],
                    "delivered_task_revision": predecessor["delivered_task_revision"],
                },
                condition,
            )
            state = "satisfied" if satisfied else f"waiting-for-{condition}"
            same_repository = bool(
                condition == "delivered"
                and satisfied
                and current_common_dir
                and predecessor["predecessor_common_dir"] == current_common_dir
            )
            if same_repository:
                delivery_revision = str(predecessor["delivery_revision"])
                if ancestor_contains is None:
                    satisfied = False
                    state = "checkout-verification-required"
                elif not ancestor_contains(delivery_revision):
                    satisfied = False
                    state = "checkout-update-required"
            result.append(
                {
                    "task_id": predecessor["predecessor_id"],
                    "short_id": predecessor["predecessor_short_id"],
                    "title": predecessor["predecessor_title"],
                    "condition": condition,
                    "reason": predecessor["reason"],
                    "satisfied": satisfied,
                    "state": state,
                    "completed_revision": predecessor["predecessor_completed_revision"],
                    "delivery_revision": predecessor["delivery_revision"],
                    "delivery_kind": predecessor["delivery_kind"],
                }
            )
        return result

    def _parallel_views(
        self, connection: sqlite3.Connection, task: dict[str, Any]
    ) -> list[dict[str, Any]]:
        initiative_id = task.get("initiative_id")
        if not initiative_id:
            return []
        cards = [
            row_dict(row)
            for row in connection.execute(
                """
                SELECT id, short_id, title, preparation_status, rank, created_at
                FROM tasks
                WHERE initiative_id = ? AND disposition = 'open'
                ORDER BY rank, created_at, id
                """,
                (initiative_id,),
            ).fetchall()
        ]
        adjacency: dict[str, set[str]] = {str(card["id"]): set() for card in cards}
        for row in connection.execute(
            """
            SELECT d.blocked_by_task_id, d.task_id
            FROM task_dependencies AS d
            JOIN tasks AS dependent ON dependent.id = d.task_id
            WHERE dependent.initiative_id = ?
            """,
            (initiative_id,),
        ).fetchall():
            adjacency.setdefault(str(row["blocked_by_task_id"]), set()).add(str(row["task_id"]))

        def reaches(start: str, target: str) -> bool:
            pending = list(adjacency.get(start, ()))
            seen: set[str] = set()
            while pending:
                candidate = pending.pop()
                if candidate == target:
                    return True
                if candidate not in seen:
                    seen.add(candidate)
                    pending.extend(adjacency.get(candidate, ()))
            return False

        task_id = str(task["id"])
        return [
            {
                "task_id": card["id"],
                "short_id": card["short_id"],
                "title": card["title"],
                "preparation_status": card["preparation_status"],
            }
            for card in cards
            if card["id"] != task_id
            and not reaches(task_id, str(card["id"]))
            and not reaches(str(card["id"]), task_id)
        ]

    def _decorate_relations(
        self,
        connection: sqlite3.Connection,
        task: dict[str, Any],
        *,
        current_common_dir: str | None = None,
        ancestor_contains: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        self._decorate(task)
        task["initiative"] = self._initiative_payload(connection, task.get("initiative_id"))
        task["blocked_by"] = self._dependency_views(
            connection,
            task,
            current_common_dir=current_common_dir,
            ancestor_contains=ancestor_contains,
        )
        task["parallel_with"] = self._parallel_views(connection, task)
        task["dependency_blocked"] = any(not item["satisfied"] for item in task["blocked_by"])
        task["capabilities"]["purge"] = not self._purge_blockers(connection, task)
        return task

    def _validate_decomposition_manifest(self, manifest: Any) -> dict[str, Any]:
        if not isinstance(manifest, dict) or set(manifest) != {
            "initiative",
            "cards",
            "dependencies",
        }:
            raise ControlError(
                "invalid", "manifest must contain only initiative, cards, and dependencies"
            )
        initiative = manifest["initiative"]
        cards = manifest["cards"]
        dependencies = manifest["dependencies"]
        if not isinstance(initiative, dict) or set(initiative) != {"title", "brief"}:
            raise ControlError("invalid", "initiative requires only title and brief")
        initiative_title = compact(str(initiative.get("title", "")), 160)
        initiative_brief = bounded(str(initiative.get("brief", "")), 8000)
        if not initiative_title or not initiative_brief:
            raise ControlError("invalid", "initiative title and brief are required")
        if not isinstance(cards, list) or len(cards) < 2:
            raise ControlError("invalid", "decomposition requires at least two cards")
        if not isinstance(dependencies, list):
            raise ControlError("invalid", "dependencies must be an array")
        normalized_cards: list[dict[str, Any]] = []
        known_keys: set[str] = set()
        allowed_card_fields = {"key", "title", "brief", "repository", "decomposition_reason"}
        for index, card in enumerate(cards):
            if not isinstance(card, dict) or not set(card).issubset(allowed_card_fields):
                raise ControlError("invalid", f"card {index + 1} contains invalid fields")
            if not {"key", "title", "brief", "repository"}.issubset(card):
                raise ControlError(
                    "invalid", f"card {index + 1} requires key, title, brief, and repository"
                )
            key = str(card["key"]).strip()
            if not CARD_KEY_PATTERN.fullmatch(key) or key in known_keys:
                raise ControlError("invalid", f"card {index + 1} has an invalid or duplicate key")
            known_keys.add(key)
            title = compact(str(card["title"]), 160)
            brief = bounded(str(card["brief"]), 8000)
            repository = str(Path(str(card["repository"])).expanduser().resolve())
            reason = bounded(str(card.get("decomposition_reason", "")), 2000) or None
            if not title or not brief or not str(card["repository"]).strip():
                raise ControlError("invalid", f"card {key} has empty required content")
            if len(cards) > 3 and not reason:
                raise ControlError(
                    "invalid",
                    f"card {key} requires decomposition_reason when creating more than three cards",
                )
            normalized_cards.append(
                {
                    "key": key,
                    "title": title,
                    "brief": brief,
                    "repository": repository,
                    "decomposition_reason": reason,
                }
            )
        normalized_dependencies: list[dict[str, str]] = []
        pairs: set[tuple[str, str]] = set()
        adjacency: dict[str, set[str]] = {key: set() for key in known_keys}
        for index, dependency in enumerate(dependencies):
            if not isinstance(dependency, dict) or set(dependency) != {
                "task",
                "blocked_by",
                "condition",
                "reason",
            }:
                raise ControlError("invalid", f"dependency {index + 1} has invalid fields")
            task_key = str(dependency["task"]).strip()
            blocker_key = str(dependency["blocked_by"]).strip()
            condition = str(dependency["condition"]).strip()
            reason = bounded(str(dependency["reason"]), 2000)
            if task_key not in known_keys or blocker_key not in known_keys:
                raise ControlError("invalid", f"dependency {index + 1} references an unknown key")
            if task_key == blocker_key:
                raise ControlError("invalid", "a card cannot block itself")
            pair = (task_key, blocker_key)
            if pair in pairs:
                raise ControlError("invalid", "duplicate dependency")
            if condition not in DEPENDENCY_CONDITIONS or not reason:
                raise ControlError("invalid", f"dependency {index + 1} requires condition and reason")
            pairs.add(pair)
            adjacency[blocker_key].add(task_key)
            normalized_dependencies.append(
                {
                    "task": task_key,
                    "blocked_by": blocker_key,
                    "condition": condition,
                    "reason": reason,
                }
            )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ControlError("invalid", "dependency graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependent in adjacency[key]:
                visit(dependent)
            visiting.remove(key)
            visited.add(key)

        for key in known_keys:
            visit(key)
        return {
            "initiative": {"title": initiative_title, "brief": initiative_brief},
            "cards": normalized_cards,
            "dependencies": normalized_dependencies,
        }

    def create_task(
        self,
        *,
        title: str,
        brief: str,
        source_harness: str,
        source_conversation: str = "",
        source_message: str = "",
        repository: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        title = compact(title, 160)
        brief = bounded(brief, 8000)
        repository = str(Path(repository).expanduser().resolve()) if repository else None
        if not title or not brief or not idempotency_key.strip():
            raise ControlError("invalid", "title, brief, and idempotency key are required")
        timestamp = now()
        task_id = str(uuid.uuid4())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                expected = (
                    title,
                    brief,
                    compact(source_harness, 80),
                    compact(source_conversation, 500),
                    compact(source_message, 500),
                    repository,
                )
                observed = tuple(
                    existing[key]
                    for key in (
                        "title",
                        "brief",
                        "source_harness",
                        "source_conversation",
                        "source_message",
                        "repository",
                    )
                )
                if observed != expected:
                    raise ControlError("invalid", "idempotency key conflicts with another task")
                return self._decorate(row_dict(existing))
            sequence = int(
                connection.execute(
                    "SELECT value FROM control_metadata WHERE key = 'next_short_sequence'"
                ).fetchone()[0]
            )
            short_id = sequence_to_short_id(sequence)
            rank = connection.execute(
                "SELECT COALESCE(MAX(rank), 0) + 1 FROM tasks WHERE disposition = 'open'"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO tasks (
                    id, short_id, title, brief, brief_revision, source_harness,
                    source_conversation, source_message, repository, rank,
                    disposition, idempotency_key, preparation_status,
                    transfer_generation, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'open', ?, 'draft', 0, ?, ?)
                """,
                (
                    task_id,
                    short_id,
                    title,
                    brief,
                    compact(source_harness, 80),
                    compact(source_conversation, 500),
                    compact(source_message, 500),
                    repository,
                    rank,
                    idempotency_key,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE control_metadata SET value = ? WHERE key = 'next_short_sequence'",
                (str(sequence + 1),),
            )
            created = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            )
        return self._decorate(created)

    def decompose_task(
        self,
        *,
        task_ref: str,
        manifest: Any,
        confirmed: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ControlError("invalid", "decomposition requires explicit confirmation")
        key = idempotency_key.strip()
        if not key:
            raise ControlError("invalid", "idempotency key is required")
        normalized = self._validate_decomposition_manifest(manifest)
        manifest_digest = digest(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM task_initiatives WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["manifest_digest"] != manifest_digest:
                    raise ControlError("invalid", "idempotency key conflicts with another initiative")
                source = self._task_row(connection, task_ref)
                if existing["source_task_id"] != source["id"]:
                    raise ControlError("invalid", "idempotency key conflicts with another source task")
                cards = [
                    self._decorate_relations(connection, row_dict(row))
                    for row in connection.execute(
                        "SELECT * FROM tasks WHERE initiative_id = ? ORDER BY rank, created_at, id",
                        (existing["id"],),
                    ).fetchall()
                ]
                return {
                    "initiative": self._initiative_payload(connection, existing["id"]),
                    "cards": cards,
                    "key_map": {
                        card_manifest["key"]: cards[index]["short_id"]
                        for index, card_manifest in enumerate(normalized["cards"])
                    },
                }
            source = row_dict(self._task_row(connection, task_ref))
            if source["disposition"] != "open" or source["preparation_status"] != "draft":
                raise ControlError("busy", "only an open draft task can be decomposed")
            if source.get("initiative_id"):
                raise ControlError("busy", "task already belongs to an initiative")
            initiative_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO task_initiatives (
                    id, title, brief, source_task_id, idempotency_key, manifest_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    initiative_id,
                    normalized["initiative"]["title"],
                    normalized["initiative"]["brief"],
                    source["id"],
                    key,
                    manifest_digest,
                    timestamp,
                ),
            )
            first = normalized["cards"][0]
            connection.execute(
                """
                UPDATE tasks SET title = ?, brief = ?, brief_revision = brief_revision + 1,
                    repository = ?, initiative_id = ?, decomposition_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    first["title"],
                    first["brief"],
                    first["repository"],
                    initiative_id,
                    first["decomposition_reason"],
                    timestamp,
                    source["id"],
                ),
            )
            key_to_id = {first["key"]: source["id"]}
            key_to_short_id = {first["key"]: source["short_id"]}
            sequence = int(
                connection.execute(
                    "SELECT value FROM control_metadata WHERE key = 'next_short_sequence'"
                ).fetchone()[0]
            )
            rank = int(
                connection.execute(
                    "SELECT COALESCE(MAX(rank), 0) FROM tasks WHERE disposition = 'open'"
                ).fetchone()[0]
            )
            for index, card in enumerate(normalized["cards"][1:], start=1):
                task_id = str(uuid.uuid4())
                short_id = sequence_to_short_id(sequence)
                sequence += 1
                rank += 1
                key_to_id[card["key"]] = task_id
                key_to_short_id[card["key"]] = short_id
                connection.execute(
                    """
                    INSERT INTO tasks (
                        id, short_id, title, brief, brief_revision, source_harness,
                        source_conversation, source_message, repository, rank,
                        disposition, idempotency_key, preparation_status,
                        transfer_generation, initiative_id, decomposition_reason,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'open', ?, 'draft', 0, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        short_id,
                        card["title"],
                        card["brief"],
                        source["source_harness"],
                        source["source_conversation"],
                        source["source_message"],
                        card["repository"],
                        rank,
                        f"decompose:{initiative_id}:{card['key']}",
                        initiative_id,
                        card["decomposition_reason"],
                        timestamp,
                        timestamp,
                    ),
                )
            for dependency in normalized["dependencies"]:
                connection.execute(
                    """
                    INSERT INTO task_dependencies (
                        task_id, blocked_by_task_id, condition, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        key_to_id[dependency["task"]],
                        key_to_id[dependency["blocked_by"]],
                        dependency["condition"],
                        dependency["reason"],
                        timestamp,
                    ),
                )
            connection.execute(
                "UPDATE control_metadata SET value = ? WHERE key = 'next_short_sequence'",
                (str(sequence),),
            )
            cards = [
                self._decorate_relations(connection, row_dict(row))
                for row in connection.execute(
                    "SELECT * FROM tasks WHERE initiative_id = ? ORDER BY rank, created_at, id",
                    (initiative_id,),
                ).fetchall()
            ]
            return {
                "initiative": self._initiative_payload(connection, initiative_id),
                "cards": cards,
                "key_map": key_to_short_id,
            }

    def get_task(self, task_ref: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = self._task_row(connection, task_ref)
            result = row_dict(row)
            result["notes"] = [
                row_dict(item)
                for item in connection.execute(
                    "SELECT * FROM task_notes WHERE task_id = ? ORDER BY created_at, id",
                    (row["id"],),
                ).fetchall()
            ]
            latest = connection.execute(
                "SELECT * FROM runs WHERE task_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            result["legacy_latest_run"] = row_dict(latest) if latest is not None else None
            return self._decorate_relations(connection, result)

    def list_tasks(
        self, include_archived: bool = False, include_trashed: bool = False
    ) -> list[dict[str, Any]]:
        dispositions = ["open"]
        if include_archived:
            dispositions.append("archived")
        if include_trashed:
            dispositions.append("trashed")
        placeholders = ", ".join("?" for _ in dispositions)
        with self._connection() as connection:
            tasks = [
                row_dict(row)
                for row in connection.execute(
                    f"SELECT * FROM tasks WHERE disposition IN ({placeholders}) "
                    "ORDER BY rank, created_at, id",
                    dispositions,
                ).fetchall()
            ]
            for task in tasks:
                latest = connection.execute(
                    "SELECT * FROM runs WHERE task_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                    (task["id"],),
                ).fetchone()
                task["legacy_latest_run"] = row_dict(latest) if latest is not None else None
                self._decorate_relations(connection, task)
            return tasks

    def add_note(
        self,
        *,
        task_id: str,
        body: str,
        source_harness: str,
        source_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        body = bounded(body, 8000)
        if not body or not idempotency_key.strip():
            raise ControlError("invalid", "note body and idempotency key are required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._task_row(connection, task_id)
            if task["disposition"] == "trashed":
                raise ControlError("invalid", "trashed task must be restored before adding notes")
            existing = connection.execute(
                "SELECT * FROM task_notes WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                expected = (
                    task["id"],
                    body,
                    compact(source_harness, 80),
                    compact(source_reference, 500),
                )
                observed = tuple(
                    existing[key]
                    for key in ("task_id", "body", "source_harness", "source_reference")
                )
                if observed != expected:
                    raise ControlError("invalid", "idempotency key conflicts with another note")
                return row_dict(existing)
            note_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO task_notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    note_id,
                    task["id"],
                    body,
                    compact(source_harness, 80),
                    compact(source_reference, 500),
                    idempotency_key,
                    now(),
                ),
            )
            return row_dict(
                connection.execute("SELECT * FROM task_notes WHERE id = ?", (note_id,)).fetchone()
            )

    def update_draft(
        self,
        *,
        task_ref: str,
        title: str,
        brief: str,
        repository: str | None | object = REPOSITORY_UNSET,
    ) -> dict[str, Any]:
        title = compact(title, 160)
        brief = bounded(brief, 8000)
        if not title or not brief:
            raise ControlError("invalid", "title and brief are required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] != "open" or task["preparation_status"] != "draft":
                raise ControlError("busy", "only an open draft task can be edited")
            if task.get("initiative_id"):
                raise ControlError("busy", "initiative cards have an immutable confirmed manifest")
            if repository is REPOSITORY_UNSET:
                canonical_repository = task.get("repository")
            elif isinstance(repository, str) and repository.strip():
                canonical_repository = str(Path(repository).expanduser().resolve())
            else:
                canonical_repository = None
            connection.execute(
                """
                UPDATE tasks SET title = ?, brief = ?, repository = ?,
                    brief_revision = brief_revision + 1, updated_at = ? WHERE id = ?
                """,
                (title, brief, canonical_repository, now(), task["id"]),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def prepare_task(
        self,
        *,
        task_ref: str,
        repository: str,
        prepared_revision: str,
        repository_common_dir: str | None = None,
        repository_context: str,
        specification: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        context = repository_context.strip()
        spec = specification.strip()
        if not context or not spec:
            raise ControlError("invalid", "repository context and specification are required")
        if not confirmed:
            raise ControlError("invalid", "ready preparation requires confirmed specification")
        revision = validate_revision(prepared_revision)
        canonical_repository = str(Path(repository).expanduser().resolve())
        canonical_common_dir = (
            str(Path(repository_common_dir).expanduser().resolve())
            if repository_common_dir
            else None
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] != "open":
                raise ControlError("invalid", "archived task must be restored before preparation")
            if task["preparation_status"] == "cancelled":
                raise ControlError(
                    "busy",
                    "cancelled task must be reopened and resumed, not prepared again",
                )
            if task["preparation_status"] in ("adopted", "completed"):
                raise ControlError("busy", "adopted or completed task cannot be prepared again")
            if task.get("adopted_at") or task.get("previous_thread_id"):
                raise ControlError(
                    "busy",
                    "task with execution history must resume its existing checkout and plan",
                )
            self._documents_directory(str(task["short_id"]), create=True)
            documents = self._document_payload(task)
            self._write_private(Path(documents["repository_context"]), context + "\n")
            self._write_private(Path(documents["specification"]), spec + "\n")
            timestamp = now()
            context_digest = digest(context + "\n")
            specification_digest = digest(spec + "\n")
            marker = {
                "id": task["id"],
                "short_id": task["short_id"],
                "prepared_revision": revision,
                "repository_context_digest": context_digest,
                "specification_digest": specification_digest,
                "specification_confirmed_at": timestamp,
            }
            self._write_private(
                Path(documents["marker"]),
                json.dumps(marker, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
            connection.execute(
                """
                UPDATE tasks SET repository = ?, preparation_status = 'ready',
                    prepared_revision = ?, repository_common_dir = ?, repository_context_digest = ?,
                    specification_digest = ?, specification_confirmed_at = ?,
                    updated_at = ? WHERE id = ?
                """,
                (
                    canonical_repository,
                    revision,
                    canonical_common_dir,
                    context_digest,
                    specification_digest,
                    timestamp,
                    timestamp,
                    task["id"],
                ),
            )
            prepared = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
        return self._decorate(prepared)

    def adopt_task(
        self,
        *,
        task_ref: str,
        thread_id: str | None,
        repository: str,
        current_revision: str,
        repository_common_dir: str | None = None,
        ancestor_contains: Callable[[str], bool] | None = None,
        repository_equivalent: Callable[[str], bool] | None = None,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        revision = validate_revision(current_revision)
        canonical_repository = str(Path(repository).expanduser().resolve())
        canonical_common_dir = (
            str(Path(repository_common_dir).expanduser().resolve())
            if repository_common_dir
            else None
        )
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["preparation_status"] == "legacy":
                raise ControlError("invalid", "legacy task cannot be adopted")
            if task["preparation_status"] == "draft":
                raise ControlError("invalid", "task is not ready; confirm its prepared specification first")
            if task["preparation_status"] == "completed":
                raise ControlError("invalid", "completed task cannot be adopted")
            if task["preparation_status"] == "cancelled":
                raise ControlError("invalid", "cancelled task must be reopened before adoption")
            if task["disposition"] != "open":
                raise ControlError("invalid", "task must be restored before adoption")
            owner = owner_identity(task)
            previous_owner = owner_identity(task, "previous")
            if not owner and previous_owner == identity and not task.get("cancelled_at"):
                raise ControlError(
                    "invalid",
                    "transferred task must be adopted from a different native host chat",
                )
            if owner and owner != identity:
                raise ControlError("busy", "task is adopted by another native host chat")
            normalize_repository = self._require_adoptable_repository(
                connection,
                task,
                canonical_repository=canonical_repository,
                canonical_common_dir=canonical_common_dir,
                ancestor_contains=ancestor_contains,
                repository_equivalent=repository_equivalent,
            )
            if normalize_repository:
                connection.execute(
                    "UPDATE tasks SET repository = ?, repository_common_dir = ? WHERE id = ?",
                    (canonical_repository, canonical_common_dir, task["id"]),
                )
            if not owner:
                connection.execute(
                    """
                    UPDATE tasks SET preparation_status = 'adopted',
                        adopted_harness = ?, adopted_thread_id = ?,
                        adopted_revision = ?, adopted_at = ?,
                        transfer_requested_at = NULL, cancelled_at = NULL,
                        updated_at = ? WHERE id = ?
                    """,
                    (identity[0], thread, revision, timestamp, timestamp, task["id"]),
                )
            adopted = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            adopted = self._decorate_relations(
                connection,
                adopted,
                current_common_dir=canonical_common_dir,
                ancestor_contains=ancestor_contains,
            )
        adopted["context_action"] = (
            "use_prepared" if revision == adopted["prepared_revision"] else "repository_context_delta"
        )
        adopted["resume_existing_checkout"] = bool(adopted.get("previous_thread_id"))
        return adopted

    def _require_adoptable_repository(
        self,
        connection: sqlite3.Connection,
        task: dict[str, Any],
        *,
        canonical_repository: str,
        canonical_common_dir: str | None,
        ancestor_contains: Callable[[str], bool] | None,
        repository_equivalent: Callable[[str], bool] | None,
    ) -> bool:
        normalize_repository = False
        if task.get("repository_common_dir") and canonical_common_dir:
            if canonical_common_dir != task["repository_common_dir"]:
                raise ControlError("invalid", "task was prepared for a different Git repository")
        elif task.get("repository") and task["repository"] != canonical_repository:
            if repository_equivalent is None or not repository_equivalent(task["repository"]):
                raise ControlError("invalid", "task was prepared for a different repository")
            normalize_repository = canonical_common_dir is not None
        elif canonical_common_dir and not task.get("repository_common_dir"):
            normalize_repository = True
        dependencies = self._dependency_views(
            connection,
            task,
            current_common_dir=canonical_common_dir,
            ancestor_contains=ancestor_contains,
        )
        blockers = [item for item in dependencies if not item["satisfied"]]
        if blockers:
            labels = ", ".join(
                f"{item['short_id']} ({item['state']})" for item in blockers
            )
            raise ControlError("blocked", f"task dependencies are not satisfied: {labels}")
        self._verify_prepared_documents(task)
        return normalize_repository

    def reclaim_task(
        self,
        *,
        task_ref: str,
        thread_id: str | None,
        repository: str,
        current_revision: str,
        authorized: bool,
        repository_common_dir: str | None = None,
        ancestor_contains: Callable[[str], bool] | None = None,
        repository_equivalent: Callable[[str], bool] | None = None,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        if not authorized:
            raise ControlError("invalid", "reclaim requires explicit authorization")
        revision = validate_revision(current_revision)
        canonical_repository = str(Path(repository).expanduser().resolve())
        canonical_common_dir = (
            str(Path(repository_common_dir).expanduser().resolve())
            if repository_common_dir
            else None
        )
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            owner = owner_identity(task)
            if task["preparation_status"] != "adopted" or not owner:
                raise ControlError(
                    "invalid",
                    "only an adopted task owned by another chat can be reclaimed",
                )
            if owner == identity:
                raise ControlError(
                    "invalid",
                    "already owned by this chat; continue instead of reclaim",
                )
            normalize_repository = self._require_adoptable_repository(
                connection,
                task,
                canonical_repository=canonical_repository,
                canonical_common_dir=canonical_common_dir,
                ancestor_contains=ancestor_contains,
                repository_equivalent=repository_equivalent,
            )
            if normalize_repository:
                connection.execute(
                    "UPDATE tasks SET repository = ?, repository_common_dir = ? WHERE id = ?",
                    (canonical_repository, canonical_common_dir, task["id"]),
                )
            connection.execute(
                """
                UPDATE tasks SET previous_harness = adopted_harness,
                    previous_thread_id = adopted_thread_id,
                    adopted_harness = ?, adopted_thread_id = ?,
                    adopted_revision = ?, adopted_at = ?,
                    transfer_generation = transfer_generation + 1,
                    transfer_requested_at = ?, updated_at = ? WHERE id = ?
                """,
                (identity[0], thread, revision, timestamp, timestamp, timestamp, task["id"]),
            )
            reclaimed = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            reclaimed = self._decorate_relations(
                connection,
                reclaimed,
                current_common_dir=canonical_common_dir,
                ancestor_contains=ancestor_contains,
            )
        reclaimed["context_action"] = (
            "use_prepared" if revision == reclaimed["prepared_revision"] else "repository_context_delta"
        )
        reclaimed["resume_existing_checkout"] = True
        return reclaimed

    def transfer_task(
        self,
        *,
        task_ref: str,
        thread_id: str | None,
        stable_checkpoint: bool,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        if not stable_checkpoint:
            raise ControlError("invalid", "transfer requires an explicit stable checkpoint")
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["preparation_status"] != "adopted" or owner_identity(task) != identity:
                raise ControlError("invalid", "only the adopting chat can transfer an active task")
            if task.get("stop_requested_at"):
                raise ControlError("blocked", "task has a pending safe-stop request")
            connection.execute(
                """
                UPDATE tasks SET preparation_status = 'ready',
                    previous_harness = adopted_harness,
                    previous_thread_id = adopted_thread_id,
                    adopted_harness = NULL, adopted_thread_id = NULL,
                    transfer_generation = transfer_generation + 1,
                    transfer_requested_at = ?, updated_at = ? WHERE id = ?
                """,
                (timestamp, timestamp, task["id"]),
            )
            transferred = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
        return self._decorate(transferred)

    def finish_task(
        self,
        *,
        task_ref: str,
        thread_id: str | None,
        repository: str | None = None,
        terminal_revision: str | None = None,
        repository_common_dir: str | None = None,
        repository_equivalent: Callable[[str], bool] | None = None,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        revision = validate_revision(terminal_revision) if terminal_revision else None
        canonical_repository = (
            str(Path(repository).expanduser().resolve()) if repository else None
        )
        canonical_common_dir = (
            str(Path(repository_common_dir).expanduser().resolve())
            if repository_common_dir
            else None
        )
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task.get("stop_requested_at"):
                raise ControlError("blocked", "task has a pending safe-stop request")
            if task["preparation_status"] == "completed":
                if owner_identity(task) != identity:
                    raise ControlError("busy", "task was completed by another chat")
                if not revision:
                    return self._decorate_relations(connection, task)
                if task.get("delivered_at"):
                    if task.get("completed_revision") != revision:
                        raise ControlError("busy", "delivered task revision cannot be changed")
                    return self._decorate_relations(connection, task)
                if (
                    task.get("repository_common_dir")
                    and canonical_common_dir != task.get("repository_common_dir")
                ):
                    raise ControlError("invalid", "task belongs to a different Git repository")
                if (
                    not task.get("repository_common_dir")
                    and canonical_repository
                    and task.get("repository") != canonical_repository
                    and (
                        repository_equivalent is None
                        or not task.get("repository")
                        or not repository_equivalent(task["repository"])
                    )
                ):
                    raise ControlError("invalid", "task belongs to a different repository")
                connection.execute(
                    """
                    UPDATE tasks SET completed_revision = ?,
                        repository = COALESCE(?, repository),
                        repository_common_dir = COALESCE(?, repository_common_dir),
                        updated_at = ? WHERE id = ?
                    """,
                    (
                        revision,
                        canonical_repository,
                        canonical_common_dir,
                        timestamp,
                        task["id"],
                    ),
                )
                updated = row_dict(
                    connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
                )
                return self._decorate_relations(connection, updated)
            if task["preparation_status"] != "adopted" or owner_identity(task) != identity:
                raise ControlError("invalid", "only the adopting chat can finish the task")
            if not revision or not canonical_repository or not canonical_common_dir:
                raise ControlError(
                    "invalid", "finish requires repository, terminal revision, and Git common-dir"
                )
            if (
                task.get("repository_common_dir")
                and canonical_common_dir != task.get("repository_common_dir")
            ):
                raise ControlError("invalid", "task belongs to a different Git repository")
            if (
                not task.get("repository_common_dir")
                and task.get("repository") != canonical_repository
                and (
                    repository_equivalent is None
                    or not task.get("repository")
                    or not repository_equivalent(task["repository"])
                )
            ):
                raise ControlError("invalid", "task belongs to a different repository")
            connection.execute(
                """
                UPDATE tasks SET preparation_status = 'completed', completed_at = ?,
                    completed_revision = ?, repository = ?, repository_common_dir = ?,
                    updated_at = ? WHERE id = ?
                """,
                (
                    timestamp,
                    revision,
                    canonical_repository,
                    canonical_common_dir,
                    timestamp,
                    task["id"],
                ),
            )
            completed = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
        return self._decorate(completed)

    def record_delivery(
        self,
        *,
        task_ref: str,
        thread_id: str | None,
        repository: str,
        task_revision: str,
        delivery_revision: str,
        kind: str,
        repository_common_dir: str | None = None,
        repository_equivalent: Callable[[str], bool] | None = None,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        terminal = validate_revision(task_revision)
        delivered = validate_revision(delivery_revision)
        delivery_kind = kind.strip()
        if delivery_kind not in DELIVERY_KINDS:
            raise ControlError("invalid", "delivery kind must be local-integration or pr-merge")
        canonical_repository = str(Path(repository).expanduser().resolve())
        canonical_common_dir = (
            str(Path(repository_common_dir).expanduser().resolve())
            if repository_common_dir
            else None
        )
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["preparation_status"] != "completed":
                raise ControlError("invalid", "only a completed task can record delivery")
            if owner_identity(task) != identity:
                raise ControlError("invalid", "only the owning native host chat can record delivery")
            if task.get("completed_revision") != terminal:
                raise ControlError("invalid", "task revision does not match the terminal revision")
            if (
                task.get("repository_common_dir")
                and canonical_common_dir != task.get("repository_common_dir")
            ):
                raise ControlError("invalid", "delivery repository does not match the task Git repository")
            if (
                not task.get("repository_common_dir")
                and task.get("repository") != canonical_repository
                and (
                    repository_equivalent is None
                    or not task.get("repository")
                    or not repository_equivalent(task["repository"])
                )
            ):
                raise ControlError("invalid", "delivery repository does not match the task")
            existing = (
                task.get("delivered_task_revision"),
                task.get("delivery_revision"),
                task.get("delivery_kind"),
            )
            requested = (terminal, delivered, delivery_kind)
            if task.get("delivered_at"):
                if existing != requested:
                    raise ControlError("busy", "task already has different delivery evidence")
                if canonical_common_dir:
                    connection.execute(
                        "UPDATE tasks SET repository = ?, repository_common_dir = ? WHERE id = ?",
                        (canonical_repository, canonical_common_dir, task["id"]),
                    )
                    task = row_dict(
                        connection.execute(
                            "SELECT * FROM tasks WHERE id = ?", (task["id"],)
                        ).fetchone()
                    )
                return self._decorate_relations(connection, task)
            connection.execute(
                """
                UPDATE tasks SET delivered_task_revision = ?, delivery_revision = ?,
                    delivery_kind = ?, delivered_at = ?,
                    repository = ?, repository_common_dir = COALESCE(?, repository_common_dir),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    terminal,
                    delivered,
                    delivery_kind,
                    timestamp,
                    canonical_repository,
                    canonical_common_dir,
                    timestamp,
                    task["id"],
                ),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def set_archived(self, task_ref: str, archived: bool) -> dict[str, Any]:
        disposition = "archived" if archived else "open"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] == "trashed":
                raise ControlError("invalid", "trashed task must be restored before archiving")
            if not archived and task["disposition"] != "archived":
                raise ControlError("invalid", "task is not archived")
            if archived and task["preparation_status"] == "adopted":
                raise ControlError("busy", "adopted task must be finished or transferred first")
            connection.execute(
                "UPDATE tasks SET disposition = ?, updated_at = ? WHERE id = ?",
                (disposition, now(), task["id"]),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def set_trashed(self, task_ref: str, trashed: bool) -> dict[str, Any]:
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if trashed:
                if task["disposition"] == "trashed":
                    return self._decorate_relations(connection, task)
                if task["preparation_status"] == "adopted":
                    raise ControlError("busy", "adopted task requires a safe stop before trashing")
                connection.execute(
                    """
                    UPDATE tasks SET disposition_before_trash = disposition,
                        disposition = 'trashed', trashed_at = ?, updated_at = ? WHERE id = ?
                    """,
                    (timestamp, timestamp, task["id"]),
                )
            else:
                if task["disposition"] != "trashed":
                    raise ControlError("invalid", "task is not in trash")
                destination = task.get("disposition_before_trash") or "open"
                connection.execute(
                    """
                    UPDATE tasks SET disposition = ?, disposition_before_trash = NULL,
                        trashed_at = NULL, updated_at = ? WHERE id = ?
                    """,
                    (destination, timestamp, task["id"]),
                )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def request_stop(self, task_ref: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] != "open" or task["preparation_status"] != "adopted":
                raise ControlError("invalid", "only an open adopted task can request a safe stop")
            if not task.get("stop_requested_at"):
                timestamp = now()
                connection.execute(
                    "UPDATE tasks SET stop_requested_at = ?, updated_at = ? WHERE id = ?",
                    (timestamp, timestamp, task["id"]),
                )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def withdraw_stop(self, task_ref: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] != "open" or task["preparation_status"] != "adopted":
                raise ControlError("invalid", "only an open adopted task has a stop request")
            if not task.get("stop_requested_at"):
                raise ControlError("invalid", "task has no pending stop request")
            connection.execute(
                "UPDATE tasks SET stop_requested_at = NULL, updated_at = ? WHERE id = ?",
                (now(), task["id"]),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def acknowledge_stop(
        self,
        task_ref: str,
        thread_id: str | None,
        source_harness: str | None = "codex",
    ) -> dict[str, Any]:
        thread = validate_thread_id(thread_id, source_harness)
        identity = (str(source_harness), thread)
        timestamp = now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["preparation_status"] != "adopted" or owner_identity(task) != identity:
                raise ControlError("invalid", "only the adopting chat can acknowledge a safe stop")
            if not task.get("stop_requested_at"):
                raise ControlError("invalid", "task has no pending stop request")
            connection.execute(
                """
                UPDATE tasks SET preparation_status = 'cancelled',
                    previous_harness = adopted_harness,
                    previous_thread_id = adopted_thread_id,
                    adopted_harness = NULL, adopted_thread_id = NULL,
                    stop_requested_at = NULL, transfer_requested_at = NULL,
                    cancelled_at = ?, updated_at = ? WHERE id = ?
                """,
                (timestamp, timestamp, task["id"]),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def reopen_cancelled(self, task_ref: str) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if task["disposition"] != "open" or task["preparation_status"] != "cancelled":
                raise ControlError("invalid", "only an open cancelled task can be reopened")
            connection.execute(
                "UPDATE tasks SET preparation_status = 'ready', updated_at = ? WHERE id = ?",
                (now(), task["id"]),
            )
            updated = row_dict(
                connection.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            )
            return self._decorate_relations(connection, updated)

    def purge_task(self, task_ref: str, confirmation: str) -> dict[str, Any]:
        quarantined: tuple[Path, Path] | None = None
        try:
            root = resolve_state_root(self.state_root)
            connection = connect(self.state_root)
        except StorageError as error:
            raise ControlError("unavailable", str(error)) from error
        try:
            connection.execute("BEGIN IMMEDIATE")
            task = row_dict(self._task_row(connection, task_ref))
            if confirmation.strip().upper() != str(task.get("short_id") or "").upper():
                raise ControlError("invalid", "purge confirmation must match the short task ID")
            blockers = self._purge_blockers(connection, task)
            if blockers:
                raise ControlError("busy", "; ".join(blockers))
            documents = self._documents_directory(str(task["short_id"]), create=False)
            if documents.exists():
                try:
                    mode = os.lstat(documents).st_mode
                except OSError as error:
                    raise ControlError(
                        "unavailable", f"cannot inspect task documents: {error}"
                    ) from error
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ControlError(
                        "unavailable", f"unsafe task documents directory: {documents}"
                    )
                quarantine_root = root / ".purge-quarantine"
                try:
                    quarantine_root.mkdir(mode=0o700, exist_ok=True)
                    quarantine_mode = os.lstat(quarantine_root).st_mode
                    if stat.S_ISLNK(quarantine_mode) or not stat.S_ISDIR(quarantine_mode):
                        raise OSError("quarantine root is not a private directory")
                    if stat.S_IMODE(quarantine_mode) != 0o700:
                        os.chmod(quarantine_root, 0o700)
                    quarantine = quarantine_root / uuid.uuid4().hex
                    os.replace(documents, quarantine)
                except OSError as error:
                    raise ControlError(
                        "unavailable", f"cannot quarantine task documents: {error}"
                    ) from error
                quarantined = (quarantine, documents)
            connection.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ControlError("unavailable", "purge would leave invalid foreign keys")
            connection.commit()
        except ControlError as error:
            connection.rollback()
            if quarantined is not None and quarantined[0].exists():
                try:
                    os.replace(quarantined[0], quarantined[1])
                except OSError as restore_error:
                    raise ControlError(
                        "unavailable",
                        f"{error.reason}; task documents could not be restored: {restore_error}",
                    ) from restore_error
            raise
        except (OSError, sqlite3.Error) as error:
            connection.rollback()
            if quarantined is not None and quarantined[0].exists():
                try:
                    os.replace(quarantined[0], quarantined[1])
                except OSError as restore_error:
                    raise ControlError(
                        "unavailable",
                        f"purge failed and task documents could not be restored: {restore_error}",
                    ) from error
            raise ControlError("unavailable", f"cannot purge task: {error}") from error
        finally:
            connection.close()
        warning: str | None = None
        if quarantined is not None:
            try:
                shutil.rmtree(quarantined[0])
            except OSError as error:
                warning = f"task was purged, but quarantined documents remain: {error}"
            else:
                try:
                    quarantined[0].parent.rmdir()
                except OSError:
                    pass
        result = {"id": task["id"], "short_id": task["short_id"], "purged": True}
        if warning is not None:
            result["warning"] = warning
        return result
