"""The learner config of a fast-chain run, as one pure function (roles.py and preflight).

`build(base, knobs, run)` turns the gate's learner-base.json into this run's learner.json:
the gate supplies the recipe, the run profile the speed levers and the run its
operational values. `recipe_drift(effective, frozen)` compares it with the frozen
long-run parameters (python/configs/rl5.json): every frozen key must be equal, except
the ones in `ALLOWED_DRIFT`, each with the reason the launcher sets it on purpose.
Recipe keys (the whole `loss` block, c_hp and its scope included) pass through unchanged
from the gate and have no default here; the one exception is the `arm_policy_update` knob,
which writes `loss.arm_policy_update` off its default. `gate_recipe(base, frozen)` first adds the `loss`
keys a gate made before them lacks (c_hp and its scope, #538), from the frozen config of
the run's own source; a key the gate has stays the gate's and A9 still compares it. Every
other missing key shows as `<missing>` drift. `learner_base(gate, frozen, knobs, run)` is the
two in order, the one call roles.py and preflight share; `sgd_gc_dataplane(knobs)` the SGD
child's GC keys roles.py adds to dataplane.json.
`window(...)` is the operating window of one generation of a resume chain (supervise.py).
`start_fields(...)` is the start a run records: the restored CP's on a resume, the site
package's (its model.json, the only file read here) on a fresh start.
`declared_changes` (RL5-METHODS C, #573): the profile knob naming a CP and each recipe key
old -> new for a continuation from it; `build` writes the new values and the declaration
(`resume_recipe_changes`), the learner accepts them only on a restore of exactly that CP.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
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


# = stpd.rl.recipe_changes.DECLARABLE (pinned by a test): the only recipe keys a
# continuation may change; the learner owns the rule and the reason per key.
DECLARABLE = ("lr", "segments.head_lr", "loss.entropy_coef", "loss.hp_coef",
              "loss.hp_advantage_coef", "loss.hp_advantage_scope", "loss.explore_ratio_floor",
              "segments.target_kl",
              "segments.kl_stop_scope", "segments.value_target", "segments.gae_lambda",
              "segments.value_config_input", "loss.arm_policy_update",
              "loss.logit_mean_penalty_coef", "loss.noncombat_log_barrier_coef",
              "segments.observe_arms")
# A key missing from a learner config (a CP's or this run's) means this value: the learner's
# own defaults (SegmentConfig, LossConfig; _resume_config adds the loss pair to older CPs).
CONFIG_DEFAULTS = {"segments.value_target": "segment", "segments.kl_stop_scope": "all",
                   "loss.hp_advantage_coef": 0.0, "loss.hp_advantage_scope": "combat_ticket",
                   "loss.explore_ratio_floor": 0.0, "segments.value_config_input": False,
                   "loss.arm_policy_update": "normal", "loss.logit_mean_penalty_coef": 0.0,
                   "loss.noncombat_log_barrier_coef": 0.0, "segments.observe_arms": []}
MISSING = "<missing>"


def config_value(config: dict[str, Any], key: str) -> Any:
    """A learner config's value at a dotted path (its default when left out)."""
    node: Any = config
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return CONFIG_DEFAULTS.get(key, MISSING)
        node = node[part]
    return node


def apply_declared(config: dict[str, Any], declared: dict[str, Any]) -> dict[str, Any]:
    """`config` with each declared key at its new value and the learner's declaration
    (`resume_recipe_changes`); unchanged when nothing is declared."""
    if not declared:
        return config
    config = copy.deepcopy(config)
    for key, change in declared["changes"].items():
        *parents, last = key.split(".")
        node = config
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = change["new"]
    config["resume_recipe_changes"] = {"checkpoint_artifact_id": declared["checkpoint"],
                                       "changes": copy.deepcopy(declared["changes"])}
    return config


