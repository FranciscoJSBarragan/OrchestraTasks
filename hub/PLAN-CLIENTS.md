# Orchestra Hub — Clients v2 Implementation Plan (Iteration 1)

Companion to `SPEC-CLIENTS.md` (frozen). Execute tasks strictly in order,
test-first where tests are specified, one commit per task. The Hub server
suite (`hub/tests`) must pass at every commit. If any ambiguity or conflict
with the spec arises, stop and ask.

Target layout:

```
hub/tui/                requirements.txt README.md
hub/tui/orchestra_hub_tui/   __init__.py __main__.py client.py viewmodel.py app.py
hub/tui/tests/          support.py test_client.py test_viewmodel.py
hub/menubar/            main.swift build.sh Info.plist README.md
hub/menubar/launchd/    com.orchestra.hub.menubar.plist
```

`hub/tui/.venv/` is git-ignored.

# Phase 1 — TUI

### Task 1: TUI scaffold and HTTP client

**Files:** `hub/tui/requirements.txt` (`textual>=1.0,<4`), `hub/tui/README.md`
(venv setup + run instructions), package `__init__.py`, `client.py`,
`hub/tui/tests/test_client.py`, `.gitignore` entry for `hub/tui/.venv/`.

`client.py` (stdlib only — `urllib.request`, no textual import, so its tests
run without the venv). Test modules follow the MVP convention: a
`tests/support.py` performs the `sys.path` setup and is imported first.

- `read_port(state_root: Path | None) -> int` — parse `hub.toml` via
  `tomllib`; missing file/key or invalid value → 7343.
- `class HubClient` with `base_url`, per-path stored ETag, and
  `fetch(path) -> FetchResult`.
- `FetchResult` is a small frozen dataclass:
  `kind: Literal["ok", "not_modified", "degraded", "unreachable", "invalid"]`,
  `payload: dict | None`, `detail: str`.
  Mapping: 200 → `ok` (store ETag); 304 → `not_modified`; 503 → `degraded`
  (detail from body when parseable); 404 → `invalid`; `URLError`/`OSError`/
  timeout/JSON decode error → `unreachable`/`invalid` with detail. Timeout
  3 seconds. No retries inside the client.

**Test first** (`test_client.py`, run against a throwaway
`http.server.ThreadingHTTPServer` fixture on a random loopback port):

- 200 with `ETag` header → `ok`, second call sends `If-None-Match`, server
  answers 304 → `not_modified`.
- 503 JSON body → `degraded` with detail.
- Connection refused (closed port) → `unreachable`.
- `read_port`: missing file → 7343; file with `port = 7400` → 7400;
  malformed TOML → 7343.

**Verify:** `python3 -m unittest discover -s hub/tui/tests -v` (repo root)
and the server suite. **Commit:** `hub/tui: scaffold and hub client`.

### Task 2: view-model

**Files:** `viewmodel.py`, `hub/tui/tests/test_viewmodel.py`. Pure functions,
no textual import.

- `build_tree(summary: dict) -> list[RepoNode]` — `RepoNode(name, path,
  active, completed, tasks)` with tasks split active-first (ordering as
  delivered by the API, active = `status != "completed"`). Repositories with
  zero tasks (pinned) still appear.
- `task_rows(task: dict) -> list[tuple[str, str]]` — ordered label/value
  pairs for the detail panel: label, tier, stage, status, branch, worktree,
  summary, blocker, next_action, created_at, updated_at, stale.
- `attention_flags(task: dict) -> frozenset[str]` — only from API evidence:
  `{"blocker"}` when blocker non-empty and status != completed, `{"stale"}`
  when `stale` is true. Never inferred from free text.
- `snapshot_age(updated_at: str, now: datetime) -> str` — "3 min ago" /
  "2 h ago" rendering; unparsable timestamp → the raw string.

**Test first:** tree building with two repos (one pinned empty), active/
completed split, attention flags matrix (blocker set/cleared, completed
with blocker → no flag), snapshot age formatting, unparsable timestamp
passthrough.

**Verify:** both TUI test modules pass. **Commit:** `hub/tui: view-model`.

### Task 3: Textual app

**Files:** `app.py`, `__main__.py`. Uses Task 1/2 modules for all logic;
`app.py` contains layout and event wiring only.

- Widgets: `Header` (custom static: status, active count, blocker count,
  last poll age), `Tree` (left), detail panel (right, `Static`/`DataTable`
  for activities and artifacts), `Footer` with bindings.
