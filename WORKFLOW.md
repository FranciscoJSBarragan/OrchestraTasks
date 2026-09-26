# Orchestra Tasks workflow

Orchestra Tasks owns card preparation, native-chat ownership and optional global
observation. It never installs or activates the Orchestra core implicitly.
The loaded `orchestra-task` skill resolves this runtime; the separately loaded
`orchestra` skill owns execution, review, verification and Git delivery.

## Durable task intake

Owner commands require exactly one adapter-provided host identity. Multiple
host identity variables block adoption or owner mutation rather than selecting
one by precedence; resolve the inherited environment in the owning native chat.
Never fabricate or overwrite an identity to get past that check.

Task Control schema upgrades are explicit installation work. A normal command
against an older schema returns `unavailable` with the migration command and
does not rebuild the database. Before `task_control.py --state-root <state-root>
storage migrate`, stop all Task Control/Hub consumers, make a consistent SQLite
backup (including committed WAL contents), and update every plugin, direct-sync
helper, MCP consumer, and bundled Hub copy sharing that state root. Rehearse on
a copy, then migrate the selected root and check integrity and the consumers.
Schema 8 supports Devin ownership; schema-7-only consumers must not be restarted
against it. Do not lower `user_version` to roll back. Restore the verified backup
and matching installations only while consumers are stopped and after preserving
any newer work. This command is not a phase tool or permission to migrate active
installations during ordinary task adoption.

The installed `task_control.py` helper is a harness-neutral JSON boundary for a
small local prepared-task Kanban. `task create` assigns an immutable human ID
and UUID; `task note` accepts caller-stable idempotency keys and bounded source
references. Capturing or preparing a card does not activate Orchestra, launch
an execution host, choose permissions or tier, create a checkout, or authorize
implementation.

Task Control is the sole allocator of the `A1`, `A2`, ... namespace. Neither a
native chat nor Coordinator may derive a short ID from task order, concurrent
activity, a branch name, or an example in these documents. A direct Orchestra
task has no short ID. Clients join a short ID to execution state only when the
Coordinator task UUID exactly matches the Control UUID.

For every newly resolved Git path, `repository` means the validated primary
worktree of that local clone and `worktree` means the task's concrete checkout.
The shared resolver records the current checkout, primary worktree, Git
common-dir, and HEAD. Preparation, adoption, resume, decomposition, and
Coordinator registration compare the common-dir when available, so another
linked worktree from the same clone is valid and a different clone or Git
repository is rejected. Draft paths that are not yet usable Git checkouts stay
verbatim until preparation. Existing rows are never mass-rewritten, and legacy
path identity remains a tolerated idempotency fallback. No remote, URL, or
GitHub lookup participates in this identity.

A card becomes `ready` only after focused repository research of callers, behavior, constraints, tests and
documentation, plus explicit specification confirmation. The helper binds
private `repository-context.md`, `specification.md`, and a marker under
`$HOME/.orchestra/tasks/<short-id>/` to the inspected full Git revision and
digests. `control.sqlite3` stores the Kanban identity and preparation metadata;
legacy `runs`, `turns`, and `interactions` remain readable after migration but
new code never writes or exposes App Server operations.

Adoption occurs only inside the user's current native Codex, Cursor, Grok
Build, or Devin chat.
`task adopt` requires the adapter-provided conversation identity; no caller may
invent or override that identity. On Codex that identity is `CODEX_THREAD_ID`
(UUID). On Cursor the plugin's `sessionStart` hook verifies that `session_id`
matches `conversation_id` and exposes that exact value through
`ORCHESTRA_HOST_THREAD_ID`; if it is unavailable, adopt is `blocked`. On Grok
Build that identity is `GROK_SESSION_ID`; if it is unavailable, adopt is
`blocked`. On Devin that identity is `ORCHESTRA_DEVIN_THREAD_ID`, supplied by
the `SessionStart` hook's `session_id`; if it is unavailable, adopt is
`blocked`. The chat then explicitly activates Orchestra,
inherits its current permissions, and applies the installed checkout policy. Matching Git
reuses prepared context; changed Git requires a focused `repository_context`
delta and specification reconfirmation only when the result materially changes.
After checkout and task-state initialization, Coordinator registers with the
Kanban UUID. An explicit stable-checkpoint transfer releases ownership so
another native chat can resume the same worktree and plan. When that owning
chat cannot release the card, an explicit user resume or reclaim of the same
ID in a different native host chat runs `task reclaim --authorized`, swaps
ownership in one transaction, and resumes the existing worktree and plan.
Ordinary `task adopt` of a card owned by another thread remains `busy`.
Reclaim abandons the previous chat; do not use it while that chat is still
working. The helper does not ping the previous host.