def window(chain_id: str, total_seconds: int, chain_deadline: float | None,
           checkpoint_id: str | None, checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """The learner's operating window for one generation of a resume chain.

    The rules are SegmentLearner's (restore of `deadline_at` / `operating_budget`); this
    only picks the one input that satisfies them, never a value of its own:
    - a CP of this chain (its budget id is the chain id): inherit budget and deadline
      (the learner keeps consumed seconds and the immutable deadline);
    - a fresh start, or a CP without budget and deadline: open the chain's window;
    - a CP of another window: open the chain's window with that CP as the parent;
    - a CP with a deadline but no budget cannot open a new window: refused.
    `checkpoint` = the CP's `config` facts (`deadline_at`, `operating_budget`).
    `chain_deadline` None (a training-time target, #603): the window is the budget alone,
    no absolute deadline enters the learner or its CPs (RL4.2: a deadline in the CP capped
    the run at 17.7 h)."""
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


def gate_recipe(base: dict[str, Any], frozen: dict[str, Any]
                ) -> tuple[dict[str, Any], dict[str, Any]]:
    """The gate's learner-base.json with the `loss` keys it lacks taken from the frozen
    config (`frozen`, python/configs/rl5.json of the run's source), and those filled keys.
    One shared gate then serves every source: an older learner refuses unknown loss keys
    (`LossConfig(**loss)`), so the gate itself must not gain them while one still runs."""
    loss = base.get("loss") or {}
    filled = {key: value for key, value in (frozen.get("loss") or {}).items()
              if key not in loss}
    if not filled:
        return base, {}
    return {**base, "loss": {**loss, **filled}}, filled


def learner_base(gate: dict[str, Any], frozen: dict[str, Any], knobs: dict[str, Any],
                 run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """This run's learner.json (roles.py writes it, preflight checks it): `gate_recipe`
    then `build`, one function so the two cannot differ. Returns it and the filled keys."""
    base, filled = gate_recipe(gate, frozen)
    return build(base, knobs, run), filled


def sgd_gc_dataplane(knobs: dict[str, Any]) -> dict[str, Any]:
    """The SGD child's GC data-plane keys (#525, RL5-METHODS F); keys at their default
    are left out, so an older source runs the default unchanged."""
    out: dict[str, Any] = {}
    if knobs["sgd_gc_freeze"]:  # one collect at each boundary, gc.freeze after the first
        out["sgd_gc_freeze"] = True
    if knobs["sgd_gc_refreeze_objects"] != 200_000:  # = sgd_process.GC_REFREEZE_OBJECTS
        out["sgd_gc_refreeze_objects"] = knobs["sgd_gc_refreeze_objects"]
    return out


def start_fields(start_package: str, checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """`start_package` and `start_model_sha256` for `build`.

    A resume never loads the start package (SegmentLearner restores the CP), so its start
    comes from the restored CP's `config`: the real lineage, also after the site's package
    was removed or replaced in place. Only a fresh start reads the site's package; the
    learner then verifies that package_sha256. `checkpoint` = the restored CP's config."""
    if checkpoint is not None:
        return {key: checkpoint.get(key) for key in ("start_package", "start_model_sha256")}
    descriptor = json.loads((Path(start_package) / "model.json").read_bytes())
    return {"start_package": start_package, "start_model_sha256": descriptor["package_sha256"]}


def observe_arms(ladder: dict[str, Any]) -> list[str]:
    """`segments.observe_arms` of a ladder: its arms with `learn_policy = false`, sorted."""
    return sorted(arm for arm, spec in ladder.items() if spec.get("learn_policy") is False)


def build(base: dict[str, Any], knobs: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """`run`: run_name, peer_dir, start_package, wall_seconds, device, generation,
    warmup_artifact_id, rendezvous_path, ranks, resume_checkpoint (or None) and, in a
    resume chain, window (`window(...)`). Optional start_model_sha256 (`start_fields`): the
    start package's package_sha256, which the learner verifies on a fresh start and every
    CP's config keeps (lineage)."""
    config = copy.deepcopy(base)
    config.pop("peer", None)  # never carry the gate's archive subtree
    config.update({
        "run": run["run_name"], "deadline_at": None, "operating_budget": None,
        "operating_budget_parent": None, "resume_checkpoint": run["resume_checkpoint"],
        "peer": {"kind": "local", "directory": run["peer_dir"]},
        "start_package": run["start_package"], "start_artifact_id": None,
        "start_model_sha256": run.get("start_model_sha256"),
        "wall_seconds": float(run["wall_seconds"]),
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
    # The knob-owned recipe keys below are written only off their default. A gate that
    # already carries one with another value would win silently whenever the knob is at its
    # default (A9 compares the frozen keys only, and a frozen config leaves a default out):
    # e.g. a fresh gate keeping kl_stop_scope "control" under a profile at "all". Refused.
    owned = (("segments", "kl_stop_scope", knobs.get("kl_stop_scope", "all")),
             ("segments", "lr_warmup_steps", knobs.get("lr_warmup_steps", 0)),
             ("segments", "value_config_input", knobs.get("value_config_input", False)),
             ("segments", "observe_arms", observe_arms(knobs.get("exploration_ladder") or {})),
             ("loss", "arm_policy_update", knobs.get("arm_policy_update", "normal")))
    # eval_only (never trained): these training-recipe keys keep the gate's value (the
    # served model's lineage); preflight names each one (check_frozen_recipe).
    kept = {(section, key): config[section][key] for section, key, _ in owned
            if knobs.get("eval_only") and key in (config.get(section) or {})}
    for section, key, value in owned:
        if (section, key) not in kept and key in (config.get(section) or {}) \
                and config[section][key] != value:
            raise ValueError(f"gate learner-base.json {section}.{key} = "
                             f"{config[section][key]!r} differs from this run's knob value "
                             f"{value!r}: the knob owns it; remove it from the gate")
    # Ticket K: written only off their default, so the default learner.json (and an older
    # source, which refuses unknown segment keys) is unchanged; resume-locked in the recipe.
    if knobs.get("kl_stop_scope", "all") != "all":
        config["segments"]["kl_stop_scope"] = knobs["kl_stop_scope"]
    if knobs.get("lr_warmup_steps", 0):
        config["segments"]["lr_warmup_steps"] = knobs["lr_warmup_steps"]
    # The value-config input (owner 11-19): the same rule (off = key left out).
    if knobs.get("value_config_input", False):
        config["segments"]["value_config_input"] = True
    # Observation cells (user 2026-11-24): the ladder's arms with learn_policy = false, the
    # same rule (none = key left out).
    observe = observe_arms(knobs.get("exploration_ladder") or {})
    if observe:
        config["segments"]["observe_arms"] = observe
    # The arm policy update (owner 11-19): the same rule (normal = key left out).
    if knobs.get("arm_policy_update", "normal") != "normal":
        config["loss"]["arm_policy_update"] = knobs["arm_policy_update"]
    for (section, key), value in kept.items():
        config[section][key] = value
    # #632: the score head in FP32 (a precision choice, not resume-compared): written only
    # when on, so the default learner.json is unchanged; roles.py gives the services the same.
    if knobs.get("score_head_fp32", False):
        config["performance"]["score_head_fp32"] = True
    if run.get("window"):
        config.update(run["window"])
    if knobs.get("eval_only") and run["resume_checkpoint"]:
        # A CP of any run served in a fresh run (LearnerConfig.serve_checkpoint): its
        # weights and heads, no restore, no fitted heads; value_config_input is its heads'.
        if run.get("served_config") is None:
            raise ValueError("eval_only serving a CP needs that CP's config (served_config)")
        config.update(serve_checkpoint=run["resume_checkpoint"], resume_checkpoint=None)
        config["segments"]["warmup_artifact_id"] = None
        config["segments"]["value_config_input"] = (run["served_config"].get(
            "segments") or {}).get("value_config_input", False)
    # RL5-METHODS C: written only when declared (an older learner refuses the key).
    config = apply_declared(config, knobs.get("declared_changes") or {})
    # "own" and the gate's (or a declared) explore_ratio_floor > 0 are alternatives; the
    # learner refuses both too (LossConfig), this says so before anything starts.
    if (config["loss"].get("arm_policy_update", "normal") == "own"
            and config["loss"].get("explore_ratio_floor", 0.0)):
        raise ValueError("loss.arm_policy_update own refuses loss.explore_ratio_floor > 0 "
                         "(the two are alternatives): set the floor to 0")
    return config


def input_cause(failure: Any) -> str | None:
    """The learner's `input_failure` (learner-report.json) as one line down to its innermost
    error, for STOP (roles.py), e.g. `dataplane_shard 0 / prepare INVALID_PACKET:
    binary_remote_assembly_failed: ValueError: prepared_batch_exceeds_...` (V4 smoke-h1c)."""
    if not isinstance(failure, dict):
        return None
    path, node, inner = [], failure, failure
    while isinstance(node, dict):
        inner = node
        path.append(" ".join(str(node[key]) for key in ("operation", "shard") if key in node))
        node = node.get("failure")
    detail = (inner.get("error") or (node if isinstance(node, str) else None)
              or inner.get("error_type") or inner.get("producer_error_type"))
    return (" / ".join(part for part in path if part) + (f" {inner['code']}" if inner.get("code")
            else "") + (f": {detail}" if detail else ""))


def recipe_drift(effective: dict[str, Any], frozen: dict[str, Any],
                 prefix: str = "", declared: frozenset[str] = frozenset()) -> list[str]:
    """Frozen keys whose effective value differs (and is not an allowed drift, or a key the
    run's `declared_changes` changes on purpose)."""
    out = []
    for key, value in frozen.items():
        name = prefix + key
        if name in ALLOWED_DRIFT or name in declared:
            continue
        have = effective.get(key, "<missing>") if isinstance(effective, dict) else "<missing>"
        if isinstance(value, dict) and isinstance(have, dict):
            out += recipe_drift(have, value, name + ".", declared)
        elif have != value:
            out.append(f"{name}: frozen {value!r}, effective {have!r}")
    return out
