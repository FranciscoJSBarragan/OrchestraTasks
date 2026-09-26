"""Textual dashboard for the Orchestra Hub (layout and wiring only)."""
from __future__ import annotations

import webbrowser
from datetime import datetime, timezone

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Footer, Static, Tree

from .client import FetchResult, HubClient, default_base_url
from .viewmodel import (
    build_tree,
    phase_progress,
    snapshot_age,
    status_tone,
    task_rows,
    worktree_name,
)

POLL_SECONDS = 5


class HubTuiApp(App):
    TITLE = "Orchestra Hub"

    CSS = """
    #hubstatus {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    #hubstatus.degraded { background: $error 30%; }
    #body { height: 1fr; }
    #body.degraded { opacity: 0.6; }
    #repos { width: 46; border-right: solid $panel; padding: 1 0 0 1; }
    #detail-scroll { padding: 1; }
    #tasktitle { margin-bottom: 1; }
    #callout {
        display: none;
        margin-bottom: 1;
        padding: 0 1;
        border: round $error;
        color: $text;
    }
    #callout.visible { display: block; }
    #detail { margin-bottom: 1; }
    .table-title { text-style: bold; margin-top: 1; color: $text-muted; }
    DataTable { height: auto; max-height: 12; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "open_panel", "Web panel"),
    ]

    def __init__(self, client: HubClient | None = None) -> None:
        super().__init__()
        self.client = client or HubClient(default_base_url())
        self.summary: dict | None = None
        self.selected_task_id: str | None = None
        self.fingerprints: dict[str, str] = {}
        self.last_success: datetime | None = None
        self.connection = "starting"

    def compose(self) -> ComposeResult:
        yield Static(id="hubstatus")
        with Horizontal(id="body"):
            yield Tree("Repositories", id="repos")
            with VerticalScroll(id="detail-scroll"):
                yield Static("", id="tasktitle")
                yield Static("", id="callout")
                yield Static("Select a task to see its details.", id="detail")
                yield Static("", id="activities-title", classes="table-title")
                yield DataTable(id="activities")
                yield Static("", id="artifacts-title", classes="table-title")
                yield DataTable(id="artifacts")
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one("#repos", Tree)
        tree.show_root = False
        activities = self.query_one("#activities", DataTable)
        activities.add_columns("agent", "role", "state", "doing", "updated")
        activities.zebra_stripes = True
        activities.cursor_type = "none"
        artifacts = self.query_one("#artifacts", DataTable)
        artifacts.add_columns("document", "phase", "published")
        artifacts.zebra_stripes = True
        artifacts.cursor_type = "none"
        self.set_interval(POLL_SECONDS, self.action_refresh)
        self.action_refresh()

    def action_open_panel(self) -> None:
        webbrowser.open(self.client.base_url + "/")

    def action_refresh(self) -> None:
        self.run_worker(
            self._poll_summary, thread=True, exclusive=True, group="summary"
        )

    def _poll_summary(self) -> None:
        result = self.client.fetch("/v1/summary")
        self.call_from_thread(self._apply_summary, result)

    def _apply_summary(self, result: FetchResult) -> None:
        if result.kind == "ok" and result.payload is not None:
            self.connection = "ok"
            self.last_success = datetime.now(timezone.utc)
            self.summary = result.payload
            fingerprints = {
                str(task.get("id")): str(task.get("material_fingerprint", ""))
                for task in result.payload.get("tasks", ())
            }
            selected = self.selected_task_id
            changed = (
                selected is not None
                and fingerprints.get(selected) != self.fingerprints.get(selected)
            )
            self.fingerprints = fingerprints
            self._rebuild_tree()
            if changed:
                self._load_detail(selected)
        elif result.kind == "not_modified":
            self.connection = "ok"
            self.last_success = datetime.now(timezone.utc)
        elif result.kind == "degraded":
            self.connection = f"degraded ({result.detail or 'database'})"
        else:
            self.connection = "unreachable"
        self._render_status()

    def _render_status(self) -> None:
        status = self.query_one("#hubstatus", Static)
        body = self.query_one("#body")
        healthy = self.connection == "ok"
        status.set_class(not healthy, "degraded")
        body.set_class(not healthy, "degraded")
        active = blockers = 0
        for task in (self.summary or {}).get("tasks", ()):
            if task.get("status") not in {"completed", "archived"}:
                active += 1
                if str(task.get("blocker", "")):
                    blockers += 1
        if self.last_success is None:
            age = "never"
        else:
            age = snapshot_age(
                self.last_success.isoformat().replace("+00:00", "Z"),
                datetime.now(timezone.utc),
            )
        if healthy:
            needs = (
                f"[bold red]{blockers} need you[/bold red]" if blockers
                else "[green]nothing needs you[/green]"
            )
            text = (
                f"[green]●[/green] Hub ok · {active} working · "
                f"{needs} · updated {age}"
            )
        else:
            text = (
                f"[red]●[/red] Hub {escape(self.connection)} — retrying · "
                f"showing last data from {age}"
            )
        status.update(text)

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#repos", Tree)
        tree.clear()
        if self.summary is None:
            return
        for node in build_tree(self.summary):
            counters = []
            if node.active:
                counters.append(f"{node.active} working")
            if node.completed:
                counters.append(f"{node.completed} done")
            suffix = " · ".join(counters) or "quiet"
            label = f"[bold]{escape(node.name)}[/bold] [dim]{suffix}[/dim]"
            branch = tree.root.add(label, expand=node.active > 0)
            for task in node.tasks:
                branch.add_leaf(self._task_label(task), data=str(task.get("id")))
        tree.root.expand()

    def _task_label(self, task: dict) -> str:
        label = escape(str(task.get("label", "")))
        tone = status_tone(task)
        color = {
            "red": "red",
            "orange": "dark_orange",
            "yellow": "yellow",
            "blue": "blue",
            "green": "green",
            "muted": "dim",
        }[tone]
        parts = [f"[{color}]●[/{color}]" if color != "dim" else "[dim]●[/dim]"]
        worktree = worktree_name(task)
        if worktree:
            parts.append(f"[dim]{escape(f'[{worktree}]')}[/dim]")
        short_id = str(task.get("short_id") or "")
        if short_id:
            parts.append(f"[bold]{escape(short_id)}[/bold]")
        parts.append(label)
        initiative = str((task.get("initiative") or {}).get("title") or "")
        if initiative:
            parts.append(f"[dim]{escape(f'[{initiative}]')}[/dim]")
        return " ".join(parts)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        task_id = event.node.data
        if isinstance(task_id, str):
            self.selected_task_id = task_id
            self._load_detail(task_id)

    def _load_detail(self, task_id: str) -> None:
        self.run_worker(
            lambda: self._poll_detail(task_id),
            thread=True,
            exclusive=True,
            group="detail",
        )

    def _poll_detail(self, task_id: str) -> None:
        result = self.client.fetch(f"/v1/tasks/{task_id}")
        self.call_from_thread(self._apply_detail, task_id, result)

    _DETAIL_FIELDS = (
        ("summary", "summary"),
        ("next_action", "next step"),
        ("initiative", "initiative"),
        ("blocked_by", "blocked by"),
        ("parallel_with", "parallel"),
        ("branch", "branch"),
        ("worktree", "worktree"),
        ("created_at", "started"),
        ("updated_at", "updated"),
    )

    def _apply_detail(self, task_id: str, result: FetchResult) -> None:
        if task_id != self.selected_task_id:
            return
        detail = self.query_one("#detail", Static)
        if result.kind == "not_modified":
            return
        if result.kind != "ok" or result.payload is None:
            detail.update(
                f"[dim]Task detail unavailable ({escape(result.kind)}).[/dim]"
            )
            return
        payload = result.payload
        task = payload.get("task", {})
        now = datetime.now(timezone.utc)
        fields = dict(task_rows(task))
        progress = phase_progress(list(payload.get("artifacts", ())))
        phase_note = ""
        if progress is not None:
            current, total = progress
            bar = "■" * current + "□" * max(total - current, 0)
            phase_note = f" · phase {current}/{total} {bar}"
        title = (
            f"[bold]{escape(fields['label'])}[/bold]  "
            f"[dim]{escape(fields['tier'])} tier · "
            f"{escape(fields['stage'])} · {escape(fields['status'])}"
            f"{phase_note}[/dim]"
        )
        self.query_one("#tasktitle", Static).update(title)
        callout = self.query_one("#callout", Static)
        blocker = fields.get("blocker", "")
        callout.set_class(bool(blocker), "visible")
        if blocker:
            callout.update(
                f"[bold red]Needs you:[/bold red] {escape(blocker)}"
            )
        lines = []
        for field, shown in self._DETAIL_FIELDS:
            value = fields.get(field, "")
            rendered = escape(value) if value else "[dim]—[/dim]"
            if field == "updated_at" and value:
                rendered = f"[dim]{escape(snapshot_age(value, now))}[/dim]"
            lines.append(f"[bold]{shown:>10}[/bold]  {rendered}")
        detail.update("\n".join(lines))
        activities = payload.get("activities", ())
        table = self.query_one("#activities", DataTable)
        table.clear()
        table.display = bool(activities)
        self.query_one("#activities-title", Static).update(
            "Who is working" if activities
            else "Who is working [dim]— no activity reported yet[/dim]"
        )
        for activity in activities:
            table.add_row(
                str(activity.get("agent_id", "")),
                str(activity.get("capability", "")),
                str(activity.get("state", "")),
                str(activity.get("summary", "")),
                snapshot_age(str(activity.get("updated_at", "")), now),
            )
        artifacts = payload.get("artifacts", ())
        table = self.query_one("#artifacts", DataTable)
        table.clear()
        table.display = bool(artifacts)
        self.query_one("#artifacts-title", Static).update(
            "Documents produced" if artifacts
            else "Documents produced [dim]— none yet[/dim]"
        )
        for artifact in artifacts:
            table.add_row(
                str(artifact.get("kind", "")),
                str(artifact.get("phase", "")),
                snapshot_age(str(artifact.get("created_at", "")), now),
            )
