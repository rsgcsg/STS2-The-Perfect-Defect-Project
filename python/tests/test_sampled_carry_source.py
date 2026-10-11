"""Pure original-basis sampled reexpression; no numerical or live operations."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_ordered_source import contract_bundle, original_basis
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.fullrun import ordered_source as sources
from stpd.native_sampled_carry_spec import INPUT_SPEC, RECIPE, VIEW
from stpd.ordered_source_spec import (
    DEFAULT_RECIPE,
    DEFAULT_VIEW,
    PRETRAIN_VIEW,
    SCOPE,
    SOURCE_SCHEMA,
    recipe_view,
    view_input_spec,
    view_specs,
)
from stpd.structured_profiles import qualification, validate_profile

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (
        ROOT / "components/policy-runtime/contracts/fixtures/sampled-current-carry-v1.json"
    ).read_bytes()
)
GOLDEN = ROOT / "components/evidence/tests/fixtures/source_session_v3/bundle"
ORIGINAL = Producer("fixture://original-Source3-production-path", "e" * 40, "a" * 64)
PROJECTOR = Producer("fixture://sampled-Source3-projection", "b" * 40, "b" * 64)


def projected(bundle):
    runs, report = sources._projection(bundle, "a" * 64, "declared_human", VIEW)
    projection, target = view_specs(VIEW)
    source = {
        "schema": SOURCE_SCHEMA,
        "source_kind": "declared_human",
        "input_spec": INPUT_SPEC,
        "projection_spec": projection,
        "target_spec": target,
        "teacher": sources._teacher("declared_human"),
        "raw_refs": [{"raw_id": "a" * 64, "admission_id": "b" * 64}],
        "runs": runs,
    }
    return source, report, sources.parse_ordered_training_dataset(json_bytes(source))


def set_basis(directory, row, observation, actions):
    for role, value, key in (
        ("pre_capture", observation, "sha256"),
        ("catalog", actions, "payload_sha256"),
    ):
        raw = json_bytes(value)
        sha = hashlib.sha256(raw).hexdigest()
        path = "sample-fixture/" + sha + ".json"
        target = directory / "raw" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        ref = dict(row[role])
        ref.update(payload_ref=path, byte_count=len(raw), **{key: sha})
        if role == "pre_capture":
            ref.update(capture_id=observation["snapshot_id"] + "-capture")
        else:
            ref.update(total_count=len(actions), structural_digest=observation["catalog"]["digest"])
        row[role] = ref
    row["outcome"].update(
        mapping_status="exact",
        delivery="delivered",
        match_count=1,
        selected_action=actions[0] if actions else None,
    )
    if not actions:
        row["outcome"].update(mapping_status="unmapped", match_count=0)


def rows(bundle):
    return sorted(bundle.inputs, key=lambda row: int(row["input_prefix_ordinal"]))


def test_input_bases_keep_unlabelled_context_without_publication_memory(tmp_path):
    bundle = contract_bundle(tmp_path)
    first, _ = rows(bundle)
    first["outcome"].update(mapping_status="unmapped", selected_action=None, match_count=0)
    source, report, dataset = projected(bundle)
    assert [step["observation"]["revision"] for step in source["runs"][0]["steps"]] == [2, 3]
    assert [step.chosen_action_id for step in dataset.runs[0].steps] == [None, "action-3"]
    assert [step.advance for step in dataset.runs[0].steps] == [True, True]
    assert [step.reset_before for step in dataset.runs[0].steps] == [True, False]
    assert len(report["index"]) == 4 and report["counts"]["eligible_unique_N"] == 1
    assert dataset.input_spec.value() == INPUT_SPEC and validate_profile(dataset, SCOPE)
    assert qualification(dataset) == "source3_decision_sample_carry_N_reexpression_only"


@pytest.mark.parametrize("missing", ["basis", "order"])
def test_missing_input_resumes_independent_span_with_explicit_W0(tmp_path, missing):
    bundle = contract_bundle(tmp_path)
    first, second = rows(bundle)
    first.update(pre_capture=None, catalog=None)
    first["outcome"].update(mapping_status="capture_missing", selected_action=None, match_count=0)
    if missing == "order":
        first["basis_order"] = {
            "status": "unproven",
            "reason_code": "source_prefix_capture_order_unproven",
        }
    source, report, dataset = projected(bundle)
    step = dataset.runs[0].steps[0]
    assert step.advance and step.reset_before and step.chosen_action_id == "action-3"
    assert step.reset_reason == (
        "input_order_unproven" if missing == "order" else "input_basis_missing"
    )
    identity = source["runs"][0]["identity"]
    assert identity["sample_segments"][0]["first_input_id"] == second["input_id"]
    assert identity["sample_segments"][0]["cuts"][0]["original"] == first
    assert dataset.runs[0].run_id == report["index"][0]["run_id"]
    assert len(report["index"]) == 4 and not identity["whole_game_recorded_capture_eligible"]


def test_verified_partial_basis_cuts_before_qualification_and_retains_next_span(tmp_path):
    directory = tmp_path / "partial-original"
    shutil.copytree(
        ROOT / "components/evidence/tests/fixtures/source_session_v3_ordered/bundle", directory
    )
    helper_type = runpy.run_path(
        str(ROOT / "components/evidence/tests/test_source_session_bundle_v2.py")
    )["SourceSessionBundleV2Tests"]
    helper = helper_type()
    helper.bundle = directory
    values = helper.rows("native-input-witnesses.jsonl")
    first = min(values, key=lambda row: int(row["input_prefix_ordinal"]))
    old_path = first["pre_capture"]["payload_ref"]
    observation = json.loads((directory / "raw" / old_path).read_bytes())
    observation["persistent"] = None
    observation["completeness"].update(
        status="partial",
        included=["interaction", "referents", "catalog"],
        missing=["persistent"],
        full_reference_complete=False,
    )
    raw = json_bytes(observation)
    sha = hashlib.sha256(raw).hexdigest()
    relative = f"public-captures/sha256/{sha[:2]}/{sha}.bin"
    target = directory / "raw" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    first["pre_capture"].update(payload_ref=relative, sha256=sha, byte_count=len(raw))
    first["outcome"].update(mapping_status="unmapped", match_count=0, selected_action=None)
    helper.write_rows("native-input-witnesses.jsonl", values)
    if not any(old_path in path.read_text() for path in (directory / "raw").glob("*.jsonl")):
        (directory / "raw" / old_path).unlink()
    helper.reseal()
    original = sources._verified(directory)
    source, report, dataset = projected(original)
    assert len(dataset.runs) == 1 and len(dataset.runs[0].steps) == 1
    step = dataset.runs[0].steps[0]
    segment_cuts = source["runs"][0]["identity"]["sample_segments"][0]["cuts"]
    assert step.reset_before and step.advance
    # This genuine fixture also pauses and hands actors off between ordinals.
    # The partial basis cause survives alongside those later original barriers.
    assert segment_cuts[0]["reason"] == "input_basis_missing"
    assert step.reset_reason == segment_cuts[-1]["reason"] == "actor_handoff"
    assert source["runs"][0]["steps"][0]["evidence"]["input_prefix_ordinal"] == "2"
    assert segment_cuts[0]["original"] == first
    partial = next(row for row in report["index"] if row["evidence"]["input_prefix_ordinal"] == "1")
    following = next(
        row for row in report["index"] if row["evidence"]["input_prefix_ordinal"] == "2"
    )
    assert not partial["admitted"] and not partial["model_exposed"] and not partial["N_eligible"]
    assert partial["target_eligibility"] == "input_basis_missing"
    assert partial["evidence"]["capture"] == first["pre_capture"]
    assert partial["run_id"] == following["run_id"] == dataset.runs[0].run_id
    assert partial["source_group"] == following["source_group"] == dataset.runs[0].source_group
    assert partial["related_keys"] == following["related_keys"]


def test_partial_basis_check_preserves_blob_integrity_failure(tmp_path):
    bundle = contract_bundle(tmp_path)
    first = rows(bundle)[0]
    path = tmp_path / "raw" / first["pre_capture"]["payload_ref"]
    observation = json.loads(path.read_bytes())
    observation["completeness"]["full_reference_complete"] = False
    # A different original byte stream without its binding is corruption, not a cut.
    path.write_bytes(json_bytes(observation))
    with pytest.raises(BoundaryError, match="original_blob_changed"):
        projected(bundle)


def test_complete_basis_qualifier_capacity_failure_is_not_a_segment_cut(tmp_path, monkeypatch):
    from stpd.fullrun import native_structured_inputs

    monkeypatch.setattr(native_structured_inputs, "MAX_SNAPSHOT_BYTES", 8)
    with pytest.raises(BoundaryError, match="input_byte_limit"):
        projected(contract_bundle(tmp_path))


def test_recording_pause_and_actor_cut_keep_original_group(tmp_path):
    bundle = contract_bundle(tmp_path)
    first, second = rows(bundle)
    bundle.boundaries = (
        {
            "sequence": 1,
            "kind": "pause",
            "position": first["pre_position"],
            "after_input_ordinal": "1",
            "transition": None,
        },
        {
            "sequence": 2,
            "kind": "resume",
            "position": first["pre_position"],
            "after_input_ordinal": "1",
            "transition": None,
        },
    )
    source, _, dataset = projected(bundle)
    assert [step.reset_reason for step in dataset.runs[0].steps] == [
        "sample_segment_start",
        "recording_pause",
    ]
    assert len(source["runs"]) == 1 and len(source["runs"][0]["identity"]["sample_segments"]) == 2
    original_group = dataset.runs[0].source_group
    declaration = copy.deepcopy(bundle.segments[0])
    declaration.update(segment_id="segment-2")
    declaration["declaration"].update(actor_id="another-actor", source_kind="agent_protocol")
    bundle.segments += (declaration,)
    second["segment_id"] = "segment-2"
    source, report, dataset = projected(bundle)
    assert dataset.runs[0].source_group == original_group
    assert dataset.runs[0].steps[1].reset_reason == "actor_handoff"
    assert dataset.runs[0].steps[1].chosen_action_id is None
    assert source["runs"][0]["steps"][1]["evidence"]["segment_id"] == "segment-2"
    assert [
        cut["reason"] for cut in source["runs"][0]["identity"]["sample_segments"][1]["cuts"]
    ] == ["recording_pause", "actor_handoff"]
    assert len(report["index"]) == 4


@pytest.mark.parametrize("delivery", ["unknown", "partially_delivered"])
def test_unknown_retains_pre_basis_context_then_cuts_next_span(tmp_path, delivery):
    bundle = contract_bundle(tmp_path)
    first, _ = rows(bundle)
    first["outcome"]["delivery"] = delivery
    source, _, dataset = projected(bundle)
    assert [step.chosen_action_id for step in dataset.runs[0].steps] == [None, "action-3"]
    assert [step.reset_reason for step in dataset.runs[0].steps] == [
        "sample_segment_start",
        "delivery_unknown",
    ]
    assert source["runs"][0]["identity"]["cuts"][0]["original"]["outcome"]["delivery"] == delivery


def test_identical_input_is_excluded_but_unknown_barrier_is_not_hidden(tmp_path):
    bundle = contract_bundle(tmp_path)
    first, second = rows(bundle)
    repeated = copy.deepcopy(first)
    repeated.update(sequence=3, input_id="input-2", input_prefix_ordinal="2")
    repeated["outcome"]["delivery"] = "unknown"
    second.update(input_id="input-3", input_prefix_ordinal="3")
    bundle.inputs = (second, repeated, first)
    bundle.final_input_prefix_ordinal = "3"
    bundle.boundaries[0]["after_input_ordinal"] = "3"
    source, report, dataset = projected(bundle)
    assert len(dataset.runs[0].steps) == 2
    assert dataset.runs[0].steps[1].reset_reason == "delivery_unknown"
    excluded = [row for row in report["index"] if row["evidence"]["input_id"] == "input-2"][0]
    assert not excluded["model_exposed"] and not excluded["N_eligible"]
    assert excluded["target_eligibility"] == "unchanged_current_readiness_not_sample"
    assert len(source["runs"][0]["identity"]["sample_segments"]) == 2


def test_empty_readiness_is_not_sample_but_actual_terminal_basis_is(tmp_path):
    bundle = contract_bundle(tmp_path)
    first, second = rows(bundle)
    wait = original_basis(tmp_path, 2, empty=True)
    summary = original_basis(tmp_path, 3, empty=True, terminal=True)
    set_basis(tmp_path, first, wait[0], wait[1])
    set_basis(tmp_path, second, summary[0], summary[1])
    source, report, dataset = projected(bundle)
    assert len(dataset.runs[0].steps) == 1
    assert not dataset.runs[0].steps[0].frame.action_ids
    assert dataset.runs[0].steps[0].chosen_action_id is None
    assert source["runs"][0]["steps"][0]["observation"] == summary[0]
    assert report["counts"]["eligible_unique_N"] == 0
    assert report["exclusions"][0]["reason"] == "empty_C_readiness_not_sample"


def test_shared_map_inspect_map_feature_equality_keeps_three_advances(tmp_path):
    bundle = contract_bundle(tmp_path)
    template = rows(bundle)[0]
    inputs = []
    for ordinal, name in enumerate(("map_a", "inspect_b", "map_c"), 1):
        row = copy.deepcopy(template)
        row.update(sequence=ordinal, input_id=f"input-{ordinal}", input_prefix_ordinal=str(ordinal))
        basis = FIXTURE["frames"][name]
        set_basis(tmp_path, row, basis["observation"], basis["catalog"])
        if name == "inspect_b":
            row["outcome"].update(mapping_status="unmapped", selected_action=None, match_count=0)
        inputs.append(row)
    bundle.inputs = tuple(reversed(inputs))
    bundle.final_input_prefix_ordinal = "3"
    bundle.boundaries[0]["after_input_ordinal"] = "3"
    _, report, dataset = projected(bundle)
    steps = dataset.runs[0].steps
    assert [s.advance for s in steps] == [True, True, True]
    assert steps[0].frame.nodes == steps[2].frame.nodes
    assert steps[1].chosen_action_id is None
    assert report["counts"]["eligible_unique_N"] == 2


def test_missing_publication_does_not_remove_independent_input_sample(tmp_path):
    bundle = contract_bundle(tmp_path)
    bundle.observations[0].update(capture=None, catalog=None, completeness="missing")
    source, report, dataset = projected(bundle)
    assert len(dataset.runs[0].steps) == 2
    assert report["counts"]["eligible_unique_N"] == 2
    assert source["runs"][0]["identity"]["cuts"] == []
    assert len(report["index"]) == 4


def test_environment_change_starts_explicit_segment(tmp_path):
    bundle = contract_bundle(tmp_path)
    _, second = rows(bundle)
    observation, actions, _, _ = original_basis(tmp_path, 3)
    observation["session"]["environment_fingerprint"] = "changed-environment"
    observation["catalog"]["stream_generation"] = "changed-generation"
    set_basis(tmp_path, second, observation, actions)
    _, _, dataset = projected(bundle)
    assert [s.reset_reason for s in dataset.runs[0].steps] == [
        "sample_segment_start",
        "environment_boundary",
    ]


def test_rejected_original_input_stays_context_without_N_or_invented_summary(tmp_path):
    bundle = contract_bundle(tmp_path)
    first, _ = rows(bundle)
    first["outcome"]["delivery"] = "rejected_before_input"
    source, _, dataset = projected(bundle)
    assert [s.chosen_action_id for s in dataset.runs[0].steps] == [None, "action-3"]
    assert [s.reset_before for s in dataset.runs[0].steps] == [True, False]
    assert [s["observation"]["revision"] for s in source["runs"][0]["steps"]] == [2, 3]


@pytest.mark.parametrize("change", ["segment_id", "reason", "extra_field", "readiness"])
def test_closed_segment_subtype_rejects_rehashed_or_unbound_projection(tmp_path, change):
    source, _, _ = projected(contract_bundle(tmp_path))
    run = source["runs"][0]
    if change == "segment_id":
        run["identity"]["sample_segments"][0]["id"] = "f" * 64
    elif change == "reason":
        run["steps"][0]["reset_reason"] = "invented_native_new_game"
    elif change == "extra_field":
        run["identity"]["sample_segments"][0]["native_new_game"] = True
    else:
        run["steps"][1]["observation"] = copy.deepcopy(run["steps"][0]["observation"])
        run["steps"][1]["catalog"] = copy.deepcopy(run["steps"][0]["catalog"])
    with pytest.raises(BoundaryError):
        sources.parse_ordered_training_dataset(json_bytes(source))


def partition(store, split="train", view=VIEW):
    raw = sources.publish_ordered_source_raw(store, GOLDEN, ORIGINAL)
    ref = sources.publish_ordered_source_admission(
        store, raw.artifact_id, PROJECTOR, cohort="agent_protocol", view=view
    )
    return raw, ref, sources.publish_ordered_source_partition(store, (ref,), split, PROJECTOR)


def test_actual_raw_partition_replay_preserves_all_original_use_split_gold_associations(tmp_path):
    store, owner = setup_store(tmp_path)
    raw, _, train = partition(store)
    assert len(train.index) == 7 and len(train.runs) == 2
    assert len(train.dataset.runs) == 1
    assert len(train.dataset.runs[0].steps) == 1 and train.dataset.runs[0].steps[0].advance
    assert train.dataset.input_spec.value() == INPUT_SPEC
    assert sources.verify_ordered_source_partition(store, train.manifest.artifact_id) == train
    owner.reserve_verified_ordered_source(store, train.manifest.artifact_id)
    receipt = owner.record_verified_ordered_training_use(
        store, train.manifest.artifact_id, "1" * 32
    )
    assert receipt["qualified_run_ids"] == sorted(train.runs)
    for view in (DEFAULT_VIEW, PRETRAIN_VIEW, VIEW):
        _, _, test = partition(store, "test", view)
        assert test.runs == train.runs
        with pytest.raises(BoundaryError, match="protocol_split_purpose_overlap"):
            owner.reserve_verified_ordered_source(store, test.manifest.artifact_id)
    with owner.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_source_decisions WHERE source=?", (raw.artifact_id,)
        ).fetchone() == (7,)
    gold_path = tmp_path / "gold"
    gold_path.mkdir()
    gold_store, gold_owner = setup_store(gold_path)
    original = sources._verified(GOLDEN)
    reserved = {sources._run_identity(original, epoch)[0] for epoch in original.epochs}
    gold_owner.ledger.claim("gold-original", "gold", reserved)
    _, _, gold = partition(gold_store)
    assert gold.runs == reserved
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        gold_owner.reserve_verified_ordered_source(gold_store, gold.manifest.artifact_id)


def test_rehashed_partition_cannot_replace_raw_original_join(tmp_path):
    store, _ = setup_store(tmp_path)
    _, _, train = partition(store)
    value = json.loads(train.dataset.source_bytes)
    value["runs"][0]["steps"][0]["chosen_action_id"] = None
    payload = store.put_payload("source", io.BytesIO(json_bytes(value)), "application/json")
    forged = Manifest(
        "dataset",
        train.manifest.producer,
        train.manifest.parents,
        (payload,),
        FrozenObject.of(train.manifest.parameters.value() | {"source_sha256": payload.sha256}),
    )
    store.publish(forged)
    with pytest.raises(BoundaryError, match="projected_original_join_mismatch"):
        sources.verify_ordered_source_partition(store, forged.artifact_id)


def test_failed_raw_accounting_or_capture_cannot_be_bypassed(tmp_path):
    directory = tmp_path / "broken-original"
    shutil.copytree(GOLDEN, directory)
    (directory / "raw/native-input-witnesses.jsonl").write_bytes(b"")
    state_path = tmp_path / "store"
    state_path.mkdir()
    store, _ = setup_store(state_path)
    with pytest.raises(BoundaryError, match="source3_bundle_verification_failed"):
        sources.publish_ordered_source_raw(store, directory, ORIGINAL)
    assert store.manifest_ids() == ()


def test_old_view_hashes_and_default_call_compatibility_are_fixed():
    assert (
        DEFAULT_VIEW == "publication_memory"
        and DEFAULT_RECIPE == "source3-native-m2-k1d96-carry-N-v1"
    )
    assert (
        view_specs(DEFAULT_VIEW)[0]["sha256"]
        == "a8056780e9fb51093db3f74958a8cdfb48ab4103fe437f365cb3c1a0d0280914"
    )
    assert (
        view_specs(DEFAULT_VIEW)[1]["sha256"]
        == "60751f9c74ea883379021ff8a547ca78cdfd213f202545c7e391c8d17baae922"
    )
    assert (
        view_specs(PRETRAIN_VIEW)[0]["sha256"]
        == "17942e5337db790dfd09e800c4449773e6a1858c7b01014ecc1a276337cea2c3"
    )
    assert (
        view_specs(PRETRAIN_VIEW)[1]["sha256"]
        == "9a6a61c28a172f577e0d0e8170e5b9a497f868de0bca5201937b788f9a2fd671"
    )
    assert recipe_view(RECIPE) == VIEW and view_input_spec(VIEW) == INPUT_SPEC


def test_sampled_catalog_metadata_does_not_import_numerical_backend():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from spireagent.workbench.trusted_recipes import describe_recipe; "
            "from stpd.native_sampled_carry_spec import RECIPE, VIEW; import sys; "
            "d=describe_recipe(RECIPE); assert d['source_view']==VIEW; "
            "assert 'torch' not in sys.modules; assert 'safetensors' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_projected_capacity_rejects_before_json_decoding(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "MAX_SOURCE_BYTES", 8)
    monkeypatch.setattr(sources, "decode_json", lambda raw: calls.append(raw))
    with pytest.raises(BoundaryError, match="projected_source_capacity"):
        sources.parse_ordered_training_dataset(b"012345678")
    assert not calls
