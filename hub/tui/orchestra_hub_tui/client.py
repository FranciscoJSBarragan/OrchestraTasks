"""Read-only HTTP client for the Orchestra Hub API (stdlib only)."""
from __future__ import annotations

import json
import os
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_PORT = 7343
TIMEOUT_SECONDS = 3.0

Kind = Literal["ok", "not_modified", "degraded", "unreachable", "invalid"]


@dataclass(frozen=True)
class FetchResult:
    kind: Kind
    payload: dict | None = None
    detail: str = ""


def read_port(state_root: Path | None = None) -> int:
    root = state_root if state_root is not None else Path.home() / ".orchestra"
    config = root / "hub.toml"
    try:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_PORT
    port = data.get("port", DEFAULT_PORT)
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536:
        return port
    return DEFAULT_PORT


def default_base_url(environ: dict | None = None) -> str:
    """Local Hub by default; ORCHESTRA_HUB_URL overrides (remote viewing)."""
    env = environ if environ is not None else os.environ
    override = str(env.get("ORCHESTRA_HUB_URL", "")).strip()
    if override.startswith(("http://", "https://")):
        return override.rstrip("/")
    return f"http://127.0.0.1:{read_port()}"


class HubClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._etags: dict[str, str] = {}

    def fetch(self, path: str) -> FetchResult:
        request = urllib.request.Request(self.base_url + path)
        etag = self._etags.get(path)
        if etag:
            request.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
                new_etag = response.headers.get("ETag")
        except urllib.error.HTTPError as error:
            return self._from_http_error(error)
        except (urllib.error.URLError, OSError) as error:
            return FetchResult(kind="unreachable", detail=str(error))
        payload = _decode(body)
        if payload is None:
            return FetchResult(kind="invalid", detail="response body is not a JSON object")
        if new_etag:
            self._etags[path] = new_etag
        return FetchResult(kind="ok", payload=payload)

    def _from_http_error(self, error: urllib.error.HTTPError) -> FetchResult:
        detail = ""
        with error:
            payload = _decode(error.read())
        if payload is not None:
            detail = str(payload.get("detail") or payload.get("reason") or "")
        if error.code == 304:
            return FetchResult(kind="not_modified")
        if error.code == 503:
            return FetchResult(kind="degraded", payload=payload, detail=detail)
        return FetchResult(
            kind="invalid", payload=payload, detail=detail or f"HTTP {error.code}"
        )


def _decode(body: bytes) -> dict | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
