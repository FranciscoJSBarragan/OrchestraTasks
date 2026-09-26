# Orchestra Hub MVP Implementation Plan

> **Status: executed and partially superseded.** This plan is a historical
> record of the MVP implementation. `hub/SPEC.md` remains the single source of
> contracts; where this plan disagrees with it, the SPEC and the code win.
> **Superseded sections** (marked inline below): everything describing
> artifacts as database rows. `coordination.py` no longer has an `artifacts`
> table — artifacts are files discovered by `orchestra_hub/artifacts.py`, and
> the artifact allowlist is `id, kind, phase, created_at` (see SPEC §7).

> **For the implementer (Codex):** `hub/SPEC.md` is frozen and is the single
> source of contracts (JSON shapes, allowlists, invariants). Follow this plan
> in order, TDD, one commit per task. **Do not add anything the plan does not
> ask for** — no dependencies, no extra abstraction, no extra endpoints, no
> schema changes, no files outside `hub/`. If something seems missing, stop
> and ask the user instead of improvising.

**Goal:** Read-only loopback HTTP viewer (JSON API + HTML panel) for the
Orchestra coordination snapshot, plus LaunchAgent, Tailscale verification,
and a SwiftBar plugin.

**Tech stack:** Python ≥ 3.11 stdlib only. Tests with `unittest`.

**Test command (repo root):** `python3 -m unittest discover -s hub/tests -v`

## File structure

```
hub/orchestra_hub/  __init__.py __main__.py config.py fingerprint.py
                    db.py artifacts.py api.py panel.py server.py
hub/tests/          support.py test_support.py test_fingerprint.py
                    test_config.py test_db.py test_schema_crosscheck.py
                    test_artifacts.py test_api.py test_panel.py
                    test_server.py test_swiftbar.py
hub/launchd/        com.orchestra.hub.plist
hub/swiftbar/       orchestra_hub.1m.py
```

`scripts/coordination.py` is imported read-only by tests only.

**Import order rule for every test module:** `import support` MUST appear
before any `orchestra_hub` or `coordination` import — `support.py` performs
the `sys.path` setup that makes both importable under
`python3 -m unittest discover -s hub/tests`.

---

# Phase 1 — Hub service and full test suite

### Task 1: Scaffold and fixtures

**Files:** create `hub/orchestra_hub/__init__.py` (empty),
`hub/tests/support.py`, `hub/tests/test_support.py`.

`support.py` builds a real coordination-schema DB by reusing the
coordinator's own `SCHEMA_STATEMENTS` (fixtures can never drift):

```python
"""Shared fixtures: real coordination-schema database for Hub tests."""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "hub"))

import coordination  # noqa: E402

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


def task_row(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()), "label": "Sample task",
        "repository": "/tmp/repo", "worktree": "/tmp/worktrees/repo/sample",
        "branch": "orchestra/sample", "base_revision": HEX40,
        "head_revision": HEX40, "tier": "standard",
        "stage": "implementation", "status": "active", "summary": "Working",
        "blocker": "", "next_action": "Continue",
        "created_at": "2026-08-02T18:00:00Z",
        "updated_at": "2026-08-02T18:00:00Z",
    }
    row.update(overrides)
    return row
```

Add `insert_task(database, **overrides)`, `insert_activity(database,
task_id, **overrides)`, and `insert_artifact(database, task_id, *, path,
**overrides)`: each opens `sqlite3.connect(database)`, INSERTs the row with
named parameters into `tasks` / `activities` / `artifacts` exactly as the
coordination schema defines them, commits, closes, and returns the row dict.
Activity defaults: `agent-1 / implementation / running / "Implementing" /
"2026-08-02T18:00:00Z"`. Artifact defaults: uuid id,
`implementation-report`, phase 1, `HEX40` revision, `worker-1`,
`"2026-08-02T18:00:00Z"`.

