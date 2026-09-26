"""Server-rendered HTML panel (SPEC §9, §11)."""
from __future__ import annotations

import html
from datetime import datetime
import posixpath

from orchestra_hub.api import parse_timestamp

_MAIN_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>Orchestra Hub</title>
  <style>
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.85rem; margin: 1.5rem; color: #d0d7de;
            background: #0d1117; }}
    h1 {{ font-size: 1.25rem; color: #e6edf3; }}
    h2 {{ font-size: 1.05rem; color: #e6edf3; }}
    h3 {{ font-size: 1rem; margin: 0; color: #e6edf3; }}
    .empty {{ font-style: italic; color: #8b949e; }}
    .attention {{ margin: 0.5rem 0 1rem; padding: 0.6rem 0.85rem;
                  background: #161b22; border: 1px solid #21262d;
                  border-left: 3px solid #f85149; }}
    .attention strong {{ color: #f85149; }}
    .repository {{ margin-top: 1.5rem; border-top: 1px solid #30363d; }}
    .repository-header {{ display: flex; gap: 0.75rem; align-items: baseline;
                          padding: 0.75rem 0 0.45rem; }}
    .repository-meta, .repository-path {{ color: #8b949e; }}
    .repository-path {{ margin: 0 0 0.5rem; }}
    details.task {{ border-top: 1px solid #21262d; }}
    details.task:last-child {{ border-bottom: 1px solid #21262d; }}
    details.task summary {{ cursor: pointer; padding: 0.55rem 0.25rem;
                            color: #d0d7de; }}
    details.task summary:hover {{ background: #161b22; }}
    .status {{ display: inline-block; width: 1.1rem; text-align: center; }}
    .status-red {{ color: #f85149; }}
    .status-orange {{ color: #d29922; }}
    .status-yellow {{ color: #e3b341; }}
    .status-blue {{ color: #58a6ff; }}
    .status-green {{ color: #3fb950; }}
    .status-muted, .worktree, .initiative {{ color: #8b949e; }}
    .task-id {{ color: #e6edf3; }}
    dl {{ display: grid; grid-template-columns: 9rem minmax(0, 1fr); gap: 0.3rem 1rem;
          margin: 0; padding: 0.35rem 1.35rem 0.8rem; }}
    dt {{ color: #8b949e; }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>Orchestra Hub</h1>
  {body}
</body>
</html>
"""

_DEGRADED_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>Orchestra Hub — degraded</title>
  <style>
    body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.85rem; margin: 1.5rem; color: #d0d7de;
            background: #0d1117; }}
    h1 {{ font-size: 1.25rem; color: #e6edf3; }}
    strong {{ color: #d29922; }}
    code {{ color: #f85149; }}
  </style>
</head>
<body>
  <h1>Orchestra Hub</h1>
  <p><strong>Degraded</strong>: database condition
    <code>{condition}</code>.</p>
  <p>{detail}</p>
  <p>Orchestra itself is unaffected.</p>
</body>
</html>
"""


def _age(updated_at: object, now: datetime) -> str:
    stamp = parse_timestamp(str(updated_at))
    minutes = int((now - stamp).total_seconds() // 60)
    return f"{minutes} min ago"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _compact_middle(value: str, limit: int = 16) -> str:
    if len(value) <= limit:
        return value
    left = (limit - 1) // 2
    right = limit - left - 1
    return value[:left] + "…" + value[-right:]


def _worktree_name(task: dict) -> str:
    repository = str(task.get("repository") or "")
    worktree = str(task.get("worktree") or "")
    if not worktree or (
        repository and posixpath.normpath(repository) == posixpath.normpath(worktree)
    ):
        return ""
    return _compact_middle(posixpath.basename(posixpath.normpath(worktree)))


def _status_tone(task: dict) -> str:
    if task.get("blocker"):
        return "red"
    if task.get("stop_requested_at"):
        return "orange"
    if task.get("stale"):
        return "yellow"
    status = str(task.get("status") or task.get("preparation_status") or "")
    if status in {"active", "adopted"}:
        return "blue"
    if status == "ready":
        return "green"
    if status in {"cancelled", "completed", "archived", "trashed"}:
        return "muted"
    return "yellow"


def _status_label(task: dict) -> str:
    if task.get("blocker"):
        return "Blocked"
    if task.get("stop_requested_at"):
        return "Stop requested"
    if task.get("stale"):
        return "Stale"
    status = str(task.get("status") or task.get("preparation_status") or "")
    if status in {"active", "adopted"}:
        return "Active"
    if status == "ready":
        return "Ready"
    if status in {"cancelled", "completed", "archived", "trashed"}:
        return "Inactive"
    return "Draft"


def _task_markup(task: dict, now: datetime) -> str:
    tone = _status_tone(task)
    pieces = [
        f'<span class="status status-{tone}" aria-label="{_escape(_status_label(task))}">●</span>'
    ]
    worktree_name = _worktree_name(task)
    if worktree_name:
        pieces.append(
            f'<span class="worktree" title="{_escape(task.get("worktree", ""))}">'
            f'[{_escape(worktree_name)}]</span>'
        )
    short_id = str(task.get("short_id") or "")
    if short_id:
        pieces.append(f'<strong class="task-id">{_escape(short_id)}</strong>')
    pieces.append(_escape(task.get("label", "")))
    initiative = str((task.get("initiative") or {}).get("title") or "")
    if initiative:
        pieces.append(f'<span class="initiative">[{_escape(initiative)}]</span>')

    blocked_by = ", ".join(
        str(item.get("short_id", ""))
        for item in task.get("blocked_by") or []
        if not item.get("satisfied")
    )
    parallel = ", ".join(
        str(item.get("short_id", "")) for item in task.get("parallel_with") or []
    )
    activity = ", ".join(
        f"{entry.get('capability', '')}: {entry.get('summary') or entry.get('state', '')}"
        for entry in task.get("current_activity") or []
    )
    values = (
        ("stage", task.get("stage")),
        ("status", task.get("status")),
        ("branch", task.get("branch")),
        ("worktree", task.get("worktree")),
        ("summary", task.get("summary")),
        ("blocker", task.get("blocker")),
        ("next action", task.get("next_action")),
        ("blocked by", blocked_by),
        ("parallel", parallel),
        ("activity", activity),
        ("updated", _age(task.get("updated_at", ""), now)),
    )
    details = "".join(
        f"<dt>{_escape(label)}</dt><dd>{_escape(value)}</dd>"
        for label, value in values
        if value
    )
    return (
        '<details class="task"><summary>'
        + " ".join(pieces)
        + f"</summary><dl>{details}</dl></details>"
    )


def render_degraded(condition: str, detail: str) -> str:
    return _DEGRADED_TEMPLATE.format(
        condition=_escape(condition),
        detail=_escape(detail),
    )


def render_panel(summary: dict, now: datetime) -> str:
    attention = list(summary.get("attention") or [])
    repositories = list(summary.get("repositories") or [])
    tasks = list(summary.get("tasks") or [])

    parts: list[str] = []
    parts.append(f"<h2>Possible attention ({len(attention)})</h2>")
    if not attention:
        parts.append('<p class="empty">No attention items.</p>')
    else:
        for entry in attention:
            label = _escape(entry.get("label", ""))
            reasons = _escape(", ".join(str(r) for r in entry.get("reasons") or []))
            blocker = entry.get("blocker") or ""
            next_action = entry.get("next_action") or ""
            action_text = blocker if blocker else next_action
            repository = _escape(entry.get("repository", ""))
            age = _escape(_age(entry.get("updated_at", ""), now))
            parts.append(
                '<div class="attention">'
                f"<div><strong>{label}</strong> [{reasons}]</div>"
                f"<div>{_escape(action_text)}</div>"
                f"<div>{repository}</div>"
                f"<div>last snapshot {age}</div>"
                "</div>"
            )

    parts.append("<h2>Repositories</h2>")
    if not repositories:
        parts.append('<p class="empty">No repositories.</p>')
    else:
        tasks_by_repository: dict[str, list[dict]] = {}
        for task in tasks:
            tasks_by_repository.setdefault(str(task.get("repository") or ""), []).append(task)
        for repo in repositories:
            flags = []
            if repo.get("pinned"):
                flags.append("pinned")
            if repo.get("observed"):
                flags.append("observed")
            path = str(repo.get("path") or "")
            counts = (
                f"{repo.get('active_tasks', 0)} active · "
                f"{repo.get('completed_tasks', 0)} completed"
            )
            meta = " · ".join(item for item in (counts, ", ".join(flags)) if item)
            task_markup = "".join(
                _task_markup(task, now) for task in tasks_by_repository.get(path, [])
            )
            if not task_markup:
                task_markup = '<p class="empty">No tasks.</p>'
            path_markup = (
                f'<p class="repository-path">{_escape(path)}</p>' if path else ""
            )
            parts.append(
                '<section class="repository">'
                '<div class="repository-header">'
                f"<h3>{_escape(repo.get('name', ''))}</h3>"
                f'<span class="repository-meta">{_escape(meta)}</span>'
                "</div>"
                f"{path_markup}"
                f"{task_markup}</section>"
            )

    return _MAIN_TEMPLATE.format(body="\n".join(parts))
