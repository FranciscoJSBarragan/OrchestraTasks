"""TOML configuration loader for Orchestra Hub."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 7343
DEFAULT_STALE_AFTER_MINUTES = 60


@dataclass(frozen=True)
class PinnedRepository:
    path: str
    name: str


@dataclass(frozen=True)
class HubConfig:
    state_root: Path
    port: int
    stale_after_minutes: int
    pinned_repositories: tuple[PinnedRepository, ...] = ()

    @property
    def database(self) -> Path:
        return self.state_root / "state.sqlite3"

    @property
    def control_database(self) -> Path:
        return self.state_root / "control.sqlite3"


def default_state_root() -> Path:
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME is unset; cannot resolve default state root")
    return Path(home) / ".orchestra"


def load_config(path: Path | None = None) -> HubConfig:
    if path is None:
        path = default_state_root() / "hub.toml"

    state_root = default_state_root()
    port = DEFAULT_PORT
    stale_after_minutes = DEFAULT_STALE_AFTER_MINUTES
    pinned: list[PinnedRepository] = []

    if path.is_file():
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        if "port" in data:
            port = int(data["port"])
        if "stale_after_minutes" in data:
            stale_after_minutes = int(data["stale_after_minutes"])
        if "state_root" in data:
            state_root = Path(str(data["state_root"]))
        for entry in data.get("repositories", []):
            repo_path = str(entry["path"])
            name = entry.get("name")
            if not name:
                name = Path(repo_path).name
            else:
                name = str(name)
            pinned.append(PinnedRepository(path=repo_path, name=name))

    return HubConfig(
        state_root=state_root,
        port=port,
        stale_after_minutes=stale_after_minutes,
        pinned_repositories=tuple(pinned),
    )
