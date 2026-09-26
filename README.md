# Orchestra Tasks

Optional local task preparation, native-chat ownership and progress views for
[Orchestra](https://github.com/FranciscoJSBarragan/Orchestra). Install Orchestra alone when you want engineering skills or its full
execution workflow without a task manager.

Tasks owns cards, global snapshots, MCP and the Hub/TUI/macOS clients. Preparation
works independently. Adopting a card for execution requires the separately
installed Orchestra skill. Installing Tasks does not activate execution or
tracking. Direct runs appear in Hub only when explicitly tracked.

## Build a plugin

```sh
python3 scripts/package_plugin.py --target portable --output /tmp/bundles/orchestra-tasks
```

Targets: `portable` (Codex), `cursor`, `grok`, `devin`. Load the result with the
host plugin manager. Cursor and Devin bundles include only their Task identity
hook; no server starts merely by loading a bundle. Use the bounded CLI without
MCP, or explicitly configure the host's MCP loader to run
`python3 <selected-runtime>/scripts/task_mcp.py`. Plugin installation inherits
host permissions and never edits global permission policy.

## Direct sync

```sh
python3 scripts/sync.py status --host codex
python3 scripts/sync.py apply --host codex --dry-run
python3 scripts/sync.py apply --host codex
python3 scripts/sync.py uninstall --host codex
```

Use one route per host. Direct sync honors `CODEX_HOME` (or `--codex-home`)
and `ORCHESTRA_TASKS_HOME` (or `--runtime`); `--home` supports isolated fixtures. Direct sync owns its distinct runtime, skill links and
Task-only hook/MCP configuration; it never owns Orchestra permissions. Codex
MCP registration retains the existing per-server write-approval default. Data in
`~/.orchestra` stays intact on update and uninstall. Existing combined Orchestra
installations must first retire their owned Task resources using the new core
sync; unknown files or drift require reconciliation. No automatic data migration
is performed. See [workflow](WORKFLOW.md) for schema operations and authority.

## Execution boundary

The root checks card owner and stop state before commit, completion, delivery
and returning to the user. An unavailable authoritative query blocks the card;
failed observational snapshots do not. Git and the reviewed plan remain the
execution truth. Source roots and plugin-cache paths are resolved at each load.

Delivery registration requires fresh exact Git/GitHub proof supplied by the
caller. If registration fails after delivery, retry registration only. The
original owning chat can recover after plan cleanup. Recovery from a different
chat for a completed card is not supported; report a blocker rather than
replaying delivery. This existing limitation is separate from repository split.

## Validation and clients

Run `python3 scripts/validate_suite.py --full`. To verify an identified source
pair without installing into your real home:

```sh
ORCHESTRA_CORE_SOURCE=/path/to/Orchestra python3 -B -m unittest discover -s tests -p test_core_pair.py
```

The pair test uses disposable homes and a version-only Codex fixture; it checks
installer interoperability, not live host discovery or model behavior.
See [architecture](ARCHITECTURE.md) for component ownership. The Hub and macOS app are
separate optional clients under `hub/`; their installation is explicit.
See [source provenance](PROVENANCE.md) for the extraction baseline.

Direct sync retains one private pre-update backup per owned path while that
path is installed. Successful removal deletes that backup; cleanup failures
are returned as warnings. Individual replacements are atomic; caught failures
restore prior contents and modes. An interrupted multi-file installation is
not a transaction: inspect its manifest and drift before retrying. No automatic
recovery or overwrite of unknown files is promised.
