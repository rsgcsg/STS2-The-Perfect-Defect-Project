"""Bounded research evaluation over the existing Managed Host lifecycle owner.

No legality, Host control implementation or evidence admission lives here. Raw
consumer observations/receipts are retained; delivery uncertainty stops the suite.
"""

from __future__ import annotations

import hashlib
import math
import random
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from spireagent.workbench.managed_task_outcome import managed_terminal

from .gates import PairedOutcome, paired_fixed_seed_report

AGENT_ID = "stpd/uniform-complete-bound-catalog-v1"
AGENT_INPUT_SCOPE = {
    "selection_input": "complete_bound_action_ids_only",
    "model_state_input": "none",
    "read_calls": [],
    "scope": "catalog_only_engineering_reliability",
    "host_all_information_fairness_verified": False,
}


@dataclass(frozen=True)
class SuiteLimits:
    max_steps: int = 10000
    max_episode_seconds: float = 120.0
    max_suite_seconds: float = 600.0
    workers: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.max_steps) is not int
            or self.max_steps < 1
            or type(self.workers) is not int
            or not 1 <= self.workers <= 16
            or any(
                not math.isfinite(v) or v <= 0
                for v in (self.max_episode_seconds, self.max_suite_seconds)
            )
        ):
            raise ValueError("invalid suite limits")


def wilson_interval(wins: int, planned: int) -> list[float] | None:
    if planned == 0:
        return None
    z = 1.959963984540054
    p, d = wins / planned, 1 + z * z / planned
    center = (p + z * z / (2 * planned)) / d
    radius = z * math.sqrt(p * (1 - p) / planned + z * z / (4 * planned**2)) / d
    return [max(0.0, center - radius), min(1.0, center + radius)]


def empty_episode(seed: str, reason: str = "not_started") -> dict[str, Any]:
    return {
        "seed": seed,
        "started": False,
        "terminal_outcome": None,
        "censor_reason": reason,
        "unknown_delivery": False,
        "unrecovered_hang": False,
        "decisions": 0,
        "observations": 0,
        "stale_refusals": 0,
        "floor": None,
        "act": None,
        "surface_counts": {},
        "complete_catalog_observations": 0,
        "unsupported_surface_observations": 0,
        "unsupported_reasons": [],
        "elapsed_seconds": 0.0,
        "close_receipt": None,
        "cleanup_confirmed": False,
    }


