"""Read-only whole-run cohort planning over the existing P1 reader and Curation.

Weights are proposed TRAIN multipliers, never training admission or loss execution.
No source, purpose, exposure, snapshot or worker is mutated by this module.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any, cast

from spireagent.json_boundary import BoundaryError

from .data import digest, json_bytes
from .dataset import Projection, _points, inspect_projections

RULE_ID = "progress-boss-run-weight-v1"
WEIGHT_PARAMETERS = {
    "loss_unknown_base": 0.25, "progress_coefficient": 1.25,
    "progress_exponent": 2, "boss_bonus": 0.25, "win": 3.0,
    "nonwin_stop_at": 3.0,
}



def projection_facts(projection: Projection) -> dict[str, Any]:
    """Use public run/player and terminal facts; do not infer outcomes from HP."""
    characters: set[str] = set()
    ascensions: set[int] = set()
    results: set[str] = set()
    floors: list[int] = []
    boss_combats: dict[tuple[int, int], dict[str, Any]] = {}
    boss_victories: dict[tuple[int, int], dict[str, Any]] = {}
    final_boss_maps: list[dict[str, Any]] = []
    incomplete: Counter[str] = Counter()
    masks: Counter[str] = Counter()
    for _, _, point in _points(projection):
        observation = point.get("observation") or {}
        run = (observation.get("run") or {}).get("content") or {}
        player = (observation.get("player") or {}).get("content") or {}
        if isinstance(player.get("character_definition_id"), str):
            characters.add(player["character_definition_id"])
        if type(run.get("ascension")) is int:
            ascensions.add(run["ascension"])
        if type(run.get("floor")) is int:
            floors.append(run["floor"])
        for target, present in point.get("mask", {}).items():
            masks[target + "_" + str(present)] += 1
        envelope = observation.get("envelope") or {}
        for missing in envelope.get("missing", []):
            incomplete[str(missing)] += 1
        # Terminal context belongs to the observed scene, not the training label.
        scene = observation.get("scene") or {}
        if scene.get("family") == "terminal":
            content = scene.get("content") or {}
            for context in (content, content.get("context") or {}):
                if context.get("result") in {"win", "loss"}:
                    results.add(context["result"])
        combat = (observation.get("combat") or {}).get("content") or {}
        mapping = (observation.get("map") or {}).get("content") or {}
        position = mapping.get("current_position") or {}
        act, floor = run.get("act"), run.get("floor")
        if type(act) is int and type(floor) is int:
            key = (act, floor)
            if combat.get("encounter_type") == "boss":
                boss_combats[key] = {"act": act, "floor": floor,
                                     "combat_event_id": point.get("source_event_id")}
            # A recorded boss combat followed by its reward is a positive witness.
            # Merely reaching/passing a floor, a boss map icon or low HP is not.
            if (scene.get("family") == "reward" and key in boss_combats
                    and position.get("point_type") == "boss"):
                boss_victories[key] = {**boss_combats[key],
                                       "reward_event_id": point.get("source_event_id")}
            if act == 3 and type(position.get("row")) is int:
                nodes = [n for n in mapping.get("nodes", [])
                         if n.get("point_type") == "boss" and type(n.get("row")) is int]
                if nodes:
                    final_boss_maps.append({
                        "source_event_id": point.get("source_event_id"),
                        "floor": floor + max(n["row"] for n in nodes) - position["row"],
                        "current_floor": floor, "current_map_row": position["row"],
                        "boss_map_rows": sorted({n["row"] for n in nodes}),
                        "boss_definitions": run.get("bosses", []),
                    })
        target = point.get("end_target") or {}
        if point.get("mask", {}).get("end") is True and target.get("result") in {"win", "loss"}:
            results.add(target["result"])
    original = projection.source.parameters.value()
    journal = [e for e in original.get("run_journal", [])
               if e.get("run_id") == projection.value["branch"]["run_id"]]
    native_results = {
        "win" if e["detail"] == "RunManager.OnEnded(isVictory=true)" else "loss"
        for e in journal if e.get("kind") == "run_ended_native"
        and e.get("detail") in {
            "RunManager.OnEnded(isVictory=true)", "RunManager.OnEnded(isVictory=false)"
        }
    }
    results.update(native_results)
    return {
        "source_id": projection.source.artifact_id,
        "root_run_id": projection.root,
        "declared_origin": original.get("declared_origin", "unknown"),
        "character": next(iter(characters)) if len(characters) == 1 else None,
        "ascension": next(iter(ascensions)) if len(ascensions) == 1 else None,
        "result": next(iter(results)) if len(results) == 1 else None,
        "fact_conflict": len(characters) > 1 or len(ascensions) > 1 or len(results) > 1,
        "floor_reached": max(floors, default=None),
        "last_recorded_floor": floors[-1] if floors else None,
        "boss_count": len(boss_victories),
        "boss_count_basis": "observed_boss_combat_then_same_act_floor_reward",
        "boss_victories": list(boss_victories.values()),
        "boss_encounters_without_victory_witness": [
            boss_combats[k] for k in boss_combats.keys() - boss_victories.keys()],
        "final_boss_maps": final_boss_maps[-1:],
        "unknown_outcome": len(results) != 1,
        "unknown_reason": (None if len(results) == 1 else "conflicting_terminal_records"
                           if results else "recording_closed_without_native_or_public_terminal"),
        "recording_closed": any(e.get("kind") == "session_closed" for e in journal),
        "excluded_N_candidates": projection.value.get("excluded_N_candidates", []),
        "native_start": any(e.get("kind") == "run_started_native" for e in journal),
        "native_end": bool(native_results),
        "masks": dict(masks), "missing_information": dict(incomplete),
        "recorder": original.get("original_recording", {}),
    }


def bind_finish_floors(facts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read per-difficulty finish floors; use A0 only as an explicit last fallback."""
    denominators: dict[int, dict[str, Any]] = {}
    for level in (0, 10):
        wins = [(root, row["floor_reached"]) for root, row in facts.items()
                if row.get("ascension") == level and row.get("result") == "win"
                and type(row.get("floor_reached")) is int and not row.get("fact_conflict")]
        values = {floor for _, floor in wins}
        if len(values) > 1:
            raise BoundaryError("p1_cohort", "conflicting_finish_floors_split_recording_versions")
        if wins:
            denominators[level] = {"floor": wins[0][1], "basis": "recorded_win_floor",
                                   "evidence_roots": sorted(root for root, _ in wins)}
        else:
            maps = [(root, m) for root, row in facts.items() if row.get("ascension") == level
                    for m in row.get("final_boss_maps", [])]
            values = {m["floor"] for _, m in maps}
            if len(values) > 1:
                raise BoundaryError("p1_cohort", "conflicting_final_boss_floors")
            if maps:
                denominators[level] = {"floor": maps[0][1]["floor"],
                                       "basis": "recorded_final_act_boss_map_floor",
                                       "evidence_roots": sorted(root for root, _ in maps)}
    if 10 not in denominators and 0 in denominators:
        denominators[10] = {**denominators[0], "basis": "A0_fallback_no_A10_win_or_final_boss_map"}
    result = copy.deepcopy(facts)
    for row in result.values():
        observed_level = row.get("ascension")
        row["finish_floor_evidence"] = (denominators.get(observed_level)
                                        if type(observed_level) is int else None)
    return result


