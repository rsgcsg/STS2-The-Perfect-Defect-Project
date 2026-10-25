"""Cohort rules, exact grouping and unchanged held-out purpose using real owners."""
from __future__ import annotations

import copy

import pytest
from test_p1_data_preparation import workspace

from spireagent.json_boundary import BoundaryError
from spireagent.p1_curation import draft_p1_allocation
from spireagent.workbench.p1_data_preparation import freeze_p1_data, prepare_p1_data
from spireagent.workbench.p1_training_data import record_p1_training_use
from stpd.p1.cohort import bind_finish_floors, cohort_plan, projection_facts, run_weight
from stpd.p1.dataset import inspect_projections, load_projection


def fact(**changes):
    return {"declared_origin": "declared_human", "character": "DEFECT", "ascension": 0,
            "result": "win", "floor_reached": 48, "boss_count": 0, "fact_conflict": False,
            "finish_floor_evidence": {"floor": 48}, **changes}


def test_user_formula_unknown_outcome_boss_bonus_and_stop():
    assert run_weight(fact()) == 3
    assert run_weight(fact(result="loss", floor_reached=0)) == 0.25
    assert run_weight(fact(result="loss", boss_count=2)) == 2
    assert run_weight(fact(result=None, floor_reached=24, boss_count=1)) == 0.8125
    assert run_weight(fact(result="loss", ascension=10, boss_count=3)) == 2.25
    assert run_weight(fact(result=None, floor_reached=None,
                           last_recorded_floor=24)) == 0.5625
    assert run_weight(fact(result="loss", boss_count=None)) == 1.5
    for changes in ({"ascension": None}, {"ascension": True},
                    {"declared_origin": "agent_native_ui"}, {"character": "SILENT"}):
        assert run_weight(fact(**changes)) == 0
    for changes in ({"boss_count": 6, "result": "loss"},
                    {"boss_count": 6, "result": None}):
        with pytest.raises(BoundaryError, match="stop_and_report"):
            run_weight(fact(**changes))
    with pytest.raises(BoundaryError, match="conflicting_run_facts"):
        run_weight(fact(fact_conflict=True))
    with pytest.raises(BoundaryError, match="recorded_progress_floor"):
        run_weight(fact(result=None, floor_reached=None))


def test_denominator_reads_wins_final_boss_map_and_marked_A0_fallback():
    rows = {"win": fact(), "a10": fact(ascension=10, result="loss",
            final_boss_maps=[{"floor": 49}])}
    bound = bind_finish_floors(rows)
    assert bound["win"]["finish_floor_evidence"]["floor"] == 48
    assert bound["a10"]["finish_floor_evidence"]["floor"] == 49
    assert "boss_map" in bound["a10"]["finish_floor_evidence"]["basis"]
    rows["a10"].pop("final_boss_maps")
    bound = bind_finish_floors(rows)
    assert "A0_fallback" in bound["a10"]["finish_floor_evidence"]["basis"]
    rows["another_win"] = fact(floor_reached=49)
    with pytest.raises(BoundaryError, match="conflicting_finish"):
        bind_finish_floors(rows)


def test_real_reader_facts_keep_unknown_and_public_ascension(tmp_path):
    _, store, projections = workspace(tmp_path)
    p = load_projection(store, projections[0].artifact_id)
    point = p.value["segments"][0]["points"][0]
    point["observation"]["run"] = {"content": {"ascension": 10, "floor": 17}}
    point["observation"]["player"] = {"content": {"character_definition_id": "DEFECT"}}
    point["end_target"] = {"result": "win"}
    # A target without its end mask cannot assert a terminal outcome.
    value = projection_facts(p)
    assert value["ascension"] == 10 and value["result"] is None
    point["mask"]["end"] = True
    assert projection_facts(p)["result"] == "win"
    other = copy.deepcopy(point)
    other["observation"]["run"]["content"]["ascension"] = 0
    p.value["segments"][0]["points"].append(other)
    assert projection_facts(p)["fact_conflict"]


def test_ledger_exposure_holdout_preserved_and_facts_change_identity(tmp_path):
    from test_artifact_store_v1 import PRODUCER

    owner, store, projections = workspace(tmp_path)
    prepared = prepare_p1_data(owner, store)
    frozen = freeze_p1_data(owner, store, PRODUCER, **prepared["selection"], reserve=True)
    record_p1_training_use(owner, store, frozen["snapshot_id"], "b" * 32)
    index = inspect_projections(store, [p.artifact_id for p in projections])
    allocation = draft_p1_allocation(owner.ledger, index, split_salt="different-salt")
    assert allocation["split_by_group"] == prepared["selection"]["split_by_group"]
    facts = {s["root_run_id"]: fact(root_run_id=s["root_run_id"], source_id=s["source_id"])
             for s in index["sources"]}
    a = cohort_plan(index, facts, allocation)
    b = cohort_plan(index, {k: {**v, "boss_count": 1, "result": "loss"}
                           for k, v in facts.items()}, allocation)
    assert a["configuration_id"] != b["configuration_id"]
    assert a == cohort_plan(index, facts, allocation)
    for split in ("dev", "test"):
        assert a["partitions"][split] == b["partitions"][split]
        assert all(r["train_weight"] is None for r in a["runs"] if r["split"] == split)
    assert a["partitions"]["train"]["N"] == 1
    assert b["partitions"]["train"]["weighted_N"] == 1.75
    with pytest.raises(BoundaryError, match="resolve_curation"):
        cohort_plan(index, facts, {**allocation, "blockers": ["quality_excluded_N"]})


