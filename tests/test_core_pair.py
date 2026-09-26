from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

TASKS = Path(__file__).resolve().parents[1]
CORE = os.environ.get('ORCHESTRA_CORE_SOURCE')


@unittest.skipUnless(CORE, 'Set ORCHESTRA_CORE_SOURCE for the optional installed-pair acceptance')
class CorePairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        tools = self.root / 'bin'
        tools.mkdir()
        codex = tools / 'codex'
        codex.write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 1\nprintf "codex-cli 0.146.0\\n"\n')
        codex.chmod(0o700)
        self.environment = {**os.environ, 'HOME': str(self.home), 'CODEX_HOME': str(self.home / '.codex'),
                            'ORCHESTRA_HOME': str(self.home / '.orchestra'),
                            'ORCHESTRA_TASKS_HOME': str(self.home / '.orchestra-tasks'),
                            'PATH': str(tools), 'PYTHONDONTWRITEBYTECODE': '1'}
        self.core = Path(CORE).resolve()
        self.assertTrue((self.core / 'codex/scripts/sync.py').is_file())

    def run_sync(self, product, action):
        source = self.core / 'codex/scripts/sync.py' if product == 'core' else TASKS / 'scripts/sync.py'
        result = subprocess.run([sys.executable, '-B', str(source), action, '--host', 'all'],
                                env=self.environment, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['status'], 'ok', payload)

    def files(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob('*') if path.is_file() and '__pycache__' not in path.parts}

    def test_both_install_orders_updates_and_removal_are_independent(self):
        for first, second in (('core', 'tasks'), ('tasks', 'core')):
            with self.subTest(first=first):
                self.run_sync(first, 'apply')
                self.run_sync(second, 'apply')
                data = self.home / '.orchestra/control.sqlite3'
                data.write_bytes(b'private Task data')
                runtime = self.home / '.orchestra-tasks'
                tasks_before = self.files(runtime)
                self.run_sync('core', 'apply')
                self.run_sync('core', 'uninstall')
                self.assertEqual(self.files(runtime), tasks_before)
                self.assertTrue((self.home / '.agents/skills/orchestra-task').is_symlink())
                config = self.home / '.codex/config.toml'
                self.assertIn('orchestra_tasks', tomllib.loads(config.read_text())['mcp_servers'])
                self.run_sync('core', 'apply')
                core_helper = self.home / '.orchestra/scripts/task_state.py'
                before = core_helper.read_bytes()
                self.run_sync('tasks', 'apply')
                self.run_sync('tasks', 'uninstall')
                self.assertEqual(core_helper.read_bytes(), before)
                parsed = tomllib.loads(config.read_text())
                self.assertNotIn('orchestra_tasks', parsed.get('mcp_servers', {}))
                self.assertEqual(parsed['approval_policy'], 'on-request')
                self.assertTrue((self.home / '.agents/skills/orchestra/SKILL.md').is_file())
                self.assertEqual(data.read_bytes(), b'private Task data')
                self.run_sync('core', 'uninstall')


if __name__ == '__main__':
    unittest.main()
