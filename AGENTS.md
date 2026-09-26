# Orchestra Tasks contributor rules

WORKFLOW.md owns card and observation policy. Core Orchestra owns engineering
execution and is a separately installed product, never a private Python import.
Preserve data and unrelated configuration. Do not add explanatory source
comments or narrative docstrings; retain functional/legal directives.
Run `python3 scripts/validate_suite.py --full` for source changes. New tests
must demonstrate behavior or a named regression. No real account, card store
or active installation is part of tests. Implementation does not authorize
commit, push, publication or active installation changes.