def baseline2_weight(facts: dict[str, Any]) -> float:
    """Historical candidate 2, retained only for the user's requested comparison."""
    if facts.get("result") not in {"win", "loss"}:
        return 0.0
    if facts["result"] == "win":
        return 3.0
    floor = facts.get("floor_reached")
    return 1.0 if type(floor) is int and floor >= 17 else 0.25


def run_weight(facts: dict[str, Any], rule: str = RULE_ID) -> float:
    """User-fixed progress weight; unknown outcomes remain explicitly counted."""
    if rule != RULE_ID:
        raise BoundaryError("p1_cohort", "unknown_weight_rule")
    if (facts.get("declared_origin") != "declared_human"
            or facts.get("character") != "DEFECT"
            or type(facts.get("ascension")) is not int
            or facts.get("ascension") not in {0, 10}):
        return 0.0
    if facts.get("fact_conflict"):
        raise BoundaryError("p1_cohort", "conflicting_run_facts")
    if facts.get("result") == "win":
        return WEIGHT_PARAMETERS["win"]
    floor = facts.get("floor_reached")
    if floor is None:
        floor = facts.get("last_recorded_floor")
    denominator = facts.get("finish_floor_evidence") or {}
    total = denominator.get("floor")
    if type(floor) is not int or floor < 0 or type(total) is not int or total <= 0:
        raise BoundaryError("p1_cohort", "recorded_progress_floor_required")
    bosses = facts.get("boss_count")
    if bosses is None:
        bosses = 0  # Missing boss evidence grants no bonus.
    if type(bosses) is not int or bosses < 0:
        raise BoundaryError("p1_cohort", "invalid_recorded_boss_count")
    value = (WEIGHT_PARAMETERS["loss_unknown_base"]
             + WEIGHT_PARAMETERS["progress_coefficient"]
             * (floor / total) ** WEIGHT_PARAMETERS["progress_exponent"]
             + WEIGHT_PARAMETERS["boss_bonus"] * bosses)
    if value >= WEIGHT_PARAMETERS["nonwin_stop_at"]:
        raise BoundaryError("p1_cohort", "nonwin_weight_not_below_win_stop_and_report")
    return float(value)



