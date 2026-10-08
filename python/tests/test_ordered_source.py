"""Source3 projection contract tests, not a native producer/verification receipt.

These isolated original-shaped bytes exercise the research projection. Public
import/admission additionally requires the real Evidence Source3 typed verifier.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.native_structured_inputs import INPUT_SPEC, native_catalog_digest
from stpd.fullrun.ordered_source import (
    OrderedSourceRef,
    _projection,
    _teacher,
    parse_ordered_training_dataset,
)
from stpd.ordered_source_spec import DEFAULT_VIEW, PRETRAIN_VIEW, SOURCE_SCHEMA, view_specs

WIRE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "components/connector/contracts/fixtures/native-logical-v1.json"
    ).read_bytes()
)


def original_basis(
    directory: Path, revision: int, *, empty: bool = False, terminal: bool = False
):
    observation = copy.deepcopy(WIRE["wire_samples"]["observation"])
    observation.update(snapshot_id=f"snapshot-{revision}", revision=revision)
    observation["owner_occurrence"].update(occurrence_id=f"occurrence-{revision}")
    actions = (
        []
        if empty
        else [
            {
                "action_id": f"action-{revision}",
                "kind": "native_input",
                "verb": "proceed",
                "label": "Proceed",
                "subject_referent_id": None,
                "arguments": [],
                "effect_domain": "native",
            }
        ]
    )
    observation["catalog"].update(
        snapshot_id=observation["snapshot_id"],
        total_count=len(actions),
        digest=native_catalog_digest(actions),
    )
    if terminal:
        observation["status"] = "interactive"
        observation["interaction"].update(kind="game_over", stage="summary")
        observation["interaction"]["content"] = {
            "context": {
                "kind": "game_over", "result": "loss", "game_mode": "standard",
                "score": 15, "floor_reached": 1, "ascension": 0,
            },
            "surface": {
                "kind": "game_over", "stage": "summary", "return_destination": "main_menu",
                "can_advance_summary": False, "can_return": True, "other_controls": [],
            },
        }
    refs = []
    for family, value, hash_key in (
        ("public-captures", observation, "sha256"),
        ("public-catalogs", actions, "payload_sha256"),
    ):
        raw = json_bytes(value)
        checksum = hashlib.sha256(raw).hexdigest()
        name = f"{family}/sha256/{checksum[:2]}/{checksum}.bin"
        target = directory / "raw" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        refs.append(
            {
                "epoch_id": "epoch-1",
                "snapshot_id": observation["snapshot_id"],
                "scope_id": observation["catalog"]["scope_id"],
                "stream_generation": observation["catalog"]["stream_generation"],
                "byte_count": len(raw),
                hash_key: checksum,
                "payload_ref": name,
            }
        )
    refs[0].update(capture_id=f"capture-{revision}", captured_at=observation["observed_at"])
    refs[1].update(
        catalog_ref=observation["catalog"]["catalog_ref"],
        total_count=len(actions),
        structural_digest=observation["catalog"]["digest"],
    )
    return observation, actions, *refs


def contract_bundle(directory: Path):
    bases = [original_basis(directory, n, empty=n == 4) for n in range(1, 5)]
    generation = bases[0][0]["catalog"]["stream_generation"]

    def point(cut):
        return {
            "epoch_id": "epoch-1",
            "stream_generation": generation,
            "publication_index": str(cut),
        }

    def publication(sequence, cut, basis):
        obs, _, capture, catalog = basis
        return {
            "sequence": sequence,
            "epoch_id": "epoch-1",
            "segment_id": "segment-1",
            "position": point(cut),
            "capture": capture,
            "catalog": catalog,
            "completeness": "complete",
            "snapshot_id": obs["snapshot_id"],
        }

    def input_row(sequence, ordinal, basis):
        _, actions, capture, catalog = basis
        return {
            "sequence": sequence,
            "epoch_id": "epoch-1",
            "segment_id": "segment-1",
            "input_id": f"input-{ordinal}",
            "input_prefix_ordinal": str(ordinal),
            "basis_order": {"status": "native_prefix_frozen", "reason_code": None},
            "pre_position": point(1),
            "pre_capture": capture,
            "catalog": catalog,
            "outcome": {
                "mapping_status": "exact",
                "match_count": 1,
                "selected_action": actions[0],
                "delivery": "delivered",
            },
        }

    return SimpleNamespace(
        directory=directory,
        content_id="c" * 64,
        human_origin_verified=False,
        final_input_prefix_ordinal="2",
        manifest={"timeline_id": "contract-only-timeline"},
        recording={"fixture_scope": "synthetic_contract_only_not_native_history"},
        epochs=(
            {
                "epoch_id": "epoch-1",
                "context": {
                    "game_continuity_id": "game-1",
                    "environment": {"runtime_instance_id": "runtime-1"},
                },
            },
        ),
        segments=(
            {
                "segment_id": "segment-1",
                "declaration": {
                    "source_kind": "declared_human",
                    "actor_id": "declared-actor",
                    "declaration_id": "declaration-1",
                    "machine_verifiable": False,
                },
            },
        ),
        observations=(publication(1, 1, bases[0]), publication(2, 2, bases[3])),
        # Original native ordinals are 1,2; completion/persistence happened 2,1.
        inputs=(input_row(1, 2, bases[2]), input_row(2, 1, bases[1])),
        boundaries=(
            {
                "sequence": 1,
                "kind": "close",
                "position": point(2),
                "after_input_ordinal": "2",
                "transition": None,
            },
        ),
    )


def projected(bundle, *, view=PRETRAIN_VIEW):
    raw_id = "a" * 64
    runs, report = _projection(bundle, raw_id, "declared_human", view)
    projection_spec, target_spec = view_specs(view)
    source = {
        "schema": SOURCE_SCHEMA,
        "source_kind": "declared_human",
        "input_spec": INPUT_SPEC,
        "projection_spec": projection_spec,
        "target_spec": target_spec,
        "teacher": _teacher("declared_human"),
        "raw_refs": [OrderedSourceRef(raw_id, "b" * 64).__dict__],
        "runs": runs,
    }
    return source, report, parse_ordered_training_dataset(json_bytes(source))


def test_same_watermark_uses_original_prefix_order_and_own_basis(tmp_path):
    source, report, dataset = projected(contract_bundle(tmp_path))
    steps = source["runs"][0]["steps"]
    assert [step["observation"]["revision"] for step in steps] == [1, 2, 3, 4]
    assert [step["evidence"]["stream_sequence"] for step in steps] == [1, 2, 1, 2]
    assert [step["evidence"]["input_prefix_ordinal"] for step in steps] == [None, "1", "2", None]
    assert [step.chosen_action_id for step in dataset.runs[0].steps] == [
        None,
        "action-2",
        "action-3",
        None,
    ]
    assert report["counts"] == {
        "original_publications": 2,
        "original_inputs": 2,
        "admitted_frames": 4,
        "eligible_unique_N": 2,
        "original_exact_delivered_cohort_choices": 2,
        "publication_basis_mismatched_N": 0,
        "ready_summary_task_masked_N": 0,
        "model_unexposed_frames": 0,
        "excluded_frames": 0,
        "original_game_runs": 1,
        "whole_game_recorded_capture_runs": 0,
    }
    assert dataset.runs[0].steps[-1].advance and not dataset.runs[0].steps[-1].frame.action_ids
    assert dataset.source_kind == "declared_human" and not dataset.capsules_verified


@pytest.mark.parametrize("delivery", ["unknown", "partially_delivered", "rejected_before_input"])
def test_ineligible_delivery_consumes_original_basis_without_target(tmp_path, delivery):
    bundle = contract_bundle(tmp_path)
    bundle.inputs[0]["outcome"]["delivery"] = delivery
    source, report, dataset = projected(bundle)
    assert [s["observation"]["revision"] for s in source["runs"][0]["steps"]] == [1, 2, 3, 4]
    assert report["counts"]["eligible_unique_N"] == 1
    assert dataset.runs[0].steps[2].chosen_action_id is None
    assert dataset.runs[0].steps[2].advance


def test_full_original_action_equality_required_for_N(tmp_path):
    bundle = contract_bundle(tmp_path)
    selected = copy.deepcopy(bundle.inputs[0]["outcome"]["selected_action"])
    selected["label"] = "id-only resemblance"
    bundle.inputs[0]["outcome"]["selected_action"] = selected
    with pytest.raises(BoundaryError, match="N_full_original_action_binding"):
        projected(bundle)


@pytest.mark.parametrize("view", [DEFAULT_VIEW, PRETRAIN_VIEW])
def test_ready_summary_context_is_retained_but_menu_return_has_no_N(tmp_path, view):
    bundle = contract_bundle(tmp_path)
    observation, actions, capture, catalog = original_basis(tmp_path, 1, terminal=True)
    bundle.observations[0].update(capture=capture, catalog=catalog)
    for row in bundle.inputs:
        row.update(pre_capture=capture, catalog=catalog)
        row["outcome"]["selected_action"] = actions[0]
    source, report, dataset = projected(bundle, view=view)
    assert report["counts"]["original_exact_delivered_cohort_choices"] == 2
    assert report["counts"]["ready_summary_task_masked_N"] == 2
    assert report["counts"]["eligible_unique_N"] == 0
    assert report["N_coverage"]["denominator"] == 2
    assert dataset.runs[0].steps[0].advance
    assert all(step.chosen_action_id is None for step in dataset.runs[0].steps)
    assert source["runs"][0]["steps"][0]["observation"] == observation
    assert source["runs"][0]["steps"][1]["catalog"] == actions
    source["runs"][0]["steps"][1]["chosen_action_id"] = actions[0]["action_id"]
    with pytest.raises(BoundaryError, match="N_ready_summary_task_complete_must_be_unlabelled"):
        parse_ordered_training_dataset(json_bytes(source))


def test_one_original_game_across_attachment_epochs_keeps_related_group_and_resets(tmp_path):
    bundle = contract_bundle(tmp_path)
    later_epoch = copy.deepcopy(bundle.epochs[0])
    later_epoch["epoch_id"] = "epoch-2"
    bundle.epochs += (later_epoch,)
    later_publication = copy.deepcopy(bundle.observations[0])
    later_publication.update(sequence=3, epoch_id="epoch-2")
    later_publication["position"]["epoch_id"] = "epoch-2"
    for reference in ("capture", "catalog"):
        later_publication[reference]["epoch_id"] = "epoch-2"
    bundle.observations += (later_publication,)
    source, _, dataset = projected(bundle, view=DEFAULT_VIEW)
    assert len({run.run_id for run in dataset.runs}) == 2
    assert len({run.source_group for run in dataset.runs}) == 1
    assert all(run.steps[0].reset_before and run.steps[0].advance for run in dataset.runs)
    assert source["runs"][0]["identity"]["related_keys"] == (
        source["runs"][1]["identity"]["related_keys"])


def test_pause_cut_includes_prior_input_excludes_later_same_cut(tmp_path):
    bundle = contract_bundle(tmp_path)
    bundle.boundaries = (
        {
            "sequence": 1,
            "kind": "pause",
            "position": bundle.inputs[0]["pre_position"],
            "after_input_ordinal": "1",
            "transition": None,
        },
        *bundle.boundaries,
    )
    source, report, dataset = projected(bundle)
    assert [s["observation"]["revision"] for s in source["runs"][0]["steps"]] == [1, 2]
    assert report["counts"]["excluded_frames"] == 2 and report["counts"]["eligible_unique_N"] == 1
    assert [s.reset_before for s in dataset.runs[0].steps] == [True, False]


def test_unproven_basis_excludes_continuous_suffix_no_current_backfill(tmp_path):
    bundle = contract_bundle(tmp_path)
    row = bundle.inputs[1]
    row["basis_order"] = {
        "status": "unproven",
        "reason_code": "source_prefix_capture_order_unproven",
    }
    row.update(pre_capture=None, catalog=None)
    row["outcome"].update(mapping_status="capture_missing", selected_action=None, match_count=0)
    source, report, _ = projected(bundle)
    assert [s["observation"]["revision"] for s in source["runs"][0]["steps"]] == [1]
    assert report["counts"]["eligible_unique_N"] == 0 and report["counts"]["excluded_frames"] == 3
    assert report["exclusions"][0]["original"]["pre_position"]["publication_index"] == "1"
    assert report["exclusions"][0]["reason"] == "input_basis_order_unproven"


def test_original_revision_regression_is_rejected_never_rewritten(tmp_path):
    bundle = contract_bundle(tmp_path)
    bundle.inputs = tuple(reversed(bundle.inputs))
    bundle.inputs[0]["input_prefix_ordinal"], bundle.inputs[1]["input_prefix_ordinal"] = "2", "1"
    with pytest.raises(BoundaryError, match="native_revision_regressed"):
        projected(bundle)


def test_operational_metadata_is_lineage_not_model_features(tmp_path):
    bundle = contract_bundle(tmp_path)
    _, _, dataset = projected(bundle)
    changed = copy.deepcopy(bundle)
    changed.inputs[0]["input_id"] = "input-opaque-poison"
    changed.inputs[0]["outcome"]["reason_code"] = "no-input-feature"
    _, _, other = projected(changed)
    assert [s.frame for s in dataset.runs[0].steps] == [s.frame for s in other.runs[0].steps]
    assert dataset.source_sha256 != other.source_sha256


def test_cohorts_and_synthetic_contracts_are_separate(tmp_path):
    from stpd.fullrun.native_training_sequences import parse_native_training_dataset

    source, _, _ = projected(contract_bundle(tmp_path))
    with pytest.raises(BoundaryError):
        parse_native_training_dataset(json_bytes(source))
    bundle = contract_bundle(tmp_path)
    bundle.segments[0]["declaration"]["source_kind"] = "agent_protocol"
    _, report, dataset = projected(bundle)
    assert report["counts"]["eligible_unique_N"] == 0
    assert len(dataset.runs[0].steps) == 4
    _, report = _projection(bundle, "a" * 64, "agent_protocol", PRETRAIN_VIEW)
    assert report["counts"]["eligible_unique_N"] == 2


def test_missing_shared_Source3_API_fails_closed_without_Source2_fallback(tmp_path, monkeypatch):
    from stpd.fullrun import ordered_source

    monkeypatch.setattr(ordered_source, "evidence_owner", SimpleNamespace())
    with pytest.raises(BoundaryError, match="source3_evidence_api_required"):
        ordered_source._verified(tmp_path)


def test_original_capture_bytes_are_rechecked_before_projection(tmp_path):
    bundle = contract_bundle(tmp_path)
    original = tmp_path / "raw" / bundle.inputs[0]["pre_capture"]["payload_ref"]
    original.write_bytes(original.read_bytes() + b" ")
    with pytest.raises(BoundaryError, match="original_blob_changed"):
        projected(bundle)


def test_default_masks_input_only_bases_without_advancing_publication_memory(tmp_path):
    source, report, dataset = projected(contract_bundle(tmp_path), view=DEFAULT_VIEW)
    assert [step["observation"]["revision"] for step in source["runs"][0]["steps"]] == [1, 4]
    assert [step.advance for step in dataset.runs[0].steps] == [True, True]
    assert report["counts"]["publication_basis_mismatched_N"] == 2
    assert report["counts"]["excluded_frames"] == 0
    assert report["N_coverage"] == {
        "cohort": "declared_human",
        "eligible": 0,
        "denominator": 2,
        "fraction": 0.0,
    }
    assert [row["evidence"]["input_prefix_ordinal"] for row in report["index"]] == [
        None,
        "1",
        "2",
        None,
    ]


def test_default_exact_publication_basis_scores_N_without_new_W_advance(tmp_path):
    bundle = contract_bundle(tmp_path)
    publication = bundle.observations[0]
    actions = json.loads((tmp_path / "raw" / publication["catalog"]["payload_ref"]).read_bytes())
    for row in bundle.inputs:
        row.update(
            pre_capture=copy.deepcopy(publication["capture"]),
            catalog=copy.deepcopy(publication["catalog"]),
        )
        row["outcome"]["selected_action"] = actions[0]
    _, report, dataset = projected(bundle, view=DEFAULT_VIEW)
    assert [step.advance for step in dataset.runs[0].steps] == [True, False, False, True]
    assert [step.chosen_action_id for step in dataset.runs[0].steps] == [
        None,
        "action-1",
        "action-1",
        None,
    ]
    assert report["N_coverage"]["fraction"] == 1.0
    assert report["counts"]["publication_basis_mismatched_N"] == 0
