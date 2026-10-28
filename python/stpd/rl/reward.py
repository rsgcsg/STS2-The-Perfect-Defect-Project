"""Episode reward (user-approved 10-28): win 1; loss 0.5*floor/finish + 0.1 per act Boss.

Boss clearance counts only the trajectory's public witnesses; a missing witness
is null and contributes nothing (never guessed from floors). Every loss scores
strictly below a win.
"""
from __future__ import annotations

from typing import Any

from stpd.p1.value_targets import VALUE_TARGET_SPEC

REWARD_SPEC = {"id": "stpd-rl-episode-reward", "version": "1", "win": 1.0,
               "loss_floor_scale": 0.5, "boss_bonus": 0.1,
               "finish_floors": VALUE_TARGET_SPEC["auxiliary"]["finish_floors"],
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
    reward = REWARD_SPEC["loss_floor_scale"] * min(floor / finish, 1.0)
    reward += REWARD_SPEC["boss_bonus"] * bosses
    return min(reward, 0.99), "loss"
