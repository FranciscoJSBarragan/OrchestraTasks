# Orchestra Hub — Clients v2 Specification (Iteration 1)

Status: superseded in part by the authorized Orchestra Tasks macOS application.
The Hub HTTP and database-read contracts below remain frozen and GET-only. The
former read-only menu client has been replaced by a local app that performs
card mutations through Task Control, never through Hub HTTP.

## Native application amendment

`hub/menubar/` builds **Orchestra Tasks**, a SwiftUI/AppKit `LSUIElement` app
with a menu-bar surface and full window. It obtains cards and capabilities from
the shared Task Control CLI, merges only Hub progress by exact UUID, and
remains usable when the Hub is unavailable. It supports card capture, draft
editing, notes, archive/restore, recoverable trash, restricted permanent purge,
safe-stop request/withdraw, and reopening a cancelled task. Its neutral start
action copies an instruction for Codex, Cursor, Grok, or Devin; it never adopts,
acknowledges a stop, finishes work, controls a host, or mutates Git resources.
The app installer owns only the app and its RunAtLoad LaunchAgent, with no
KeepAlive; sync remains the sole owner of the shared runtime. The detailed
legacy menu-only layout below is historical context, not the current contract.

Current repository presentation supersedes the initiative-grouped historical
layout below. The window, menu, TUI, and web panel group tasks directly under
the Hub repository catalog name (configured pinned name, otherwise primary
checkout basename) and reuse the compact row
`● [worktree] A1 Title [Initiative]`. Worktree is omitted for the primary
checkout, middle-truncated at 16 characters in the row, and preserved in full
in detail or tooltip. A missing short ID has no placeholder. Initiative is a
secondary badge rather than a tree level. Blocker, requested stop, stale,
active, ready, draft, and inactive states use red, orange, yellow, blue, green,
yellow, and gray respectively. Stage, textual status, branch, and full paths
remain in detail. The web panel uses repository sections and native expandable
details; the TUI has no intermediate “Independent tasks” node.

## 1. Purpose and motivation

The MVP validated the Hub server and its read-only contract. Real use produced
two findings, both anticipated by `SPEC.md` §2 entry criteria:

- The primary need is **rich state visibility** (what is being worked on,
  where, and how it is going), not notifications.
- The SwiftBar plugin proved insufficient: third-party container, limited
  personalization. The "native menu bar app" entry criterion is met.

Iteration 1 therefore delivers two first-party read-only clients and retires
SwiftBar:

1. A terminal dashboard (TUI) as the primary state view.
2. A minimal native macOS menu bar app for quick glances.
3. (Optional) cosmetic styling for the existing web panel.

## 2. Frozen product decisions

- The Hub server API and security model remain GET-only. Its database access now
  combines prepared and coordinated cards by UUID.
  Both clients consume the existing endpoints only: `GET /v1/summary`,
  `GET /v1/tasks/{id}`, `GET /v1/health`.
- Both clients are strictly read-only observers. No actions, no writes, no
  remote control. Same posture as SPEC.md §1.
- Both clients read the listen port from `<state_root>/hub.toml` (`port`,
  default `7343`) and connect to `http://127.0.0.1:<port>` by default.
  The TUI additionally honors an `ORCHESTRA_HUB_URL` environment override
  (http/https base URL) for remote viewing over Tailscale from another
  machine — the config knob anticipated by the deferred item, approved by
  the user. The menu bar app stays local-only.
- Both clients poll with `If-None-Match`/`ETag`; a `304` means "render
  nothing new".
- Hub unreachable or degraded (`503`) → each client shows an explicit
  "Hub unreachable"/"degraded" state. No crash, no fabricated data, no
  retry storm (next poll retries naturally).
- SwiftBar (app and plugin) is uninstalled and `hub/swiftbar/` is deleted
  from the repository **after** the menu bar app passes acceptance.
- Native notifications were initially deferred and later approved by the
  user for the menu bar app: one aggregated notification when blockers
  appear or clear, first-poll baseline, never one per task (same contract
  as the retired SwiftBar client, §5).

### Out of scope (deferred, with entry criteria)

- **Actions from any client (resume/approve/cancel)** — separate product
  line with explicit authority design; unchanged from SPEC.md.
- **Historic/timeline views** — would require the events ledger; unchanged
  entry criteria from SPEC.md §2.

## 3. Dependency policy (amends the MVP guardrail, server unchanged)

- The Hub **server** remains Python stdlib only (SPEC.md §15 stands).
- The **TUI client** may use exactly one third-party dependency: `textual`
  (pure Python), pinned in `hub/tui/requirements.txt`, installed in a local
  virtual environment that is never imported by the server or its tests.
- The menu bar app uses Swift with Apple system frameworks only (`AppKit`,
  `Foundation`). No Xcode project, no SwiftPM dependencies, no code signing
  or notarization (personal use, local build).

## 4. TUI (`hub/tui/`)

Package `orchestra_hub_tui`, entry `python -m orchestra_hub_tui`.

- Python ≥ 3.11. Dependency: `textual` (pinned range in `requirements.txt`).
- Layout (single screen):
  - **Header:** Hub status (`ok`/`degraded`/`unreachable`), total active
    tasks, blocker count, last successful poll age.
  - **Left panel:** tree of repositories → their tasks. Active tasks
    emphasized; completed tasks collapsed under the repository node.
    Repository rows show `name (active/completed)`.
  - **Main panel:** detail of the selected task — label, tier, stage,
    status, branch, worktree, summary, blocker (highlighted when non-empty),
    next_action, snapshot age, activities table
    (`agent · capability · state · summary · updated_at`), artifacts table
    (`kind · phase · created_at`).
  - **Footer:** key bindings.
