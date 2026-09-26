from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def validate(full: bool) -> int:
    for path in ROOT.rglob('*.py'):
        if any(part in path.parts for part in ('__pycache__', '.venv', 'dist', 'build')):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if any(name == '_common' or name == 'codex' or name.startswith('codex.') for name in modules):
                print(f'FAIL: {path}: imports private Orchestra source')
                return 1
    if not full:
        print('OK: Tasks source boundary and syntax')
        return 0
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    for directory in ('tests', 'hub/tests', 'hub/tui/tests'):
        result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', directory], cwd=ROOT, env=environment)
        if result.returncode:
            return result.returncode
    print('OK: Tasks full validation')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate Orchestra Tasks independently of core')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--full', action='store_true')
    group.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    return validate(args.full)


if __name__ == '__main__':
    raise SystemExit(main())
