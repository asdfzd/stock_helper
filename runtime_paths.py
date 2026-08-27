from __future__ import annotations

import sys
from pathlib import Path


def application_root() -> Path:
    """Return the directory that should contain user-managed runtime files."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_ROOT = application_root()
