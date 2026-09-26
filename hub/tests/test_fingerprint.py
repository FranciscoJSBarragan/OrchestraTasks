"""Material fingerprint contract tests (SPEC section 6 / PLAN Task 2)."""
from __future__ import annotations

import re
import unittest

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.fingerprint import (  # noqa: E402
    MATERIAL_FIELDS,
    MATERIAL_FINGERPRINT_VERSION,
    material_fingerprint,
)


FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMMUTABLE_OR_TIMESTAMP_FIELDS = (
    "updated_at",
    "created_at",
    "base_revision",
    "repository",
    "worktree",
    "branch",
)


class MaterialFingerprintTests(unittest.TestCase):
    def test_version_and_format(self) -> None:
        self.assertEqual(MATERIAL_FINGERPRINT_VERSION, 3)
        fingerprint = material_fingerprint(support.task_row())
        self.assertRegex(fingerprint, FINGERPRINT_RE)

    def test_deterministic_under_key_reordering_and_extra_keys(self) -> None:
        base = support.task_row()
        reordered = {key: base[key] for key in reversed(list(base))}
        with_extra = dict(base)
        with_extra["extra_noise"] = "ignored"
        with_extra["updated_at"] = "2099-01-01T00:00:00Z"
        self.assertEqual(
            material_fingerprint(base),
            material_fingerprint(reordered),
        )
        self.assertEqual(
            material_fingerprint(base),
            material_fingerprint(with_extra),
        )

    def test_material_fields_include_task_relations(self) -> None:
        self.assertEqual(len(MATERIAL_FIELDS), 13)
        self.assertEqual(
            set(MATERIAL_FIELDS),
            {
                "blocker",
                "head_revision",
                "id",
                "label",
                "next_action",
                "stage",
                "status",
                "summary",
                "tier",
                "initiative",
                "blocked_by",
                "parallel_with",
                "stop_requested_at",
            },
        )

    def test_each_material_field_changes_hash(self) -> None:
        base = support.task_row()
        baseline = material_fingerprint(base)
        for field in MATERIAL_FIELDS:
            mutated = dict(base)
            mutated[field] = f"mutated-{field}"
            self.assertNotEqual(
                baseline,
                material_fingerprint(mutated),
                msg=f"mutating {field} must change the fingerprint",
            )

    def test_timestamps_and_immutable_location_fields_excluded(self) -> None:
        base = support.task_row()
        baseline = material_fingerprint(base)
        for field in IMMUTABLE_OR_TIMESTAMP_FIELDS:
            mutated = dict(base)
            mutated[field] = f"changed-{field}"
            self.assertEqual(
                baseline,
                material_fingerprint(mutated),
                msg=f"mutating {field} must not change the fingerprint",
            )


if __name__ == "__main__":
    unittest.main()
