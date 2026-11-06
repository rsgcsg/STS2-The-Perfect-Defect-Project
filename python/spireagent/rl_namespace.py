"""Immutable RL run namespace shared by Exchange and archive seeding."""

from __future__ import annotations

import re

from spireagent.json_boundary import BoundaryError

RL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def exchange_prefix(run: str) -> str:
    """The existing immutable run namespace, without constructing an Exchange."""
    if not RL_NAME.fullmatch(run):
        raise BoundaryError("rl_exchange", "invalid_run_name")
    return f"exchange/rl/{run}/"
