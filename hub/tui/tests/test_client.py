"""Tests for the TUI hub client (stdlib only, no textual import)."""
from __future__ import annotations

import http.server
import json
import tempfile
import threading
import unittest
from pathlib import Path

import support  # noqa: F401  (sys.path setup)
from orchestra_hub_tui.client import HubClient, default_base_url, read_port


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves canned responses configured on the server instance."""

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        server = self.server
        server.requests.append(dict(self.headers))
        etag = server.etag
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        body = json.dumps(server.body).encode("utf-8")
        self.send_response(server.status)
        self.send_header("Content-Type", "application/json")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class ClientHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.status = 200
        self.server.body = {"status": "ok"}
        self.server.etag = None
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        port = self.server.server_address[1]
        self.client = HubClient(f"http://127.0.0.1:{port}")

    def test_ok_then_not_modified_via_etag(self) -> None:
        self.server.etag = '"abc123"'
        first = self.client.fetch("/v1/summary")
        self.assertEqual(first.kind, "ok")
        self.assertEqual(first.payload, {"status": "ok"})
        second = self.client.fetch("/v1/summary")
        self.assertEqual(second.kind, "not_modified")
        self.assertIsNone(second.payload)
        self.assertEqual(
            self.server.requests[1].get("If-None-Match"), '"abc123"'
        )

    def test_etag_is_tracked_per_path(self) -> None:
        self.server.etag = '"abc123"'
        self.client.fetch("/v1/summary")
        self.assertNotIn("If-None-Match", self.server.requests[0])
        self.client.fetch("/v1/tasks")
        self.assertNotIn("If-None-Match", self.server.requests[1])

    def test_degraded_503(self) -> None:
        self.server.status = 503
        self.server.body = {
            "status": "degraded",
            "database": "missing",
            "detail": "no database",
        }
        result = self.client.fetch("/v1/summary")
        self.assertEqual(result.kind, "degraded")
        self.assertIn("no database", result.detail)

    def test_invalid_404(self) -> None:
        self.server.status = 404
        self.server.body = {"status": "invalid", "reason": "unknown task"}
        result = self.client.fetch("/v1/tasks/absent")
        self.assertEqual(result.kind, "invalid")

    def test_invalid_json_body(self) -> None:
        self.server.body = None  # json.dumps(None) -> "null", not a dict
        result = self.client.fetch("/v1/summary")
        self.assertEqual(result.kind, "invalid")


class ClientUnreachableTest(unittest.TestCase):
    def test_connection_refused(self) -> None:
        probe = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = probe.server_address[1]
        probe.server_close()
        client = HubClient(f"http://127.0.0.1:{port}")
        result = client.fetch("/v1/summary")
        self.assertEqual(result.kind, "unreachable")
        self.assertTrue(result.detail)


class DefaultBaseUrlTest(unittest.TestCase):
    def test_no_override_uses_local_hub(self) -> None:
        self.assertTrue(default_base_url({}).startswith("http://127.0.0.1:"))

    def test_override_is_used_and_normalized(self) -> None:
        env = {"ORCHESTRA_HUB_URL": "https://studio.example.ts.net/"}
        self.assertEqual(
            default_base_url(env), "https://studio.example.ts.net"
        )

    def test_non_http_override_is_ignored(self) -> None:
        env = {"ORCHESTRA_HUB_URL": "studio.example.ts.net"}
        self.assertTrue(
            default_base_url(env).startswith("http://127.0.0.1:")
        )


class ReadPortTest(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.root = Path(tempdir.name)

    def test_missing_file_defaults(self) -> None:
        self.assertEqual(read_port(self.root), 7343)

    def test_configured_port(self) -> None:
        (self.root / "hub.toml").write_text("port = 7400\n", encoding="utf-8")
        self.assertEqual(read_port(self.root), 7400)

    def test_malformed_toml_defaults(self) -> None:
        (self.root / "hub.toml").write_text("port = = nope", encoding="utf-8")
        self.assertEqual(read_port(self.root), 7343)

    def test_non_integer_port_defaults(self) -> None:
        (self.root / "hub.toml").write_text('port = "high"\n', encoding="utf-8")
        self.assertEqual(read_port(self.root), 7343)


if __name__ == "__main__":
    unittest.main()
