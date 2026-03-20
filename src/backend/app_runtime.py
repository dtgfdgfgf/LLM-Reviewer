"""
Runtime metadata for the local reviewer application.

Separates transport/runtime concerns (packaged EXE vs dev server) from the
review orchestration code so the FastAPI layer can expose lightweight app
control endpoints without leaking those details into the core pipeline.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(slots=True)
class AppRuntime:
    """Process-level runtime metadata shared through ``app.state``."""

    packaged: bool = False
    base_url: str | None = None
    port: int | None = None
    frontend_dist: Path | None = None
    shutdown_callback: Callable[[], None] | None = None

    @property
    def shutdown_supported(self) -> bool:
        return self.packaged and self.shutdown_callback is not None


def resolve_frontend_dist() -> Path | None:
    """Return the frontend dist directory if it exists."""
    candidates: list[Path] = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "frontend_dist")

    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / "src" / "frontend" / "dist")

    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate

    return None
