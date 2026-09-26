from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from package_plugin import build_plugin, TARGETS
from sync import synchronize
import sync


class DistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.runtime = self.home / '.orchestra-tasks'
        self.environment = mock.patch.dict(os.environ, {'ORCHESTRA_HOME': str(self.home / '.orchestra')})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def apply(self, host='all', action='apply', dry_run=False):
        return synchronize(self.home, self.runtime, host, action, dry_run)

    def test_each_bundle_runs_without_core_and_relocates(self):
        for host in TARGETS:
            plugin = build_plugin(ROOT, self.root / host / 'orchestra-tasks', host)
            moved = self.root / 'relocated' / host / 'orchestra-tasks'
            moved.parent.mkdir(parents=True)
            shutil.move(plugin, moved)
            for page in (moved / 'skills').rglob('*.md'):
                for link in re.findall(r'\]\(([^)]+)\)', page.read_text()):
                    if link.startswith(('http:', 'https:', '#')):
                        continue
                    target = (page.parent / link.split('#')[0]).resolve()
                    self.assertTrue(target.is_relative_to(moved))
                    self.assertTrue(target.is_file(), (page, link))
            result = subprocess.run([sys.executable, '-B', str(moved / 'scripts/task_control.py'), '--state-root', str(self.root / host / 'data'), 'task', 'list'], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'ok')
            self.assertFalse((moved / 'skills/orchestra').exists())
            self.assertFalse((moved / 'scripts/pr.py').exists())
            if host in ('cursor', 'devin'):
                hook_file = moved / ('hooks/hooks.json' if host == 'cursor' else 'hooks.json')
                hooks = json.loads(hook_file.read_text())
                command = hooks['hooks']['sessionStart'][0]['command'] if host == 'cursor' else hooks['SessionStart'][0]['hooks'][0]['command']
                payload = {'conversation_id': 'test-1', 'session_id': 'test-1'}
                environment = {**os.environ, f'{host.upper()}_PLUGIN_ROOT': str(moved)}
                result = subprocess.run(['/bin/sh', '-c', command], input=json.dumps(payload), cwd=self.root,
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('test-1', result.stdout)

    def test_apply_repeat_partial_remove_and_uninstall_preserve_user_content(self):
        codex = self.home / '.codex/config.toml'
        devin = self.home / '.config/devin/config.json'
        codex.parent.mkdir(parents=True)
        devin.parent.mkdir(parents=True)
        codex.write_text('model = "user-model"\n[features]\ncustom = true\n')
        existing = {'model': 'user-model', 'hooks': {'SessionStart': [{'hooks': [{'command': 'user-hook'}]}]}}
        devin.write_text(json.dumps(existing))
        devin.chmod(0o640)
        data = self.home / '.orchestra/control.sqlite3'
        data.parent.mkdir()
        data.write_bytes(b'private cards')
        first = self.apply()
        self.assertEqual(first['status'], 'ok')
        server = tomllib.loads(codex.read_text())['mcp_servers']['orchestra_tasks']
        self.assertEqual(server['default_tools_approval_mode'], 'writes')
        self.assertEqual(server['command'], sys.executable)
        self.assertEqual(self.apply()['status'], 'ok')
        skill = self.home / '.agents/skills/orchestra-task'
        self.assertEqual(skill.resolve(), self.runtime / 'skills/orchestra-task')
        self.assertEqual(self.apply(host='codex', action='uninstall')['status'], 'ok')
        self.assertTrue(skill.is_symlink())
        self.assertNotIn('orchestra_tasks', codex.read_text())
        self.assertEqual(self.apply(action='uninstall')['status'], 'ok')
        self.assertFalse(skill.exists())
        self.assertEqual(json.loads(devin.read_text()), existing)
        self.assertFalse((self.runtime / 'backup').exists())
        self.assertEqual(devin.stat().st_mode & 0o777, 0o640)
        self.assertEqual(data.read_bytes(), b'private cards')
        self.assertIn('model = "user-model"', codex.read_text())
        self.assertEqual(self.apply()['status'], 'ok')

    def test_read_only_preview_and_drift_never_overwrite(self):
        self.apply(dry_run=True)
        self.assertFalse(self.home.exists())
        self.apply()
        helper = self.runtime / 'scripts/task_control.py'
        helper.write_text('user modification\n')
        config = (self.home / '.codex/config.toml').read_bytes()
        for action in ('apply', 'uninstall'):
            with self.assertRaisesRegex(ValueError, 'drift'):
                self.apply(action=action)
        self.assertEqual(helper.read_text(), 'user modification\n')
        self.assertEqual((self.home / '.codex/config.toml').read_bytes(), config)

    def test_unknown_skill_and_old_mcp_registration_block_before_write(self):
        skill = self.home / '.agents/skills/orchestra-task'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('old combined installation')
        with self.assertRaises(ValueError):
            self.apply(host='codex')
        self.assertFalse(self.runtime.exists())
        shutil.rmtree(skill)
        config = self.home / '.codex/config.toml'
        config.parent.mkdir(parents=True)
        config.write_text('[mcp_servers.orchestra_tasks]\ncommand = "old"\n')
        with self.assertRaisesRegex(ValueError, 'combined core'):
            self.apply(host='codex')
        self.assertFalse(self.runtime.exists())

    def test_runtime_cannot_overlap_data_or_core(self):
        for runtime in (self.home, self.home / '.orchestra', self.home / '.orchestra/tasks/runtime', self.home / '.codex/plugins/test', self.home / 'worktrees/task'):
            with self.subTest(runtime=runtime), self.assertRaises(ValueError):
                synchronize(self.home, runtime, 'codex', 'apply', False)
        self.assertFalse(self.home.exists())

    def test_symlink_destination_cannot_escape_owned_paths(self):
        target = self.root / 'outside'
        target.mkdir()
        self.home.mkdir()
        (self.home / '.config').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.apply(host='devin')
        self.assertEqual(list(target.iterdir()), [])

    def test_malformed_hook_configuration_blocks_before_writes(self):
        config = self.home / '.config/devin/config.json'
        config.parent.mkdir(parents=True)
        for value in ([], {'hooks': []}, {'hooks': {'SessionStart': [None]}}, {'hooks': {'SessionStart': [{'hooks': [None]}]}}):
            config.write_text(json.dumps(value))
            before = config.read_bytes()
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.apply()
            self.assertFalse(self.runtime.exists())
            self.assertEqual(config.read_bytes(), before)

    def test_failed_uninstall_restores_files_and_modes(self):
        self.apply()
        before = {path: (path.read_bytes(), path.stat().st_mode & 0o777)
                  for path in self.runtime.rglob('*') if path.is_file() and 'backup' not in path.parts}
        manifest = self.runtime / 'install-manifest.json'
        original = sync.replace_bytes
        failed = False
        def fail_manifest(path, *args, **kwargs):
            nonlocal failed
            if path == manifest and not failed:
                failed = True
                raise OSError('injected manifest failure')
            return original(path, *args, **kwargs)
        with mock.patch.object(sync, 'replace_bytes', fail_manifest), self.assertRaisesRegex(OSError, 'injected'):
            self.apply(action='uninstall')
        for path, (content, mode) in before.items():
            self.assertEqual(path.read_bytes(), content, path)
            self.assertEqual(path.stat().st_mode & 0o777, mode, path)
        self.assertTrue((self.home / '.agents/skills/orchestra-task').is_symlink())
        self.assertEqual(self.apply()['status'], 'ok')

    def test_custom_codex_home_is_owned_without_touching_default(self):
        selected = self.root / 'selected-codex'
        for action in ('apply', 'uninstall'):
            result = synchronize(self.home, self.runtime, 'codex', action, False, codex_home=selected)
            self.assertEqual(result['status'], 'ok')
            self.assertFalse((self.home / '.codex').exists())
        self.assertNotIn('orchestra_tasks', (selected / 'config.toml').read_text())
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            synchronize(self.home, selected / 'runtime', 'codex', 'apply', False, codex_home=selected)

    def test_selected_worktree_root_cannot_be_used_as_runtime(self):
        selected = self.root / 'custom-worktrees'
        with mock.patch.dict(os.environ, {'ORCHESTRA_WORKTREE_ROOT': str(selected)}):
            with self.assertRaisesRegex(ValueError, 'selected worktrees'):
                synchronize(self.home, selected / 'tasks-runtime', 'codex', 'apply', False)
        self.assertFalse(selected.exists())

    def test_new_devin_config_is_private(self):
        self.apply(host='devin')
        self.assertEqual((self.home / '.config/devin/config.json').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
