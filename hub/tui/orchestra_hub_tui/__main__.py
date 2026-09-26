"""Entry point: python -m orchestra_hub_tui."""
from __future__ import annotations

from .app import HubTuiApp


def main() -> None:
    HubTuiApp().run()


if __name__ == "__main__":
    main()
