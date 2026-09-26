# Orchestra Tasks architecture

WORKFLOW.md owns operational authority and lifecycle. This repository supplies
one optional companion; it has no private imports from Orchestra.

### Durable task intake

`control/orchestra_control` and the thin `task_control.py` entry point own
one private prepared-task Kanban. Their direct consumers are
`$orchestra-task`, the stdio MCP adapter, the read-only Hub, and harnesses using
the JSON CLI. Capture and preparation are inert. They never launch an execution
host, create a checkout, select permissions, or own an implementation process.

`control.sqlite3` schema v8 owns UUID and immutable human ID, briefs, origin
references, notes, preparation state, revision and document digests,
host-namespaced native-chat ownership, transfer generation, recoverable trash,
and safe-stop/cancellation timestamps. Complete private context, specification, and marker
documents live under `$HOME/.orchestra/tasks/<short-id>/`. The v3 migration
leaves preexisting tasks without human IDs and retains the old run, turn, and
interaction tables as read-only legacy history. New public code exposes no App
Server operation. Native-chat adoption requires the adapter-provided conversation
identity (`CODEX_THREAD_ID` on Codex; on Cursor, the plugin `sessionStart` hook
verifies `session_id == conversation_id` and exports that value as
`ORCHESTRA_HOST_THREAD_ID`; on Grok Build, `GROK_SESSION_ID`; on Devin, the
`SessionStart` hook supplies `session_id` as `ORCHESTRA_DEVIN_THREAD_ID`). The
chat then invokes normal Orchestra. Coordinator receives the same UUID only
after checkout creation. Hub joins both stores by UUID and remains GET-only.
The macOS app obtains bounded mutation authority only through the same local
Task Control JSON CLI; it never adds a Hub write endpoint. Prepared documents
may be replaced before first adoption, while any card with execution-owner
history must resume its existing checkout and plan.
Cooperative transfer remains owning-chat plus stable checkpoint. When that
chat cannot release the card, explicit reclaim from another native host chat
swaps ownership without passing through `ready`.

The explicit storage migration described in WORKFLOW "Durable task intake"
upgrades to schema v8. Ordinary commands never migrate an older database.
The migration atomically reconstructs the task table after recognizing the exact
column shape of v2 through v7, including the incompatible ownership and
safe-stop/trash variants that both used `user_version = 5`; its owner-harness
allowlist adds `devin`. Historical un-namespaced owners become `codex`;
unknown shapes roll back without changes, and every migration must pass
`foreign_key_check` before commit.

Task Control alone computes card action capabilities. The Hub reads compatible
control schemas and projects observational state only; the macOS app obtains
cards and capabilities from Task Control and joins only Hub progress by exact
UUID. State-check-plus-mutation operations serialize with an immediate SQLite
write transaction so lifecycle decisions cannot race adoption or completion.
A safe-stop request never interrupts an active owner. Transfer and finish
are blocked while it is pending; reclaim preserves it. At a stable boundary the
owner cleans resources, marks the existing plan blocked, and acknowledges with
its Codex, Cursor, Grok, or Devin identity. Cancellation preserves the
checkout and plan so reopening may return ownership to the same prior
conversation.

The short-ID namespace has one allocator: the transaction in Task Control.
Coordinator stores no short ID, direct tasks have none, and clients may expose
one only after an exact UUID join to Control. Consequently concurrent chats do
not need leases or a second counter: prepared cards serialize in the existing
SQLite transaction, while direct tasks use repository plus title.

The Tasks-owned `scripts/git_identity.py` resolver separates clone identity from execution
location: `repository` is the validated primary worktree of the clone,
`worktree` is the specific task checkout, and Git common-dir is the comparison
key for cross-worktree preparation, adoption, resume, and registration. This
uses only local Git metadata. It neither resolves remotes nor treats a GitHub
URL as identity, so separate clones intentionally remain separate repository
groups. The existing columns and API shape are sufficient; rows that contain
older checkout paths remain readable and are not backfilled.

## Runtime and packaging

`skills/orchestra-task` resolves its selected package. `scripts/` contains the
JSON CLI, stdio MCP, observation helper and Git identity resolver. Host hooks
only expose verified native conversation identity. Portable, Cursor, Grok and
Devin bundles copy these same sources. `scripts/sync.py` is the separate direct
installation route, with its own manifest in `~/.orchestra-tasks` by default.
It preserves existing data in `~/.orchestra` and owns no host permission policy.

The Hub server reads both stores. The TUI consumes its HTTP interface. The macOS
client combines that observation with card actions through the Tasks CLI. Its
bundled fallback is built from this source, and explicit runtime selection
never silently changes after an error.

Execution, local plans and Git delivery stay in the separately loaded Orchestra
runtime. Tasks preparation is self-contained; adoption requires core discovery.
The core attachment boundary uses existing card identity, owner/stop queries
and delivery results. No shared import package or new workflow engine is needed.
