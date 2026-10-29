"""Episode reward v2 (user-approved 10-29): quadratic progress plus public Boss bonus.

Boss clearance counts only the trajectory's public witnesses; a missing witness
is null and contributes nothing (never guessed from floors). A0 has 48 floors
and 3 Bosses; A10 has 49 floors and 4 Bosses. Clearing the final Boss is a win,
whose reward is 1, rather than a capped loss. Original terminal facts are retained.
"""
from __future__ import annotations

from typing import Any

REWARD_SPEC = {"id": "stpd-rl-episode-reward", "version": "2", "win": 1.0,
               "loss_floor_scale": 0.5, "boss_bonus": 0.1,
               "loss_progress_exponent": 2,
               "finish_floors": {"0": 48, "10": 49},
               "boss_totals": {"0": 3, "10": 4},
               "win_source": "observed natural terminal win (final Boss defeated)",
               "boss_source": "outcome.boss_clearance (public witnesses only)"}


def episode_reward(episode: dict[str, Any]) -> tuple[float | None, str]:
    """Return (reward, disposition). Only a natural observed terminal gets a reward."""
    outcome = episode["outcome"]
    terminal = outcome.get("terminal") or {}
    if outcome.get("unknown") or outcome.get("status") == "unknown":
        return None, "unknown"
    if outcome.get("status") != "finished":
        return None, "censored"
    if terminal.get("status") != "observed" or terminal.get("result") not in {"win", "loss"}:
        return None, "no_natural_terminal"
    if terminal["result"] == "win":
        return REWARD_SPEC["win"], "win"
    finish = REWARD_SPEC["finish_floors"].get(str(episode["identity"].get("ascension")))
    floor = outcome.get("max_observed_floor")
    if not finish or type(floor) is not int or floor < 0:
        return None, "no_floor"
    bosses = (outcome.get("boss_clearance") or {}).get("count") or 0
    reward = REWARD_SPEC["loss_floor_scale"] * min(floor / finish, 1.0) ** 2
    reward += REWARD_SPEC["boss_bonus"] * bosses
    return reward, "loss"
