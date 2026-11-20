"""Developer batch admission, separate from game and Host authority."""
from __future__ import annotations

import subprocess
from pathlib import Path


def require_managed_slot(timeout_seconds: float = 10) -> None:
    """Fail before launching a batch unless its node-local reservation is held."""
    tool = Path(__file__).resolve().parents[2] / "tools" / "resource-slot.mjs"
    subprocess.run(["node", str(tool), "check"], check=True, timeout=timeout_seconds)
