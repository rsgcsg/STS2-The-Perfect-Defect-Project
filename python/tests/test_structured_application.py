"""Synthetic CPU composition over real closed packages, sources and application owners."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
import test_protocol_source as original
import test_structured_s0 as fixtures
import torch
from test_local_model_registration import _caps

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.policies import SUPPORTED_ADAPTERS, policy_support
from spireagent.workbench import local_memory_evaluation as evaluation_module
from spireagent.workbench import local_model_registration as registration_module
from spireagent.workbench.developer import ROOT, ProjectConfig, combination
from spireagent.workbench.local_evaluation import summary
from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import VERBS, LocalModelRegistration
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.trusted_agents import agent_capabilities
from stpd.fullrun import protocol_source as sources

cpu_threads = fixtures.cpu_threads
exported = fixtures.exported


def setup(tmp_path, exported, *, partition="dev", training_seed="1", evaluation_seed="2"):
    store, owner = original.setup_store(tmp_path)
    train_ref, _, _ = original.publish_run(store, tmp_path, training_seed, "train")
    dev_ref, _, _ = original.publish_run(store, tmp_path, evaluation_seed, partition)
    train = sources.publish_protocol_source_partition(
        store, (train_ref,), "train", original.PROJECTION
    )
    dev = sources.publish_protocol_source_partition(
        store, (dev_ref,), partition, original.PROJECTION
    )
    # Only the evaluation source needs a local claim. Foreign model ancestry is immutable.
    owner.reserve_verified_protocol_source(store, dev.manifest.artifact_id)
    package, metadata, _ = exported
    payloads = tuple(
        store.put_payload(role, io.BytesIO((package / name).read_bytes()), media)
        for role, name, media in (
            ("package_manifest", "model.json", "application/json"),
            ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
        )
    )
    model = Manifest(
        "model",
        original.PROJECTION,
        (Parent("source", train.manifest.artifact_id),),
        payloads,
        FrozenObject.of(
            {"schema": "stpd/structured-m2-model-v1", "model_id": metadata["model_id"]}
        ),
    )
    store.publish(model)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    return config, owner, store, model, dev, train


def settle(service):
    thread = service._thread if hasattr(service, "_thread") else service.thread
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive()
    return service.status()["operation"]


def runtime_registration(config, monkeypatch):
    exported = LocalModelExport(config)
    models = LocalModelService(config)
    models.private_root.mkdir(parents=True, exist_ok=True)
    runtime = models.private_root / "runtime-fixture"
    runtime.mkdir()
    pin = {"package": "@rsgcsg/sts2-policy-runtime"}
    monkeypatch.setattr(models, "text_runtime_profile", lambda profile: (runtime, pin))
    monkeypatch.setattr(registration_module, "validate_runtime_install", lambda *a: {})
    monkeypatch.setattr(registration_module, "_v2_sdk_available", lambda *a: True)
    service = LocalModelRegistration(config, exported, models)
    caps = _caps() | {
        "input_profile": "text-menu-v2",
        "snapshot_schema": "sts2.player-environment/text-menu-snapshot-2",
        "receipt_schema": "sts2.player-environment/text-menu-action-result-2",
        "verbs": [*VERBS, "select_card", "select_target", "cancel_selection"],
    }
    caps["game"]["modset"]["fingerprint"] = "b" * 64
    monkeypatch.setattr(service, "_capabilities", lambda *a, **kw: caps)
    monkeypatch.setattr(service, "_context_available", lambda *a, **kw: None)
    monkeypatch.setattr(service, "_m2_runtime_manifest_compatible", lambda *a, **kw: None)
    return exported, models, service


def test_trusted_structured_export_register_readiness_launch_arguments(
    tmp_path, exported, monkeypatch
):
    config, _, store, model, _, _ = setup(tmp_path, exported)
    export, models, registration = runtime_registration(config, monkeypatch)
    assert "stpd-s0-structured-adapter" in SUPPORTED_ADAPTERS
    assert policy_support("stpd-s0-structured-adapter").ADAPTER == "stpd-s0-structured-adapter"
    assert (
        next(
            row for row in agent_capabilities() if row["adapter_id"] == "stpd-s0-structured-adapter"
        )["installation_available"]
        is True
    )
    export.start(model.artifact_id)
    assert settle(export)["status"] == "completed"
    result = registration.register(model.artifact_id)
    assert result["status"] == "registered"
    assert registration.register(model.artifact_id) == result
    assert registration.status(model.artifact_id) == result
    entry = models.selection(result["selection_id"])
    assert entry["adapter"] == "stpd-s0-structured-adapter"
    assert entry["runtime_profile"] == "text-menu-m2-v2"
    args = models.adapter_arguments(entry)
    assert args[:2] == ["-m", "stpd.policy.structured_port"]
    assert Path(args[args.index("--manifest") + 1]).is_absolute()
    monkeypatch.setattr(models, "_runtime_package", lambda *a: {"verified": True})
    monkeypatch.setattr(models, "_public_manifest_contract", lambda *a: None)
    ready = models.readiness(entry["id"])
    assert ready["status"] == "ready_to_load", ready
    manifest = json.loads(models.entry_path(entry, "manifest").read_bytes())
    assert manifest["claims"]["full_run"] is False
    assert (
        manifest["representation"]["input_schema"] == "sts2.player-environment/text-menu-snapshot-2"
    )
    # Both binding and readiness call the real closed-package owner.
    package = config.state_dir / "model-exports" / model.artifact_id
    (package / "weights.tensor-tree").write_bytes(b"corrupt")
    assert models.readiness(entry["id"])["status"] == "blocked"
    with pytest.raises(BoundaryError):
        registration.register(model.artifact_id)


def test_download_install_does_not_open_absent_private_ancestry(tmp_path, exported, monkeypatch):
    config, _, store, model, _, _ = setup(tmp_path, exported)
    directory = config.state_dir / "downloads" / model.artifact_id
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(model.to_bytes())
    (directory / "download.json").write_text(
        json.dumps({"schema": "stpd/result-download-v1", "artifact_id": model.artifact_id})
    )
    for payload in model.payloads:
        (directory / (payload.sha256 + ".bin")).write_bytes(store.bytes(payload))
    # Remove private source ancestor. The cache remains only a closed model result.
    (store.blobs.root / f"manifests/{model.parent('source')}.json").unlink()
    export, models, registration = runtime_registration(config, monkeypatch)
    export.start(model.artifact_id)
    assert settle(export)["status"] == "completed"
    assert registration.register(model.artifact_id)["status"] == "registered"
    (directory / (model.payload("weights").sha256 + ".bin")).write_bytes(b"corrupt")
    with pytest.raises(BoundaryError, match="download_payload_invalid"):
        export.verified_for_registration(model.artifact_id)


def actual_child(monkeypatch):
    original_child = evaluation_module._private_child

    def launch(command, log, environment, **kwargs):
        # Shared interpreter is editable to another checkout. Fix this synthetic
        # child's import path and producer explicitly; production uses fixed -m.
        script = (
            "import sys; sys.path.insert(0, " + repr(str(ROOT)) + ");"
            "from spireagent.workbench import structured_evaluation_child as child;"
            "from spireagent.artifact_contracts import Producer;"
            "child.source_identity=lambda root: Producer('fixture://child', 'c'*40, 'd'*64);"
            "sys.argv=['child']+sys.argv[1:]; child.main()"
        )
        return original_child(
            [sys.executable, "-c", script, *command[3:]], log, environment, **kwargs
        )

    monkeypatch.setattr(evaluation_module, "_private_child", launch)


@pytest.mark.parametrize("partition", ["dev", "test"])
def test_existing_eval_slot_actual_child_fixed_weights_use_and_result(
    tmp_path, exported, monkeypatch, partition
):
    config, owner, store, model, source, _ = setup(tmp_path, exported, partition=partition)
    actual_child(monkeypatch)
    before_rng, before_threads = torch.get_rng_state().clone(), torch.get_num_threads()
    service = LocalMemoryEvaluationService(config)
    started = service.start(model.artifact_id, source.manifest.artifact_id)["operation"]
    assert started["partition"] == partition
    completed = settle(service)
    assert completed["status"] == "completed", completed
    assert completed["child_exit"]["exit_code"] == 0
    assert completed["child_exit"]["forced"] is False
    assert "timed_out" not in completed["child_exit"]
    assert torch.equal(before_rng, torch.get_rng_state())
    assert torch.get_num_threads() == before_threads
    recorded = summary(store, completed["evaluation_id"])
    assert recorded["optimizer_updates"] == 0 and recorded["decision_count"] == 2
    assert recorded["partition"] == partition
    with owner.transaction() as db:
        assert db.execute("SELECT kind,reference FROM curation_source_uses").fetchall() == [
            ("evaluation", started["operation_id"])
        ]
    assert service.start(model.artifact_id, source.manifest.artifact_id)["operation"] == completed


def test_eval_overlap_and_missing_ancestry_never_launch(tmp_path, exported, monkeypatch):
    config, owner, store, model, source, train = setup(tmp_path, exported, evaluation_seed="1")
    service = LocalMemoryEvaluationService(config)
    monkeypatch.setattr(
        evaluation_module, "_private_child", lambda *a, **kw: pytest.fail("child launched")
    )
    with pytest.raises(BoundaryError, match="model_held_out_source_group_overlap"):
        service.start(model.artifact_id, source.manifest.artifact_id)
    assert service.status()["operation"]["status"] == "idle"
    (store.blobs.root / f"manifests/{train.manifest.artifact_id}.json").unlink()
    with pytest.raises(BoundaryError, match="object_not_found"):
        service.start(model.artifact_id, source.manifest.artifact_id)
    assert service._thread is None


def test_eval_unknown_child_not_replayed(tmp_path, exported, monkeypatch):
    config, _, _, model, source, _ = setup(tmp_path, exported)

    def lost(command, log, environment, *, on_started, on_exited, timeout_seconds):
        on_started()
        raise OSError("child result lost")

    monkeypatch.setattr(evaluation_module, "_private_child", lost)
    service = LocalMemoryEvaluationService(config)
    service.start(model.artifact_id, source.manifest.artifact_id)
    assert settle(service)["status"] == "interrupted_unknown"
    with pytest.raises(BoundaryError, match="previous_evaluation_outcome_unknown"):
        service.start(model.artifact_id, source.manifest.artifact_id)


@pytest.mark.parametrize("failure", ["gold", "train", "raw_tamper"])
def test_eval_source_admission_precedes_any_child(tmp_path, exported, monkeypatch, failure):
    config, owner, store, model, source, train = setup(tmp_path, exported)
    service = LocalMemoryEvaluationService(config)
    monkeypatch.setattr(
        evaluation_module,
        "_private_child",
        lambda *a, **kw: pytest.fail("child launched before admission"),
    )
    source_id = source.manifest.artifact_id
    if failure == "gold":
        with owner.transaction() as db:
            db.execute("UPDATE curation_claims SET purpose='gold' WHERE artifact=?", (source_id,))
        expected = "gold_reserved_data"
    elif failure == "train":
        source_id = train.manifest.artifact_id
        expected = "held_out_source_required"
    else:
        payload = store.get_manifest(source.source_ids[0]).payload("archive")
        index = json.loads(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
        key = "objects/sha256/" + index["chunks"][0]["sha256"]
        (store.blobs.root / key).write_bytes(b"corrupt")
        expected = "integrity"
    with pytest.raises(BoundaryError, match=expected):
        service.start(model.artifact_id, source_id)
    assert service._thread is None
    assert service.status()["operation"]["status"] == "idle"


def test_actual_child_start_callback_failure_records_forced_exit_without_timeout(
    tmp_path, exported, monkeypatch
):
    config, _, _, model, source, _ = setup(tmp_path, exported)
    actual_child(monkeypatch)
    service = LocalMemoryEvaluationService(config)

    def failed_started_receipt(*_):
        raise OSError("synthetic child-start journal write failure")

    monkeypatch.setattr(service, "_mark_child_started", failed_started_receipt)
    service.start(model.artifact_id, source.manifest.artifact_id)
    operation = settle(service)
    assert operation["status"] == "interrupted_unknown"
    receipt = operation["child_exit"]
    assert receipt["exit_code"] != 0
    assert receipt["elapsed_seconds"] < operation["wall_seconds"]
    assert receipt["forced"] is True
    assert "timed_out" not in receipt
    assert operation["error_code"] == "evaluation_storage_or_process_error"
    with pytest.raises(BoundaryError, match="previous_evaluation_outcome_unknown"):
        service.start(model.artifact_id, source.manifest.artifact_id)