A10_PROGRESS_RULE = "progress-boss-a10-progress-v1"


def a10_progress_weight(facts: dict[str, Any], *, maximum: int = 2) -> float:
    """Apply the new A10 multiplier to the existing whole-run rule."""
    if maximum not in (2, 3):
        raise BoundaryError("p1_cohort", "unsupported_a10_multiplier")
    original = run_weight(facts)
    if facts.get("ascension") != 10 or original == 0:
        return original
    if facts.get("result") == "win":
        return original * maximum
    floor = facts.get("floor_reached")
    if floor is None:
        floor = facts.get("last_recorded_floor")
    total = (facts.get("finish_floor_evidence") or {}).get("floor")
    progress = min(1.0, cast(int, floor) / cast(int, total))
    return float(min(3.0, original * (1 + (maximum - 1) * progress)))


def with_a10_progress(plan: dict[str, Any]) -> dict[str, Any]:
    """Derive a new content-addressed proposal from an existing cohort plan."""
    if plan["definition"].get("rule") != RULE_ID:
        raise BoundaryError("p1_cohort", "original_progress_rule_required")
    result = copy.deepcopy(plan)
    result.pop("configuration_id")
    result["definition"] = weight_definition(A10_PROGRESS_RULE)
    for row in result["runs"]:
        proposed = a10_progress_weight(row)
        row["proposed_run_weight"] = proposed
        row["train_weight"] = proposed if row["split"] == "train" else None
    train = [r for r in result["runs"] if r["split"] == "train"]
    total = sum(r["N"] * r["train_weight"] for r in train)
    result["partitions"]["train"]["weighted_N"] = total
    for key, predicate in {
        "A0": lambda r: r["ascension"] == 0, "A10": lambda r: r["ascension"] == 10,
        "win": lambda r: r["result"] == "win", "loss": lambda r: r["result"] == "loss",
        "unknown_outcome": lambda r: r["result"] is None,
    }.items():
        weighted = sum(r["N"] * r["train_weight"] for r in train if predicate(r))
        result["TRAIN_component_shares"][key]["weighted_N"] = weighted
        result["TRAIN_component_shares"][key]["weighted_share"] = weighted / total
    return {**result, "configuration_id": digest(json_bytes(result))}


