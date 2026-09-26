"""Material fingerprint for notifiable task changes (SPEC section 6)."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

MATERIAL_FINGERPRINT_VERSION = 3
MATERIAL_FIELDS = (
    "blocker", "head_revision", "id", "label", "next_action",
    "stage", "status", "summary", "tier", "initiative", "blocked_by", "parallel_with",
    "stop_requested_at",
)


def material_fingerprint(task: Mapping[str, object]) -> str:
    payload = {field: str(task.get(field)) for field in MATERIAL_FIELDS}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
