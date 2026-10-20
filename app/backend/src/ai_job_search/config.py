from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def framework_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "framework"
    return Path(__file__).resolve().parents[4]


def data_home() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return base / "AIJobSearch"


@dataclass
class Config:
    workspace: Path = field(default_factory=lambda: data_home() / "workspace")
    resources: Path = field(default_factory=framework_root)
    origin: str = "http://127.0.0.1:8765"
    start_worker: bool = True
    session_seconds: int = 43_200

    @property
    def frontend(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys._MEIPASS) / "frontend"
        return self.resources / "app" / "frontend" / "dist"
