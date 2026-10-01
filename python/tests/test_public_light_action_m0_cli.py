"""Synthetic public-snapshot M0 training and export path; no raw source or runtime."""

from __future__ import annotations

from dataclasses import replace

import pytest

from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore


@pytest.mark.parametrize(
    ("profile", "view_schema", "compact", "operation"),
    [
        ("public_lite", "stpd/public-observation-bc-view-v1", False, "b" * 32),
        ("public_compact", "stpd/public-observation-bc-view-v2", True, "c" * 32),
    ],
)
def test_public_m0_owner_train_resume_export_and_snapshot_parity(
    tmp_path,
    monkeypatch,
    profile,
    view_schema,
    compact,
    operation,
):
    import json
    import subprocess
    import sys

    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    import test_decision_store
    from platform_bundle3_fixture import bundle3
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace
    from test_public_inputs import snapshot

    monkeypatch.setattr(
        test_decision_store, "bundle3", lambda path: bundle3(path, public_bindings=True)
    )
    config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    common = ("--store", str(store_dir))

    prepared = _cli(
        monkeypatch,
        *common,
        "prepare-light-action-m0",
        "--project-config",
        str(config_path),
        "--dataset",
        dataset_id,
        "--operation",
        operation,
        "--backbone",
        "s",
        "--input-profile",
        profile,
        "--train-limit",
        "8",
        "--dev-limit",
        "4",
        "--max-state-tokens",
        "8193",
        "--max-action-bytes",
        "8193",
    )
    assert prepared["input_schema"] == "stpd/stage1a-light-action-m0-public-input-v1"
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    from stpd.fullrun.features import load_model_view
    from stpd.fullrun.light_action_inputs import load_light_action_inputs
    from stpd.policy.token_decision import LightActionM0DecisionScorer

    view = store.get_manifest(prepared["model_view_id"])
    assert view.parameters.value()["schema"] == view_schema
    _, samples = load_model_view(store, view.artifact_id)
    assert len(samples) == 6 and {sample.split for sample in samples} == {"train", "dev"}
    expected_renderer_fragment = (
        "public-snapshot-compact-v2" if compact else "public-snapshot-lite-v1"
    )
    assert all(expected_renderer_fragment in sample.state_text for sample in samples)
    inputs = load_light_action_inputs(store, prepared["training_input_id"])
    assert (
        inputs.manifest.parameters.value()["training_binding"]["allocation_id"]
        == prepared["allocation_id"]
    )

    paused = _cli(
        monkeypatch,
        *common,
        "train-light-action-m0",
        "--project-config",
        str(config_path),
        "--inputs",
        prepared["training_input_id"],
        "--operation",
        operation,
        "--recipe",
        "stage1a.dsimple.light-action.m0.s.v1",
        "--steps",
        "2",
        "--stop-after",
        "1",
        "--max-state-tokens",
        "8193",
        "--max-action-bytes",
        "8193",
    )
    assert paused["state"] == "paused" and paused["checkpoint_id"]
    completed = _cli(
        monkeypatch,
        *common,
        "run-light-action-m0",
        "--project-config",
        str(config_path),
        "--run",
        paused["run_id"],
        "--operation",
        operation,
        "--resume",
        paused["checkpoint_id"],
    )
    assert completed["state"] == "completed"
    run = store.get_manifest(paused["run_id"])
    result = store.get_manifest(completed["result_id"])
    model_id = result.parent("model")
    exported = tmp_path / "public-m0-export"
    receipt = _cli(
        monkeypatch,
        *common,
        "export-light-action-m0",
        "--project-config",
        str(config_path),
        "--operation",
        operation,
        "--model",
        model_id,
        "--destination",
        str(exported),
    )
    assert receipt["schema"] == "stpd/stage1a-light-action-m0-public-export-v1"
    model = store.get_manifest(model_id)
    assert model.parameters.value()["schema"] == "stpd/stage1a-light-action-m0-public-model-v1"
    assert model.parameters.value()["qualification"] == "engineering_only"
    assert model.parameters.value()["input_schema"] == prepared["input_schema"]
    assert model.parameters.value()["config"]["public_profile"] == profile
    assert (
        run.parameters.value()["training_binding"] == model.parameters.value()["training_binding"]
    )

    if not compact:
        # A forged completion edge must be rejected from manifests before owner or
        # worker payloads are read during export verification.
        from spireagent.artifact_contracts import Parent
        from spireagent.json_boundary import BoundaryError
        from spireagent.research_cli import _verify_m0_completion
        from stpd.workers.token_worker import _verify_completed

        forged_model = replace(
            model,
            parents=tuple(
                Parent("checkpoint", paused["checkpoint_id"])
                if parent.role == "checkpoint" else parent
                for parent in model.parents
            ),
        )
        store.publish(forged_model)
        completed_manifest = store.get_manifest(completed["result_id"])
        evaluation = store.get_manifest(completed_manifest.parent("offline_evaluation"))
        forged_evaluation = replace(
            evaluation,
            parents=tuple(
                Parent("model", forged_model.artifact_id)
                if parent.role == "model" else parent
                for parent in evaluation.parents
            ),
        )
        store.publish(forged_evaluation)
        forged_result = replace(
            completed_manifest,
            parents=tuple(
                (Parent("model", forged_model.artifact_id)
                 if parent.role == "model" else
                 Parent("offline_evaluation", forged_evaluation.artifact_id)
                 if parent.role == "offline_evaluation" else parent)
                for parent in completed_manifest.parents
            ),
        )
        store.publish(forged_result)
        payload_reads = []
        original_read_payload = ManifestArtifactStore.read_payload

        def record_payload_read(self, payload):
            payload_reads.append(payload.role)
            yield from original_read_payload(self, payload)

        monkeypatch.setattr(ManifestArtifactStore, "read_payload", record_payload_read)
        with pytest.raises(BoundaryError, match="completed_model_required"):
            _verify_m0_completion(store, forged_model)
        assert payload_reads == []
        with pytest.raises(BoundaryError, match="light_action_model_identity_mismatch"):
            _verify_completed(store, forged_result, run)
        assert payload_reads == []
        monkeypatch.setattr(ManifestArtifactStore, "read_payload", original_read_payload)

    observation = snapshot()
    scorer = LightActionM0DecisionScorer(exported)
    scores = scorer.score_snapshot(observation)
    from stpd.fullrun.public_inputs import project_public_snapshot

    current = project_public_snapshot(observation, compact=compact)
    assert tuple(scores) == tuple(action.key for action in current.actions)
    assert tuple(scores.values()) == scorer.score_texts(current.state_text, current.action_texts)
    observation["bound_actions"]["actions"].reverse()
    assert scorer.score_snapshot(observation) == scores
    observation_path = tmp_path / "public-snapshot.json"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sys
