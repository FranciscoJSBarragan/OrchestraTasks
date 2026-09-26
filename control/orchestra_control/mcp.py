"""Harness-neutral stdio MCP adapter for prepared-task capture and preparation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


S = {"type": "string"}
TOOLS: dict[str, dict[str, Any]] = {
    "task_create": {
        "description": "Create one durable prepared-task card without starting Codex or Orchestra.",
        "inputSchema": schema(
            {
                "title": S,
                "brief": S,
                "source_harness": S,
                "source_conversation": S,
                "source_message": S,
                "repository": S,
                "idempotency_key": S,
            },
            ["title", "brief"],
        ),
        "readOnly": False,
    },
    "task_get": {
        "description": "Get one prepared-task card and its private document references.",
        "inputSchema": schema({"task": S}, ["task"]),
        "readOnly": True,
    },
    "task_decompose": {
        "description": "Atomically create a confirmed minimal initiative from one draft card without starting Codex.",
        "inputSchema": schema(
            {
                "task": S,
                "initiative": schema({"title": S, "brief": S}, ["title", "brief"]),
                "cards": {
                    "type": "array",
                    "minItems": 2,
                    "items": schema(
                        {
                            "key": S,
                            "title": S,
                            "brief": S,
                            "repository": S,
                            "decomposition_reason": S,
                        },
                        ["key", "title", "brief", "repository"],
                    ),
                },
                "dependencies": {
                    "type": "array",
                    "items": schema(
                        {
                            "task": S,
                            "blocked_by": S,
                            "condition": {"type": "string", "enum": ["completed", "delivered"]},
                            "reason": S,
                        },
                        ["task", "blocked_by", "condition", "reason"],
                    ),
                },
                "confirmed": {"type": "boolean"},
                "idempotency_key": S,
            },
            [
                "task",
                "initiative",
                "cards",
                "dependencies",
                "confirmed",
                "idempotency_key",
            ],
        ),
        "readOnly": False,
    },
    "task_list": {
        "description": "List durable prepared-task cards.",
        "inputSchema": schema({"include_archived": {"type": "boolean"}}),
        "readOnly": True,
    },
    "task_note": {
        "description": "Attach durable context to a prepared-task card.",
        "inputSchema": schema(
            {
                "task": S,
                "body": S,
                "source_harness": S,
                "source_reference": S,
                "idempotency_key": S,
            },
            ["task", "body"],
        ),
        "readOnly": False,
    },
    "task_prepare": {
        "description": "Store confirmed repository context and specification; never starts a chat or checkout.",
        "inputSchema": schema(
            {
                "task": S,
                "repository": S,
                "prepared_revision": S,
                "repository_context": S,
                "specification": S,
                "confirmed": {"type": "boolean"},
            },
            ["task", "repository", "repository_context", "specification", "confirmed"],
        ),
        "readOnly": False,
    },
    "task_archive": {
        "description": "Archive a task that is not adopted by a native host chat.",
        "inputSchema": schema({"task": S}, ["task"]),
        "readOnly": False,
    },
    "task_restore": {
        "description": "Restore an archived prepared-task card.",
        "inputSchema": schema({"task": S}, ["task"]),
        "readOnly": False,
    },
}


def tool_definition(name: str, definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": definition["description"],
        "inputSchema": definition["inputSchema"],
        "annotations": {
            "readOnlyHint": definition["readOnly"],
            "destructiveHint": False,
            "idempotentHint": name != "task_create",
            "openWorldHint": False,
        },
    }


def cli_arguments(name: str, arguments: dict[str, Any]) -> list[str]:
    _, command = name.split("_", 1)
    result = ["task", command]
    if name == "task_decompose":
        manifest = {
            "initiative": arguments.get("initiative"),
            "cards": arguments.get("cards"),
            "dependencies": arguments.get("dependencies"),
        }
        result.extend(("--task", str(arguments.get("task", ""))))
        result.extend(("--manifest-json", json.dumps(manifest, ensure_ascii=False, sort_keys=True)))
        if arguments.get("confirmed") is True:
            result.append("--confirmed")
        result.extend(("--idempotency-key", str(arguments.get("idempotency_key", ""))))
        return result
    for key, value in arguments.items():
        if value is None or value is False:
            continue
        flag = "--" + key.replace("_", "-")
        if value is True:
            result.append(flag)
        else:
            result.extend((flag, str(value)))
    return result


def call_tool(name: str, arguments: Any) -> dict[str, Any]:
    if name not in TOOLS or not isinstance(arguments, dict):
        return {
            "content": [{"type": "text", "text": json.dumps({"status": "invalid", "reason": "unknown tool or invalid arguments"})}],
            "isError": True,
        }
    helper = Path(__file__).resolve().parents[2] / "scripts" / "task_control.py"
    environment = None
    if name == "task_decompose":
        environment = {**os.environ, "ORCHESTRA_MCP_INTERNAL": "1"}
    completed = subprocess.run(
        [sys.executable, str(helper), *cli_arguments(name, arguments)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "unavailable",
            "reason": completed.stderr.strip() or "task-control helper returned invalid JSON",
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {
        "content": [{"type": "text", "text": encoded}],
        "structuredContent": payload,
        "isError": completed.returncode != 0 or payload.get("status") != "ok",
    }


def response(
    request_id: Any,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response(None, error={"code": -32700, "message": "Parse error"})
            continue
        if not isinstance(message, dict) or message.get("jsonrpc") not in (None, "2.0"):
            response(
                message.get("id") if isinstance(message, dict) else None,
                error={"code": -32600, "message": "Invalid Request"},
            )
            continue
        if "id" not in message:
            continue
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol = requested if requested in PROTOCOLS else PROTOCOLS[-1]
            response(
                message["id"],
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "orchestra-task-control", "version": "0.4.0"},
                },
            )
        elif method == "ping":
            response(message["id"], {})
        elif method == "tools/list":
            response(
                message["id"],
                {"tools": [tool_definition(name, definition) for name, definition in TOOLS.items()]},
            )
        elif method == "tools/call":
            response(
                message["id"],
                call_tool(str(params.get("name", "")), params.get("arguments", {})),
            )
        else:
            response(message["id"], error={"code": -32601, "message": "Method not found"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
