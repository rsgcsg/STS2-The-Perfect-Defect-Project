"""Historical closed M0 metadata at the genuine local owner seam; no backend/fit."""

from __future__ import annotations

import copy
import hashlib
import io
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from metadata_import_guard import install_torch_import_guard
from metadata_import_guard import no_torch_imports as no_torch_imports

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.encoding import semantic_hash
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.legacy_completed_training import (
    ATTEMPT_FIELDS,
    HISTORY_PREFIX,
    MAX_JOURNAL_BYTES,
    archive_completed,
    decode_completed,
    validate_history,
    verify_completion_metadata,
)
from spireagent.workbench.legacy_completed_training import (
    RECIPE as OLD_RECIPE,
)
from spireagent.workbench.legacy_completed_training import (
    SCHEMA as OLD_SCHEMA,
)
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.reward_m0_spec import RECIPE, RewardM0Config

PRODUCER = Producer("fixture://legacy-m0", "c" * 40, "d" * 64)


class MetadataBlobs(LocalBlobStore):
    def __init__(self, root):
        super().__init__(root)
        self.reads = []

    def get(self, key):
        assert not key.startswith(
            ("local-training/remote-request-chunks/", "local-training/remote-result-chunks/")
        )
        self.reads.append(key)
        return super().get(key)