def extend_holdouts(index: dict[str, Any], facts: dict[str, dict[str, Any]],
                    allocation: dict[str, Any], targets: dict[str, dict[str, int]], *,
                    split_salt: str) -> dict[str, Any]:
    """Fill fixed stratum targets from new whole groups; prior roles never move.

    This is a draft of the existing explicit split_by_group, not a new ledger.
    Selection uses difficulty and a fixed hash, never win/loss or step count.
    """
    if set(targets) != {"dev", "test"} or any(
        set(values) != {"0", "10"}
        or any(type(n) is not int or n < 0 for n in values.values())
        for values in targets.values()
    ):
        raise BoundaryError("p1_cohort", "invalid_holdout_targets")
    result = copy.deepcopy(allocation)
    details = {g["group_id"]: g for g in allocation["groups"]}
    nonempty = {g["group_id"] for g in index["groups"] if g["N"] > 0}
    strata = {}
    eligible = set()
    for group in index["groups"]:
        gid = group["group_id"]
        rows = [facts[root] for root in group["root_run_ids"]]
        levels = {r.get("ascension") for r in rows}
        strata[gid] = str(next(iter(levels))) if len(levels) == 1 else None
        if (group["N"] > 0 and details[gid]["basis"] == "new_group"
                and details[gid]["training_exposure"] is False
                and allocation["split_by_group"][gid] == "train"
                and all(r.get("native_start") and r.get("native_end")
                        and not r.get("fact_conflict")
                        and r.get("declared_origin") == "declared_human"
                        and r.get("character") == "DEFECT" for r in rows)):
            eligible.add(gid)
    ordered = sorted(eligible, key=lambda gid: digest(json_bytes([split_salt, gid])))
    shortfall: dict[str, dict[str, int]] = {}
    for role in ("dev", "test"):
        shortfall[role] = {}
        for level in ("0", "10"):
            present = sum(split == role and strata[gid] == level and gid in nonempty
                          for gid, split in result["split_by_group"].items())
            needed = max(0, targets[role][level] - present)
            selected = [gid for gid in ordered if strata[gid] == level][:needed]
            for gid in selected:
                result["split_by_group"][gid] = role
                ordered.remove(gid)
            shortfall[role][level] = needed - len(selected)
    result["partition_N"] = dict(Counter(
        result["split_by_group"][p["group_id"]] for p in index["points"]))
    result["holdout_targets"] = targets
    result["holdout_shortfall_groups"] = shortfall
    result["ready_to_freeze"] = result["ready_to_freeze"] and not any(
        n for values in shortfall.values() for n in values.values())
    return result


def cohort_plan(index: dict[str, Any], facts: dict[str, dict[str, Any]],
                allocation: dict[str, Any], *, rule: str = RULE_ID) -> dict[str, Any]:
    """Preserve ledger group roles; counts use unique admitted N, never aliases."""
    if allocation.get("blockers") or allocation.get("conflicts"):
        raise BoundaryError("p1_cohort", "resolve_curation_blockers_before_cohort")
    splits = allocation["split_by_group"]
    roots = {root: group["group_id"] for group in index["groups"]
             for root in group["root_run_ids"]}
    counts = Counter(p["root_run_id"] for p in index["points"])
    rows = []
    for root in sorted(counts):
        if root not in facts or root not in roots:
            raise BoundaryError("p1_cohort", "missing_run_facts")
        split = splits[roots[root]]
        if split not in {"train", "dev", "test"}:
            raise BoundaryError("p1_cohort", "unassigned_group")
        proposed = run_weight(facts[root], rule)
        weight = proposed if split == "train" else None
        rows.append({"root_run_id": root, "group_id": roots[root], "split": split,
                     "N": counts[root], "train_weight": weight, "proposed_run_weight": proposed,
                     "baseline2_train_weight": baseline2_weight(facts[root])
                     if split == "train" else None, **facts[root]})
    partitions = {}
    for split in ("train", "dev", "test"):
        selected = [r for r in rows if r["split"] == split]
        active = [r for r in selected if split != "train" or r["train_weight"] > 0]
        partitions[split] = {
            "runs": len(selected), "N": sum(r["N"] for r in selected),
            "positive_weight_runs": len(active), "positive_weight_N": sum(r["N"] for r in active),
            "A0_runs": sum(r["ascension"] == 0 for r in active),
            "A10_runs": sum(r["ascension"] == 10 for r in active),
            "A0_N": sum(r["N"] for r in active if r["ascension"] == 0),
            "A10_N": sum(r["N"] for r in active if r["ascension"] == 10),
            "weighted_N": sum(r["N"] * (r["train_weight"] if split == "train" else 1)
                              for r in active),
        }
    definition = weight_definition(rule)
    train = [r for r in rows if r["split"] == "train"]
    component_shares = {}
    for label, predicate in {
        "A0": lambda r: r["ascension"] == 0,
        "A10": lambda r: r["ascension"] == 10,
        "win": lambda r: r["result"] == "win",
        "loss": lambda r: r["result"] == "loss",
        "unknown_outcome": lambda r: r["result"] is None,
    }.items():
        subset = [r for r in train if predicate(r)]
        weighted = sum(r["N"] * r["train_weight"] for r in subset)
        baseline = sum(r["N"] * r["baseline2_train_weight"] for r in subset)
        total_weighted = sum(r["N"] * r["train_weight"] for r in train)
        total_baseline = sum(r["N"] * r["baseline2_train_weight"] for r in train)
        component_shares[label] = {
            "runs": len(subset), "N": sum(r["N"] for r in subset),
            "N_share": sum(r["N"] for r in subset) / sum(r["N"] for r in train),
            "weighted_N": weighted, "weighted_share": weighted / total_weighted,
            "baseline2_weighted_N": baseline,
            "baseline2_weighted_share": baseline / total_baseline if total_baseline else None,
        }
    body = {"schema": "stpd/p1-cohort-plan-1", "definition": definition,
            "sources": index["sources"], "selection": index["selection"],
            "splits": splits, "runs": rows, "partitions": partitions,
            "TRAIN_component_shares": component_shares,
            "holdout_targets": allocation.get("holdout_targets"),
            "holdout_shortfall_groups": allocation.get("holdout_shortfall_groups"),
            "isolation_claim": allocation.get("isolation_claim"),
            "status": "user_rule_fixed_pre_freeze_confirmation_and_trainer_support_required"}
    return {**body, "configuration_id": digest(json_bytes(body))}


