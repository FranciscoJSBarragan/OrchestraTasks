---
name: orchestra-task
description: Use only for an explicit `$orchestra-task` invocation or an unequivocal request to prepare, minimally decompose, adopt, continue, transfer, reclaim, inspect, archive, or restore one Orchestra Kanban task. Do not use for ordinary mentions of tasks or Orchestra. Preparation never creates a host chat, branch, worktree, or implementation run; adoption happens only from the user's current native Codex, Cursor, Grok Build, or Devin chat and then activates the normal `$orchestra` workflow.
---

# Orchestra Task

Read [runtime resources](runtime.md) before resolving workflow files or helpers.

Use the selected runtime and invoke the deterministic helper at
`${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py`. Never write
`control.sqlite3` or task documents directly. Human task IDs are
case-insensitive; always display their canonical uppercase form.

## Prepare a task card

Preparation is discovery, not Orchestra execution. It never creates or owns a
host chat, branch, worktree, plan, implementation process, or permission
profile.

1. Require a non-empty objective. Resolve the canonical Git root when known;
   otherwise capture a `draft` and report that repository context is still
   required.
2. Create the card with a caller-stable idempotency key and exact origin
   references. Return both its immutable human ID (`A1`, `A2`, …) and UUID.
3. Before marking it `ready`, perform focused read-only repository research
   of the relevant callers, behavior, constraints, tests and documentation.
   Record the inspected revision, evidence and unresolved material questions.
   Distinguish observed behavior from product policy; do not infer approval.
   Align and obtain explicit
   confirmation of a specification containing Objective, User-visible
   behavior, Constraints, Acceptance, Exclusions, Decisions, and Open
   questions. Preparation is not a tier choice and grants no implementation
   authority.
4. Store the complete context and confirmed specification with `task prepare`.
   The helper writes private `0600` documents below
   `$HOME/.orchestra/tasks/<short-id>/` and binds them to the inspected Git
   revision by digest.

```sh
python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
  task create --title "<title>" --brief "<brief>" \
  --source-harness codex --idempotency-key "<stable-key>" \
  --repository "<canonical-root>"

python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
  task prepare --task "<short-id>" --repository "<canonical-root>" \
  --repository-context-file "<private-context-file>" \
  --specification-file "<private-specification-file>" --confirmed
```

## Propose the smallest sensible card set

Keep one card as the default. Complexity, sequential implementation steps,
review, verification, and delivery phases remain inside that card's Orchestra
plan. Do not turn phases into cards.

Propose multiple cards only when there are real independent boundaries of
execution, acceptance, repository, or delivery. Present the smallest useful
set—normally two or three—with each card's objective, acceptance, repository,
dependencies and their reasons, plus which cards can proceed in parallel. More
than three cards requires one specific `decomposition_reason` per card.

If the decomposition is the agent's recommendation, obtain explicit user
confirmation before creating any additional card. A user instruction that
already specifies the split is confirmation; do not ask twice. Without
confirmation, keep the original draft unchanged.

After confirmation, write the approved manifest to a private JSON file and run
one atomic decomposition. The first manifest card reuses the source ID and the
response maps manifest keys to human IDs. Never retry with a new idempotency key
after an uncertain result; first query the source card.

```sh
python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
  task decompose --task "<short-id>" --manifest-file "<private-manifest.json>" \
  --confirmed --idempotency-key "<stable-key>"
```

Use only `blocked_by` dependencies with a non-empty reason. Choose `completed`
when the predecessor's implementation, review, verification, and terminal
commit are enough. Choose `delivered` only when the dependent card requires
verified local integration or PR merge. No dependency path means the cards are
parallel; never persist a `related` relation. Every card remains a
self-contained specification because dependencies order work but do not carry
context. Decomposition is fixed after creation; material restructuring creates
a new initiative instead of rewriting ready or adopted cards.

## Adopt or continue from a native host chat

When the user says `Start A1 with Orchestra`, `Arranca A1 con Orchestra`, or
an equivalent unequivocal instruction in a native Codex, Cursor, Grok Build, or Devin chat:

1. Before changing ownership, resolve the separately installed `orchestra` skill
   through the host. If it is missing or ambiguous, report that execution
   requires Orchestra and leave the card unchanged. Then run `task adopt --task <short-id> --repository <current-checkout-root>` from that
   chat. The helper requires the adapter-provided conversation identity; never
   supply, invent, copy, or override it. On Codex that identity is
   `CODEX_THREAD_ID`. On Cursor the plugin's `sessionStart` hook verifies
   `session_id == conversation_id` and exposes that exact value as
   `ORCHESTRA_HOST_THREAD_ID`. On Grok Build that identity is `GROK_SESSION_ID`.
   On Devin the `SessionStart` hook supplies `session_id` as
   `ORCHESTRA_DEVIN_THREAD_ID`.
   If the identity is missing, adopt is `blocked`.
   The helper resolves the clone's primary worktree as `repository`, keeps the
   current checkout as `worktree` where applicable, and compares Git common-dir;
   a linked worktree from the prepared clone is valid, while a different local
   clone or Git repository is not. Do not query GitHub or remotes for identity.