def _build_closed_legacy(tmp_path):
    blobs = MetadataBlobs(tmp_path / "store")
    store = ManifestArtifactStore(blobs)
    owner = SimpleNamespace(
        path=blobs.root / ".curation.sqlite",
        store_dir=blobs.root,
        identity=("1" * 32, "2" * 32, "3" * 32, str(blobs.root)),
    )

    def publish(kind, parents=(), payloads=(), **info):
        manifest = Manifest(kind, PRODUCER, tuple(parents), tuple(payloads), FrozenObject.of(info))
        store.publish(manifest)
        return manifest

    dataset = publish("dataset", purpose="training")
    allocation = publish(
        "protocol", (Parent("dataset", dataset.artifact_id),), schema="stpd/decision-allocation-v1"
    )
    view = publish(
        "model_view",
        (Parent("dataset", dataset.artifact_id), Parent("allocation", allocation.artifact_id)),
        schema="stpd/public-observation-bc-view-v2",
    )
    operation = "a" * 32
    binding = {
        "schema": "stpd/light-action-m0-training-binding-v1",
        "dataset_ids": [dataset.artifact_id],
        "allocation_id": allocation.artifact_id,
        "model_view_id": view.artifact_id,
        "training_operation_id": operation,
    }
    config = {
        "recipe": OLD_RECIPE,
        "steps": 78,
        "public_profile": "public_compact",
        "device": "cuda",
    }
    codecs = tuple(
        store.put_payload(role, io.BytesIO(role.encode()), "application/json")
        for role in ("state_tokenizer", "action_codec")
    )
    inputs = publish(
        "training_input",
        (Parent("model_view", view.artifact_id),),
        codecs,
        schema="stpd/stage1a-light-action-m0-public-input-v1",
        training_binding=binding,
    )
    experiment = publish("experiment")
    run = publish(
        "run",
        (
            Parent("experiment", experiment.artifact_id),
            Parent("training_input", inputs.artifact_id),
        ),
        schema="stpd/stage1a-run-v1",
        config=config,
        training_binding=binding,
    )
    checkpoints = [
        publish(
            "checkpoint",
            (Parent("run", run.artifact_id), Parent("training_input", inputs.artifact_id)),
            schema="stpd/stage1a-light-action-m0-checkpoint-v1",
            recipe=OLD_RECIPE,
            step=3 * (i + 1),
            training_binding=binding,
        )
        for i in range(26)
    ]
    weights = store.put_payload("weights", io.BytesIO(b"opaque-no-weights-decoding"))
    model = publish(
        "model",
        (
            Parent("run", run.artifact_id),
            Parent("training_input", inputs.artifact_id),
            Parent("checkpoint", checkpoints[-1].artifact_id),
            Parent("model_view", view.artifact_id),
        ),
        (*codecs, weights),
        schema="stpd/stage1a-light-action-m0-public-model-v1",
        recipe=OLD_RECIPE,
        steps=78,
        config=config,
        training_binding=binding,
    )
    evaluation = publish(
        "offline_evaluation",
        (Parent("model", model.artifact_id), Parent("model_view", view.artifact_id)),
        schema="stpd/stage1a-ranking-evaluation-v1",
    )
    result = publish(
        "run_result",
        (
            Parent("model", model.artifact_id),
            Parent("run", run.artifact_id),
            Parent("training_input", inputs.artifact_id),
            Parent("offline_evaluation", evaluation.artifact_id),
        ),
        schema="stpd/run-result-v1",
        state="completed",
        steps=78,
    )
    ObjectStoreRunReporter(store, blobs).complete(result)

    def control(kind, value):
        raw = json_bytes(value)
        sha = hashlib.sha256(raw).hexdigest()
        ref = "local-training/remote-control/" + kind + "/" + sha
        blobs.put_if_absent(ref, raw)
        return sha, ref

    plan = {
        "cpu": 2.0,
        "function_timeout_seconds": 900,
        "gpu": "L4",
        "max_containers": 1,
        "memory_mib": 8192,
        "retries": 0,
        "scaledown_seconds": 30,
        "startup_timeout_seconds": 120,
    }
    runtime_spec = {"torch_version": "2.13.0+cu130", "cpu_threads": 2}
    attempts = []
    for i in range(26):
        attempt_id = f"{i + 1:032x}"
        request_sha = f"{i + 100:064x}"
        result_sha = f"{i + 200:064x}"
        spec = {
            "schema": "stpd/modal-m0-attempt-spec-v1",
            "attempt_id": attempt_id,
            "request_sha256": request_sha,
            "account_id": "synthetic-account",
            "environment_name": "synthetic",
            "image_object_id": "im-fixture",
            "producer": PRODUCER.to_dict(),
            "resource_plan": plan,
            "target_runtime": runtime_spec,
            "app_name": "stpd-m0-update-" + attempt_id,
        }
        app = {
            **spec,
            "schema": "stpd/modal-m0-app-ref-v1",
            "app_id": "ap-fixture" + str(i),
            "app_version": 1,
            "function_id": "fu-fixture",
            "function_name": "token_remote_update",
        }
        target = {k: v for k, v in app.items() if k != "schema"}
        target_id = semantic_hash(target)
        resume = None if i == 0 else checkpoints[i - 1].artifact_id
        handle = {
            "schema": "stpd/modal-m0-call-v3",
            "target": target,
            "target_id": target_id,
            "target_runtime": {
                **runtime_spec,
                "target_id": target_id,
                "producer": PRODUCER.to_dict(),
                "image_object_id": "im-fixture",
            },
            "attempt_id": attempt_id,
            "request_sha256": request_sha,
            "request_size_bytes": 9,
            "call_id": "fc-fixture" + str(i),
            "input_id": inputs.artifact_id,
            "operation_id": operation,
            "run_id": run.artifact_id,
            "producer": PRODUCER.to_dict(),
            "result_identity_sha256": "e" * 64,
            "target_device": "cuda",
            "target_step": 3 * (i + 1),
            "resume_checkpoint_id": resume,
        }
        runtime = {
            "schema": "stpd/modal-m0-runtime-evidence-v1",
            "attempt_id": attempt_id,
            "request_sha256": request_sha,
            "producer": PRODUCER.to_dict(),
            "target_id": target_id,
            "call_id": handle["call_id"],
            "image_object_id": "im-fixture",
            "torch_version": runtime_spec["torch_version"],
            "cpu_threads": 2,
            "result_sha256": result_sha,
            "result_size_bytes": 7,
        }
        record = dict.fromkeys(ATTEMPT_FIELDS)
        record.update(
            attempt_id=attempt_id,
            request_sha256=request_sha,
            request_ref="local-training/remote-requests/" + request_sha,
            provider_result_sha256=result_sha,
            provider_result_ref="local-training/remote-results/" + result_sha,
            start_step=3 * i,
            target_step=3 * (i + 1),
            resume_checkpoint_id=resume,
            resume_of_attempt_id=None if i == 0 else attempts[-1]["attempt_id"],
            app_phase="submitted",
            phase="terminal",
            provider_terminal=True,
            terminal_state="completed" if i == 25 else "paused",
            validated_checkpoint_id=checkpoints[i].artifact_id,
            checkpoint_candidate_sha256="f" * 64,
            submit_intent_at_unix_ns=100 + i * 10,
            provider_terminal_observed_at_unix_ns=101 + i * 10,
            local_acceptance_started_at_unix_ns=102 + i * 10,
            local_acceptance_completed_at_unix_ns=103 + i * 10,
            core_finalizer_attempt_seconds=1.0,
            stop_confirmation={
                "app_id": app["app_id"],
                "app_state": "stopped",
                "active_tasks": 0,
                "active_containers": 0,
                "confirmed": True,
            },
        )
        for stem, kind, payload in [
            ("attempt_spec", "attempt-spec", spec),
            ("app_ref", "app-ref", app),
            ("handle", "call-handle", handle),
            ("runtime_evidence", "runtime-evidence", runtime),
        ]:
            record[stem + "_sha256"], record[stem + "_ref"] = control(kind, payload)
        # Descriptor SHA is the LOGICAL payload identity, deliberately not index file SHA.
        request_index = {
            "schema": "stpd/local-training-remote-request-wire-index-v1",
            "sha256": request_sha,
            "size": 19,
            "wire_sha256": "9" * 64,
            "wire_size": 9,
            "chunks": [{"sha256": "8" * 64, "size": 9}],
        }
        result_index = {
            "schema": "stpd/local-training-remote-evidence-index-v1",
            "sha256": result_sha,
            "size": 7,
            "chunks": [{"sha256": "7" * 64, "size": 7}],
        }
        for key, index in [("request_ref", request_index), ("provider_result_ref", result_index)]:
            blobs.put_if_absent(
                record[key], json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
            )
        attempts.append(record)
    target_identity = {
        k: spec[k]
        for k in [
            "account_id",
            "environment_name",
            "image_object_id",
            "producer",
            "target_runtime",
            "resource_plan",
        ]
    }
    journal = {
        "schema": OLD_SCHEMA,
        "_owner": list(owner.identity),
        "status": "completed",
        "stage": "completed",
        "operation_id": operation,
        "dataset_id": dataset.artifact_id,
        "recipe": OLD_RECIPE,
        "input_profile": "public_compact",
        "result_type": "evaluated",
        "evaluation_status": "completed",
        "input_id": inputs.artifact_id,
        "run_id": run.artifact_id,
        "checkpoint_id": checkpoints[-1].artifact_id,
        "checkpoint_step": 78,
        "model_id": model.artifact_id,
        "result_id": result.artifact_id,
        "evaluation_id": evaluation.artifact_id,
        "created_at_unix_ns": 1,
        "completed_at_unix_ns": 1000,
        "total_wall_seconds": 1.0,
        "remote": {
            "target": {
                "target_id": hashlib.sha256(json_bytes(target_identity)).hexdigest(),
                "app_id": attempts[0]["attempt_id"],
                "deployment_source_sha256": hashlib.sha256(
                    json_bytes(PRODUCER.to_dict())
                ).hexdigest(),
                "gpu": "L4",
                "timeout_seconds": 900,
                "startup_timeout_seconds": 120,
                "max_total_steps": 78,
            },
            "attempts": attempts,
        },
    }
    journal["remote"]["target"]["app_id"] = "stpd-m0-update-" + attempts[0]["attempt_id"]
    raw = (json.dumps(journal, indent=2) + "\n").encode("utf-8")
    path = owner.path.parent / "local-training-operation.json"
    path.write_bytes(raw)
    cfg = ProjectConfig(tmp_path / "state", "", "", None, combination())
    return SimpleNamespace(
        store=store,
        blobs=blobs,
        owner=owner,
        journal=journal,
        raw=raw,
        path=path,
        config=cfg,
    )