> **Superseded:** there is no `artifacts` table and no `insert_artifact`.
> `support.py` provides `make_worktree(root, linked=...)` and
> `write_artifact(worktree, name)`, which publish real
> `<NN>-<kind>[-p<phase>].md` files under the worktree-local private
> `.orchestra/artifacts` directory.

`test_support.py` creates a temporary state database and asserts
`PRAGMA user_version == coordination.SCHEMA_VERSION`; it then uses the insert
helpers and asserts one row exists in each real schema table (plus one
published artifact file). This
provides the first discoverable unittest so the mandatory full-suite command
can pass before the Task 1 commit.

**Verify:** a 3-line python snippet creating the DB and printing
`PRAGMA user_version` → `1`; then the full suite passes. **Commit:**
`hub: scaffold and fixtures`.

### Task 2: `fingerprint.py` (SPEC §6)

**Test first** (`test_fingerprint.py`, uses `support.task_row()`):
- format matches `^sha256:[0-9a-f]{64}$`; `MATERIAL_FINGERPRINT_VERSION == 1`.
- deterministic under key reordering and extra keys.
- `len(MATERIAL_FIELDS) == 9`; mutating each material field changes the hash.
- mutating `updated_at`, `created_at`, `base_revision`, `repository`,
  `worktree`, `branch` does NOT change the hash.

Run → FAIL (module missing). **Implementation (complete):**

