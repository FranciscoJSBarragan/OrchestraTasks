"""Shared sys.path setup for the TUI test suite.

Import this module before any ``orchestra_hub_tui`` import so the package
resolves under ``python3 -m unittest discover -s hub/tui/tests``.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TUI_ROOT = Path(__file__).resolve().parent.parent
if str(_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_TUI_ROOT))
