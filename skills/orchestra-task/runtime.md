# Tasks runtime

Resolve the real absolute path of the `orchestra-task` SKILL.md loaded by the
host. For a source or plugin layout at `<root>/skills/orchestra-task`, use that
root as `ORCHESTRA_TASKS_RUNTIME_ROOT`; it must contain `scripts/task_control.py`,
`control/` and `WORKFLOW.md`. Direct sync installs the same layout and links the
selected host skill to it. Resolve the link before selecting the runtime.

Read only that runtime. A missing resource is an installation error, never a
reason to load another copy. Helpers live in `<root>/scripts`. The optional
`ORCHESTRA_TASKS_HOME` selects a direct-sync installation, not the data root.
Data remains at `$HOME/.orchestra` or the explicit helper `--state-root`. Never
write task data or settings into plugin caches. Re-resolve paths after updates.

Load `orchestra` independently through the host for execution. Its runtime owns
its own skills, helpers and workflow. There are no cross-package relative links.
Tasks alone can capture, research, prepare and inspect cards. It cannot start
Orchestra execution without the separately loaded core skill.
