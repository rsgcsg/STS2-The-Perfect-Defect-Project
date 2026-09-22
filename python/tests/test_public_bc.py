"""Synthetic public-H BC admission and offline/standalone parity, not Human evidence."""
import copy
import io
import json
from dataclasses import replace

import pytest
import torch
from platform_bundle3_fixture import bundle3
from test_decision_training import prepared
from test_public_inputs import snapshot
from test_stage1a_training import tiny_config

from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation
from stpd.fullrun.features import load_model_view
from stpd.fullrun.public_bc import bound_choice, project_allocation, publish_public_bc_view
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
from stpd.policy.token_decision import TokenDecisionScorer, export_token_model
from stpd.workers.token_worker import execute_tokens, prepare_token_run


def binding():
    observation = snapshot()
    selected = observation["bound_actions"]["actions"][0]
    action = {"human_observation_snapshot_id": observation["snapshot_id"],
              "mapping": {"status": "exact_unique", "match_count": 1,
                          "basis": "reference_equality_to_frozen_host_binding"},
              "bound_action": {**selected, "arguments": {}}}
    frame = {"snapshot_id": observation["snapshot_id"], "snapshot": observation}
    return frame, {"snapshot_id": frame["snapshot_id"]}, action


def test_exact_human_choice_uses_ids_not_semantic_guess_or_candidate_position():
    frame, ref, action = binding()
    expected, index = bound_choice(frame, ref, action)
    assert index == 0
    # Equal-looking legal actions still have distinct, owner-bound occurrence IDs.
    duplicate = copy.deepcopy(frame["snapshot"]["bound_actions"]["actions"][0])
    duplicate["bound_action_id"] = "another-card-instance"
    frame["snapshot"]["bound_actions"]["actions"][1] = duplicate
    frame["snapshot"]["bound_actions"]["actions"].reverse()
    public, index = bound_choice(frame, ref, action)
    assert index == 1 and public.action_texts[0] == public.action_texts[1]
    assert public.state_text == expected.state_text


@pytest.mark.parametrize("change,code", [
    (lambda f, r, a: a.update(human_observation_snapshot_id="stale"), "observation_binding"),
    (lambda f, r, a: a.pop("bound_action"), "public_binding"),
    (lambda f, r, a: a.update(native_input={}), "public_binding"),
    (lambda f, r, a: a["mapping"].update(match_count=2), "owner_mapping"),
    (lambda f, r, a: a["mapping"].update(basis="nearest_snapshot"), "owner_mapping"),
    (lambda f, r, a: a["bound_action"].update(verb="native_play"), "choice_not_unique"),
    (lambda f, r, a: a["bound_action"].update(arguments={"target": "wrong"}), "choice_not_unique"),
    (lambda f, r, a: f["snapshot"].update(status="settling"), "interactive_complete"),
])
def test_missing_or_changed_binding_cannot_supply_a_label(change, code):
    frame, ref, action = binding()
    change(frame, ref, action)
    with pytest.raises(BoundaryError, match=code):
        bound_choice(frame, ref, action)


def test_unavailable_human_observation_is_reported_without_substituting_execution(tmp_path):
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(max_train=2, max_dev=1),
                                    owner.producer)
    before = owner.store.manifest_ids()
    _, _, samples, report = project_allocation(owner.store, allocation.artifact_id)
    assert not samples
    assert report["counts"] == {"excluded": 3}
    assert report["exclusions"] == {"human_observation_missing": 3}
    with pytest.raises(BoundaryError, match="nonempty_train_dev"):
        publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
    assert owner.store.manifest_ids() == before


def test_public_view_reprojects_sources_and_scores_same_text_after_export(tmp_path, monkeypatch):
    import test_decision_store

    monkeypatch.setattr(test_decision_store, "bundle3", lambda p: bundle3(p, public_bindings=True))
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(max_train=2, max_dev=1),
                                    owner.producer)
    view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
    _, samples = load_model_view(owner.store, view.artifact_id)
    assert len(samples) == 3 and {s.split for s in samples} == {"train", "dev"}
    assert all("public-snapshot-compact-v2" in s.state_text for s in samples)
    report = json.loads(b"".join(owner.store.read_payload(view.payload("dispositions"))))
    assert report["counts"] == {"included": 3}
    assert len(report["rows"]) == 3 and report["successor_supervision"] is False
    altered = replace(view, parameters=FrozenObject.of({**view.parameters.value(), "samples": 4}))
    owner.store.publish(altered)
    with pytest.raises(BoundaryError, match="identity_mismatch"):
        load_model_view(owner.store, altered.artifact_id)
    # A signed-looking but changed label still must reproduce the original witness.
    values = [s.to_dict() for s in samples]
    values[0]["chosen_index"] = 10
    forged = owner.store.put_payload("samples", io.BytesIO(b"".join(map(json_bytes, values))),
                                      "application/x-ndjson")
    altered = replace(view, payloads=(forged, view.payload("dispositions")))
    owner.store.publish(altered)
    with pytest.raises(BoundaryError, match="source_projection"):
        load_model_view(owner.store, altered.artifact_id)
    token_manifest = publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer)
    inputs = load_token_inputs(owner.store, token_manifest.artifact_id)
    run = prepare_token_run(owner.store, inputs, tiny_config(), owner.producer)
    result = execute_tokens(owner.store, ObjectStoreRunReporter(owner.store, owner.store.blobs),
                            run.artifact_id, owner.producer)
    model = owner.store.get_manifest(result.result_id).parent("model")
    destination = tmp_path / "export"
    export_token_model(owner.store, model, destination)
    scorer = TokenDecisionScorer(destination)
    # Arbitrary new public input uses precisely the offline projection entry point.
    observation = snapshot()
    public = project_public_snapshot(observation, compact=True)
    expected = scorer.score_texts(public.state_text, public.action_texts)
    original_scores = scorer.score_snapshot(observation)
    assert tuple(original_scores.values()) == expected
    original_keys = tuple(original_scores)
    assert len(original_keys) == len(set(original_keys)) == len(public.actions)

    def assert_reordered_scores(expected_by_key, actual_by_key):
        expected_keys = tuple(expected_by_key)
        actual_keys = tuple(actual_by_key)
        assert len(actual_keys) == len(expected_keys)
        assert set(actual_keys) == set(expected_keys)
        assert actual_keys == expected_keys[::-1]
        expected_fp32 = torch.tensor(
            [expected_by_key[key] for key in expected_keys], dtype=torch.float32,
        )
        actual_fp32 = torch.tensor(
            [actual_by_key[key] for key in expected_keys], dtype=torch.float32,
        )
        assert bool(torch.isfinite(expected_fp32).all())
        assert bool(torch.isfinite(actual_fp32).all())
        torch.testing.assert_close(actual_fp32, expected_fp32, rtol=1.3e-6, atol=1e-5)

    observation["bound_actions"]["actions"].reverse()
    reordered_scores = scorer.score_snapshot(observation)
    assert_reordered_scores(original_scores, reordered_scores)
    # A distinct, deliberately cross-bound score must fail the same key-aware check.
    with pytest.raises(AssertionError):
        assert_reordered_scores(
            {"key-a": 0.0, "key-b": 1.0},
            {"key-b": 0.0, "key-a": 1.0},
        )
    with pytest.raises(AssertionError):
        assert_reordered_scores(
            {"key-a": 0.0, "key-b": 1.0},
            {"key-b": float("nan"), "key-a": 0.0},
        )
    with pytest.raises(BoundaryError, match="requires_snapshot"):
        scorer.score(None, ())