2. Open the returned private context and specification paths. Verify that the
   objective matches the user's instruction and adopt the specification as
   already confirmed context. Never expose the marker or private document body
   through the Hub or MCP response.
3. Activate `$orchestra` in the current chat. Reuse its normal model, tier,
   permission, checkout, planning, review, verification, commit, and delivery
   gates. The Kanban never chooses or elevates permissions.
4. If `context_action` is `use_prepared`, treat the prepared
   `repository_context` as satisfied at its exact revision. If it is
   `repository_context_delta`, dispatch only a focused delta after the normal
   task checkout exists. Reconfirm the specification only when that delta
   materially changes it.
5. After Orchestra creates the checkout and initializes task state, register
   Coordinator with `coordination.py task create --task-id <kanban-uuid>` so
   Control and Coordinator use the same technical identity. Registration never
   occurs before the checkout exists.
6. If `resume_existing_checkout` is true, first recover the exact existing
   Orchestra worktree and approved `plan.md`; do not create another checkout.
   Do not require a clean worktree or a phase commit. Preserve uncommitted
   unique work. Report observed Git and plan status (`clean` or `dirty`,
   `active`/`blocked`/`completed`, HEAD) and continue. Skip the previous host's
   wait and close contract; spawn fresh workers on this host. Re-read this
   host's assignment matrix and recommend an assigned tier; a recorded Codex
   tier is not a Cursor assignment. Permissions stay those of the current chat.

## Transfer, reclaim, finish, and manage

- Transfer only from the adopting chat at a stable Orchestra checkpoint:
  `task transfer --task <short-id> --stable-checkpoint`. The next native chat
  adopts the same UUID and resumes the existing worktree and plan.
- Reclaim only from a different native host chat after an explicit user
  resume or reclaim of that ID (`retoma A1`, `continue A1 here`, `reclaim A1`).
  That instruction is the authority checkpoint; do not ask again while the
  facts are unchanged. Reclaim abandons the previous chat; do not use it while
  that chat is still working. The helper does not ping the previous host.
  Pass `--authorized` only after that explicit instruction in this chat:

  ```sh
  python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
    task reclaim --task "<short-id>" --repository "<canonical-or-checkout>" \
    --authorized
  ```

  The helper keeps the card `adopted`, swaps `adopted_thread_id`, and returns
  `resume_existing_checkout: true`. Ordinary `task adopt` of a card owned by
  another thread remains `busy`. Then activate `$orchestra` and follow the
  resume rule above.
- Mark the Kanban card completed from the adopting chat after the reviewed
  terminal commit exists:

  ```sh
  python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
    task finish --task "<short-id>" --repository "<task-checkout>" \
    --task-revision "<terminal-sha>"
  ```

  A reviewed PR correction inside the same completed plan may update that SHA
  from the same owning chat before delivery. Delivery authority remains
  separate.
- After an authorized helper returns `delivery_verified: true`, register its
  exact result from the owning native chat:

  ```sh
  python3 "${ORCHESTRA_TASKS_RUNTIME_ROOT}/scripts/task_control.py" \
    task record-delivery --task "<short-id>" --repository "<base-checkout>" \
    --task-revision "<terminal-sha>" \
    --delivery-revision "<integrated-base-sha>" \
    --kind "<local-integration|pr-merge>"
  ```

  Opening a PR, holding a task, or an unverified mutation is not delivery. A
  same-repository dependent card may still require its current checkout to
  contain the recorded delivery revision; update that checkout explicitly and
  retry adoption. Never pull automatically.
- Inspect with `task get` or `task list`; add later context with `task note`.
- Archive only on explicit direction. An adopted task must first reach a stable
  finish or transfer checkpoint. Archive and restore never delete Git,
  documents, worktrees, plans, or host chats.
- On `busy`, `blocked`, or digest/identity mismatch, report the exact reason and
  stop. Never steal ownership except through `task reclaim --authorized` after
  an explicit reclaim or resume in this chat. Never invent identity, edit the
  database, start a host through a CLI or App Server, replay a turn, or bypass
  the current chat's permissions.

## Explicitly track a direct run

Only when requested, register the existing Orchestra checkout through the
resolved Tasks `coordination.py`. Supply its absolute path and returned ID in
transient packets; a tracked direct run has no card identity. Follow WORKFLOW
"Optional observation". Installing Tasks alone never starts tracking.

## Attached execution and delivery

Read this runtime's WORKFLOW for authoritative ownership, safe-stop and delivery
reconciliation rules. The host-loaded `orchestra` skill owns implementation and
local artifacts; do not resolve its files relative to this package. Before each
core attachment checkpoint query owner and stop state. Missing authoritative
evidence blocks; observation failure does not. After delivery cleanup, retry
only exact registration with fresh delivery proof. If the original owning chat
cannot be resumed, registration recovery is blocked; completed-card reclaim
from another chat is not supported.