def run_episode(
    seed: str,
    factory: Callable[..., Any],
    finite_action_view: Any,
    *,
    limits: SuiteLimits,
    random_seed: int,
    suite_deadline: float,
    stop: threading.Event,
    emit: Callable[[str, Any], None],
    expected_character: str,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    row = empty_episode(seed)
    began = clock()
    deadline = min(began + limits.max_episode_seconds, suite_deadline)
    expired = threading.Event()
    holder: dict[str, Any] = {}
    environment = None
    pending: str | None = None
    close_error = None
    rng = random.Random(
        int.from_bytes(
            hashlib.sha256(f"{AGENT_ID}\0{random_seed}\0{seed}".encode()).digest(), "big"
        )
    )

    def expire() -> None:
        expired.set()
        owned = holder.get("environment")
        if owned is not None:
            try:
                owned.close(force=True)
            except Exception as error:
                holder["deadline_cleanup_error"] = repr(error)

    timer = threading.Timer(max(0.001, deadline - clock()), expire)
    timer.daemon = True
    timer.start()

    def on_started(owned: Any) -> None:
        holder["environment"] = owned
        if expired.is_set():
            owned.close(force=True)

    def bounded() -> None:
        if expired.is_set() or clock() >= deadline:
            raise TimeoutError("episode_wall_budget")
        if stop.is_set():
            raise InterruptedError("suite_stopped")

    try:
        bounded()
        pending = "startup"
        environment = factory(on_started=on_started)
        pending = None
        emit("ready", environment.ready)
        exact = environment.ready.get("exact_game", {})
        if exact.get("version") != "v0.111.0":
            stop.set()
            raise ValueError("game_version_mismatch")
        bounded()
        row["started"] = True  # reset offered; not only successful startup/terminal
        pending = "reset"
        emit("reset_offer", {"seed": seed})
        snapshot = environment.reset(seed)
        pending = None
        emit("reset_snapshot", snapshot)
        identity = environment.episode_identity()
        emit("episode_identity", identity)
        provenance = identity.get("episode_provenance", {})
        if (
            provenance.get("verdict") != "provenance_pass"
            or provenance.get("actual_seed") != seed
            or provenance.get("requested_seed") != seed
        ):
            stop.set()
            raise ValueError("episode_seed_identity_mismatch")
        row["identity"] = {"ready": environment.ready, "episode": identity}
        while True:
            bounded()
            pending = "observe"
            snapshot = environment.observe()
            pending = None
            row["observations"] += 1
            emit("snapshot", snapshot)
            persistent = snapshot.get("persistent", {}).get("content", {})
            observed_run = persistent.get("run", {})
            player = persistent.get("player", {})
            if (
                player.get("character_definition_id") != expected_character.upper()
                or observed_run.get("ascension") != 0
            ):
                stop.set()
                raise ValueError("episode_configuration_mismatch")
            session = snapshot.get("session", {})
            ready = environment.ready
            if session.get("runtime_instance_id") != ready.get(
                "adapter_runtime_instance_id"
            ) or session.get("environment_fingerprint") != ready.get("environment_fingerprint"):
                stop.set()
                raise ValueError("snapshot_runtime_identity_mismatch")
            for field in ("floor", "act"):
                value = observed_run.get(field)
                if type(value) is int:
                    row[field] = max(row[field] or 0, value)
            surface_kind = snapshot.get("interaction", {}).get("kind", "unknown")
            row["surface_counts"][surface_kind] = row["surface_counts"].get(surface_kind, 0) + 1
            row["complete_catalog_observations"] += (
                snapshot.get("bound_actions", {}).get("status") == "complete"
            )
            row["unsupported_surface_observations"] += (
                snapshot.get("status") == "visible_unsupported"
            )
            terminal = managed_terminal(snapshot)
            if terminal is not None:
                row["terminal_outcome"], row["censor_reason"] = terminal, None
                break
            if row["decisions"] >= limits.max_steps:
                row["censor_reason"] = "step_budget"
                break
            if snapshot.get("status") == "visible_unsupported":
                row["censor_reason"] = "unsupported_surface"
                row["unsupported_reasons"] = snapshot.get("completeness", {}).get("missing", [])
                break
            view = finite_action_view.from_snapshot(snapshot)
            if not view.action_ids:
                row["censor_reason"] = "empty_complete_catalog"
                break
            catalog = snapshot["bound_actions"]
            if catalog.get("total_count") != len(view.action_ids) or catalog.get(
                "materialized_count"
            ) != len(view.action_ids):
                raise ValueError("incomplete_action_catalog")
            action_id = rng.choice(view.action_ids)
            request_id = uuid4().hex
            bounded()
            pending = "step"
            emit(
                "step_offer",
                {"request_id": request_id, "action_id": action_id, "snapshot_id": view.snapshot_id},
            )
            receipt = environment.step(action_id, view.snapshot_id, request_id)
            row["decisions"] += 1
            emit(
                "step",
                {
                    "request_id": request_id,
                    "action_id": action_id,
                    "snapshot_id": view.snapshot_id,
                    "receipt": receipt,
                },
            )
            if not isinstance(receipt, dict) or not isinstance(receipt.get("action"), dict):
                raise ValueError("malformed_receipt")
            if receipt.get("delivery") == "unknown":
                row["unknown_delivery"] = True
                pending = None
                row["censor_reason"] = "unknown_delivery"
                stop.set()
                break
            if (
                receipt.get("request_id") != request_id
                or receipt.get("action", {}).get("bound_action_id") != action_id
            ):
                row["unknown_delivery"] = True
                stop.set()
                raise ValueError("receipt_binding_mismatch")
            if receipt.get("delivery") not in {"delivered", "not_delivered"}:
                raise ValueError("receipt_delivery_unrecognized")
            pending = None
            if receipt.get("delivery") == "not_delivered":
                if (
                    receipt.get("reason_code") == "stale_snapshot"
                    and receipt.get("retry", {}).get("allowed") is True
                ):
                    row["stale_refusals"] += 1
                    continue  # fresh observe then fresh uniform choice; no old action replay
                row["censor_reason"] = "not_delivered:" + str(receipt.get("reason_code"))
                break
            if receipt.get("delivery") != "delivered":
                row["unknown_delivery"] = True
                stop.set()
                raise ValueError("receipt_delivery_unrecognized")
    except Exception as error:
        emit("error", {"type": type(error).__name__, "message": str(error), "pending": pending})
        row["error"] = {"type": type(error).__name__, "message": str(error), "pending": pending}
        row["censor_reason"] = (
            str(error)
            if isinstance(error, (ValueError, TimeoutError, InterruptedError))
            else "driver_failure"
        )
        if expired.is_set() and pending not in {"step", "reset"}:
            row["censor_reason"] = "episode_wall_budget"
        if pending in {"step", "reset"}:
            row["unknown_delivery"] = True
            row["censor_reason"] = "unknown_delivery"
            stop.set()
        if pending == "startup":
            stop.set()  # readiness/audit failed, no further native attempts
    finally:
        environment = environment or holder.get("environment")
        if environment is not None:
            try:
                environment.close(force=expired.is_set() or row["unknown_delivery"])
            except Exception as error:
                close_error = str(error)
                emit("cleanup_error", {"message": close_error})
            row["close_receipt"] = dict(environment.close_receipt())
            receipt = row["close_receipt"]
            row["cleanup_confirmed"] = (
                receipt.get("driver_exit_code") == 0
                and receipt.get("cleanup_error") is None
                and receipt.get("forced_fallback") is False
                and close_error is None
                and holder.get("deadline_cleanup_error") is None
            )
            if not row["cleanup_confirmed"]:
                row["unrecovered_hang"] = True
                stop.set()
                row["censor_reason"] = row["censor_reason"] or "cleanup_unknown"
            emit("close_receipt", receipt)
        timer.cancel()
        timer.join(timeout=12)
        if timer.is_alive():
            row["unrecovered_hang"] = True
            row["cleanup_confirmed"] = False
            stop.set()
        row["elapsed_seconds"] = clock() - began
        emit("episode_end", row)
    return row


def suite_report(
    manifest: dict[str, Any],
    episodes: list[dict[str, Any]],
    *,
    elapsed: float,
    identity: dict[str, Any],
) -> dict[str, Any]:
    by_seed = {row["seed"]: row for row in episodes}
    if len(by_seed) != len(episodes) or set(by_seed) != set(manifest["seeds"]):
        raise ValueError("suite rows must match every planned seed exactly")
    planned = len(manifest["seeds"])
    wins = sum(row["terminal_outcome"] == "win" for row in episodes)
    losses = sum(row["terminal_outcome"] == "loss" for row in episodes)
    b6 = paired_fixed_seed_report(
        [
            PairedOutcome(
                row["seed"],
                AGENT_ID,
                row["terminal_outcome"] is not None,
                row["terminal_outcome"],
                row["censor_reason"],
                row["unknown_delivery"],
            )
            for row in episodes
            if row["started"]
        ],
        required_policies=(AGENT_ID,),
        planned_seeds=manifest["seeds"],
    )
    return {
        "schema": "stpd/managed-suite-report-v1",
        "suite_id": manifest["suite_id"],
        "verdict": b6["verdict"],
        "planned": planned,
        "started": sum(row["started"] for row in episodes),
        "not_started": sum(not row["started"] for row in episodes),
        "natural_terminal": wins + losses,
        "wins": wins,
        "losses": losses,
        "censored": sum(row["terminal_outcome"] is None for row in episodes),
        "censor_reasons": dict(
            sorted(
                Counter(
                    row["censor_reason"] for row in episodes if row["censor_reason"] is not None
                ).items()
            )
        ),
        "unknown_delivery": sum(row["unknown_delivery"] for row in episodes),
        "unrecovered_hang": sum(row["unrecovered_hang"] for row in episodes),
        "win_rate": wins / planned if planned else None,
        "win_rate_wilson_95": wilson_interval(wins, planned),
        "natural_terminal_rate": (wins + losses) / planned if planned else None,
        "floor_distribution": dict(sorted(Counter(str(row["floor"]) for row in episodes).items())),
        "act_distribution": dict(sorted(Counter(str(row["act"]) for row in episodes).items())),
        "decisions": sum(row["decisions"] for row in episodes),
        "elapsed_seconds": elapsed,
        "decisions_per_second": sum(row["decisions"] for row in episodes) / elapsed
        if elapsed
        else None,
        "observed_surface_counts": dict(
            sum((Counter(row["surface_counts"]) for row in episodes), Counter())
        ),
        "complete_catalog_observations": sum(
            row["complete_catalog_observations"] for row in episodes
        ),
        "unsupported_surface_observations": sum(
            row["unsupported_surface_observations"] for row in episodes
        ),
        "unsupported_reason_counts": dict(
            Counter(reason for row in episodes for reason in row["unsupported_reasons"])
        ),
        "agent_input_scope": dict(AGENT_INPUT_SCOPE),
        "identity": identity,
        "b6": b6,
        "episodes": episodes,
        "non_claims": [
            "Reference equivalence",
            "P1 r1 qualification",
            "Human evidence",
            "strategy quality",
        ],
    }


def run_suite(
    manifest: dict[str, Any],
    factory: Callable[..., Any],
    finite_action_view: Any,
    *,
    limits: SuiteLimits,
    random_seed: int,
    emit_factory: Callable[[int], Callable],
    identity: dict[str, Any],
    max_episodes: int | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    started = clock()
    deadline = started + limits.max_suite_seconds
    stop = threading.Event()
    seeds = manifest["seeds"]
    if max_episodes is not None and not 1 <= max_episodes <= len(seeds):
        raise ValueError("invalid max episodes")
    count = len(seeds) if max_episodes is None else max_episodes
    rows = [
        empty_episode(seed, "canary_not_selected" if i >= count else "not_started")
        for i, seed in enumerate(seeds)
    ]
    next_index = 0
    with ThreadPoolExecutor(max_workers=limits.workers) as executor:
        active: dict[Any, int] = {}
        while active or next_index < count:
            while (
                len(active) < limits.workers
                and next_index < count
                and not stop.is_set()
                and clock() < deadline
            ):
                index = next_index
                next_index += 1
                future = executor.submit(
                    run_episode,
                    seeds[index],
                    factory,
                    finite_action_view,
                    limits=limits,
                    random_seed=random_seed,
                    suite_deadline=deadline,
                    stop=stop,
                    emit=emit_factory(index),
                    expected_character=manifest["character"],
                    clock=clock,
                )
                active[future] = index
            if not active:
                break
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                rows[active.pop(future)] = future.result()
    reason = "suite_stopped_after_failure" if stop.is_set() else "suite_wall_budget"
    for row in rows[:count]:
        if not row["started"] and row["censor_reason"] == "not_started":
            row["censor_reason"] = reason
    return suite_report(manifest, rows, elapsed=clock() - started, identity=identity)
