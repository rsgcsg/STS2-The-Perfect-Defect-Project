"""Historical d6 metadata and common application seams; no training or game."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER, store
from test_public_m0_policy_port import capabilities

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import PUBLIC_M0_PROFILE, LocalModelService
from spireagent.workbench.public_m0_export_support import lineage, package_receipt
from stpd.light_action_codec import SPEC_BYTES, SPEC_SHA256
from stpd.policy.token_decision import (
    check_light_action_m0_model,
    decode_public_m0_config,
    light_action_m0_runtime_config,
)

FIXTURE = Path(__file__).parent / "fixtures/public_m0_d6_export_metadata.json"


def historical_model() -> Manifest:
    value = json.loads(FIXTURE.read_bytes())
    return Manifest.from_bytes(json_bytes(value["model"]), value["model_id"])


def test_actual_d6_metadata_is_accepted_without_rewriting_training_identity():
    original = historical_model()
    before = original.to_bytes()
    config, info = check_light_action_m0_model(original)
    assert (
        original.artifact_id == "d6f752394f8b9a07a80c57f789accd283ae7712cd573349a87c449deebbf2250"
    )
    assert info["config"]["device"] == "cuda" and info["steps"] == 76450
    assert config.device == "cpu"
    assert light_action_m0_runtime_config(config, "mps").device == "mps"
    assert original.to_bytes() == before
    assert info["source_renderer"] == {
        "profile": "public_compact",
        "status": "provisional",
        "version": "stpd-public-snapshot-compact-v2",
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda info: info["config"].update(learning_rate=0),
        lambda info: info["config"].update(device="automatic"),
        lambda info: info["config"].update(public_profile="native-logical-v1"),
        lambda info: info["config"].update(extra=True),
        lambda info: info["training_binding"].update(model_view_id="b" * 64),
        lambda info: info["training_binding"].update(dataset_ids=["invalid"]),
        lambda info: info["source_renderer"].update(version="other"),
        lambda info: info.update(weights_sha256="b" * 64),
        lambda info: info.update(
            input_schema="sts2.player-environment/native-logical-observation-1"
        ),
    ],
)
def test_public_model_metadata_rejects_crossed_or_malformed_identity(change):
    original = historical_model()
    info = original.parameters.value()
    change(info)
    malformed = replace(original, parameters=FrozenObject.of(info))
    with pytest.raises(BoundaryError):
        check_light_action_m0_model(malformed)


def synthetic_completion(tmp_path):
    """Real immutable manifest graph with tiny bytes; never an inference fixture."""
    archive = store(tmp_path / "store")
    template = historical_model().parameters.value()

    def publish(kind, info, parents=(), payloads=()):
        item = Manifest(
            kind,
            PRODUCER,
            tuple(Parent(role, parent.artifact_id) for role, parent in parents),
            tuple(payloads),
            FrozenObject.of(info),
        )
        archive.publish(item)
        return archive.get_manifest(item.artifact_id)

    dataset = publish("dataset", {"schema": "test/dataset"})
    allocation = publish(
        "protocol", {"schema": "stpd/decision-allocation-v1"}, [("dataset", dataset)]
    )
    view = publish(
        "model_view",
        {"schema": template["source_view_schema"], "serializer": template["source_renderer"]},
        [("allocation", allocation), ("dataset", dataset)],
    )
    binding = {
        **template["training_binding"],
        "dataset_ids": [dataset.artifact_id],
        "allocation_id": allocation.artifact_id,
        "model_view_id": view.artifact_id,
    }
    payloads = tuple(
        archive.put_payload(role, io.BytesIO(raw))
        for role, raw in (
            ("state_tokenizer", b"synthetic tokenizer"),
            ("action_codec", SPEC_BYTES),
            ("weights", b"synthetic weights"),
        )
    )
    tokenizer, codec, weights = payloads
    template["state_codec"]["sha256"] = tokenizer.sha256
    template.update(
        training_binding=binding,
        weights_sha256=weights.sha256,
        state_tokenizer_sha256=tokenizer.sha256,
        action_codec_sha256=SPEC_SHA256,
    )
    source_info = {
        "schema": template["input_schema"],
        "format": template["input_format"],
        "source_schema": template["source_view_schema"],
        "source_renderer": template["source_renderer"],
        "training_binding": binding,
        "state_codec": template["state_codec"],
        "action_codec": template["action_codec"],
    }
    source = publish(
        "training_input",
        source_info,
        [("model_view", view)],
        [tokenizer, codec, archive.put_payload("rows", io.BytesIO(b"rows"))],
    )
    run_info = {
        "schema": "stpd/stage1a-run-v1",
        "config": template["config"],
        "training_binding": binding,
    }
    experiment = publish(
        "experiment", {**run_info, "schema": "stpd/experiment-v1"}, [("training_input", source)]
    )
    run = publish("run", run_info, [("experiment", experiment), ("training_input", source)])
    checkpoint_info = {
        key: template.get(key)
        for key in (
            "recipe",
            "graph",
            "action_codec",
            "backbone",
            "adapter_config",
            "adapter_tensor_names",
            "input_schema",
            "input_format",
            "source_view_schema",
            "source_renderer",
            "training_binding",
        )
    }
    checkpoint_info.update(schema="stpd/stage1a-light-action-m0-checkpoint-v1", step=76450)
    checkpoint = publish("checkpoint", checkpoint_info, [("run", run), ("training_input", source)])
    model = publish(
        "model",
        template,
        [
            ("run", run),
            ("training_input", source),
            ("model_view", view),
            ("checkpoint", checkpoint),
        ],
        payloads,
    )
    expected_eval = {
        "evaluation_scope": "within_training_purpose_allocation",
        "historical_external_exposure": "unknown",
        "physical_game_independence": "unresolved",
        "clean_held_out_claim": False,
    }
    metrics = {
        "admission": expected_eval,
        "summary": {"bootstrap": {"status": "unknown", "unit": "session_scoped_run_group"}},
        "baselines": {},
    }
    report = publish(
        "offline_evaluation",
        {**expected_eval, "schema": "stpd/stage1a-ranking-evaluation-v1"},
        [("model", model)],
        [archive.put_payload("metrics", io.BytesIO(json_bytes(metrics)))],
    )
    result = publish(
        "run_result",
        {"schema": "stpd/run-result-v1", "state": "completed", "steps": 76450},
        [
            ("model", model),
            ("offline_evaluation", report),
            ("run", run),
            ("training_input", source),
        ],
    )
    ObjectStoreRunReporter(archive, archive.blobs).complete(result)
    calls = []
    owner = SimpleNamespace(verified_training_receipt=lambda *args: calls.append(args))
    return archive, model, owner, calls


def test_lineage_checks_completion_and_current_use_separately(tmp_path):
    archive, model, owner, calls = synthetic_completion(tmp_path)
    checked = lineage(archive, owner, model, verify_payloads=True)
    assert checked["run_id"] == model.parent("run")
    assert checked["checkpoint_id"] == model.parent("checkpoint")
    assert calls == [
        (
            archive,
            tuple(model.parameters.value()["training_binding"]["dataset_ids"]),
            model.parameters.value()["training_binding"]["training_operation_id"],
        )
    ]


@pytest.mark.parametrize("edge", ["checkpoint", "training_input", "run", "model_view"])
def test_lineage_rejects_cross_model_parent_before_current_use(tmp_path, edge):
    archive, model, owner, calls = synthetic_completion(tmp_path)
    unrelated = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({"unrelated": edge}))
    archive.publish(unrelated)
    crossed = replace(
        model,
        parents=tuple(
            Parent(p.role, unrelated.artifact_id) if p.role == edge else p for p in model.parents
        ),
    )
    with pytest.raises(BoundaryError, match="public_m0_lineage_invalid"):
        lineage(archive, owner, crossed)
    assert calls == []


def test_common_export_register_rebind_and_budget_route(tmp_path, monkeypatch):
    archive, model, owner, _ = synthetic_completion(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination())
    exported = LocalModelExport(config)
    monkeypatch.setattr(exported, "_source", lambda _: archive)
    monkeypatch.setattr(exported, "_memory_owner", lambda _: owner)
    monkeypatch.setattr(exported, "_workspace", lambda: SimpleNamespace(store=archive))
    # This test owns bytes/lineage/application routing; actual d6 inference is
    # separately compared with the unchanged historical scorer.
    inference_loads = []
    monkeypatch.setattr(
        "stpd.policy.token_decision.LightActionM0DecisionScorer",
        lambda *args, **kwargs: inference_loads.append(kwargs) or SimpleNamespace(artifact=model),
    )
    assert exported.support(model.artifact_id)["runtime_profile"] == PUBLIC_M0_PROFILE
    exported.start(model.artifact_id)
    exported.thread.join(timeout=10)
    assert exported.status()["operation"]["status"] == "completed"
    assert inference_loads == [{"runtime_device": "cpu"}]
    previous = exported.status()["operation"]
    assert exported.start(model.artifact_id)["operation"] == previous
    assert inference_loads == [{"runtime_device": "cpu"}]
    destination = exported.verified_public_m0_for_registration(model.artifact_id)
    assert inference_loads == [{"runtime_device": "cpu"}]  # byte proof rebind, no second load
    service = LocalModelService(config)
    registration = LocalModelRegistration(config, exported, service)
    monkeypatch.setattr(registration, "_native_runtime", lambda **kwargs: (state, state / "sdk.js"))
    monkeypatch.setattr(registration, "_capabilities", lambda *args, **kwargs: capabilities())
    monkeypatch.setattr(service, "_public_manifest_contract", lambda *args, **kwargs: None)
    registered = registration.register(model.artifact_id)
    assert registered["status"] == "registered" and registered["loaded"] is False
    assert registered["runtime_profile"] == PUBLIC_M0_PROFILE
    assert registration.register(model.artifact_id) == registered
    assert registration.status(model.artifact_id) == registered
    assert service._run_profile(registered["selection_id"], "short") is True
    assert service._run_profile(registered["selection_id"], "extended") is True
    assert service.adapter_arguments(service.selection(registered["selection_id"]))[1] == (
        "stpd.policy.public_m0_port"
    )
    before = (service.private_root / "token-policies-v1.json").read_bytes()
    receipt = package_receipt(model, destination, lineage(archive, owner, model))
    assert receipt["model_id"] == model.artifact_id
    (destination / "weights.safetensors").write_bytes(b"cross-model bytes")
    with pytest.raises(BoundaryError):
        registration.register(model.artifact_id)
    assert (service.private_root / "token-policies-v1.json").read_bytes() == before


@pytest.mark.parametrize("device", ["cuda", "", "auto", None, ["cpu"]])
def test_runtime_device_rejects_automatic_fallback(device):
    config = decode_public_m0_config(historical_model().parameters.value()["config"])
    with pytest.raises(BoundaryError, match="unsupported_runtime_device"):
        light_action_m0_runtime_config(config, device)


def test_export_status_shape_cannot_authorize_public_registration(monkeypatch):
    identity = "a" * 64
    exported = SimpleNamespace(
        status=lambda: {
            "schema": "stpd/local-model-export-operation-v1",
            "operation": {"model_id": identity, "model_type": "public_m0", "status": "completed"},
        }
    )
    registration = LocalModelRegistration(object(), exported, object())
    monkeypatch.setattr(
        "spireagent.workbench.local_model_registration.require_local_models", lambda _: None
    )
    with pytest.raises(BoundaryError, match="public_m0_export_verification_required"):
        registration.register(identity)
