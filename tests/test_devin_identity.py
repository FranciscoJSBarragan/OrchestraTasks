from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hosts/devin"
HOOK = PLUGIN / "task_identity.py"


class IdentityTests(unittest.TestCase):
    def run_hook(self, payload: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_plugin_declares_devin_session_start_hook(self) -> None:
        hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("SessionStart", hooks)
        self.assertNotIn("version", hooks)
        entries = hooks["SessionStart"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["matcher"], "")
        hook = entry["hooks"][0]
        self.assertEqual(hook["type"], "command")
        self.assertIn("devin_task_identity.py", hook["command"])
        self.assertIn("$DEVIN_PLUGIN_ROOT", hook["command"])

    def test_session_hook_exports_validated_adapter_identity(self) -> None:
        result = self.run_hook({"session_id": "devin-abc123"})
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn(
            "ORCHESTRA_DEVIN_THREAD_ID=devin-abc123",
            output["hookSpecificOutput"]["additionalContext"],
        )

    def test_session_hook_fails_closed_without_valid_identity(self) -> None:
        for payload in (
            {},
            {"session_id": "bad value"},
        ):
            with self.subTest(payload=payload):
                result = self.run_hook(payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("identity hook blocked", result.stderr)