def _legacy_files(root):
    return tuple(
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


@pytest.fixture(scope="module")
def closed_legacy_template(tmp_path_factory):
    # Keep the real publishers/completion owner, including all 26 attempts.
    # A module fixture precedes the function import guard, so guard this seam too.
    with pytest.MonkeyPatch.context() as monkeypatch:
        guard = install_torch_import_guard(monkeypatch)
        original = _build_closed_legacy(tmp_path_factory.mktemp("closed-legacy-template"))
        assert (
            LocalTrainingService._read(original.path, original.owner.identity) == original.journal
        )
        verify_completion_metadata(original.store, original.journal)
        assert not guard.attempts
    # Only immutable byte tuples cross tests; no store, database, reads or dicts do.
    files = _legacy_files(original.blobs.root)
    yield files
    assert _legacy_files(original.blobs.root) == files
    with pytest.MonkeyPatch.context() as monkeypatch:
        guard = install_torch_import_guard(monkeypatch)
        assert (
            LocalTrainingService._read(original.path, original.owner.identity) == original.journal
        )
        verify_completion_metadata(original.store, original.journal)
        assert not guard.attempts


def _clone_closed_legacy(files, tmp_path):
    blobs = MetadataBlobs(tmp_path / "store")
    # copy_artifact alone omits remote controls/indexes and the completion marker.
    # Fresh regular files preserve their exact bytes without links or fsync replay.
    for name, raw in files:
        path = blobs.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    path = blobs.root / "local-training-operation.json"
    journal = json.loads(path.read_bytes())
    owner = SimpleNamespace(
        path=blobs.root / ".curation.sqlite",
        store_dir=blobs.root,
        identity=(*journal["_owner"][:3], str(blobs.root)),
    )
    journal["_owner"] = list(owner.identity)
    raw = (json.dumps(journal, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return SimpleNamespace(
        store=ManifestArtifactStore(blobs),
        blobs=blobs,
        owner=owner,
        journal=journal,
        raw=raw,
        path=path,
        config=ProjectConfig(tmp_path / "state", "", "", None, combination()),
    )


@pytest.fixture
def closed_legacy(closed_legacy_template, tmp_path):
    return _clone_closed_legacy(closed_legacy_template, tmp_path)


def test_private_history_copies_preserve_closure_and_isolate_corruption(
    closed_legacy_template, tmp_path
):
    template_hashes = tuple(
        (name, hashlib.sha256(raw).hexdigest()) for name, raw in closed_legacy_template
    )
    damaged = _clone_closed_legacy(closed_legacy_template, tmp_path / "damaged")
    intact = _clone_closed_legacy(closed_legacy_template, tmp_path / "intact")
    assert damaged.owner.identity != intact.owner.identity
    assert damaged.config.state_dir != intact.config.state_dir
    assert damaged.config.combination is not intact.config.combination
    assert all(
        not (damaged.blobs.root / name).samefile(intact.blobs.root / name)
        for name, _ in closed_legacy_template
    )
    for f in (damaged, intact):
        assert not f.blobs.reads
        assert len(f.journal["remote"]["attempts"]) == 26
        assert LocalTrainingService._read(f.path, f.owner.identity) == f.journal
        verify_completion_metadata(f.store, f.journal)
        assert {
            name: raw for name, raw in _legacy_files(f.blobs.root)
            if name != "local-training-operation.json"
        } == {
            name: raw for name, raw in closed_legacy_template
            if name != "local-training-operation.json"
        }
    intact_before = _legacy_files(intact.blobs.root)
    intact_reads = list(intact.blobs.reads)
    attempt = damaged.journal["remote"]["attempts"][-1]
    control = damaged.blobs.root / attempt["handle_ref"]
    control_raw = control.read_bytes()
    control.write_bytes(control_raw + b" ")
    with pytest.raises(BoundaryError, match="historical_control_digest_mismatch"):
        verify_completion_metadata(damaged.store, damaged.journal)
    control.write_bytes(control_raw)
    index_path = damaged.blobs.root / attempt["request_ref"]
    index = json.loads(index_path.read_bytes())
    index["sha256"] = "0" * 64
    index_path.write_bytes(json_bytes(index))
    with pytest.raises(BoundaryError, match="historical_index_descriptor_invalid"):
        verify_completion_metadata(damaged.store, damaged.journal)
    # Nested metadata/owner and noncanonical raw journal are private as well.
    damaged.journal["_owner"][0] = "0" * 32
    damaged.journal["remote"]["attempts"][-1]["stop_confirmation"]["active_tasks"] = 1
    damaged.path.write_bytes(json_bytes(damaged.journal))
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        LocalTrainingService._read(damaged.path, damaged.owner.identity)
    assert intact.blobs.reads == intact_reads
    assert _legacy_files(intact.blobs.root) == intact_before
    assert LocalTrainingService._read(intact.path, intact.owner.identity) == intact.journal
    verify_completion_metadata(intact.store, intact.journal)
    assert tuple(
        (name, hashlib.sha256(raw).hexdigest()) for name, raw in closed_legacy_template
    ) == template_hashes


def test_closed_26_history_reader_and_metadata_are_readonly(closed_legacy):
    f = closed_legacy
    assert LocalTrainingService._read(f.path, f.owner.identity) == f.journal
    before = {
        p.relative_to(f.blobs.root): p.read_bytes() for p in f.blobs.root.rglob("*") if p.is_file()
    }
    verify_completion_metadata(f.store, decode_completed(f.raw, f.owner.identity))
    assert (
        f.path.read_bytes() == f.raw
        and {
            p.relative_to(f.blobs.root): p.read_bytes()
            for p in f.blobs.root.rglob("*")
            if p.is_file()
        }
        == before
    )
    assert not any(
        "/remote-request-chunks/" in k or "/remote-result-chunks/" in k for k in f.blobs.reads
    )


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "stage",
        "owner",
        "terminal",
        "stop",
        "step",
        "resume",
        "acceptance",
        "repeat",
        "model",
        "result",
    ],
)
def test_closed_reader_rejects_uncertain_or_rebound_history(closed_legacy, field):
    f = closed_legacy
    value = copy.deepcopy(f.journal)
    attempt = value["remote"]["attempts"][-1]
    if field == "status":
        value["status"] = "interrupted_unknown"
    elif field == "stage":
        value["stage"] = "remote_running"
    elif field == "owner":
        value["_owner"][0] = "0" * 32
    elif field == "terminal":
        attempt["provider_terminal"] = False
    elif field == "stop":
        attempt["stop_confirmation"]["active_tasks"] = 1
    elif field == "step":
        attempt["start_step"] -= 1
    elif field == "resume":
        attempt["resume_of_attempt_id"] = "0" * 32
    elif field == "acceptance":
        attempt["local_acceptance_completed_at_unix_ns"] = None
    elif field == "repeat":
        attempt["attempt_id"] = value["remote"]["attempts"][0]["attempt_id"]
    else:
        value[field + "_id"] = "bad"
    with pytest.raises((BoundaryError, KeyError, TypeError)):
        decode_completed(json_bytes(value), f.owner.identity)
    assert f.path.read_bytes() == f.raw


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_archive_preserves_all_raw_bytes_and_is_immutable(closed_legacy, newline):
    f = closed_legacy
    raw = f.raw.replace(b"\n", newline)
    f.path.write_bytes(raw)
    assert LocalTrainingService._read(f.path, f.owner.identity) == f.journal
    pointer = archive_completed(f.store, raw, f.journal)
    assert pointer["archive_ref"] == HISTORY_PREFIX + hashlib.sha256(raw).hexdigest()
    assert pointer["archive_sha256"] == hashlib.sha256(raw).hexdigest()
    assert pointer["archive_size"] == len(raw)
    assert f.blobs.get(pointer["archive_ref"]) == raw
    assert raw != json_bytes(f.journal) and f.path.read_bytes() == raw
    assert archive_completed(f.store, raw, f.journal) == pointer
    assert len(json.loads(f.blobs.get(pointer["archive_ref"]))["remote"]["attempts"]) == 26


def test_legacy_completion_status_exposes_history_without_remote_control(
    closed_legacy, monkeypatch
):
    f = closed_legacy
    service = LocalTrainingService(f.config)
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    result = service.status()
    assert result["availability"] == "ready" and result["schema"] == OLD_SCHEMA
    assert (
        result["operation"]["status"] == "completed"
        and result["operation"]["supported_actions"] == []
    )
    assert result["operation"]["historical_remote_attempts"] == 26
    assert f.path.read_bytes() == f.raw
    with pytest.raises(BoundaryError, match="stale_operation_attempt"):
        service.pause(f.journal["operation_id"], "1" * 32)


def test_explicit_successor_archives_before_pending_and_preserves_nested_history(
    closed_legacy, monkeypatch
):
    f = closed_legacy
    service = LocalTrainingService(f.config)
    events = []
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    monkeypatch.setattr(
        "spireagent.workbench.local_training.require_recipe_dependencies", lambda *_: None
    )

    def admitted(store, owner, request):
        assert store is f.store and owner is f.owner and f.path.read_bytes() == f.raw
        assert request.source_id != f.journal["dataset_id"]
        events.append("independent_new_admission")

    monkeypatch.setattr(
        "spireagent.workbench.trusted_recipes.recipe_adapter",
        lambda _: SimpleNamespace(preflight_request=admitted),
    )
    from spireagent.workbench import local_training as module

    actual_write = module.write_replaceable_json

    def write(path, value):
        pointer = value["previous_completed"]["historical_operation"]
        assert f.blobs.get(pointer["archive_ref"]) == f.raw
        events.append("pending_write_after_exact_archive")
        actual_write(path, value)

    monkeypatch.setattr(module, "write_replaceable_json", write)

    def launch(held, path, value, *_):
        events.append("new_local_launch_boundary")
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_launch_attempt", launch)
    request = TrainingRequest(
        "9" * 32,
        RECIPE,
        "9" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 900, "scratch_bytes": 1024**3},
        after_completed_operation_id=f.journal["operation_id"],
    )
    result = service.start(request)
    assert (
        result["operation"]["status"] == "pending"
        and result["operation"]["limits"] == request.limits
    )
    assert events == [
        "independent_new_admission",
        "pending_write_after_exact_archive",
        "new_local_launch_boundary",
    ]
    current = LocalTrainingService._read(f.path, f.owner.identity)
    validate_history(current["previous_completed"])
    assert current["previous_completed"]["schema"] == OLD_SCHEMA
    assert current["previous_completed"]["model_id"] == f.journal["model_id"]
    # The next ordinary v3 completion carries the original raw-history pointer too.
    current["status"] = "completed"
    current["model_id"] = "4" * 64
    current["run_id"] = "5" * 64
    current["result_id"] = "6" * 64
    next_op = LocalTrainingService._new_operation(
        replace(request, intent_id="8" * 32), f.owner, "7" * 32, previous=current
    )
    validate_history(next_op["previous_completed"])
    assert (
        next_op["previous_completed"]["previous_completed"]["historical_operation"]
        == current["previous_completed"]["historical_operation"]
    )


