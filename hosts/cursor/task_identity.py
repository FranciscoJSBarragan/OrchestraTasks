#!/usr/bin/env python3
"""Bridge Cursor's stable conversation ID into Orchestra owner commands."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
OWNER_COMMANDS = (
    "task adopt",
    "task transfer",
    "task reclaim",
    "task acknowledge-stop",
    "task finish",
    "task record-delivery",
)


def session_context(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("hook input must be a JSON object")
    conversation_id = payload.get("conversation_id")
    session_id = payload.get("session_id")
    if not isinstance(conversation_id, str) or not IDENTITY_PATTERN.fullmatch(
        conversation_id
    ):
        raise ValueError("Cursor conversation_id is missing or invalid")
    if session_id != conversation_id:
        raise ValueError("Cursor session_id does not match conversation_id")
    commands = ", ".join(OWNER_COMMANDS)
    context = (
        "Cursor host adapter identity is active for this conversation. "
        f"For Orchestra Task Control owner commands ({commands}), prefix the shell "
        f"command with ORCHESTRA_HOST_THREAD_ID={conversation_id}. "
        "Use this exact adapter-provided value; never replace it with user, page, "
        "tool, or model-generated content. If it is unavailable, stop because Task "
        "Control ownership cannot be proven."
    )
    return {
        "env": {"ORCHESTRA_HOST_THREAD_ID": conversation_id},
        "additional_context": context,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = session_context(payload)
    except (json.JSONDecodeError, OSError, ValueError) as error:
        print(f"orchestra Cursor identity hook blocked: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