def inspect_cohort(store: Any, ledger: Any, projection_ids: list[str], *,
                   selected_decisions: dict[str, list[str]] | None = None,
                   split_salt: str = "p1-next-training-20261025-v1",
                   holdout_targets: dict[str, dict[str, int]] | None = None) -> dict[str, Any]:
    """Use exact named versions; conflicts fail in the existing reader."""
    facts: dict[str, dict[str, Any]] = {}

    def observe(projection: Projection) -> None:
        current = projection_facts(projection)
        old = facts.get(projection.root)
        if old is not None and old != current:
            raise BoundaryError("p1_cohort", "conflicting_run_facts_select_one_projection")
        facts[projection.root] = current

    index = inspect_projections(store, projection_ids, selected_decisions=selected_decisions,
                                on_projection=observe)
    return plan_cohort(index, facts, ledger, split_salt=split_salt,
                       holdout_targets=holdout_targets)


def plan_cohort(index: dict[str, Any], facts: dict[str, dict[str, Any]], ledger: Any, *,
                split_salt: str, holdout_targets: dict[str, dict[str, int]] | None = None
                ) -> dict[str, Any]:
    """Compose verified inspections, including the existing private preparation cache.

    Allocation is always read afresh from Curation; a cache never reserves a role.
    """
    from spireagent.p1_curation import draft_p1_allocation

    facts = bind_finish_floors(facts)
    allocation = draft_p1_allocation(ledger, index, split_salt=split_salt,
                                    allocation_policy="preserve_existing")
    if holdout_targets is not None:
        allocation = extend_holdouts(index, facts, allocation, holdout_targets,
                                     split_salt=split_salt)
    return {"index": index, "facts": facts, "allocation": allocation,
            "plan": cohort_plan(index, facts, allocation, rule=RULE_ID)}


