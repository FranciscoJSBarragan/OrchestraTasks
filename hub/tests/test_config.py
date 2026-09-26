"""TOML configuration contract tests (SPEC section 10 / PLAN Task 3)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.config import (  # noqa: E402
    DEFAULT_PORT,
    HubConfig,
    PinnedRepository,
    default_state_root,
    load_config,
)


class ConfigTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "absent-hub.toml"
        config = load_config(missing)
        self.assertIsInstance(config, HubConfig)
        self.assertEqual(config.port, DEFAULT_PORT)
        self.assertEqual(DEFAULT_PORT, 7343)
        self.assertEqual(config.stale_after_minutes, 60)
        self.assertEqual(config.pinned_repositories, ())
        self.assertTrue(str(config.state_root).endswith(".orchestra"))
        self.assertEqual(config.database.name, "state.sqlite3")
        self.assertEqual(config.database, config.state_root / "state.sqlite3")
        self.assertEqual(
            config.control_database, config.state_root / "control.sqlite3"
        )

    def test_full_file_overrides_and_repository_name_fallback(self) -> None:
        root = Path(tempfile.mkdtemp())
        state_root = root / "custom-state"
        config_path = root / "hub.toml"
        config_path.write_text(
            "\n".join(
                [
                    "port = 8123",
                    "stale_after_minutes = 15",
                    f'state_root = "{state_root}"',
                    "[[repositories]]",
                    'path = "/Users/me/Code/NeniTPV"',
                    'name = "NeniTPV"',
                    "[[repositories]]",
                    'path = "/Users/me/Code/Orchestra"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        config = load_config(config_path)
        self.assertEqual(config.port, 8123)
        self.assertEqual(config.stale_after_minutes, 15)
        self.assertEqual(config.state_root, state_root)
        self.assertEqual(config.database, state_root / "state.sqlite3")
        self.assertEqual(
            config.pinned_repositories,
            (
                PinnedRepository(path="/Users/me/Code/NeniTPV", name="NeniTPV"),
                PinnedRepository(path="/Users/me/Code/Orchestra", name="Orchestra"),
            ),
        )

    def test_unknown_keys_are_ignored(self) -> None:
        root = Path(tempfile.mkdtemp())
        config_path = root / "hub.toml"
        config_path.write_text(
            "\n".join(
                [
                    "port = 9001",
                    'host = "0.0.0.0"',
                    "bind_host = \"example.invalid\"",
                    'mystery = "ignored"',
                    "[[repositories]]",
                    'path = "/tmp/pinned"',
                    'name = "Pinned"',
                    'extra = true',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        config = load_config(config_path)
        self.assertEqual(config.port, 9001)
        self.assertEqual(
            config.pinned_repositories,
            (PinnedRepository(path="/tmp/pinned", name="Pinned"),),
        )
        self.assertFalse(hasattr(config, "host"))
        self.assertFalse(hasattr(config, "bind_host"))
        self.assertFalse(hasattr(config, "mystery"))

    def test_database_property(self) -> None:
        state_root = Path("/tmp/orchestra-hub-state")
        config = HubConfig(
            state_root=state_root,
            port=7343,
            stale_after_minutes=60,
            pinned_repositories=(),
        )
        self.assertEqual(config.database, state_root / "state.sqlite3")
        self.assertEqual(config.database.name, "state.sqlite3")

    def test_home_unset_raises_runtime_error(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("HOME", None)
            with self.assertRaises(RuntimeError):
                default_state_root()


if __name__ == "__main__":
    unittest.main()
