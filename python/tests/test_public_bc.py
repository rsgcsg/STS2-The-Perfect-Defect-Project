"""Synthetic public-H BC admission and offline/standalone parity, not Human evidence."""
import copy
import io
import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from platform_bundle3_fixture import bundle3
from test_decision_training import prepared
from test_public_inputs import snapshot
from test_stage1a_training import tiny_config

from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.blobs import StoreError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation
from stpd.fullrun.features import load_model_view
from stpd.fullrun.light_action_inputs import (
    TRAINING_BINDING_SCHEMA,
    load_light_action_inputs,
    publish_light_action_inputs,
)
from stpd.fullrun.public_bc import bound_choice, project_allocation, publish_public_bc_view
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
from stpd.fullrun.view_session import verified_model_views
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


def _public_allocation(tmp_path, monkeypatch):
    import test_decision_store

    monkeypatch.setattr(test_decision_store, "bundle3", lambda p: bundle3(p, public_bindings=True))
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(
        owner.store, dataset, AllocationSpec(max_train=2, max_dev=1), owner.producer,
    )
    return owner, allocation


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


def test_publication_and_input_load_share_one_verified_projection_in_same_session(
    tmp_path, monkeypatch,
):
    from stpd.fullrun import public_bc

    owner, allocation = _public_allocation(tmp_path, monkeypatch)
    original = public_bc.project_allocation
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(public_bc, "project_allocation", counted)
    with verified_model_views(owner.store) as session:
        view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
        assert calls == 1
        binding = {
            "schema": TRAINING_BINDING_SCHEMA,
            "dataset_ids": [allocation.parent("dataset")],
            "training_operation_id": "a" * 32,
            "allocation_id": allocation.artifact_id,
            "model_view_id": view.artifact_id,
        }
        inputs = publish_light_action_inputs(
            owner.store, view.artifact_id, "s", owner.producer,
            training_binding=binding,
        )
        loaded = load_light_action_inputs(owner.store, inputs.artifact_id)
        assert len(loaded.samples) == 3
        assert calls == 1
        assert (session.misses, session.hits) == (1, 2)


@pytest.mark.parametrize("session_store", ["none", "different_object_same_blobs"])
def test_publication_does_not_seed_without_the_exact_active_store_session(
    tmp_path, monkeypatch, session_store,
):
    from stpd.fullrun import public_bc

    owner, allocation = _public_allocation(tmp_path, monkeypatch)
    original = public_bc.project_allocation
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(public_bc, "project_allocation", counted)
    if session_store == "none":
        context = nullcontext(None)
    else:
        other_store = ManifestArtifactStore(owner.store.blobs)
        context = verified_model_views(other_store)
    with context as session:
        view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
        loaded = load_model_view(owner.store, view.artifact_id)
        assert loaded[0] == view and len(loaded[1]) == 3
        assert calls == 2
    if session is not None:
        assert session.identity is None and session.value is None
        assert (session.misses, session.hits) == (0, 0)


@pytest.mark.parametrize("damage", ["view_payload", "source_archive", "missing_allocation"])
def test_published_view_hit_still_checks_its_complete_artifact_closure(
    tmp_path, monkeypatch, damage,
):
    owner, allocation = _public_allocation(tmp_path, monkeypatch)
    with verified_model_views(owner.store) as session:
        view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
        load_model_view(owner.store, view.artifact_id)
        assert session.hits == 1

        if damage == "missing_allocation":
            manifest_path = (Path(owner.store.blobs.root) / "manifests"
                             / f"{allocation.artifact_id}.json")
            manifest_path.unlink()
            expected_error = "object_not_found"
        else:
            if damage == "view_payload":
                payload = view.payload("samples")
            else:
                pending = [view]
                source = None
                while pending:
                    item = pending.pop()
                    if item.kind == "evidence":
                        source = item
                        break
                    pending.extend(owner.store.get_manifest(p.artifact_id) for p in item.parents)
                assert source is not None
                payload = source.payload("archive")
            index = json.loads(
                owner.store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json")
            )
            chunk = index["chunks"][0]["sha256"]
            object_path = Path(owner.store.blobs.root) / "objects" / "sha256" / chunk
            object_path.write_bytes(b"corrupt")
            expected_error = "integrity"

        with pytest.raises(StoreError, match=expected_error):
            load_model_view(owner.store, view.artifact_id)
        assert session.hits == 1


def test_published_view_cache_is_exact_identity_keyed_and_rejects_changed_identity(
    tmp_path, monkeypatch,
):
    owner, allocation = _public_allocation(tmp_path, monkeypatch)
    with verified_model_views(owner.store) as session:
        view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
        altered = replace(
            view,
            parameters=FrozenObject.of({**view.parameters.value(), "samples": 4}),
        )
        owner.store.publish(altered)
        with pytest.raises(BoundaryError, match="view_identity_mismatch"):
            load_model_view(owner.store, altered.artifact_id)
        assert session.identity == view.artifact_id
        assert session.value is not None and session.value[0] == view


def test_publication_cache_respects_nested_session_restore(tmp_path, monkeypatch):
    from stpd.fullrun import public_bc
    from stpd.fullrun.view_session import _CURRENT

    owner, allocation = _public_allocation(tmp_path, monkeypatch)
    original = public_bc.project_allocation
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(public_bc, "project_allocation", counted)
    with verified_model_views(owner.store) as outer:
        outer_view = publish_public_bc_view(owner.store, allocation.artifact_id, owner.producer)
        assert outer.value is not None
        outer_value = outer.value
        with verified_model_views(owner.store) as inner:
            inner_view = publish_public_bc_view(
                owner.store, allocation.artifact_id, owner.producer, compact=False,
            )
            assert inner.identity == inner_view.artifact_id
            assert load_model_view(owner.store, inner_view.artifact_id)[0] == inner_view
            assert (inner.misses, inner.hits) == (1, 1)
        assert _CURRENT.get() is outer
        assert outer.identity == outer_view.artifact_id
        assert load_model_view(owner.store, outer_view.artifact_id) is outer_value
        assert (outer.misses, outer.hits) == (1, 1)
    assert calls == 2


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
