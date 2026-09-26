"""Loopback GET-only Hub server tests (SPEC §7, §8, §11 / PLAN Task 8)."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import support  # noqa: F401  # path setup before orchestra_hub imports

from orchestra_hub.config import HubConfig, PinnedRepository  # noqa: E402
from orchestra_hub.server import BIND_HOST, create_server  # noqa: E402


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_root = Path(tempfile.mkdtemp())
        self.database = support.create_state_db(self.state_root)
        self.task = support.insert_task(
            self.database,
            id="task-server-1",
            label="Server task",
            repository="/obs/alpha",
            blocker="needs review",
            status="active",
            updated_at="2099-01-01T00:00:00Z",
        )
        self.config = HubConfig(
            state_root=self.state_root,
            port=0,
            stale_after_minutes=60,
            pinned_repositories=(
                PinnedRepository(path="/pinned/repo", name="Pinned"),
            ),
        )
        self.server = create_server(self.config)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

        self.degraded_root = Path(tempfile.mkdtemp())
        self.degraded_config = HubConfig(
            state_root=self.degraded_root,
            port=0,
            stale_after_minutes=60,
            pinned_repositories=(),
        )
        self.degraded_server = create_server(self.degraded_config)
        self.degraded_thread = threading.Thread(
            target=self.degraded_server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.degraded_thread.start()
        dhost, dport = self.degraded_server.server_address[:2]
        self.degraded_base = f"http://{dhost}:{dport}"

    def tearDown(self) -> None:
        for server in (getattr(self, "server", None), getattr(self, "degraded_server", None)):
            if server is None:
                continue
            server.shutdown()
            server.server_close()
        for thread in (
            getattr(self, "thread", None),
            getattr(self, "degraded_thread", None),
        ):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive(), "server thread did not stop")

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        base: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = f"{base or self.base}{path}"
        request = urllib.request.Request(url, method=method)
        if headers:
            for key, value in headers.items():
                request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return (
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read(),
                )
        except urllib.error.HTTPError as error:
            try:
                return (
                    error.code,
                    {k.lower(): v for k, v in error.headers.items()},
                    error.read(),
                )
            finally:
                error.close()

    def test_bind_host_is_loopback(self) -> None:
        self.assertEqual(BIND_HOST, "127.0.0.1")
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_health_summary_detail_and_panel(self) -> None:
        status, _headers, body = self._request("/v1/health")
        self.assertEqual(status, 200)
        health = json.loads(body.decode("utf-8"))
        self.assertEqual(health["database"], "available")
        self.assertEqual(health["status"], "ok")

        status, _headers, body = self._request("/v1/summary")
        self.assertEqual(status, 200)
        summary = json.loads(body.decode("utf-8"))
        self.assertEqual(summary["material_fingerprint_version"], 3)
        self.assertTrue(
            any(
                "blocker" in entry.get("reasons", [])
                for entry in summary["attention"]
            )
        )

        status, _headers, body = self._request(f"/v1/tasks/{self.task['id']}")
        self.assertEqual(status, 200)
        detail = json.loads(body.decode("utf-8"))
        self.assertEqual(detail["task"]["id"], self.task["id"])

        status, headers, body = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertIn("Orchestra Hub", body.decode("utf-8"))

    def test_etag_304_for_summary_tasks_filtered_and_detail(self) -> None:
        paths = (
            "/v1/summary",
            "/v1/tasks",
            "/v1/tasks?status=active",
            f"/v1/tasks/{self.task['id']}",
        )
        for path in paths:
            with self.subTest(path=path):
                status, headers, body = self._request(path)
                self.assertEqual(status, 200)
                etag = headers.get("etag")
                self.assertTrue(etag)
                self.assertTrue(body)

                status304, headers304, body304 = self._request(
                    path, headers={"If-None-Match": etag}
                )
                self.assertEqual(status304, 304)
                self.assertEqual(body304, b"")
                self.assertEqual(headers304.get("etag"), etag)
                content_length = headers304.get("content-length")
                # 304 must not advertise length 0 for a non-empty 200 body
                # (omit Content-Length, or echo the selected representation length).
                if content_length is None:
                    pass
                else:
                    self.assertEqual(int(content_length), len(body))
                    self.assertNotEqual(content_length, "0")

    def test_mutating_methods_are_rejected(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, headers, body = self._request("/v1/summary", method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers.get("allow"), "GET")
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(payload["status"], "invalid")
                self.assertEqual(payload["reason"], "method not allowed")

    def test_unknown_path_and_task(self) -> None:
        status, _headers, body = self._request("/v1/nope")
        self.assertEqual(status, 404)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload, {"status": "invalid", "reason": "not found"})

        status, _headers, body = self._request("/v1/tasks/absent-id")
        self.assertEqual(status, 404)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["reason"], "unknown task")

    def test_degraded_missing_database(self) -> None:
        status, _headers, body = self._request("/v1/summary", base=self.degraded_base)
        self.assertEqual(status, 503)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["status"], "degraded")
        self.assertEqual(payload["database"], "missing")

        status, _headers, body = self._request("/v1/health", base=self.degraded_base)
        self.assertEqual(status, 200)
        health = json.loads(body.decode("utf-8"))
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["database"], "missing")

        status, headers, body = self._request("/", base=self.degraded_base)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertIn("degraded", body.decode("utf-8").lower())


if __name__ == "__main__":
    unittest.main()