def test_fresh_holdouts_whole_groups_preserve_exposure_and_ignore_result():
    from stpd.p1.cohort import extend_holdouts

    names = ["old-train", "old-dev", "old-test", "a", "b", "c", "d", "e", "f"]
    index = {"groups": [{"group_id": n, "root_run_ids": [n], "N": 1} for n in names],
             "points": [{"group_id": n} for n in names]}
    facts = {n: fact(native_start=True, native_end=True, ascension=10) for n in names}
    allocation = {
        "split_by_group": {n: "dev" if n == "old-dev" else "test" if n == "old-test"
                           else "train" for n in names},
        "groups": [{"group_id": n, "basis": "prior_claim_or_permanent_exposure"
                    if n.startswith("old-") else "new_group", "training_exposure":
                    n == "old-train"} for n in names], "ready_to_freeze": True,
    }
    targets = {"dev": {"0": 0, "10": 3}, "test": {"0": 0, "10": 3}}
    a = extend_holdouts(index, facts, allocation, targets, split_salt="fixed")
    assert a["split_by_group"]["old-train"] == "train"
    assert a["split_by_group"]["old-dev"] == "dev"
    assert a["split_by_group"]["old-test"] == "test"
    assert a["ready_to_freeze"]
    changed = {n: {**r, "result": "loss", "floor_reached": 1} for n, r in facts.items()}
    b = extend_holdouts(index, changed, allocation, targets, split_salt="fixed")
    assert a == b  # No cherry-picking by outcomes or length.
    facts["a"]["native_start"] = False
    facts["b"]["native_end"] = False
    facts["c"]["native_start"] = False
    c = extend_holdouts(index, facts, allocation, targets, split_salt="fixed")
    assert not c["ready_to_freeze"]
    assert sum(v["10"] for v in c["holdout_shortfall_groups"].values()) == 1


def test_boss_bonus_needs_positive_witness_and_double_bosses_count_separately(tmp_path):
    _, store, projections = workspace(tmp_path)
    p = load_projection(store, projections[0].artifact_id)
    template = p.value["segments"][0]["points"][0]
    points = []
    for floor in (48, 49):
        for family in ("combat", "reward"):
            point = copy.deepcopy(template)
            point["source_event_id"] = f"{family}-{floor}"
            o = point["observation"]
            o["run"] = {"content": {"act": 3, "floor": floor, "ascension": 10}}
            o["scene"] = {"family": family}
            o["combat"] = {"content": {"encounter_type": "boss"}} if family == "combat" else {}
            o["map"] = {"content": {"current_position": {"point_type": "boss"}}}
            points.append(point)
    p.value["segments"][0]["points"] = points
    assert projection_facts(p)["boss_count"] == 2
    # Arrival/combat without the following victory reward grants no bonus.
    p.value["segments"][0]["points"] = [points[0], points[2]]
    assert projection_facts(p)["boss_count"] == 0
    # A lone reward without a matching observed boss combat also grants no bonus.
    p.value["segments"][0]["points"] = [points[1]]
    assert projection_facts(p)["boss_count"] == 0


def test_unknown_only_train_can_compare_with_zero_baseline(tmp_path):
    owner, store, projections = workspace(tmp_path)
    prepared = prepare_p1_data(owner, store)
    index = prepared["index"]
    allocation = prepared["allocation"]
    facts = {s["root_run_id"]: fact(root_run_id=s["root_run_id"], source_id=s["source_id"],
                                   result=None, floor_reached=5)
             for s in index["sources"]}
    plan = cohort_plan(index, facts, allocation)
    assert plan["TRAIN_component_shares"]["unknown_outcome"]["weighted_share"] == 1
    assert plan["TRAIN_component_shares"]["unknown_outcome"]["baseline2_weighted_share"] is None


def test_refresh_keeps_anchor_versions_and_rejects_ambiguous_new_source(tmp_path):
    from dataclasses import replace

    from test_p1_snapshot import projection_fixture

    from spireagent.json_boundary import FrozenObject
    from spireagent.workbench.p1_data_preparation import discover_p1_projections
    from tools.p1_cohort import selection_ids

    _, store, originals = workspace(tmp_path)
    projections = []
    for name, original_projection in zip(("a", "b", "c"), originals, strict=True):
        original_source = store.get_manifest(original_projection.parent("source"))
        human = replace(original_source, parameters=FrozenObject.of({
            **original_source.parameters.value(), "declared_origin": "declared_human"}))
        store.publish(human)
        projections.append(projection_fixture(store, name, original=human)[0])
    anchor = projections[0].artifact_id
    request = {"projection_ids": [anchor], "new_source_tool_release_ids": ["tool-release-fixture"]}
    assert set(selection_ids(store, request, discover_p1_projections(store))) == {
        p.artifact_id for p in projections}
    # A second version of an anchored source cannot replace the selected version.
    alias = replace(projections[0], parameters=FrozenObject.of({
        **projections[0].parameters.value(), "conversion_variant": "other"}))
    store.publish(alias)
    assert alias.artifact_id not in selection_ids(store, request, discover_p1_projections(store))
    # Two reviewed versions of a fresh source require an explicit operator choice.
    alias = replace(projections[1], parameters=FrozenObject.of({
        **projections[1].parameters.value(), "conversion_variant": "other"}))
    store.publish(alias)
    with pytest.raises(BoundaryError, match="new_source_projection_version_ambiguous"):
        selection_ids(store, request, discover_p1_projections(store))