def test_preflight_refusal_does_not_archive_or_replace_legacy(closed_legacy, monkeypatch):
    f = closed_legacy
    service = LocalTrainingService(f.config)
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    monkeypatch.setattr(
        "spireagent.workbench.local_training.require_recipe_dependencies", lambda *_: None
    )

    def denied(*_):
        raise BoundaryError("new_recipe", "new_source_not_admitted")

    monkeypatch.setattr(
        "spireagent.workbench.trusted_recipes.recipe_adapter",
        lambda _: SimpleNamespace(preflight_request=denied),
    )
    req = TrainingRequest(
        "9" * 32,
        RECIPE,
        "9" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 900, "scratch_bytes": 1024**3},
        after_completed_operation_id=f.journal["operation_id"],
    )
    with pytest.raises(BoundaryError, match="new_source_not_admitted"):
        service.start(req)
    assert f.path.read_bytes() == f.raw and not any(
        k.startswith(HISTORY_PREFIX) for k in f.blobs.keys(HISTORY_PREFIX)
    )


def test_historical_transition_requires_exact_explicit_predecessor(closed_legacy, monkeypatch):
    f = closed_legacy
    service = LocalTrainingService(f.config)
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    req = TrainingRequest(
        "9" * 32,
        RECIPE,
        "9" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 900, "scratch_bytes": 1024**3},
    )
    with pytest.raises(BoundaryError, match="explicit_historical_successor_required"):
        service.start(req)
    with pytest.raises(BoundaryError, match="explicit_historical_successor_required"):
        service.start(replace(req, after_completed_operation_id="0" * 32))
    with pytest.raises(BoundaryError, match="explicit_local_successor_required"):
        service.start("9" * 64)
    assert f.path.read_bytes() == f.raw


