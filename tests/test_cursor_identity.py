from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hosts/cursor"
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

    def test_plugin_declares_session_start_identity_hook(self) -> None:
        hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(hooks["version"], 1)
        self.assertEqual(
            hooks["hooks"]["sessionStart"],
            [{"command": 'python3 "${CURSOR_PLUGIN_ROOT}/scripts/cursor_task_identity.py"'}],
        )

    def test_session_hook_exports_validated_adapter_identity(self) -> None:
        result = self.run_hook(
            {
                "hook_event_name": "sessionStart",
                "conversation_id": "cursor.conversation-1",
                "session_id": "cursor.conversation-1",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(
            output["env"]["ORCHESTRA_HOST_THREAD_ID"],
            "cursor.conversation-1",
        )
        self.assertIn(
            "ORCHESTRA_HOST_THREAD_ID=cursor.conversation-1",
            output["additional_context"],
        )

    def test_session_hook_fails_closed_without_matching_safe_identity(self) -> None:
        for payload in (
            {},
            {"conversation_id": "one", "session_id": "two"},
            {"conversation_id": "bad value", "session_id": "bad value"},
        ):
            with self.subTest(payload=payload):
                result = self.run_hook(payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("identity hook blocked", result.stderr)