from pathlib import Path
from stpd.policy.token_decision import LightActionM0DecisionScorer
scorer = LightActionM0DecisionScorer(Path(sys.argv[1]))
snapshot = json.loads(Path(sys.argv[2]).read_bytes())
print(json.dumps(scorer.score_snapshot(snapshot), sort_keys=True))
""",
            str(exported),
            str(observation_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(child.stdout) == scores


def test_public_capacity_is_profile_scoped_and_serialized():
    from spireagent.json_boundary import BoundaryError
    from stpd.models.stage1a import build_scorer
    from stpd.models.token_core import ScratchShape, ScratchTokenCore
    from stpd.workers.token_ranking import LightActionM0Config, config_payload
    torch = pytest.importorskip("torch")

    public = LightActionM0Config(public_profile="public_lite", max_action_bytes=9000)
    assert public.max_action_bytes == 9000
    assert config_payload(public)["public_profile"] == "public_lite"
    assert LightActionM0Config.decode(config_payload(public)) == public
    with pytest.raises(BoundaryError, match="invalid_independent_length_limits"):
        LightActionM0Config(max_action_bytes=9000)
    with pytest.raises(BoundaryError, match="invalid_public_m0_profile"):
        LightActionM0Config(public_profile="truthy")

    recipe = "stage1a.dsimple.light-action.m0.s.v1"
    shape = ScratchShape(256, 384, 2, 6, 1536, 0.1, 8192)

    def scorer(*, max_action_bytes, public_profile=None):
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(101)
            return build_scorer(
                recipe, ScratchTokenCore(shape), max_action_bytes=max_action_bytes,
                scoring_seed=17, public_profile=public_profile,
            )

    with pytest.raises(ValueError, match="invalid action byte limit"):
        scorer(max_action_bytes=8193)
    public_large = scorer(max_action_bytes=8193, public_profile="public_lite")
    public_default = scorer(max_action_bytes=8192, public_profile="public_lite")
    canonical_default = scorer(max_action_bytes=8192)
    assert set(public_large.state_dict()) == set(public_default.state_dict())
    assert all(torch.equal(value, public_default.state_dict()[name])
               for name, value in public_large.state_dict().items())
    assert all(torch.equal(value, canonical_default.state_dict()[name])
               for name, value in public_default.state_dict().items())
    with pytest.raises(ValueError, match="invalid public profile"):
        scorer(max_action_bytes=8193, public_profile="truthy")


def test_public_input_family_forgery_rejects_before_payload_reads(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    import test_decision_store
    from platform_bundle3_fixture import bundle3
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    monkeypatch.setattr(
        test_decision_store, "bundle3", lambda path: bundle3(path, public_bindings=True)
    )
    config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    prepared = _cli(
        monkeypatch,
        "--store",
        str(store_dir),
        "prepare-light-action-m0",
        "--project-config",
        str(config_path),
        "--dataset",
        dataset_id,
        "--operation",
        "a" * 32,
        "--backbone",
        "s",
        "--input-profile",
        "public_lite",
        "--train-limit",
        "8",
        "--dev-limit",
        "4",
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    from spireagent.json_boundary import BoundaryError, FrozenObject
    from stpd.fullrun.light_action_inputs import CANONICAL_SCHEMA, load_light_action_inputs

    original = store.get_manifest(prepared["training_input_id"])
    forged_info = {**original.parameters.value(), "schema": CANONICAL_SCHEMA}
    forged = replace(original, parameters=FrozenObject.of(forged_info))
    store.publish(forged)
    payload_reads = []
    original_read_payload = store.read_payload

    def record_read(payload):
        payload_reads.append(payload.role)
        yield from original_read_payload(payload)

    monkeypatch.setattr(ManifestArtifactStore, "read_payload", record_read)
    with pytest.raises(BoundaryError, match="input_family_source_mismatch"):
        load_light_action_inputs(store, forged.artifact_id)
    assert payload_reads == []


def test_public_manifest_and_owner_preflight_reject_before_payload_reads(tmp_path, monkeypatch):
    import io

    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    import test_decision_store
    from platform_bundle3_fixture import bundle3
    from test_artifact_store_v1 import PRODUCER
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    monkeypatch.setattr(
        test_decision_store, "bundle3", lambda path: bundle3(path, public_bindings=True)
    )
    config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    operation = "f" * 32
    prepared = _cli(
        monkeypatch,
        "--store", str(store_dir), "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s", "--input-profile", "public_lite",
        "--train-limit", "8", "--dev-limit", "4",
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.json_boundary import BoundaryError, FrozenObject
    from spireagent.research_cli import _admit_m0_input, _admit_m0_run
    from stpd.fullrun.light_action_inputs import (
        public_training_binding,
        publish_light_action_inputs,
    )

    original = store.get_manifest(prepared["training_input_id"])
    original_info = original.parameters.value()
    view = store.get_manifest(prepared["model_view_id"])
    allocation = store.get_manifest(prepared["allocation_id"])

    def republish_input(new_view=None, changes=None):
        source = view if new_view is None else new_view
        info = {**original_info, **(changes or {})}
        binding = dict(original_info["training_binding"])
        binding["model_view_id"] = source.artifact_id
        binding["allocation_id"] = source.parent("allocation")
        info["training_binding"] = binding
        item = replace(
            original,
            parents=(Parent("model_view", source.artifact_id),),
            parameters=FrozenObject.of(info),
        )
        store.publish(item)
        return item

    bad_format = republish_input(changes={
        "format": "stpd-token-light-action-m0-canonical-v1",
        "source_schema": "stpd/decision-model-view-v1",
    })
    bad_renderer = replace(
        view,
        parameters=FrozenObject.of({
            **view.parameters.value(),
            "serializer": {"schema": "stpd/fullrun-serializer-v1", "profile": "standard"},
        }),
    )
    store.publish(bad_renderer)
    bad_renderer_input = republish_input(bad_renderer)
    bad_allocation = replace(
        allocation,
        parameters=FrozenObject.of({
            **allocation.parameters.value(), "schema": "stpd/unrelated-allocation-v1",
        }),
    )
    store.publish(bad_allocation)
    bad_allocation_view = replace(
        view,
        parents=tuple(Parent(parent.role, bad_allocation.artifact_id)
                      if parent.role == "allocation" else parent for parent in view.parents),
        parameters=FrozenObject.of({
            **view.parameters.value(), "allocation_id": bad_allocation.artifact_id,
        }),
    )
    store.publish(bad_allocation_view)
    bad_allocation_input = republish_input(bad_allocation_view)

    reads = []
    original_read_payload = ManifestArtifactStore.read_payload

    def record_read(self, payload):
        reads.append(payload.role)
        yield from original_read_payload(self, payload)

    monkeypatch.setattr(ManifestArtifactStore, "read_payload", record_read)
    for invalid in (bad_format, bad_renderer_input, bad_allocation_input):
        reads.clear()
        with pytest.raises(BoundaryError):
            public_training_binding(store, invalid.artifact_id)
        assert reads == []
        with pytest.raises(BoundaryError):
            _admit_m0_input(owner, store, invalid.artifact_id, operation)
        assert reads == []

    reads.clear()
    with pytest.raises(BoundaryError, match="training_binding_required"):
        publish_light_action_inputs(store, view.artifact_id, "s", PRODUCER)
    assert reads == []

    # A run's immutable operation mismatch must be rejected before owner archive access.
    from stpd.workers.token_ranking import LightActionM0Config
    from stpd.workers.token_worker import prepare_token_run

    config = LightActionM0Config(
        recipe="stage1a.dsimple.light-action.m0.s.v1", steps=2,
        max_state_tokens=8192, max_action_bytes=8192, public_profile="public_lite",
    )
    from stpd.fullrun.light_action_inputs import load_light_action_inputs

    inputs = load_light_action_inputs(store, original.artifact_id)
    run = prepare_token_run(store, inputs, config, PRODUCER)
    run_info = run.parameters.value()
    bad_binding = {**run_info["training_binding"], "training_operation_id": "0" * 32}
    forged_run = replace(
        run,
        parameters=FrozenObject.of({**run_info, "training_binding": bad_binding}),
    )
    store.publish(forged_run)
    reads.clear()
    with pytest.raises(BoundaryError, match="training_binding_mismatch"):
        _admit_m0_run(owner, store, forged_run.artifact_id, operation)
    assert reads == []

    # The shared CLI/worker preflight rejects a checkpoint bound to another run
    # before loading source rows or checkpoint bytes.
    from stpd.workers.token_worker import preflight_token_run

    checkpoint_payload = store.put_payload(
        "checkpoint", io.BytesIO(b"synthetic checkpoint"), "application/octet-stream",
    )
    checkpoint = Manifest(
        "checkpoint", PRODUCER,
        (Parent("run", run.artifact_id), Parent("training_input", original.artifact_id)),
        (checkpoint_payload,),
        FrozenObject.of({
            "schema": "stpd/stage1a-token-light-action-m0-checkpoint-v1", "step": 1,
        }),
    )
    store.publish(checkpoint)
    another_run = prepare_token_run(store, inputs, config, PRODUCER, replicate="other")
    reads.clear()
    with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
        preflight_token_run(store, another_run.artifact_id, PRODUCER,
                            resume=checkpoint.artifact_id)
    assert reads == []


def test_public_m0_excludes_native_only_row_without_losing_allocation_disposition(
    tmp_path, monkeypatch,
):
    import json

    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    import test_decision_store
    from platform_bundle3_fixture import bundle3
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    monkeypatch.setattr(
        test_decision_store, "bundle3", lambda path: bundle3(path, public_bindings=True)
    )
    config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    import stpd.fullrun.public_bc as public_bc
    from spireagent.json_boundary import BoundaryError

    project_choice = public_bc.bound_choice
    native_only_snapshot: set[str] = set()

    def project_with_native_only(frame, reference, action, *, compact=False):
        snapshot_id = frame["snapshot"]["snapshot_id"]
        if not native_only_snapshot:
            native_only_snapshot.add(snapshot_id)
        if snapshot_id in native_only_snapshot:
            raise BoundaryError("public_bc", "human_observation_missing")
        return project_choice(frame, reference, action, compact=compact)

    monkeypatch.setattr(public_bc, "bound_choice", project_with_native_only)
    receipt = _cli(
        monkeypatch,
        "--store",
        str(store_dir),
        "prepare-light-action-m0",
        "--project-config",
        str(config_path),
        "--dataset",
        dataset_id,
        "--operation",
        "e" * 32,
        "--backbone",
        "s",
        "--input-profile",
        "public_lite",
        "--train-limit",
        "8",
        "--dev-limit",
        "4",
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    view = store.get_manifest(receipt["model_view_id"])
    report = json.loads(b"".join(store.read_payload(view.payload("dispositions"))))
    from stpd.fullrun.decision_training import load_allocation

    allocation_manifest, _, allocation = load_allocation(store, receipt["allocation_id"])
    allocated_occurrences = {member["occurrence"] for member in allocation["members"]}
    dispositions = report["rows"]
    assert len(dispositions) == len(allocated_occurrences) == 6
    assert {row["occurrence"] for row in dispositions} == allocated_occurrences
    excluded = [row for row in dispositions if row["status"] == "excluded"]
    assert len(excluded) == 1
    assert excluded[0]["reason"] == "human_observation_missing"
    assert allocation_manifest.artifact_id == receipt["allocation_id"]
    assert report["counts"] == {"included": 5, "excluded": 1}