def test_historical_journal_and_archive_pointer_are_bounded(closed_legacy):
    f = closed_legacy
    with pytest.raises(BoundaryError, match="historical_journal_bound_invalid"):
        decode_completed(f.raw + b" " * (MAX_JOURNAL_BYTES + 1), f.owner.identity)
    pointer = archive_completed(f.store, f.raw, f.journal)
    previous = LocalTrainingService._public({**f.journal, "_historical_operation": pointer})
    previous["historical_operation"]["archive_ref"] = "other/" + pointer["archive_sha256"]
    with pytest.raises(BoundaryError, match="historical_history_archive_reference_invalid"):
        validate_history(previous)


@pytest.mark.parametrize(
    "mutation", ["control_digest", "stop_binding", "index_logical_sha", "completion_marker"]
)
def test_transition_metadata_rejects_corruption_without_archive(closed_legacy, mutation):
    f = closed_legacy
    value = copy.deepcopy(f.journal)
    attempt = value["remote"]["attempts"][-1]
    if mutation == "control_digest":
        path = f.blobs.root / attempt["handle_ref"]
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "stop_binding":
        attempt["stop_confirmation"]["app_id"] = "ap-foreign"
    elif mutation == "index_logical_sha":
        path = f.blobs.root / attempt["request_ref"]
        index = json.loads(path.read_bytes())
        index["sha256"] = "0" * 64
        path.write_bytes(json_bytes(index))
    else:
        old = f.store.get_manifest(value["result_id"])
        changed = replace(old, parameters=FrozenObject.of({**old.parameters.value(), "steps": 79}))
        f.store.publish(changed)
        (f.blobs.root / "run-completions" / f"{value['run_id']}.json").write_bytes(
            json_bytes(
                {
                    "schema": "stpd/run-completion-v1",
                    "run_id": value["run_id"],
                    "result_id": changed.artifact_id,
                }
            )
        )
    with pytest.raises(
        BoundaryError, match="historical_.*mismatch|historical_index_descriptor_invalid"
    ):
        archive_completed(f.store, json_bytes(value), value)
    assert not f.blobs.keys(HISTORY_PREFIX) and f.path.read_bytes() == f.raw


