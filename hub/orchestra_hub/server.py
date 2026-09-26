"""Loopback GET-only Hub HTTP server (SPEC §7, §8, §11)."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from orchestra_hub.api import (
    summary_payload,
    task_detail_payload,
    tasks_payload,
)
from orchestra_hub.config import HubConfig, load_config
from orchestra_hub.db import HubUnavailable, read_control_snapshot, read_snapshot
from orchestra_hub.panel import render_degraded, render_panel

BIND_HOST = "127.0.0.1"


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _iso_z(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HubRequestHandler(BaseHTTPRequestHandler):
    config: HubConfig

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: Iterable[tuple[str, str]] = (),
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        # 304 must not advertise Content-Length 0 for a non-empty selected
        # representation; omit the header (RFC 9110 §15.4.5).
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        for key, value in extra_headers:
            self.send_header(key, value)
        self.end_headers()
        if status != 304:
            self.wfile.write(body)

    def _send_json(
        self,
        payload: object,
        status: int = 200,
        *,
        cacheable: bool = False,
    ) -> None:
        body = _canonical_json(payload)
        extra: list[tuple[str, str]] = []
        if cacheable:
            etag = '"' + hashlib.sha256(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                self._send(304, b"", "application/json; charset=utf-8", (("ETag", etag),))
                return
            extra.append(("ETag", etag))
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _method_not_allowed(self) -> None:
        body = _canonical_json(
            {"status": "invalid", "reason": "method not allowed"}
        )
        self._send(
            405,
            body,
            "application/json; charset=utf-8",
            (("Allow", "GET"),),
        )

    do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed

    def _snapshots(self, stack: ExitStack) -> tuple[object | None, object | None]:
        coordination = None
        control = None
        if self.config.database.is_file():
            coordination = stack.enter_context(read_snapshot(self.config.database))
        if self.config.control_database.is_file():
            control = stack.enter_context(read_control_snapshot(self.config.control_database))
        if coordination is None and control is None:
            raise HubUnavailable(
                "missing",
                f"databases not found: {self.config.database}, {self.config.control_database}",
            )
        return coordination, control

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        now = datetime.now(timezone.utc)

        if path == "/":
            self._handle_panel(now)
            return
        if path == "/v1/health":
            self._handle_health(now)
            return
        if path == "/v1/summary":
            self._handle_summary(now)
            return
        if path == "/v1/tasks":
            status_values = parse_qs(parsed.query).get("status")
            status = status_values[0] if status_values else None
            self._handle_tasks(now, status=status)
            return
        if path.startswith("/v1/tasks/"):
            task_id = path[len("/v1/tasks/") :]
            if not task_id or "/" in task_id:
                self._send_json(
                    {"status": "invalid", "reason": "not found"},
                    status=404,
                )
                return
            self._handle_detail(now, task_id)
            return
        self._send_json(
            {"status": "invalid", "reason": "not found"},
            status=404,
        )

    def _handle_health(self, now: datetime) -> None:
        try:
            with ExitStack() as stack:
                coordination, control = self._snapshots(stack)
                versions = {
                    "coordination": coordination.execute("PRAGMA user_version").fetchone()[0]
                    if coordination is not None else None,
                    "control": control.execute("PRAGMA user_version").fetchone()[0]
                    if control is not None else None,
                }
            self._send_json(
                {
                    "status": "ok",
                    "database": "available",
                    "schema_versions": versions,
                    "generated_at": _iso_z(now),
                }
            )
        except HubUnavailable as error:
            self._send_json(
                {
                    "status": "degraded",
                    "database": error.condition,
                    "detail": error.detail,
                    "generated_at": _iso_z(now),
                }
            )

    def _handle_panel(self, now: datetime) -> None:
        try:
            with ExitStack() as stack:
                coordination, control = self._snapshots(stack)
                summary = summary_payload(
                    coordination, self.config, now, control_connection=control
                )
            html = render_panel(summary, now)
        except HubUnavailable as error:
            html = render_degraded(error.condition, error.detail)
        body = html.encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def _handle_summary(self, now: datetime) -> None:
        try:
            with ExitStack() as stack:
                coordination, control = self._snapshots(stack)
                payload = summary_payload(
                    coordination, self.config, now, control_connection=control
                )
            self._send_json(payload, cacheable=True)
        except HubUnavailable as error:
            self._send_json(
                {
                    "status": "degraded",
                    "database": error.condition,
                    "detail": error.detail,
                },
                status=503,
            )

    def _handle_tasks(self, now: datetime, *, status: str | None) -> None:
        try:
            with ExitStack() as stack:
                coordination, control = self._snapshots(stack)
                payload = tasks_payload(
                    coordination,
                    self.config,
                    now,
                    status=status,
                    control_connection=control,
                )
            self._send_json(payload, cacheable=True)
        except HubUnavailable as error:
            self._send_json(
                {
                    "status": "degraded",
                    "database": error.condition,
                    "detail": error.detail,
                },
                status=503,
            )

    def _handle_detail(self, now: datetime, task_id: str) -> None:
        try:
            with ExitStack() as stack:
                coordination, control = self._snapshots(stack)
                payload = task_detail_payload(
                    coordination,
                    self.config,
                    now,
                    task_id,
                    control_connection=control,
                )
            if payload is None:
                self._send_json(
                    {"status": "invalid", "reason": "unknown task"},
                    status=404,
                )
                return
            self._send_json(payload, cacheable=True)
        except HubUnavailable as error:
            self._send_json(
                {
                    "status": "degraded",
                    "database": error.condition,
                    "detail": error.detail,
                },
                status=503,
            )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def create_server(config: HubConfig) -> ThreadingHTTPServer:
    handler = type(
        "BoundHubRequestHandler",
        (HubRequestHandler,),
        {"config": config},
    )
    return ThreadingHTTPServer((BIND_HOST, config.port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestra_hub")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    server = create_server(config)
    host, port = server.server_address[:2]
    sys.stderr.write(f"Orchestra Hub listening on http://{host}:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nShutting down Orchestra Hub\n")
    finally:
        server.server_close()
    return 0
