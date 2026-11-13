"""The learner config of a fast-chain run, as one pure function (roles.py and preflight).

`build(base, knobs, run)` turns the gate's learner-base.json into this run's learner.json:
the gate supplies the recipe, the run profile the speed levers and the run its
operational values. `recipe_drift(effective, frozen)` compares it with the frozen
long-run parameters (python/configs/rl5.json): every frozen key must be equal, except
the ones in `ALLOWED_DRIFT`, each with the reason the launcher sets it on purpose.
`window(...)` is the operating window of one generation of a resume chain (supervise.py).
"""
from __future__ import annotations

import copy
from typing import Any

# frozen key -> why the launcher may differ from rl5.json there. Speed levers change no
# semantics (judged by strength/wall-h); operational values belong to the run.
ALLOWED_DRIFT = {
    "device": "the run's learner device (cuda or cuda:1 by layout)",
    "performance.prepared_kernels": "speed lever (run profile)",
    "performance.prepared_bucket_rows": "speed lever (run profile)",
    "segments.diagnostics_every": "diagnostics cadence (run profile)",
    "segments.warmup_artifact_id": "the gate's warm-up artifact (operational input)",
    "segments.prepared_bytes": "prepared-output memory budget (run profile)",
    "segments.preparation_workers": "local prepare pools (run profile; idle under remote)",
}


def window(chain_id: str, total_seconds: int, chain_deadline: float,
           checkpoint_id: str | None, checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """The learner's operating window for one generation of a resume chain.

    The rules are SegmentLearner's (restore of `deadline_at` / `operating_budget`); this
    only picks the one input that satisfies them, never a value of its own:
    - a CP of this chain (its budget id is the chain id): inherit budget and deadline
      (the learner keeps consumed seconds and the immutable deadline);
    - a fresh start, or a CP without budget and deadline: open the chain's window;
    - a CP of another window: open the chain's window with that CP as the parent;
    - a CP with a deadline but no budget cannot open a new window: refused.
    `checkpoint` = the CP's `config` facts (`deadline_at`, `operating_budget`)."""
    budget = {"id": chain_id, "total_seconds": total_seconds, "consumed_seconds": 0}
    if not 0 < total_seconds <= 72000:
        raise ValueError(f"chain window {total_seconds} s outside 1..72000")
    if checkpoint is None:
        return {"operating_budget": budget, "operating_budget_parent": None,
                "deadline_at": chain_deadline}
    cp_budget, cp_deadline = checkpoint.get("operating_budget"), checkpoint.get("deadline_at")
    if cp_budget and cp_budget.get("id") == chain_id:
        return {"operating_budget": None, "operating_budget_parent": None, "deadline_at": None}
    if cp_budget:
        return {"operating_budget": budget, "deadline_at": chain_deadline,
                "operating_budget_parent": {"checkpoint_artifact_id": checkpoint_id,
                                            "operating_budget": cp_budget,
                                            "deadline_at": cp_deadline}}
    if cp_deadline is not None:
        raise ValueError(f"checkpoint {checkpoint_id} has deadline_at {cp_deadline} but no "
                         "operating budget: the learner cannot open a new window from it")
    return {"operating_budget": budget, "operating_budget_parent": None,
            "deadline_at": chain_deadline}


def build(base: dict[str, Any], knobs: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """`run`: run_name, peer_dir, start_package, wall_seconds, device, generation,
    warmup_artifact_id, rendezvous_path, ranks, resume_checkpoint (or None) and, in a
    resume chain, window (`window(...)`)."""
    config = copy.deepcopy(base)
    config.pop("peer", None)  # never carry the gate's archive subtree
    config.update({
        "run": run["run_name"], "deadline_at": None, "operating_budget": None,
        "operating_budget_parent": None, "resume_checkpoint": run["resume_checkpoint"],
        "peer": {"kind": "local", "directory": run["peer_dir"]},
        "start_package": run["start_package"], "start_artifact_id": None,
        "start_model_sha256": None, "wall_seconds": float(run["wall_seconds"]),
        "device": run["device"], "max_versions": knobs["max_versions"]})
    # h100i: the offline 1,866-1,934 wan/h step ran with compile, the kernel set and one
    # bucket per group; the gate base had them off.
    if knobs["levers"]:
        config["performance"].update({
            "prepared_compile": True, "prepared_kernels": knobs["prepared_kernels"],
            "prepared_bucket_rows": knobs["prepared_bucket_rows"]})
    config["threads"] = (knobs["sgd_threads_per_rank"] if run["ranks"] > 1
                         else knobs["sgd_threads_single"])
    config["segments"].update({
        "training_generation": run["generation"],
        "warmup_artifact_id": run["warmup_artifact_id"],
        "rendezvous_path": run["rendezvous_path"],
        "archive_min_free_bytes": 0, "archive_min_free_inodes": 0,
        "capacity_rows": knobs["capacity_rows"], "capacity_limit_rows": knobs["capacity_rows"],
        "prepared_bytes": int(knobs["prepared_gib"] * 1024**3),
        # s1d: the gate pins the compiler digest of the code it was built with; this run's
        # code has its own, which the services and aggregators use too.
        "input_compiler_identity": None,
        "diagnostics_every": knobs["diagnostics_every"],
        **({"checkpoint_seconds": float(knobs["checkpoint_seconds"])}
           if knobs["checkpoint_seconds"] else {})})
    if run.get("window"):
        config.update(run["window"])
    return config


def recipe_drift(effective: dict[str, Any], frozen: dict[str, Any],
                 prefix: str = "") -> list[str]:
    """Frozen keys whose effective value differs (and is not an allowed drift)."""
    out = []
    for key, value in frozen.items():
        name = prefix + key
        if name in ALLOWED_DRIFT:
            continue
        have = effective.get(key, "<missing>") if isinstance(effective, dict) else "<missing>"
        if isinstance(value, dict) and isinstance(have, dict):
            out += recipe_drift(have, value, name + ".")
        elif have != value:
            out.append(f"{name}: frozen {value!r}, effective {have!r}")
    return out
