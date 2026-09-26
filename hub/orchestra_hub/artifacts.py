"""Read-only discovery from one worktree-local Orchestra artifact directory.

Orchestra artifacts have no database locator: they are UTF-8 Markdown files
named `<NN>-<kind>[-p<phase>].md` under the directory resolved by
`<worktree>/.orchestra/artifacts`. The Hub retains read compatibility with the
legacy Git-private location, resolves both layouts without running Git, and
reads names and mtimes only; artifact content is never read or served.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT_FIELDS = ("id", "kind", "phase", "created_at")
ARTIFACT_NAME = re.compile(
    r"^\d+-(?P<kind>[a-z0-9-]+?)(?:-p(?P<phase>\d+))?\.md$"
)
GITDIR_PREFIX = "gitdir:"
STATE_DIRECTORY = ".orchestra"
STATE_MARKER = ".gitignore"
STATE_MARKER_CONTENT = (
    "# Orchestra task-private state; removed after successful delivery.\n"
    "*\n"
)


def _legacy_artifacts_directory(worktree: Path) -> Path | None:
    marker = worktree / ".git"
    if marker.is_symlink():
        return None
    if marker.is_dir():
        return marker / "orchestra" / "artifacts"
    if not marker.is_file():
        return None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.startswith(GITDIR_PREFIX):
            continue
        gitdir = Path(line[len(GITDIR_PREFIX):].strip())
        if not str(gitdir):
            return None
        if not gitdir.is_absolute():
            gitdir = worktree / gitdir
        return gitdir / "orchestra" / "artifacts"
    return None


def artifacts_directory(worktree: Path) -> Path | None:
    """Resolve worktree-local artifacts, or an existing legacy directory."""
    legacy = _legacy_artifacts_directory(worktree)
    if legacy is None:
        return None
    private = worktree / STATE_DIRECTORY
    workspace_exists = private.exists() or private.is_symlink()
    legacy_exists = legacy.exists() or legacy.is_symlink()
    if workspace_exists and legacy_exists:
        return None
    if workspace_exists:
        ownership = private / STATE_MARKER
        if (
            private.is_symlink()
            or not private.is_dir()
            or ownership.is_symlink()
            or not ownership.is_file()
        ):
            return None
        try:
            if ownership.read_text(encoding="utf-8") != STATE_MARKER_CONTENT:
                return None
        except OSError:
            return None
        return private / "artifacts"
    if legacy_exists:
        return legacy
    return private / "artifacts"


def _created_at(path: Path) -> str:
    moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def artifact_entries(worktree: str) -> list[dict]:
    """List published artifacts for a task, newest ordinal first."""
    if not worktree:
        return []
    directory = artifacts_directory(Path(worktree))
    if directory is None or directory.is_symlink() or not directory.is_dir():
        return []
    entries: list[dict] = []
    try:
        candidates = sorted(directory.iterdir(), reverse=True)
    except OSError:
        return []
    for path in candidates:
        match = ARTIFACT_NAME.match(path.name)
        if match is None:
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            created_at = _created_at(path)
        except OSError:
            continue
        phase = match.group("phase")
        entries.append(
            {
                "id": path.name,
                "kind": match.group("kind"),
                "phase": int(phase) if phase else 0,
                "created_at": created_at,
            }
        )
    return entries