One card is the default. The agent proposes a minimal two- or three-card
initiative only for independent execution, acceptance, repository, or delivery
boundaries and obtains explicit confirmation unless the user already directed
the split. The confirmed decomposition is one transaction: it reuses the
source draft as the first card, creates the remaining cards, allocates global
human IDs, and persists only an immutable `blocked_by` DAG. More than three
cards requires a specific reason for every card. Failed validation consumes no
IDs and creates no partial rows. Cards without a dependency path are parallel;
no `related` relation is stored. Each card remains self-contained and receives
its own preparation, adoption, checkout, plan, review, verification, terminal
commit, and delivery evidence.

Blocked cards may be prepared but not adopted. `completed` requires the
blocking card to finish implementation, independent gates, and its terminal
commit. `delivered` additionally requires an exact verified local integration
or PR merge registered by the predecessor's owning native chat. For cards in
the same Git common-dir, adoption also proves the delivered base revision is an
ancestor of the checkout HEAD and otherwise asks the user to update it; no
helper pulls automatically. Opening a PR or choosing hold never satisfies
delivery.

The prepared specification is an already satisfied final-specification
checkpoint. It may be replaced only before the card has any execution-owner
history. After first adoption, transfer and safe-stop reopening preserve and
resume the existing checkout and plan rather than rewriting the prepared
documents. A checkout at the prepared revision proceeds to formal planning
after the ordinary tier choice. A changed revision requests only a focused
context delta; only a material specification change requires confirmation
again.

The public stdio MCP exposes capture, query, notes, preparation, confirmed
decomposition, archive, and restore only. It cannot adopt, transfer, reclaim,
finish, record delivery, start an execution host, or mutate a checkout. The Hub
remains GET-only. The native macOS app invokes the local JSON CLI for its
bounded card actions and never mutates through Hub HTTP. Git, the approved
`plan.md`, the native conversation, and explicit user authority remain
authoritative for formal work.

### Cooperative safe stop and card lifecycle

`task request-stop` records a cooperative request and never interrupts a tool,
agent, process, or mutable implementation owner. For every adopted card, the
root queries current Task Control state at real pauses: before a phase commit,
before terminal completion, before PR or local delivery, and whenever it
returns to the user for input. Routine capability dispatches and stable
handoffs inside an actively running phase do not each require a query. A
pending request prevents new work at those boundaries. `task transfer` and
`task finish` reject it; `task reclaim --authorized` preserves it.

After the current owner reaches a stable handoff, it closes its exact owned
resources using the normal cleanup contract, writes the existing approved plan
as `blocked` with the safe stop as blocker and resume as next action, then runs
`task acknowledge-stop` with the adapter-provided owner identity. Codex uses
`CODEX_THREAD_ID`, Cursor uses `ORCHESTRA_HOST_THREAD_ID`, Grok uses
`GROK_SESSION_ID`, and Devin uses `ORCHESTRA_DEVIN_THREAD_ID`. Acknowledgement
changes the card to `cancelled`, releases
current ownership to the matching previous owner fields, and preserves the
checkout and plan. `task reopen` returns it to `ready`; the same previous owner
may adopt it and must resume the exact checkout and blocked plan instead of
creating a second task. A withdrawn request resumes normal boundary checks.

Archive remains metadata-only. Trash is recoverable and hidden from Hub
results. Permanent purge requires the caller to type the exact short ID and is
allowed only for a trashed, unprepared draft with no owner, notes,
dependencies, initiative, legacy runs, completion, or delivery evidence. Task
Control quarantines only that card's exact documents directory inside the
database transaction, restores it on rollback, and never reuses the consumed
short ID. If post-commit removal of that quarantine fails, the purge result
reports the residual private documents explicitly and native clients surface
the warning instead of claiming a clean deletion. The stdio MCP exposes none
of purge, owner mutation, or safe-stop commands.