- Polling: `set_interval(5, ...)` → `HubClient.fetch("/v1/summary")` in a
  worker; `ok` → rebuild tree + header, refresh open detail if its
  fingerprint changed; `not_modified` → update poll age only; `degraded`/
  `unreachable` → banner + dim, keep last data (SPEC-CLIENTS §4).
- Selection → `fetch("/v1/tasks/<id>")`; `invalid` (task gone) → notice in
  the detail panel, tree refresh on next poll.
- Bindings: `q` quit, `r` manual refresh; tree navigation is Textual's own.

**Verify (manual smoke, recorded in the commit message):** venv install,
run against the live Hub — tree shows both repositories, selecting a task
shows detail with activities/artifacts, stopping the Hub shows the banner
and data stays dimmed, restarting recovers, `q` exits cleanly. Both TUI
test modules and the server suite pass.
**Commit:** `hub/tui: textual dashboard app`.

# Phase 2 — Menu bar app

### Task 4: Swift source, bundle build, plists

**Files:** `hub/menubar/main.swift`, `build.sh`, `Info.plist`,
`launchd/com.orchestra.hub.menubar.plist`, `README.md`.

`main.swift` (single file, ≤ ~300 lines):

- `readPort()` — tolerant line scan of `~/.orchestra/hub.toml` for
  `^port\s*=\s*(\d+)`; fallback 7343 (SPEC-CLIENTS §5).
- `Poller` — `URLSession` GET `/v1/summary` every 30 s (`Timer` on main
  run loop), stores/sends ETag, 304 → no-op; decodes only the fields the
  menu needs (`repositories[].{path,name}`, `tasks[].{id,label,repository,
  stage,status,blocker}`).
- `StatusController` — owns the `NSStatusItem`; title per the frozen rule
  (`◦` / `<name>:<n>` / `<n> repos·<total>` / `⚠ Hub`, 40-char cap) and
  rebuilds the menu: per-repo sections with active tasks
  (`label — stage/status`, blocker as indented disabled item truncated to
  80 chars), then separator, "Open panel", "Refresh now", "Quit".
- `AppDelegate` + `NSApplication` main; no windows, no Dock presence
  (activation policy `.accessory`; `LSUIElement` in Info.plist).

`build.sh`: `swiftc -O main.swift -o OrchestraHubMenu`, assemble
`build/OrchestraHubMenu.app/Contents/{MacOS/OrchestraHubMenu,Info.plist}`.
`build/` is git-ignored. LaunchAgent template points at the bundle binary
with `RunAtLoad` + `KeepAlive`, same placeholder convention as
`hub/launchd/com.orchestra.hub.plist`.

**Verify (manual smoke):** `./build.sh` succeeds; `open build/OrchestraHubMenu.app`
shows the status item; with the live Hub the title matches the frozen rule
and the menu lists active tasks; stop the Hub → `⚠ Hub` and "Hub
unreachable" item; restart → recovers; "Open panel" opens the browser;
"Quit" exits. **Commit:** `hub/menubar: native status item app`.

### Task 5: LaunchAgent install and SwiftBar retirement

Manual + repo cleanup task.

1. Install and load the LaunchAgent; verify the status item survives
   `launchctl kickstart -k` and logout/login.
2. Run the SPEC-CLIENTS §8 journeys 2–5.
3. Only after both pass: unload/remove the SwiftBar plugin symlink and the
   SwiftBar app; delete `hub/swiftbar/` and its references in `hub/PLAN.md`
   status notes (history stays in git).

**Verify:** server suite passes; `git grep -l swiftbar hub/` returns only
historical plan/spec documents. **Commit:** `hub/menubar: launch agent;
retire SwiftBar`.

# Phase 3 — Optional panel styling

### Task 6: embedded panel CSS

**Files:** `hub/orchestra_hub/panel.py` (style block only), existing panel
tests updated at assertion level if markup changes.

- Single `<style>` constant: dark background, readable light text,
  monospace numerals, badge styling for stage/status cells and attention
  reasons, subtle table borders. No JS, no external assets.
- Escaping untouched; XSS tests must pass unchanged.

**Verify:** full `hub/tests` suite passes; visual check in the browser.
**Commit:** `hub: panel dark styling`.

# Acceptance mapping

- Journey 1 → Tasks 1–3.
- Journeys 2–3 → Task 4.
- Journey 4 → Tasks 3 and 4 (degraded behavior), Task 5 step 2.
- Journey 5 → Task 5.
