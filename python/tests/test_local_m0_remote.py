"""Durable local controller tests with real M0 core and a fake Modal boundary."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_local_training import _ready

    from spireagent.source import source_identity
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from spireagent.workbench.developer import ROOT as PYTHON_ROOT
    from spireagent.workbench.inplace_curation import configured_owner
    from spireagent.workbench.local_training import LocalTrainingService
    from stpd.fullrun.light_action_inputs import load_light_action_inputs
    from stpd.workers.token_ranking import LightActionM0Config, TokenTargetRuntime
    from stpd.workers.token_worker import prepare_token_run

    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    producer = source_identity(PYTHON_ROOT)
    operation_id = "d" * 32
    from spireagent import research_cli

    monkeypatch.setattr(research_cli, "source_identity", lambda _root: producer)
    store = ManifestArtifactStore(LocalBlobStore(config.local_research.store_dir,
                                                  create=False))
    argv = [
        "research-cli", "--store", str(config.local_research.store_dir),
        "prepare-light-action-m0", "--project-config", str(config_path),
        "--dataset", dataset_id, "--operation", operation_id,
        "--backbone", "s", "--input-profile", "public_lite",
        "--train-limit", "8", "--dev-limit", "4",
    ]
    stdout = io.StringIO()
    from spireagent.research_cli import main

    monkeypatch.setattr(sys, "argv", argv)
    with contextlib.redirect_stdout(stdout):
        assert main() == 0
    prepared = json.loads(stdout.getvalue().strip().splitlines()[-1])
    inputs = load_light_action_inputs(store, prepared["training_input_id"])
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    runtime = TokenTargetRuntime.current()
    model_config = LightActionM0Config(
        recipe="stage1a.dsimple.light-action.m0.s.v1", steps=2,
        device="cuda", public_profile="public_lite",
    )
    run = prepare_token_run(store, inputs, model_config, producer,
                            target_runtime=runtime)
    owner = configured_owner(config)
    service = LocalTrainingService(config, config_path=config_path)
    return (torch, previous_threads, service, store, owner, producer, run, model_config,
            operation_id, dataset_id)


class _FakeModalState:
    def __init__(self, store, ready: bool = True) -> None:
        self.store = store
        self.ready = ready
        self.submits = 0
        self.prepares = 0
        self.cancels = 0
        self.stops = 0
        self.request = None
        self.result_bytes = None
        self.handle = None
        self.runtime_evidence = None
        self.target = None


class _FakeProvider:
    def __init__(self, state: _FakeModalState, spec) -> None:
        self.state, self.spec = state, spec

    def prepare_app(self, spec):
        from stpd.cloud_jobs.m0_modal import ModalM0Target

        assert spec == self.spec
        self.state.prepares += 1
        target = ModalM0Target(
            spec.account_id, spec.environment_name, "ap-test-app", 1,
            "fu-test-function", spec.image_object_id, spec.producer,
            spec.attempt_id, spec.request_sha256, spec.target_runtime,
            spec.resource_plan,
        )
        self.state.target = target
        return target

    def submit(self, request_bytes: bytes, app_ref):
        from stpd.cloud_jobs.m0_modal import (
            ModalM0Call,
            ModalM0TargetRuntimeMetadata,
            _request_result_identity_sha256,
        )
        from stpd.workers.token_remote_update import TokenRemoteUpdateRequest

        request = TokenRemoteUpdateRequest.from_bytes(request_bytes)
        target = app_ref
        metadata = ModalM0TargetRuntimeMetadata(
            target.target_id, target.producer, target.image_object_id,
            target.target_runtime.torch_version, target.target_runtime.cpu_threads,
        )
        self.state.request = request
        self.state.result_bytes = self._make_result(request)
        self.state.submits += 1
        self.state.handle = ModalM0Call(
            target, metadata, "fc-test-" + request.attempt_id,
            request.request_sha256, len(request_bytes), request.attempt_id,
            request.run_id, request.input_id, request.operation_id,
            request.producer, _request_result_identity_sha256(request),
            "cuda", request.target_step, request.resume_checkpoint_id,
        )
        from stpd.cloud_jobs.m0_modal import ModalM0RuntimeEvidence

        self.state.runtime_evidence = ModalM0RuntimeEvidence(
            target.target_id, self.state.handle.call_id, request.request_sha256,
            request.attempt_id, request.producer, target.image_object_id,
            request.target_runtime.torch_version, "3.12.0", "12.8", True,
            "NVIDIA L4", request.target_runtime.cpu_threads,
            hashlib.sha256(self.state.result_bytes).hexdigest(),
            len(self.state.result_bytes),
        )
        return self.state.handle

    def _make_result(self, request):
        import torch

        from tokenizers import Tokenizer

        from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
        from stpd.workers.token_ranking import (
            CUDA_STEP_RNG_PROTOCOL,
            LightActionM0TrainOnlyInputs,
            TokenRankingEngine,
            config_payload,
            token_training_identity,
        )
        from stpd.workers.token_remote_update import TokenRemoteUpdateResult
        from stpd.workers.token_ranking import STEP_RNG_PROTOCOL

        tokenizer = Tokenizer.from_bytes(request.state_tokenizer)
        train_inputs = LightActionM0TrainOnlyInputs(
            request.input_manifest, request.train_rows, tokenizer,
        )
        cpu_config = replace(request.config, device="cpu")
        engine = TokenRankingEngine(train_inputs, cpu_config)
        if request.resume_checkpoint is not None:
            resume_state = decode_checkpoint(request.resume_checkpoint)
            resume_state["config"] = config_payload(cpu_config)
            resume_state["data_identity"] = engine.data_identity
            resume_state["rng_protocol"] = STEP_RNG_PROTOCOL
            resume_state["torch_version"] = str(torch.__version__)
            resume_state["cpu_threads"] = torch.get_num_threads()
            engine.restore(encode_checkpoint(resume_state))
        while engine.step < request.target_step:
            engine.advance()
        state = decode_checkpoint(engine.checkpoint())
        _, data_identity = token_training_identity(
            train_inputs, request.config, request.backbone_identity.value(),
        )
        state["config"] = config_payload(request.config)
        state["data_identity"] = data_identity
        state["rng_protocol"] = CUDA_STEP_RNG_PROTOCOL
        state["torch_version"] = request.target_runtime.torch_version
        state["cpu_threads"] = request.target_runtime.cpu_threads
        checkpoint = encode_checkpoint(state)
        return TokenRemoteUpdateResult(
            request.request_sha256, request.attempt_id, request.run_id,
            request.input_id, request.producer, request.operation_id,
            request.training_binding, request.target_device, request.target_step,
            request.config, request.resume_checkpoint_id,
            (hashlib.sha256(request.resume_checkpoint).hexdigest()
             if request.resume_checkpoint is not None else None),
            request.target_step, hashlib.sha256(checkpoint).hexdigest(), checkpoint,
            request.backbone_identity,
        ).to_bytes()

    def restore_handle(self, raw: bytes, app_ref):
        from stpd.cloud_jobs.m0_modal import ModalM0Call

        handle = ModalM0Call.from_bytes(raw)
        assert handle.target == app_ref
        return handle

    def poll(self, handle, *, timeout_seconds=0):
        if self.state.ready:
            return self.state.result_bytes
        return None

    def get_runtime_evidence(self, handle):
        assert self.state.handle == handle
        return self.state.runtime_evidence

    def cancel(self, handle):
        self.state.cancels += 1
        return type("Ack", (), {"acknowledged": True, "status": "acknowledged"})()

    def stop_app(self, app_ref):
        self.state.stops += 1
        return type("Ack", (), {"acknowledged": True, "status": "acknowledged"})()

    def inspect_stop(self, app_ref):
        from stpd.cloud_jobs.m0_modal import M0StopInspection

        return M0StopInspection(app_ref.app_id, "stopped", 0, 0, True)


def _controller(service, state, settings=None, **kwargs):
    from spireagent.workbench.local_m0_remote import LocalM0RemoteController

    return LocalM0RemoteController(
        service, settings,
        provider_factory=lambda *, spec: _FakeProvider(state, spec),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def test_remote_controller_restarts_by_polling_saved_handle_and_accepts_step_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch)
    torch, previous_threads, service, store, _owner, _producer, run, _config, _, _ = case
    try:
        from spireagent.workbench.local_m0_remote import ModalM0Settings

        state = _FakeModalState(store, ready=False)
        settings = ModalM0Settings("test-account", "test-env", "im-test-image")
        first = _controller(service, state, settings).start(
            run.artifact_id, 1, wait_seconds=0,
        )
        assert first["status"] == "interrupted_unknown"
        assert first.get("checkpoint_id") is None
        assert state.submits == state.prepares == 1
        assert state.stops == 1

        state.ready = True
        recovered = _controller(service, state).reconcile(
            first["operation_id"], wait_seconds=0,
        )
        assert recovered["status"] == "paused"
        assert recovered["checkpoint_step"] == 1
        assert state.submits == state.prepares == 1
        operation = service.status()["operation"]
        attempt = operation["remote"]["attempts"][-1]
        assert attempt["provider_runtime_evidence_sha256"] == hashlib.sha256(
            state.runtime_evidence.to_bytes(),
        ).hexdigest()
        assert attempt["provider_result_sha256"]

        completed = _controller(service, state).resume(
            first["operation_id"], 2, wait_seconds=0,
        )
        assert completed["status"] == "completed"
        assert completed["checkpoint_step"] == 2
        assert state.submits == state.prepares == 2
        assert completed["model_id"] and completed["evaluation_id"]
        assert completed["remote"]["attempts"][-1]["core_finalizer_attempt_seconds"] >= 0
        assert (store.get_manifest(completed["evaluation_id"])
                .parameters.value()["scoring_runtime"] == {
                    "mode": "weights_only", "device": "cpu",
                })
    finally:
        torch.set_num_threads(previous_threads)


def test_cancel_ack_does_not_claim_stopped_or_resumable_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch)
    torch, previous_threads, service, store, _owner, _producer, run, _config, _, _ = case
    try:
        from spireagent.workbench.local_m0_remote import ModalM0Settings

        state = _FakeModalState(store, ready=False)
        settings = ModalM0Settings("test-account", "test-env", "im-test-image")
        started = _controller(service, state, settings).start(
            run.artifact_id, 2, wait_seconds=0,
        )
        cancelled = _controller(service, state).cancel(
            started["operation_id"], wait_seconds=0,
        )
        assert state.cancels == 1
        assert cancelled["status"] == "interrupted_unknown"
        assert cancelled.get("checkpoint_id") is None
        assert cancelled["remote"]["attempts"][-1]["stop_confirmation"] == {
            "app_id": state.target.app_id, "app_state": "stopped",
            "active_tasks": 0, "active_containers": 0, "confirmed": True,
        }
        assert state.submits == 1
    finally:
        torch.set_num_threads(previous_threads)