```python
"""Material fingerprint for notifiable task changes (SPEC section 6)."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

MATERIAL_FINGERPRINT_VERSION = 1
MATERIAL_FIELDS = (
    "blocker", "head_revision", "id", "label", "next_action",
    "stage", "status", "summary", "tier",
)


def material_fingerprint(task: Mapping[str, object]) -> str:
    payload = {field: str(task[field]) for field in MATERIAL_FIELDS}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

Run → PASS. **Commit:** `hub: material fingerprint v1`.

### Task 3: `config.py` (SPEC §10)

**Test first** (`test_config.py`):
- missing file → defaults: `port == DEFAULT_PORT (7343)`,
  `stale_after_minutes == 60`, no pinned repos, `state_root` ends with
  `.orchestra`, `config.database.name == "state.sqlite3"`.
- full file overrides all values; `[[repositories]]` without `name` falls
  back to path basename.
- unknown keys ignored.

**Implementation:** frozen dataclasses `PinnedRepository(path, name)` and
`HubConfig(state_root, port, stale_after_minutes, pinned_repositories)` with
property `database = state_root / "state.sqlite3"`.
`load_config(path: Path | None = None) -> HubConfig`: default config path is
`<default_state_root>/hub.toml` where `default_state_root()` is
`$HOME/.orchestra` (raise `RuntimeError` if HOME unset); missing file → all
defaults; parse with `tomllib.load`; honor keys `port`,
`stale_after_minutes`, `state_root`, `[[repositories]]` (`path` required,
`name` optional → basename). Bind host is NOT configurable anywhere.

Run → PASS. **Commit:** `hub: TOML configuration with defaults`.

### Task 4: `db.py` (SPEC §5)

**Test first** (`test_db.py`):
- `SUPPORTED_SCHEMA_VERSIONS == frozenset({1})`.
- missing file → `HubUnavailable` with `condition == "missing"`.
- `PRAGMA user_version = 99` → condition `"unsupported-schema"`.
- consistency + non-blocking (the key regression test): inside
  `read_snapshot`, read `status` → `"active"`; from a second connection with
  `busy_timeout = 0` UPDATE status to `completed` and commit (must succeed —
  proves readers don't block writers under WAL); re-read inside the same
  snapshot → still `"active"`; a fresh snapshot → `"completed"`.
- writes on the snapshot connection raise `sqlite3.OperationalError`.

**Implementation (complete):**

```python
"""Read-only consistent snapshot access to the coordination database."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


class HubUnavailable(Exception):
    """The coordination database cannot be read right now."""

    def __init__(self, condition: str, detail: str) -> None:
        super().__init__(detail)
        self.condition = condition
        self.detail = detail


@contextmanager
def read_snapshot(database: Path) -> Iterator[sqlite3.Connection]:
    if not database.is_file():
        raise HubUnavailable("missing", f"database not found: {database}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro", uri=True, timeout=2,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 2000")
        connection.execute("BEGIN DEFERRED")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise HubUnavailable(
                "unsupported-schema",
                f"unsupported coordination schema version: {version}",
            )
        yield connection
    except HubUnavailable:
        raise
    except sqlite3.OperationalError as error:
        message = str(error)
        condition = "busy" if "locked" in message or "busy" in message else "error"
        raise HubUnavailable(condition, f"database is {condition}: {message}") from error
    except sqlite3.Error as error:
        raise HubUnavailable("error", f"database error: {error}") from error
    finally:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            connection.close()
```

Notes: `isolation_level=None` keeps transaction control fully manual; the
immediate `PRAGMA user_version` is the first read that materializes the WAL
snapshot and doubles as the schema gate.

Run → PASS. **Commit:** `hub: read-only snapshot access with schema gate`.

### Task 5: Schema cross-check (SPEC §4)

`test_schema_crosscheck.py` (passes immediately; breaks on future bumps):

```python
import unittest

import support  # noqa: F401  (sys.path setup)
import coordination
from orchestra_hub.db import SUPPORTED_SCHEMA_VERSIONS


class SchemaCrossCheckTest(unittest.TestCase):
    def test_hub_supports_installed_coordination_schema(self) -> None:
        self.assertIn(
            coordination.SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS,
            "coordination.SCHEMA_VERSION changed. Review Hub compatibility "
            "(fields, queries, fingerprint), then update "
            "SUPPORTED_SCHEMA_VERSIONS deliberately.",
        )
```

Run → PASS. **Commit:** `hub: schema cross-check test`.

### Task 6: `api.py` (SPEC §7-8)

**Test first** (`test_api.py`, fixture DB + `HubConfig` with one pinned repo
`/pinned/repo` named `Pinned`, `stale_after_minutes=60`, fixed
`NOW = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)`):
- task payload keys are EXACTLY the 15 allowlisted columns plus
  `material_fingerprint` and `stale` (compare as sets — this test is the
  allowlist guarantee).
- attention rules: task with blocker → `["blocker"]`; task with
  `updated_at` 2h old → `["stale"]`; completed task with blocker and old
  timestamp → absent; fresh task without blocker → absent.
- repositories: two tasks in `/obs/alpha` (one active, one completed) →
  entry `observed=True, pinned=False, active_tasks=1, completed_tasks=1`;
  `/pinned/repo` appears with `pinned=True, observed=False, active_tasks=0`
  (acceptance journey 6).
- `tasks_payload(..., status="completed")` returns only completed tasks.
- task detail: artifacts have keys `{id, kind, phase, revision, producer,
  created_at, available}` and **never** `path`; `available` is `True` for an
  existing file and `False` for a deleted one; activities have exactly the
  5 allowlisted keys.
- `task_detail_payload` returns `None` for an unknown id.

> **Superseded:** artifact keys are `{id, kind, phase, created_at}`, all
> derived from the file name and mtime. `revision`, `producer`, and `available`
> no longer exist (presence is intrinsic to listing the directory); `path` is
> still never serialized.

**Implementation** — module constants and functions:

```python
TASK_FIELDS = (
    "id", "label", "repository", "worktree", "branch", "base_revision",
    "head_revision", "tier", "stage", "status", "summary", "blocker",
    "next_action", "created_at", "updated_at",
)
ACTIVITY_FIELDS = ("agent_id", "capability", "state", "summary", "updated_at")
ARTIFACT_FIELDS = ("id", "kind", "phase", "revision", "producer", "created_at")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

- `task_summary(row, *, now, stale_after) -> dict`: allowlist projection +
  `material_fingerprint(task)` + `stale = status != "completed" and
  now - parse_timestamp(updated_at) > stale_after`.
- `attention_entries(tasks) -> list`: skip completed; reasons `"blocker"`
  if `task["blocker"]`, `"stale"` if `task["stale"]`; entry shape per SPEC §7
  (`task_id, label, repository, reasons, blocker, next_action, updated_at`).
- `repository_entries(tasks, pinned) -> list`: dict keyed by path; pinned
  entries seeded first (`pinned=True, observed=False`, zero counts); each
  task marks its repo `observed=True` and increments `active_tasks` or
  `completed_tasks`; name = pinned name else `Path(path).name`; sort by
  `(name, path)`.
- `_tasks(connection, *, now, stale_after, status=None)`: `SELECT * FROM
  tasks ORDER BY updated_at DESC, id` with optional parameterized
  `WHERE status = ?` (same ordering as `coordination.py task list`).
- `summary_payload(connection, config, now)` /
  `tasks_payload(connection, config, now, *, status=None)` /
  `task_detail_payload(connection, config, now, task_id)`: assemble the SPEC
  §7 bodies verbatim, all including
  `"material_fingerprint_version": MATERIAL_FINGERPRINT_VERSION`. Detail
  queries activities ordered `updated_at DESC, agent_id, capability` and
  artifacts ordered `created_at DESC, id`; artifact `available` =
  `Path(row["path"]).is_file() and not is_symlink()`; `path` never copied
  into the payload. Detail returns `None` when the task row is missing.
  No `generated_at` in any of these bodies (ETag stability, SPEC §7).

> **Superseded:** `ARTIFACT_FIELDS` lives in `orchestra_hub/artifacts.py` and is
> `("id", "kind", "phase", "created_at")`. Detail runs no artifact query at all:
> it calls `artifact_entries(task["worktree"])`, which lists the task-private
> `.orchestra/artifacts` directory (newest ordinal first) reading names and
> mtimes only.

Run → PASS. **Commit:** `hub: allowlisted payloads, attention, repositories`.

### Task 7: `panel.py` (SPEC §9, §11)

**Test first** (`test_panel.py`):
- XSS: insert a task with `label='<script>alert(1)</script>'`,
  `summary='<img src=x onerror=alert(2)>'`, `blocker='"><svg onload=alert(3)>'`,
  `next_action='<b onmouseover=alert(4)>go</b>'`; rendered HTML must NOT
  contain any raw payload and MUST contain
  `&lt;script&gt;alert(1)&lt;/script&gt;` (acceptance for SPEC §11).
- sections: with a blocked task, output contains `Possible attention`,
  `Repositories`, `12 min ago` (task updated 18:48, NOW 19:00), and
  `http-equiv="refresh"`.
- `render_degraded("missing", "database not found")` renders standalone
  (contains `degraded` case-insensitively and `missing`).

**Implementation:** two module-level HTML templates (main page and degraded
page), both with `<meta http-equiv="refresh" content="30">`, small inline
CSS, no JS. Public functions:

- `render_panel(summary: dict, now: datetime) -> str` — sections: Possible
  attention (count in heading; each entry: label, reasons, blocker or
  next_action, repository, `last snapshot N min ago`), Repositories table
  (name, path, active, completed, pinned/observed), Tasks table (label,
  repository, stage, status, summary, last snapshot age with ` · stale`
  suffix when stale). Empty states render a short italic paragraph.
- `render_degraded(condition: str, detail: str) -> str` — states database
  condition and that Orchestra itself is unaffected.
- `_age(updated_at, now) -> "N min ago"` via `parse_timestamp`.

**Every dynamic value passes through `html.escape` — no exceptions.**
Build rows with f-strings around escaped values only.

Run → PASS. **Commit:** `hub: escaped server-rendered panel`.

### Task 8: `server.py` + `__main__.py` (SPEC §7, §8, §11)

**Test first** (`test_server.py`; start `create_server(config)` with
`port=0` on a background thread, talk to it with `urllib.request`; a second
degraded server points at an empty state root):
- `BIND_HOST == "127.0.0.1"` and `server.server_address[0] == "127.0.0.1"`.
- `/v1/health` → 200 `database == "available"`; `/v1/summary` → 200 with
  `material_fingerprint_version == 1` and blocker attention;
  `/v1/tasks/{id}` → 200 with matching id.
- ETag: first GETs for `/v1/summary`, `/v1/tasks`,
  `/v1/tasks?status=active`, and `/v1/tasks/{id}` each return an `ETag`;
  repeat each with `If-None-Match` → 304 empty body.
- POST, PUT, PATCH, DELETE on `/v1/summary` → 405 with header `Allow: GET`.
- `/v1/nope` → 404 `{"status":"invalid","reason":"not found"}`;
  `/v1/tasks/absent-id` → 404 with reason `unknown task`.
- degraded server: `/v1/summary` → 503 `{"status":"degraded",
  "database":"missing",...}`; `/v1/health` → 200 with `status degraded`;
  `/` → 200 HTML containing `degraded`.
- `/` → 200 with `text/html` content type containing `Orchestra Hub`.

**Implementation:**

```python
BIND_HOST = "127.0.0.1"


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
```

`HubRequestHandler(BaseHTTPRequestHandler)` with class attribute `config`:

- `_send(status, body, content_type, extra_headers=())` — writes status,
  `Content-Type`, `Content-Length`, extras, body.
- `_send_json(payload, status=200, cacheable=False)` — canonical JSON body;
  when cacheable: `etag = '"' + sha256(body).hexdigest() + '"'`; if
  `If-None-Match` matches → `_send(304, b"", ..., (("ETag", etag),))`; else
  include the `ETag` header.
- `do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed` → 405
  JSON `{"status":"invalid","reason":"method not allowed"}` + `Allow: GET`.
- `do_GET`: `urlparse(self.path)`; route on `parsed.path.rstrip("/") or "/"`:
  `/` → panel; `/v1/health` → health; `/v1/summary` → data;
  `/v1/tasks` → data with optional `status` from `parse_qs(parsed.query)`;
  `/v1/tasks/<id>` → detail (404 `unknown task` when payload is `None`);
  anything else → 404 `not found`. Successful `/v1/summary`, `/v1/tasks`
  (including filtered requests), and `/v1/tasks/<id>` responses call
  `_send_json(..., cacheable=True)`. Data/detail wrap `read_snapshot` calls;
  `HubUnavailable` → 503 degraded JSON. Health catches `HubUnavailable`
  itself and always answers 200 (`generated_at` included, SPEC §7). Panel
  catches it and renders `render_degraded` with 200.
- `now = datetime.now(timezone.utc)` computed per request and passed to api.
- `log_message` → single stderr line.
- `create_server(config)` → `ThreadingHTTPServer((BIND_HOST, config.port),
  handler)` where `handler` is a subclass created with
  `type("BoundHubRequestHandler", (HubRequestHandler,), {"config": config})`.
- `main(argv)` → argparse with single `--config` Path option;
  `load_config`; print listening line to stderr; `serve_forever()` with
  `KeyboardInterrupt` handling; `server_close()` in finally; return 0.

`__main__.py`:

```python
import sys

from orchestra_hub.server import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

Run → PASS. **Commit:** `hub: loopback GET-only server with ETag and degradation`.

### Task 9: Full suite and manual smoke run

- Run: `python3 -m unittest discover -s hub/tests -v` → all PASS.
- Smoke against the real database: `PYTHONPATH=hub python3 -m orchestra_hub`
  then, in another shell:

```
curl -s http://127.0.0.1:7343/v1/health
curl -s http://127.0.0.1:7343/v1/summary | python3 -m json.tool | head -40
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:7343/v1/summary
open http://127.0.0.1:7343/
```

Expected: health `ok` (or `degraded/missing` if no task was ever registered
— both acceptable), POST → `405`, panel renders.
**Commit** only if fixes were needed.

---

# Phase 2 — Deployment on the main machine (manual, with the user)

### Task 10: LaunchAgent (SPEC §12)

**Files:** create `hub/launchd/com.orchestra.hub.plist`.

- **Step 1 — verify the interpreter:**
  `python3 -c "import sys, tomllib; print(sys.executable, sys.version)"`.
  If it fails or reports < 3.11, use the absolute path of a 3.11+
  interpreter (e.g. `/opt/homebrew/bin/python3`) in the plist.

- **Step 2 — plist template (complete):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.orchestra.hub</string>
  <key>ProgramArguments</key>
  <array>
    <string>REPLACE_WITH_ABSOLUTE_PYTHON3_PATH</string>
    <string>-m</string>
    <string>orchestra_hub</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>/Users/fjsbarragan/Code/Orchestra/hub</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key>
  <string>/Users/fjsbarragan/.orchestra/hub.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/fjsbarragan/.orchestra/hub.log</string>
</dict>
</plist>
```

- **Step 3 — install and verify (run with the user, not silently):**

```
cp hub/launchd/com.orchestra.hub.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.orchestra.hub.plist
curl -s http://127.0.0.1:7343/v1/health
```

Expected: health JSON. On failure inspect `~/.orchestra/hub.log`.
Restart after edits:
`launchctl kickstart -k gui/$(id -u)/com.orchestra.hub`.

- **Commit:** `hub: LaunchAgent template`.

### Task 11: Tailscale exposure and network verification (SPEC §11, journeys 2 and 7)

Manual checklist executed with the user on the real machine. No code. The
`tailscale serve` syntax varies by version — check `tailscale serve --help`
first; do not assume.

- [ ] Confirm the Hub answers on loopback:
  `curl -s http://127.0.0.1:7343/v1/health` → JSON.
- [ ] Confirm nothing else listens publicly on the port:
  `lsof -nP -iTCP:7343 -sTCP:LISTEN` → only `127.0.0.1:7343`.
- [ ] Expose through Tailscale (adjust to installed syntax), e.g.:
  `tailscale serve --bg http://127.0.0.1:7343` and record the HTTPS URL.
- [ ] From the laptop (on the tailnet):
  `curl -s https://<magicdns-host>/v1/health` → JSON (journey 2 canary).
- [ ] From the laptop, against the Mac's **LAN IP**:
  `curl --max-time 3 http://<lan-ip>:7343/v1/health` → connection refused
  or timeout (negative check).
- [ ] Through Tailscale:
  `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<magicdns-host>/v1/summary`
  → `405`.
- [ ] Record which Tailscale variant runs on this machine and whether it
  starts before login (informational note for SPEC §12; do not change the
  LaunchAgent decision).

Document results as a short checklist in the task/commit message.
**Commit** only if the plist or docs needed adjustments.

---

# Phase 3 — SwiftBar plugin (SPEC §13)

### Task 12: `hub/swiftbar/orchestra_hub.1m.py`,
`hub/tests/test_swiftbar.py`

Single-file Python plugin (complete implementation below). Install by
symlinking into the SwiftBar plugins folder. It polls `/v1/summary`,
compares the server-computed fingerprints against a local baseline, and
notifies only on material changes — one aggregated notification on first
baseline (SPEC §13).

**Test first** (`test_swiftbar.py`; load the plugin module by file path without
executing `main`, patch its network/state/notification functions, and capture
stdout):
- a first baseline with only a `stale` attention reason emits no notification;
- a first baseline with blocker attention emits exactly one aggregated
  notification counted from blocker entries only;
- dynamic labels, details, stages, and statuses containing `|`, `\r`, or `\n`
  cannot create extra SwiftBar parameters or output lines.

```python
#!/usr/bin/env python3
"""SwiftBar plugin: Orchestra Hub attention monitor (read-only)."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