## Storage implementation

Schema v4 adds descriptive `task_initiatives`, immutable directed
`task_dependencies`, Git common-dir identity, and exact completion and delivery
revisions. Initiative membership groups cards but has no state or executable
human ID. Parallelism is a read-time graph derivation. The Control service owns
transactional decomposition, DAG validation, dependency satisfaction, and
native-chat delivery registration; the CLI supplies current Git identity and
ancestry evidence. The Hub accepts control schemas v3 through v8 during migration,
projects initiative/dependency fields through an allowlist, and stays GET-only.

## Optional observation

Only an adopted card or an explicit request to track a direct Orchestra run
enables observation. Installing Tasks alone never does. Resolve the Tasks
`scripts/coordination.py` from this runtime and register the exact worktree after
Orchestra initializes it. An adopted card uses its existing UUID; a direct run
uses the returned observation ID and never invents a card ID. Pass that ID and
the absolute helper path to participating roles. Do not infer helper paths from
Orchestra or persist plugin-cache paths. Re-resolve after restart; direct-run
tracking is not silently restarted. Registration converges by worktree.

The helper stores snapshots in `$HOME/.orchestra/state.sqlite3` or the explicit
`--state-root`. Authorized snapshot writes are best-effort. Missing or failed
snapshots never block execution or delivery. This differs from an unavailable
authoritative card query, which blocks the attached-card path. Semantic
artifacts and their cleanup remain owned by Orchestra, not the snapshot store.

The root updates task stage, tier, revision, summary, blocker, and next action
only at material transitions. Each delegated agent may update its own activity
at start, final outcome, or blocker; there are no heartbeats. Stages and states
are descriptive labels with no transition graph. Timestamps indicate freshness
but never prove that an agent or process is live.

For external progress surfaces, the root writes `summary` as one concise,
localized milestone line at material transitions only: phase started, blocked
(with the blocker), phase committed, and the delivery outcome (implementation
complete, hold, PR open/clean/merged, or verified local integration). Once an
approved plan exists it may prefix the manifest's exact `Phase X/Y`. Interior
review numbering, accepted-finding counts, and per-return transitions are not
required milestones; only findings accepted by the root ever appear. Managed
or hybrid checkout mode is not itself reported unless it explains a delivery
blocker. The root never derives milestones from free-text agent output and
creates no event ledger, locale field, or second progress state machine.

Coordination keeps machine-facing `tier`, `stage`, `status`, activity
`capability`, and activity `state` labels in English. User-visible task
`summary`, `blocker`, `next_action`, and activity `summary` use the user-facing
language selected by applicable instructions, falling back to the language of
the user's conversation when none is configured. Localized prose preserves
literal errors, commands, paths, and identifiers verbatim. This language policy
does not change the English-only internal plan, semantic artifacts, code, or
technical logs, and requires no locale field or coordination schema change.

## Delivery reconciliation

Before delivery, complete the card at the reviewed terminal revision and check
its owner and stop state. Retain the exact card identity, terminal revision and
delivery inputs in the native handoff before the core helper can clean the plan.
Consume the actual verified delivery result and register it from the owning
chat. If registration fails after Git delivery, report that delivery succeeded
and Tasks registration is pending. Never repeat the merge or integration.

Reconciliation requires fresh exact Git/GitHub proof, not merely CLI success.
`record-delivery` validates owner, repository and the completed revision; it
does not prove that the change was delivered. Local delivery must contain the
terminal revision; PR proof must identify the unambiguous merged PR for that
exact terminal revision, including squash semantics and the actual merge SHA.
Retry only the identical idempotent registration. Missing proof blocks.

The existing completed revision and native conversation allow the original
owner to reconcile after plan cleanup. A different chat cannot reclaim a
completed card today. If the original owner cannot be resumed, report an
explicit registration blocker. Cross-chat recovery is a separate future change.
