#!/usr/bin/env python3
"""Run the installed Orchestra task-control CLI."""

from pathlib import Path
import sys

CONTROL_ROOT = Path(__file__).resolve().parents[1] / "control"
sys.path.insert(0, str(CONTROL_ROOT))

from orchestra_control.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