HUB = "http://127.0.0.1:7343"
STATE_PATH = Path.home() / ".orchestra" / "hub-monitor.json"


def load_state() -> dict | None:
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if (
            isinstance(state, dict)
            and "material_fingerprint_version" in state
            and isinstance(state.get("fingerprints"), dict)
        ):
            return state
    except (OSError, ValueError):
        pass
    return None


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def notify(title: str, message: str) -> None:
    script = 'display notification "{}" with title "{}"'.format(
        message.replace("\\", "\\\\").replace('"', '\\"'),
        title.replace("\\", "\\\\").replace('"', '\\"'),
    )
    subprocess.run(["osascript", "-e", script], check=False,
                   capture_output=True)


def fetch_summary() -> dict | None:
    try:
        with urllib.request.urlopen(f"{HUB}/v1/summary", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def swiftbar_text(value: object) -> str:
    return str(value).replace("|", "¦").replace("\r", " ").replace("\n", " ")


def main() -> None:
    summary = fetch_summary()
    if summary is None or summary.get("status") != "ok":
        print("Hub ?")
        print("---")
        print("Hub unreachable or degraded | color=red")
        print(f"Open panel | href={HUB}/")
        return

    state = load_state()
    version = summary["material_fingerprint_version"]
    current = {
        task["id"]: task["material_fingerprint"]
        for task in summary["tasks"]
    }
    attention = summary["attention"]
    blockers = [
        entry for entry in attention
        if "blocker" in entry["reasons"]
    ]
    baseline = (
        state is None
        or state["material_fingerprint_version"] != version
    )
    if baseline:
        if blockers:
            notify(
                "Orchestra Hub",
                f"{len(blockers)} task(s) may need attention",
            )
    else:
        changed = [
            task for task in summary["tasks"]
            if state["fingerprints"].get(task["id"])
            != task["material_fingerprint"]
        ]
        for task in changed:
            if task["blocker"]:
                notify("Orchestra: blocked", f"{task['label']}: {task['blocker']}")
            elif task["status"] == "completed":
                notify("Orchestra: completed", task["label"])
            else:
                notify("Orchestra: updated", f"{task['label']} — {task['status']}")

    save_state({
        "material_fingerprint_version": version,
        "fingerprints": current,
    })

    count = len(attention)
    print(f"O {count}" if count else "O")
    print("---")
    for entry in attention:
        label = swiftbar_text(entry["label"])
        reasons = swiftbar_text(",".join(entry["reasons"]))
        detail = swiftbar_text(
            entry["blocker"] or entry["next_action"] or ""
        )
        print(f"{label} ({reasons}) | color=red")
        if detail:
            print(f"-- {detail[:80]}")
    if not attention:
        print("Nothing needs attention")
    print("---")
    for task in summary["tasks"]:
        if task["status"] != "completed":
            label = swiftbar_text(task["label"])
            stage = swiftbar_text(task["stage"])
            status = swiftbar_text(task["status"])
            print(f"{label} — {stage}/{status}")
    print("---")
    print(f"Open panel | href={HUB}/")


if __name__ == "__main__":
    main()
```

Verification (manual):

- Run the script directly: `python3 hub/swiftbar/orchestra_hub.1m.py` →
  SwiftBar-format text output; second run produces no notifications when
  nothing changed.
- Delete `~/.orchestra/hub-monitor.json`, run again → single aggregated
  notification at most (journey 3 / SPEC §13).
- Symlink into SwiftBar plugins folder and confirm the menu renders.

**Commit:** `hub: SwiftBar attention plugin`.

---

## Completion checklist (maps to SPEC §14 journeys)

- [ ] J1 tasks grouped by repository — Task 6/7, smoke in Task 9.
- [ ] J2 laptop via Tailscale — Task 11 canary.
- [ ] J3 single blocker notification — Task 12 verification.
- [ ] J4 clean degradation — Tasks 4 and 8 tests.
- [ ] J5 stale snapshots and only really existing artifacts shown, without
      inference — Tasks 6 and 7 tests.
- [ ] J6 pinned repo with zero tasks — Task 6 test.
- [ ] J7 negative network verification — Task 11 checklist.

Out of scope (do not implement): events, migrations, MCP, native app,
remote actions, auth tokens, WebSockets/SSE, central cursors (SPEC §2, §15).