- Keys: arrows/`j`/`k` navigate, `enter`/selection loads detail, `r` forces
  refresh, `q` quits. Nothing else in iteration 1.
- Data flow: poll `/v1/summary` every 5 seconds with ETag; fetch
  `/v1/tasks/{id}` when the selection changes or its fingerprint changed in
  the latest summary. All rendering from allowlisted API fields; the TUI
  never invents states (free-text `stage`/`status` rendered verbatim,
  SPEC.md §8 semantics).
- Attention styling: a task row is highlighted only for the API-reported
  reasons (`blocker` non-empty, `stale == true`).
- Unreachable/degraded: keep the last rendered data dimmed with a visible
  banner "Hub unreachable (retrying)"; never clear the screen to an error.

## 5. Menu bar app (`hub/menubar/`)

Name: **OrchestraHubMenu**. Swift, AppKit, single source file
(`main.swift`, target ≤ ~300 lines), built by `build.sh` with `swiftc` into
a minimal `OrchestraHubMenu.app` bundle (`Info.plist` with
`LSUIElement = true`, no Dock icon, no main window).

- **Status item** (frozen rule, updated after user-approved presentation
  pass): a template SF Symbol icon is always shown; the adjacent
  monospaced-digit title (max 40 characters) is:
  - No active tasks → empty (icon alone).
  - One repository with active tasks → `<name> <active_count>`
    (e.g. `Orchestra 2`).
  - Multiple → `<n> repos · <total_active>` (e.g. `2 repos · 5`).
  - Hub unreachable/degraded → `!` with the icon tinted red.
  - A repository is "active" when it has at least one task with
    `status != "completed"`.
  - A prepared card displays its human ID before the label (for example
    `A1 · Add receipts — preparation`).
  - Cards are grouped by initiative where space permits and display factual
    `blocked by A1` and `parallel with A2` badges from the API. Clients never
    infer or mutate graph relations.
- **Menu contents** (attributed text, informational items disabled):
  - One section per active repository (bold header = repo name), listing
    its active tasks as `● label — stage`; a task with a non-empty
    `blocker` renders instead as red `⛔ label — needs you` with the
    blocker text as an indented secondary item (truncated to 80 chars).
  - When nothing is active: "All quiet — no active tasks".
  - When unreachable: red "Hub unreachable — retrying".
  - A dim "Updated X min ago" footer precedes the actions.
  - Separator, then: "Open panel" (opens `http://127.0.0.1:<port>/` in the
    default browser), "Refresh now", "Quit".
- Poll `/v1/summary` every 30 seconds with `URLSession` and ETag. All menu
  strings come from API fields verbatim (AppKit menu items are plain text;
  no injection surface).
- **Notifications** (`UNUserNotificationCenter`, requires the ad-hoc signed
  bundle produced by `build.sh`): baseline on the first successful poll
  (aggregated "needs you" only if blockers already exist); afterwards one
  aggregated notification when blockers appear and one when they clear.
  Unreachable polls never reset the baseline.
- Current autostart: user LaunchAgent `com.orchestra.tasks` with `RunAtLoad`
  and no `KeepAlive`, pointing at the installed app binary. Installation
  replaces app and plist atomically and restores their previous versions when
  launchd cannot start the replacement.
- Config: reads `port` from `~/.orchestra/hub.toml` with a tolerant
  line-based parse (`port = N` top-level); malformed or missing → 7343.
  The menu bar app must not gain a TOML library for this.

## 6. Web panel styling (optional task)

- Embedded `<style>` block in `panel.py` only: dark theme, monospace
  numerals, colored badges for stage/status and attention reasons.
- No JavaScript, no external assets, no CDN. Escaping and structure
  unchanged; the existing XSS and panel tests must keep passing with at
  most selector/markup-level assertion updates.

## 7. Testing contract

- **TUI:** `unittest` for the HTTP client (ETag round-trip, 304 handling,
  unreachable → typed result, degraded 503 → typed result) and for the
  pure view-model functions (summary JSON → tree rows / detail rows,
  attention flags passthrough). Textual widget/UI behavior is verified by
  a manual smoke checklist, not automated UI tests, in this iteration.
- **Menu bar app:** manual smoke checklist (build succeeds, status item
  appears with the frozen title rule for 0/1/n active repos, menu contents,
  unreachable state, Open panel, LaunchAgent load/unload). No XCTest
  infrastructure in this iteration.
- **Panel styling:** the entire existing `hub/tests` suite passes.
- The existing Hub server test suite must pass untouched at every commit.

## 8. Acceptance journeys

1. From a terminal, see all repositories and tasks, select a task, and read
   its summary, blocker, activities, and artifacts — updating live.
2. Glance at the menu bar and know which repositories have active work and
   how many tasks, without opening anything.
3. Open the menu and see active tasks per repository with stage/status and
   blockers; jump to the web panel from it.
4. Stop the Hub: TUI shows a banner over dimmed data; menu bar shows
   `⚠ Hub`; both recover alone when the Hub returns.
5. SwiftBar is fully removed; the menu bar app survives logout/login via
   its LaunchAgent.

## 9. Anti-over-engineering guardrails (binding)

- No changes to `hub/orchestra_hub/` beyond the optional `panel.py` style
  block. No new endpoints, fields, or query parameters.
- No shared "client framework" between the TUI and the Swift app; they are
  independent consumers of a stable HTTP contract.
- TUI: one package, ≤ 4 modules (`__main__`, `app`, `client`, `viewmodel`).
  If a module approaches ~250 lines, simplify rather than add structure.
- Swift app: one source file plus build script and plists. No SwiftPM
  package, no asset catalogs, no preferences UI.
- No caching layers, no persistence in either client beyond in-memory
  state (the ETag value included).