def pool_plan(index: dict[str, Any], facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Inventory facts and selection without assigning any experiment role."""
    facts = bind_finish_floors(facts)
    counts = Counter(p["root_run_id"] for p in index["points"])
    roots = {r: g["group_id"] for g in index["groups"] for r in g["root_run_ids"]}
    body = {"schema": "stpd/p1-cohort-pool-1", "sources": index["sources"],
            "selection": index["selection"], "groups": index["groups"], "points": index["points"],
            "runs": [{**facts[root], "root_run_id": root, "group_id": roots[root],
                      "N": n, "split": "pool", "train_weight": None,
                      "proposed_run_weight": None} for root, n in sorted(counts.items())],
            "partitions": {"pool": {"runs": len(counts), "N": sum(counts.values())}},
            "holdout_shortfall_groups": None, "isolation_claim": "pool_no_experiment_roles",
            "status": "pool_not_assigned_not_frozen"}
    return {**body, "configuration_id": digest(json_bytes(body))}


def experiment_plan(pool: dict[str, Any], ledger: Any, request: dict[str, Any], *,
                    weight_rule: str = "a10-progress-v1") -> dict[str, Any]:
    from spireagent.p1_curation import draft_model_allocation

    if (pool.get("schema") != "stpd/p1-cohort-pool-1" or
            digest(json_bytes({k: v for k, v in pool.items() if k != "configuration_id"})) !=
            pool.get("configuration_id")):
        raise BoundaryError("p1_cohort", "verified_pool_plan_required")
    required = {"model_id", "parent_model_id", "split_salt"}
    if (not isinstance(request, dict) or not required <= request.keys() or
            set(request) - (required | {"dev_fraction", "test_fraction",
                                        "split_by_group", "selected_group_ids"})):
        raise BoundaryError("p1_cohort", "explicit_experiment_request_required")
    index = copy.deepcopy({k: pool[k] for k in ("sources", "selection", "groups", "points")})
    selected = request.get("selected_group_ids")
    if selected is not None:
        known = {g["group_id"] for g in index["groups"] if g["N"]}
        if (not isinstance(selected, list) or not selected or
                any(not isinstance(g, str) for g in selected) or
                len(set(selected)) != len(selected) or set(selected) - known):
            raise BoundaryError("p1_cohort", "explicit_unique_pool_groups_required")
        index["points"] = [p for p in index["points"] if p["group_id"] in selected]
        for group in index["groups"]:
            if group["group_id"] not in selected:
                group["N"] = 0
        decisions: dict[str, list[str]] = {s["projection_id"]: [] for s in index["sources"]}
        for point in index["points"]:
            for ref in [point, *point["aliases"]]:
                decisions[ref["projection_id"]].append(ref["source_decision_id"])
        index["selection"] = {k: sorted(set(v)) for k, v in decisions.items()}
        for source in index["sources"]:
            source["selected_N_before_dedup"] = len(index["selection"][source["projection_id"]])
    allocation = draft_model_allocation(ledger, index, **{k: v for k, v in request.items()
                                                        if k != "selected_group_ids"})
    facts = {r["root_run_id"]: {k: v for k, v in r.items() if k not in {
        "N", "split", "train_weight", "proposed_run_weight", "group_id"}} for r in pool["runs"]}
    result = cohort_plan(index, facts, allocation)
    if weight_rule == "a10-progress-v1":
        result = with_a10_progress(result)
    elif weight_rule != "original":
        raise BoundaryError("p1_cohort", "unknown_weight_rule")
    result.pop("configuration_id")
    result["experiment"] = {k: request[k] for k in ("model_id", "parent_model_id")}
    result["allocation_request"] = request
    result["pool_configuration_id"] = pool["configuration_id"]
    result["status"] = "experiment_draft_not_frozen"
    return {**result, "configuration_id": digest(json_bytes(result))}



def weight_definition(rule: str = RULE_ID) -> dict[str, Any]:
    """One versioned definition shared by preview and authoritative freeze validation."""
    base = {"rule": RULE_ID, "parameters": WEIGHT_PARAMETERS,
                  "gate": "declared_human_DEFECT_A0_A10_valid_N",
                  "partial_recordings": "valid_N_retained_with_observed_progress",
                  "unknown_terminal": "same_progress_formula_separate_unknown_outcome_count",
                  "finish_floor": "same_difficulty_wins_else_final_boss_map_else_A0_marked",
                  "boss_bonus": "actual_positive_witness_only_never_floor_inference",
                  "weight_scope": "TRAIN_only_same_multiplier_per_run",
                  "loss": "sum(w_run * NLL_point) / sum(w_run); all_ones_original_mean",
                  "holdout": "preserve_Curation_roles_unweighted_DEV_TEST"}
    if rule == RULE_ID:
        return copy.deepcopy(base)
    if rule == A10_PROGRESS_RULE:
        return {**copy.deepcopy(base), "rule": A10_PROGRESS_RULE, "base_rule": RULE_ID,
                "difficulty_multiplier": {"A0": 1, "A10_maximum": 2,
                    "progress": "min(floor/finish_floor,1); win=1", "nonwin_cap": 3, "version": 1}}
    raise BoundaryError("p1_cohort", "unknown_weight_rule")