def test_ordinary_v3_same_source_predecessor_rule_is_unchanged(closed_legacy, monkeypatch):
    f = closed_legacy
    service = LocalTrainingService(f.config)
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    original = TrainingRequest(
        "8" * 32,
        RECIPE,
        "8" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 900, "scratch_bytes": 1024**3},
    )
    completed = service._new_operation(original, f.owner, "8" * 32, previous={"status": "idle"})
    completed.update(
        status="completed",
        writer_terminal=True,
        run_id="1" * 64,
        result_id="2" * 64,
        model_id="3" * 64,
    )
    raw = json_bytes(completed)
    f.path.write_bytes(raw)
    request = replace(
        original,
        intent_id="9" * 32,
        source_id="9" * 64,
        after_completed_operation_id=completed["operation_id"],
    )
    with pytest.raises(BoundaryError, match="new_experiment_precondition_failed"):
        service.start(request)
    assert f.path.read_bytes() == raw and not f.blobs.keys(HISTORY_PREFIX)


def test_archive_failure_prevents_pending_replacement_or_child_preparation(
    closed_legacy, monkeypatch
):
    from spireagent.storage.blobs import StoreError

    f = closed_legacy
    service = LocalTrainingService(f.config)
    monkeypatch.setattr(service, "_selected", lambda: (f.owner, f.store, None))
    monkeypatch.setattr(
        "spireagent.workbench.local_training.require_recipe_dependencies", lambda *_: None
    )
    monkeypatch.setattr(
        "spireagent.workbench.trusted_recipes.recipe_adapter",
        lambda _: SimpleNamespace(preflight_request=lambda *_: None),
    )
    actual = f.blobs.put_if_absent

    def denied(key, data):
        if key.startswith(HISTORY_PREFIX):
            raise StoreError("synthetic_archive_write_failed")
        return actual(key, data)

    monkeypatch.setattr(f.blobs, "put_if_absent", denied)
    request = TrainingRequest(
        "9" * 32,
        RECIPE,
        "9" * 64,
        RewardM0Config().to_dict(),
        limits={"wall_seconds": 900, "scratch_bytes": 1024**3},
        after_completed_operation_id=f.journal["operation_id"],
    )
    with pytest.raises(BoundaryError, match="remote_operation_history_persistence_failed"):
        service.start(request)
    assert f.path.read_bytes() == f.raw and not f.blobs.keys(HISTORY_PREFIX)
    assert not list(f.path.parent.glob("*.child.lock")) and service._thread is None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("previous_remote_failure", {}, "unsupported_historical_remote_recovery_metadata"),
        ("previous_remote_failure", None, "unsupported_historical_remote_recovery_metadata"),
        ("remote_retry_permission", True, "historical_completed_fields_invalid"),
        ("stopped_at_unix_ns", False, "historical_stop_time_invalid"),
        ("stopped_at_unix_ns", 0, "historical_stop_time_invalid"),
        ("stopped_at_unix_ns", "100", "historical_stop_time_invalid"),
        ("stopped_at_unix_ns", float("inf"), "historical_stop_time_invalid"),
    ],
)
def test_closed_top_fields_do_not_weaken_original_history_checks(closed_legacy, field, value, code):
    f = closed_legacy
    modified = copy.deepcopy(f.journal)
    modified[field] = value
    # Use the pure dictionary gate so the finite-number rule itself is covered.
    from spireagent.workbench.legacy_completed_training import validate_completed

    with pytest.raises(BoundaryError, match=code):
        validate_completed(modified, f.owner.identity)
    assert f.path.read_bytes() == f.raw and not f.blobs.keys(HISTORY_PREFIX)


def test_optional_stopped_timestamp_is_preserved_without_normalization(closed_legacy):
    f = closed_legacy
    for stamp in (None, 1000):
        modified = {**f.journal, "stopped_at_unix_ns": stamp}
        raw = json.dumps(modified, indent=4).encode()
        assert decode_completed(raw, f.owner.identity) == modified
    modified = {**f.journal, "created_at_unix_ns": 100, "stopped_at_unix_ns": 99}
    with pytest.raises(BoundaryError, match="historical_stop_time_invalid"):
        decode_completed(json_bytes(modified), f.owner.identity)
    assert f.path.read_bytes() == f.raw
