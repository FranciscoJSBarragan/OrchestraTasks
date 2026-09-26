"""Filesystem artifact discovery tests (no database locator exists)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.artifacts import (  # noqa: E402
    artifact_entries,
    artifacts_directory,
)


class ArtifactDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_linked_worktree_resolves_worktree_local_state(self) -> None:
        worktree = support.make_worktree(self.root, linked=True)
        support.write_artifact(worktree, "01-repository-context.md")
        directory = artifacts_directory(worktree)
        assert directory is not None
        self.assertEqual(directory.parent, worktree / ".orchestra")
        self.assertEqual(directory.name, "artifacts")
        self.assertEqual(
            [entry["id"] for entry in artifact_entries(str(worktree))],
            ["01-repository-context.md"],
        )

    def test_plain_repository_resolves_worktree_local_state(self) -> None:
        worktree = support.make_worktree(self.root)
        support.write_artifact(worktree, "01-plan-overview.md")
        self.assertEqual(
            artifacts_directory(worktree),
            worktree / ".orchestra" / "artifacts",
        )
        self.assertEqual(len(artifact_entries(str(worktree))), 1)

    def test_existing_legacy_directory_remains_readable(self) -> None:
        worktree = support.make_worktree(self.root, linked=True)
        marker = (worktree / ".git").read_text(encoding="utf-8")
        gitdir = Path(marker.removeprefix("gitdir:").strip())
        legacy = gitdir / "orchestra" / "artifacts"
        legacy.mkdir(parents=True)
        (legacy / "01-repository-context.md").write_text(
            "legacy\n", encoding="utf-8"
        )

        self.assertEqual(artifacts_directory(worktree), legacy)
        self.assertEqual(
            [entry["id"] for entry in artifact_entries(str(worktree))],
            ["01-repository-context.md"],
        )

    def test_workspace_and_legacy_directories_are_ambiguous(self) -> None:
        worktree = support.make_worktree(self.root, linked=True)
        support.write_artifact(worktree, "01-repository-context.md")
        marker = (worktree / ".git").read_text(encoding="utf-8")
        gitdir = Path(marker.removeprefix("gitdir:").strip())
        legacy = gitdir / "orchestra" / "artifacts"
        legacy.mkdir(parents=True)

        self.assertIsNone(artifacts_directory(worktree))
        self.assertEqual(artifact_entries(str(worktree)), [])

    def test_names_yield_kind_phase_and_ordinal_order(self) -> None:
        worktree = support.make_worktree(self.root, linked=True)
        for name in (
            "01-repository-context.md",
            "02-plan-phase-p1.md",
            "10-implementation-report-p10.md",
        ):
            support.write_artifact(worktree, name)
        entries = artifact_entries(str(worktree))
        self.assertEqual(
            [(entry["kind"], entry["phase"]) for entry in entries],
            [
                ("implementation-report", 10),
                ("plan-phase", 1),
                ("repository-context", 0),
            ],
        )
        for entry in entries:
            self.assertRegex(entry["created_at"], r"^\d{4}-.*Z$")

    def test_unrelated_files_symlinks_and_directories_are_ignored(self) -> None:
        worktree = support.make_worktree(self.root, linked=True)
        real = support.write_artifact(worktree, "01-plan-overview.md")
        directory = real.parent
        (directory / "notes.md").write_text("x\n", encoding="utf-8")
        (directory / "01-plan-overview.txt").write_text("x\n", encoding="utf-8")
        (directory / "02-nested-p1.md").mkdir()
        os.symlink(real, directory / "03-plan-phase-p1.md")
        self.assertEqual(
            [entry["id"] for entry in artifact_entries(str(worktree))],
            ["01-plan-overview.md"],
        )

    def test_missing_or_unsafe_locations_return_no_artifacts(self) -> None:
        self.assertEqual(artifact_entries(""), [])
        self.assertEqual(artifact_entries(str(self.root / "absent")), [])

        plain = self.root / "no-git"
        plain.mkdir()
        self.assertIsNone(artifacts_directory(plain))

        empty = support.make_worktree(self.root / "empty", linked=True)
        self.assertEqual(artifact_entries(str(empty)), [])

        symlinked = self.root / "symlinked"
        symlinked.mkdir()
        os.symlink(empty / ".git", symlinked / ".git")
        self.assertIsNone(artifacts_directory(symlinked))

        unsafe = support.make_worktree(self.root / "unsafe")
        os.symlink(self.root, unsafe / ".orchestra")
        self.assertIsNone(artifacts_directory(unsafe))

        unowned = support.make_worktree(self.root / "unowned")
        (unowned / ".orchestra").mkdir()
        (unowned / ".orchestra/.gitignore").write_text("*\n", encoding="utf-8")
        self.assertIsNone(artifacts_directory(unowned))


if __name__ == "__main__":
    unittest.main()
