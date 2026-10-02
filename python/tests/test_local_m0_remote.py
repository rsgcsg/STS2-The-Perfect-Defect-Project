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


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, steps: int = 2):
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
    assert config.research_workspace is not None
    store = ManifestArtifactStore(LocalBlobStore(config.research_workspace.store_dir,
                                                  create=False))
    argv = [
        "research-cli", "--store", str(config.research_workspace.store_dir),
        "prepare-light-action-m0", "--project-config", str(config_path),
        "--dataset", dataset_id, "--operation", operation_id,
        "--backbone", "s", "--input-profile", "public_lite",
        "--train-limit", "8", "--dev-limit", "4",
        "--max-state-tokens", "1024", "--max-action-bytes", "1024",
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
        recipe="stage1a.dsimple.light-action.m0.s.v1", steps=steps,
        device="cuda", public_profile="public_lite",
        max_state_tokens=1024, max_action_bytes=1024,
    )
    run = prepare_token_run(store, inputs, model_config, producer,
                            target_runtime=runtime)
    owner = configured_owner(config)
    service = LocalTrainingService(config, config_path=config_path)
    return (torch, previous_threads, service, store, owner, producer, run, model_config,
            operation_id, dataset_id)


def test_private_directory_sync_is_a_noop_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from spireagent.storage import replaceable_file

    monkeypatch.setattr(replaceable_file, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        replaceable_file,
        "os",
        SimpleNamespace(open=lambda *_args: pytest.fail("Windows must not open a directory")),
    )
    replaceable_file.sync_directory(Path("unused"))


@pytest.mark.skipif(sys.platform == "win32", reason="directory fsync is POSIX-specific")
def test_private_file_removal_syncs_parent_directory_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from spireagent.storage import replaceable_file
    from spireagent.workbench.local_training import LocalTrainingService

    path = tmp_path / "retry-state.json"
    path.write_text("private", encoding="utf-8")
    observed: list[int] = []
    original_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        observed.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(replaceable_file.os, "fsync", record_fsync)
    LocalTrainingService._remove_private_file(path)
    assert not path.exists()
    assert len(observed) == 1


class _FakeModalState:
    def __init__(
        self,
        store,
        ready: bool = True,
        *,
        canonical_account_id: str | None = None,
        workspace_name: str | None = None,
        app_found: bool = False,
        lookup_error: Exception | None = None,
        prepare_error: Exception | None = None,
        prepare_stopped: bool = False,
    ) -> None:
        self.store = store
        self.ready = ready
        self.canonical_account_id = canonical_account_id
        self.workspace_name = workspace_name
        self.app_found = app_found
        self.lookup_error = lookup_error
        self.prepare_error = prepare_error
        self.prepare_stopped = prepare_stopped
        self.stop_inspections = 0
        self.stop_proof_transform = None
        self.lookups = 0
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

    def verify_account_id(self, account_id, *, allow_legacy_workspace_alias=False):
        from spireagent.json_boundary import BoundaryError

        if self.state.canonical_account_id is None:
            self.state.canonical_account_id = self.spec.account_id
        if account_id == self.state.canonical_account_id:
            return self.state.canonical_account_id
        if (allow_legacy_workspace_alias
                and account_id == self.state.workspace_name):
            return self.state.canonical_account_id
        raise BoundaryError("modal_m0", "modal_account_mismatch")

    def prepare_app(self, spec):
        from stpd.cloud_jobs.m0_modal import ModalM0Target

        assert spec == self.spec
        self.state.prepares += 1
        if self.state.prepare_error is not None:
            raise self.state.prepare_error
        target = ModalM0Target(
            spec.account_id, spec.environment_name, "ap-test-app", 1,
            "fu-test-function", spec.image_object_id, spec.producer,
            spec.attempt_id, spec.request_sha256, spec.target_runtime,
            spec.resource_plan,
        )
        self.state.target = target
        return target

    def resolve_app(self, spec, *, allow_legacy_workspace_alias=False):
        from stpd.cloud_jobs.m0_modal import ModalM0Target

        self.verify_account_id(
            spec.account_id,
            allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        self.state.lookups += 1
        if self.state.lookup_error is not None:
            raise self.state.lookup_error
        if not self.state.app_found:
            return None
        target = ModalM0Target(
            spec.account_id, spec.environment_name, "ap-test-app", 1,
            "fu-test-function", spec.image_object_id, spec.producer,
            spec.attempt_id, spec.request_sha256, spec.target_runtime,
            spec.resource_plan,
        )
        self.state.target = target
        return target

    def inspect_prepare_stopped(self, spec, *, allow_legacy_workspace_alias=False):
        from spireagent.json_boundary import BoundaryError
        from stpd.cloud_jobs.m0_modal import M0PrepareStoppedProof, M0StopInspection

        self.verify_account_id(
            spec.account_id, allow_legacy_workspace_alias=allow_legacy_workspace_alias,
        )
        self.state.stop_inspections += 1
        if not self.state.prepare_stopped:
            raise BoundaryError("modal_m0", "prepare_stop_unproven")
        proof = M0PrepareStoppedProof(
            spec, self.state.canonical_account_id, "ap-test-app", "fu-test-function",
            M0StopInspection("ap-test-app", "stopped", 0, 0, True),
        )
        if self.state.stop_proof_transform is not None:
            proof = self.state.stop_proof_transform(proof)
        return proof

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
            STEP_RNG_PROTOCOL,
            LightActionM0TrainOnlyInputs,
            TokenRankingEngine,
            config_payload,
            token_training_identity,
        )
        from stpd.workers.token_remote_update import TokenRemoteUpdateResult

        tokenizer = Tokenizer.from_str(request.state_tokenizer.decode("utf-8"))
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

    def stop_app(self, app_ref, *, allow_legacy_workspace_alias=False):
        self.state.stops += 1
        return type("Ack", (), {"acknowledged": True, "status": "acknowledged"})()

    def inspect_stop(self, app_ref, *, allow_legacy_workspace_alias=False):
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


def _prepare_intent_operation(service, dataset_id: str, *, account_id: str):
    from test_local_training import _remote_m0_request, _remote_m0_target

    from spireagent.artifact_contracts import Producer
    from stpd.cloud_jobs.m0_modal import M0AttemptSpec
    from stpd.workers.token_ranking import TokenTargetRuntime

    operation_id = "d" * 32
    attempt_id = "e" * 32
    request_bytes = _remote_m0_request(operation_id, attempt_id, 1)
    operation = service.reserve_remote_m0(
        dataset_id,
        input_profile="public_lite",
        request_bytes=request_bytes,
        target=_remote_m0_target(),
        target_step=1,
    )["operation"]
    attempt = operation["remote"]["attempts"][-1]
    spec = M0AttemptSpec(
        attempt["attempt_id"], attempt["request_sha256"], account_id,
        "test-env", "im-test-image",
        Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "a" * 40, "b" * 64),
        TokenTargetRuntime("2.8.0", 2),
    )
    service.persist_remote_attempt_spec(operation["operation_id"], spec.to_bytes())
    # Simulate process loss after prepare_intent was durable.
    interrupted = service.status()["operation"]
    assert interrupted["status"] == "interrupted_unknown"
    return interrupted, spec.to_bytes()


def test_reconcile_stops_durable_app_ref_when_submit_handle_was_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        from test_local_training import (
            _pin_remote_m0_provider_identity,
            _remote_m0_request,
            _remote_m0_target,
        )

        operation = service.reserve_remote_m0(
            dataset_id, input_profile="public_lite",
            request_bytes=_remote_m0_request("6" * 32, "7" * 32, 1),
            target=_remote_m0_target(), target_step=1,
        )["operation"]
        app_ref_bytes, _, _ = _pin_remote_m0_provider_identity(service, operation)
        state = _FakeModalState(store)

        recovered = _controller(service, state).reconcile(
            operation["operation_id"], wait_seconds=0,
        )

        assert recovered["status"] == "interrupted_unknown"
        assert recovered["stage"] == "remote_unknown"
        attempt = recovered["remote"]["attempts"][-1]
        assert attempt["stop_confirmation"]["confirmed"] is True
        evidence = service.remote_provider_evidence(operation["operation_id"])
        assert evidence["app_ref_bytes"] == app_ref_bytes
        assert evidence["handle_bytes"] is None
        assert state.stops == 1
        assert state.submits == state.prepares == 0
    finally:
        torch.set_num_threads(previous_threads)


def test_new_start_rejects_workspace_slug_before_creating_remote_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, run, _, _, _ = case
    try:
        from spireagent.json_boundary import BoundaryError
        from spireagent.workbench.local_m0_remote import ModalM0Settings

        state = _FakeModalState(
            store, canonical_account_id="ac-BRJL3wJjpxPVWozQkvp9xf",
            workspace_name="chensunguo1210",
        )
        with pytest.raises(BoundaryError, match="modal_account_mismatch"):
            _controller(
                service, state,
                ModalM0Settings("chensunguo1210", "test-env", "im-test-image"),
            ).start(run.artifact_id, 1, wait_seconds=0)

        assert service.status()["operation"]["status"] == "idle"
        assert state.prepares == state.submits == state.lookups == 0
    finally:
        torch.set_num_threads(previous_threads)


@pytest.mark.parametrize("lookup_error_code", [
    "modal_app_list_incomplete", "deployed_target_identity_unavailable",
    "deployed_target_identity_mismatch", "deployed_target_resource_mismatch",
    "attempt_app_redeployed",
])
def test_prepare_error_with_incomplete_recovery_lookup_stays_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup_error_code: str,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, run, _, _, _ = case
    try:
        from spireagent.json_boundary import BoundaryError
        from spireagent.workbench.local_m0_remote import ModalM0Settings

        state = _FakeModalState(
            store,
            prepare_error=BoundaryError("modal_m0", "prepare_unknown"),
            lookup_error=BoundaryError("modal_m0", lookup_error_code),
        )
        operation = _controller(
            service, state,
            ModalM0Settings("test-account", "test-env", "im-test-image"),
        ).start(run.artifact_id, 1, wait_seconds=0)

        assert operation["status"] == "interrupted_unknown"
        assert operation["stage"] == "remote_unknown"
        evidence = service.remote_provider_evidence(operation["operation_id"])
        assert evidence["attempt_spec_bytes"] is not None
        assert evidence["app_ref_bytes"] is None
        assert evidence["handle_bytes"] is None
        assert state.prepares == 1
        assert state.submits == state.stops == 0
    finally:
        torch.set_num_threads(previous_threads)


def test_old_slug_prepare_intent_binds_same_workspace_and_stops_exact_app_without_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        from spireagent.json_boundary import BoundaryError
        from stpd.cloud_jobs.m0_modal import M0AttemptSpec

        operation, spec_bytes = _prepare_intent_operation(
            service, dataset_id, account_id="chensunguo1210",
        )
        state = _FakeModalState(
            store, canonical_account_id="ac-BRJL3wJjpxPVWozQkvp9xf",
            workspace_name="chensunguo1210", app_found=True,
        )

        recovered = _controller(service, state).reconcile(
            operation["operation_id"], wait_seconds=0,
        )

        assert recovered["status"] == "interrupted_unknown"
        assert recovered["stage"] == "remote_unknown"
        evidence = service.remote_provider_evidence(operation["operation_id"])
        assert evidence["attempt_spec_bytes"] == spec_bytes
        assert evidence["app_ref_bytes"] is not None
        assert evidence["handle_bytes"] is None
        saved_spec = M0AttemptSpec.from_bytes(spec_bytes)
        expected_app_name = "stpd-m0-update-" + operation["remote"]["attempts"][-1]["attempt_id"]
        assert saved_spec.app_name == expected_app_name
        assert recovered["remote"]["attempts"][-1]["stop_confirmation"]["confirmed"] is True
        assert state.lookups == state.stops == 1
        assert state.prepares == state.submits == 0
        with pytest.raises(BoundaryError, match="remote_preflight_retirement_unavailable"):
            _controller(service, state).retire_preflight_failure(
                operation["operation_id"],
            )
    finally:
        torch.set_num_threads(previous_threads)


def test_old_prepare_intent_absence_closes_only_after_complete_lookup_and_no_submit_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        operation, spec_bytes = _prepare_intent_operation(
            service, dataset_id, account_id="chensunguo1210",
        )
        state = _FakeModalState(
            store, canonical_account_id="ac-BRJL3wJjpxPVWozQkvp9xf",
            workspace_name="chensunguo1210", app_found=False,
        )

        recovered = _controller(service, state).reconcile(
            operation["operation_id"], wait_seconds=0,
        )

        attempt = recovered["remote"]["attempts"][-1]
        assert recovered["status"] == "failed"
        assert recovered["error_code"] == "provider_app_absent_before_submit"
        assert attempt["phase"] == "preflight_failed"
        assert attempt["app_phase"] == "prepare_intent"
        assert attempt["submit_intent_at_unix_ns"] is None
        evidence = service.remote_provider_evidence(operation["operation_id"])
        assert evidence["attempt_spec_bytes"] == spec_bytes
        assert evidence["app_ref_bytes"] is evidence["handle_bytes"] is None
        assert state.lookups == 1
        assert state.prepares == state.submits == state.stops == 0
    finally:
        torch.set_num_threads(previous_threads)


def test_old_slug_absence_can_be_retired_and_restarted_under_same_training_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from spireagent import source as source_module
    from spireagent.artifact_contracts import Producer

    old_producer = Producer(
        "rsgcsg/STS2-The-Perfect-Defect-Project", "a" * 40, "b" * 64,
    )
    new_producer = Producer(
        "rsgcsg/STS2-The-Perfect-Defect-Project", "c" * 40, "d" * 64,
    )
    monkeypatch.setattr(source_module, "source_identity", lambda _root: old_producer)
    case = _case(tmp_path, monkeypatch, steps=1)
    (torch, previous_threads, service, store, owner, producer, run,
     model_config, operation_id, dataset_id) = case
    try:
        from spireagent.json_boundary import BoundaryError
        from spireagent.workbench import local_m0_remote as remote_module
        from spireagent.workbench.local_m0_remote import ModalM0Settings
        from spireagent.workbench.local_training import OPERATION_FILE
        from stpd.fullrun.light_action_inputs import load_light_action_inputs
        from stpd.workers.token_ranking import TokenTargetRuntime
        from stpd.workers.token_remote_update import prepare_token_remote_update
        from stpd.workers.token_worker import prepare_token_run

        # The controller imported source_identity by value. Pin that alias to the
        # new runtime producer so preflight reaches the retry workspace gate.
        monkeypatch.setattr(remote_module, "source_identity", lambda _root: new_producer)

        old_attempt_id = "e" * 32
        old_request = prepare_token_remote_update(
            store, owner, run.artifact_id, producer, operation_id, 1,
            attempt_id=old_attempt_id,
        )
        old_settings = ModalM0Settings("chensunguo1210", "test-env", "im-test-image")
        old_controller = _controller(service, _FakeModalState(store), old_settings)
        old_spec = old_controller._spec(old_request, old_settings)
        old_target = old_controller._target(old_spec, 1)
        reserved = service.reserve_remote_m0(
            dataset_id, input_profile="public_lite",
            request_bytes=old_request.to_bytes(), target=old_target, target_step=1,
        )["operation"]
        service.persist_remote_attempt_spec(operation_id, old_spec.to_bytes())
        assert service.status()["operation"]["status"] == "interrupted_unknown"

        state = _FakeModalState(
            store, canonical_account_id="ac-BRJL3wJjpxPVWozQkvp9xf",
            workspace_name="chensunguo1210", app_found=False,
        )
        controller = _controller(service, state)
        closed = controller.reconcile(operation_id, wait_seconds=0)
        assert closed["status"] == "failed"
        assert closed["error_code"] == "provider_app_absent_before_submit"
        assert closed["remote"]["attempts"][-1]["recovery_receipt"] == {
            "schema": "stpd/local-modal-m0-prepare-absence-receipt-v1",
            "observed_at_unix_ns": closed["remote"]["attempts"][-1]["recovery_receipt"][
                "observed_at_unix_ns"],
            "attempt_id": old_attempt_id,
            "attempt_spec_sha256": hashlib.sha256(old_spec.to_bytes()).hexdigest(),
            "canonical_account_id": "ac-BRJL3wJjpxPVWozQkvp9xf",
            "pinned_account_id": "chensunguo1210",
            "environment_name": "test-env",
            "app_name": old_spec.app_name,
            "lookup_result": "complete_app_list_and_exact_history_not_found",
            "submit_boundary": "no_app_ref_handle_or_submit_intent",
        }
        journal_path = owner.path.parent / OPERATION_FILE
        original_journal = journal_path.read_bytes()
        evidence = service.remote_provider_evidence(operation_id)
        assert evidence["attempt_spec_bytes"] == old_spec.to_bytes()
        assert evidence["app_ref_bytes"] is evidence["handle_bytes"] is None

        retired = controller.retire_preflight_failure(operation_id)
        assert retired["status"] == "retired_for_preflight_retry"
        assert retired["error_code"] == "provider_app_absent_before_submit"
        assert not journal_path.exists()
        assert store.blobs.get(retired["archive_ref"]) == original_journal
        assert service.status()["operation"]["status"] == "idle"

        # A new owner operation or workspace cannot consume this one-use retry permit.
        with pytest.raises(BoundaryError):
            prepare_token_remote_update(
                store, owner, run.artifact_id, producer, "f" * 32, 1,
                attempt_id="a" * 32,
            )

        new_settings = ModalM0Settings(
            "ac-BRJL3wJjpxPVWozQkvp9xf", "test-env", "im-recovery-image",
        )
        # The old run remains bound to its old producer; the new controller must
        # refuse to reinterpret it under the new source identity.
        with pytest.raises(BoundaryError, match="source_or_contract_mismatch"):
            _controller(service, state, new_settings).start(
                run.artifact_id, 1, wait_seconds=0,
            )
        assert state.prepares == state.submits == 0

        # Re-prepare a fresh run through the existing production API, preserving
        # the exact admitted input, config/seed, and target runtime.
        inputs = load_light_action_inputs(store, old_request.input_id)
        fresh_run = prepare_token_run(
            store, inputs, model_config, new_producer,
            replicate="preflight-retry-" + operation_id,
            target_runtime=TokenTargetRuntime.from_run_info(run.parameters.value()),
        )
        assert fresh_run.artifact_id != run.artifact_id
        assert fresh_run.parent("training_input") == old_request.input_id
        assert fresh_run.producer == new_producer != run.producer
        assert fresh_run.parameters.value()["config"] == run.parameters.value()["config"]

        wrong_workspace = _FakeModalState(
            store, canonical_account_id="ac-foreign", workspace_name="other-workspace",
        )
        with pytest.raises(BoundaryError, match="preflight_retry_workspace_mismatch"):
            _controller(
                service, wrong_workspace,
                ModalM0Settings("ac-foreign", "test-env", "im-recovery-image"),
            ).start(fresh_run.artifact_id, 1, wait_seconds=0)
        assert wrong_workspace.prepares == wrong_workspace.submits == 0
        assert service.status()["operation"]["status"] == "idle"

        # Reproduce the race where the advisory pre-lock read misses a permit
        # published immediately before the reservation lock is acquired.
        raced_workspace = _FakeModalState(
            store, canonical_account_id="ac-foreign", workspace_name="other-workspace",
        )
        with monkeypatch.context() as race_patch:
            race_patch.setattr(service, "remote_preflight_retry_context", lambda: None)
            with pytest.raises(BoundaryError, match="remote_preflight_retry_workspace_mismatch"):
                _controller(
                    service, raced_workspace,
                    ModalM0Settings("ac-foreign", "test-env", "im-recovery-image"),
                ).start(fresh_run.artifact_id, 1, wait_seconds=0)
        assert raced_workspace.prepares == raced_workspace.submits == 0
        assert service.status()["operation"]["status"] == "idle"
        assert service.remote_preflight_retry_context() is not None

        # A caller cannot pair a typed new producer spec with stale or forged
        # target_id/app_id/source hashes. Each attempt leaves the permit intact.
        candidate_request = prepare_token_remote_update(
            store, owner, fresh_run.artifact_id, new_producer, operation_id, 1,
            attempt_id="a" * 32,
        )
        candidate_spec = _controller(service, state, new_settings)._spec(
            candidate_request, new_settings,
        )
        candidate_target = _controller(service, state, new_settings)._target(
            candidate_spec, model_config.steps,
        )
        assert candidate_spec.app_name != old_spec.app_name
        for field, wrong_value in (
            ("target_id", "f" * 64),
            ("app_id", candidate_spec.app_name + "-stale"),
            ("deployment_source_sha256", "e" * 64),
        ):
            forged_target = dict(candidate_target)
            forged_target[field] = wrong_value
            with pytest.raises(BoundaryError, match="remote_preflight_retry_binding_mismatch"):
                service.reserve_remote_m0(
                    dataset_id, input_profile="public_lite",
                    request_bytes=candidate_request.to_bytes(), target=forged_target,
                    target_step=1, attempt_spec_bytes=candidate_spec.to_bytes(),
                )
            assert service.status()["operation"]["status"] == "idle"

        state.prepare_error = None
        restarted = _controller(service, state, new_settings).start(
            fresh_run.artifact_id, 1, wait_seconds=0,
        )
        assert restarted["status"] == "completed"
        assert restarted["operation_id"] == operation_id
        assert restarted["previous_remote_failure"]["archive_ref"] == retired["archive_ref"]
        assert restarted["previous_remote_failure"]["archive_sha256"] == retired[
            "archive_sha256"
        ]
        assert restarted["remote"]["attempts"][-1]["attempt_id"] != old_attempt_id
        assert state.request is not None
        assert state.request.run_id == fresh_run.artifact_id != run.artifact_id
        assert state.request.input_id == old_request.input_id
        assert state.request.producer == new_producer
        assert state.request.config == model_config
        assert state.request.resume_checkpoint_id is None
        recovered_target = restarted["remote"]["target"]
        assert recovered_target["target_id"] != old_target["target_id"]
        assert recovered_target["app_id"] != old_target["app_id"]
        assert (recovered_target["deployment_source_sha256"]
                != old_target["deployment_source_sha256"])
        assert state.target.image_object_id == "im-recovery-image"
        assert state.lookups == 1
        assert state.prepares == state.submits == 1
        assert state.stops <= 1
        archived = json.loads(store.blobs.get(retired["archive_ref"]))
        assert archived["error_code"] == "provider_app_absent_before_submit"
        assert archived["remote"]["attempts"][-1]["attempt_spec_sha256"] == hashlib.sha256(
            old_spec.to_bytes(),
        ).hexdigest()
        assert archived["remote"]["attempts"][-1]["attempt_id"] == old_attempt_id
        assert reserved["remote"]["attempts"][-1]["attempt_id"] == old_attempt_id
    finally:
        torch.set_num_threads(previous_threads)


def test_wrong_account_keeps_old_prepare_intent_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        wrong_operation, _ = _prepare_intent_operation(
            service, dataset_id, account_id="chensunguo1210",
        )
        from spireagent.json_boundary import BoundaryError

        wrong_account = _FakeModalState(
            store, canonical_account_id="ac-foreign", workspace_name="other-workspace",
        )
        refused = _controller(service, wrong_account).reconcile(
            wrong_operation["operation_id"], wait_seconds=0,
        )
        assert refused["status"] == "interrupted_unknown"
        assert wrong_account.lookups == wrong_account.stops == wrong_account.submits == 0
        with pytest.raises(BoundaryError, match="remote_preflight_retirement_unavailable"):
            _controller(service, wrong_account).retire_preflight_failure(
                wrong_operation["operation_id"],
            )

    finally:
        torch.set_num_threads(previous_threads)


def test_incomplete_lookup_keeps_old_prepare_intent_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        from spireagent.json_boundary import BoundaryError

        operation, _ = _prepare_intent_operation(
            service, dataset_id, account_id="chensunguo1210",
        )
        incomplete = _FakeModalState(
            store,
            canonical_account_id="ac-BRJL3wJjpxPVWozQkvp9xf",
            workspace_name="chensunguo1210",
            lookup_error=BoundaryError("modal_m0", "modal_app_list_incomplete"),
        )
        left_unknown = _controller(service, incomplete).reconcile(
            operation["operation_id"], wait_seconds=0,
        )
        assert left_unknown["status"] == "interrupted_unknown"
        assert left_unknown["stage"] == "remote_unknown"
        assert incomplete.lookups == 1
        assert incomplete.stops == incomplete.submits == 0
        with pytest.raises(BoundaryError, match="remote_preflight_retirement_unavailable"):
            _controller(service, incomplete).retire_preflight_failure(
                operation["operation_id"],
            )
    finally:
        torch.set_num_threads(previous_threads)


def test_reconcile_closes_reservation_crash_before_attempt_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, _, _, _, _, _, dataset_id = case
    try:
        from test_local_training import _remote_m0_request, _remote_m0_target

        operation = service.reserve_remote_m0(
            dataset_id, input_profile="public_lite",
            request_bytes=_remote_m0_request("8" * 32, "9" * 32, 1),
            target=_remote_m0_target(), target_step=1,
        )["operation"]
        assert operation["remote"]["attempts"][-1]["app_phase"] == "not_prepared"
        # Status recovery may already have classified the orphaned reservation as unknown.
        assert service.status()["operation"]["status"] == "interrupted_unknown"
        state = _FakeModalState(store)

        recovered = _controller(service, state).reconcile(
            operation["operation_id"], wait_seconds=0,
        )

        assert recovered["status"] == "failed"
        assert recovered["stage"] == "remote_failed"
        assert recovered["error_code"] == "interrupted_before_provider_prepare"
        assert recovered["remote"]["attempts"][-1]["phase"] == "preflight_failed"
        assert state.stops == state.submits == state.prepares == 0
    finally:
        torch.set_num_threads(previous_threads)


def test_remote_controller_restarts_by_polling_saved_handle_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=1)
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
        assert recovered["status"] == "completed"
        assert recovered["checkpoint_step"] == 1
        assert state.submits == state.prepares == 1
        operation = service.status()["operation"]
        attempt = operation["remote"]["attempts"][-1]
        assert attempt["runtime_evidence_sha256"] == hashlib.sha256(
            state.runtime_evidence.to_bytes(),
        ).hexdigest()
        assert attempt["provider_result_sha256"]

        assert recovered["model_id"] and recovered["evaluation_id"]
        assert recovered["remote"]["attempts"][-1]["core_finalizer_attempt_seconds"] >= 0
        assert (store.get_manifest(recovered["evaluation_id"])
                .parameters.value()["scoring_runtime"] == {
                    "mode": "weights_only", "device": "cpu",
                })
    finally:
        torch.set_num_threads(previous_threads)


def test_partial_checkpoint_is_accepted_and_resumed_through_real_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(tmp_path, monkeypatch, steps=2)
    torch, previous_threads, service, store, _owner, _producer, run, _config, _, _ = case
    try:
        from spireagent.workbench.local_m0_remote import ModalM0Settings

        state = _FakeModalState(store, ready=True)
        operation = _controller(
            service, state,
            ModalM0Settings("test-account", "test-env", "im-test-image"),
        ).start(run.artifact_id, 1, wait_seconds=0)
        assert operation["status"] == "paused"
        assert operation["checkpoint_step"] == 1
        attempt = operation["remote"]["attempts"][-1]
        assert attempt["validated_checkpoint_id"] == operation["checkpoint_id"]
        assert attempt["provider_result_sha256"]
        assert attempt["runtime_evidence_sha256"]
        assert service.status()["operation"]["status"] == "paused"
        resumed = _controller(service, state).resume(
            operation["operation_id"], 2, wait_seconds=0,
        )
        assert resumed["status"] == "completed"
        assert resumed["checkpoint_step"] == 2
        assert resumed["model_id"] and resumed["evaluation_id"]
        attempts = resumed["remote"]["attempts"]
        assert len(attempts) == 2
        assert attempts[-1]["resume_checkpoint_id"] == operation["checkpoint_id"]
        assert attempts[-1]["validated_checkpoint_id"] == resumed["checkpoint_id"]
        assert state.submits == state.prepares == 2
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


def _real_prepare_intent(case):
    from spireagent.workbench.local_m0_remote import ModalM0Settings
    from stpd.workers.token_remote_update import prepare_token_remote_update

    _, _, service, store, owner, producer, run, _, operation_id, dataset_id = case
    settings = ModalM0Settings("ac-account01", "test-env", "im-test-image")
    state = _FakeModalState(store, canonical_account_id=settings.account_id)
    controller = _controller(service, state, settings)
    request = prepare_token_remote_update(
        store, owner, run.artifact_id, producer, operation_id, 1, attempt_id="e" * 32,
    )
    spec = controller._spec(request, settings)
    service.reserve_remote_m0(
        dataset_id, input_profile="public_lite", request_bytes=request.to_bytes(),
        target=controller._target(spec, request.config.steps), target_step=1,
    )
    service.persist_remote_attempt_spec(operation_id, spec.to_bytes())
    assert service.status()["operation"]["status"] == "interrupted_unknown"
    return request, spec, settings


def _stopped_state(store, *, canonical_account_id="ac-account01"):
    from spireagent.json_boundary import BoundaryError

    return _FakeModalState(
        store, canonical_account_id=canonical_account_id, prepare_stopped=True,
        lookup_error=BoundaryError("modal_m0", "deployed_target_identity_unavailable"),
    )


def _stopped_receipt(spec):
    import time

    from stpd.cloud_jobs.m0_modal import ModalM0Provider

    return {
        "schema": "stpd/local-modal-m0-prepare-stopped-receipt-v1",
        "observed_at_unix_ns": time.time_ns(), "attempt_id": spec.attempt_id,
        "attempt_spec_sha256": hashlib.sha256(spec.to_bytes()).hexdigest(),
        "request_sha256": spec.request_sha256,
        "canonical_account_id": spec.account_id, "pinned_account_id": spec.account_id,
        "environment_name": spec.environment_name, "app_name": spec.app_name,
        "app_id": "ap-test-app", "app_version": 1,
        "deployment_tag": ModalM0Provider._deployment_tag(spec),
        "function_id": "fu-test-function",
        "stop_confirmation": {
            "app_id": "ap-test-app", "app_state": "stopped", "active_tasks": 0,
            "active_containers": 0, "confirmed": True,
        },
        "lookup_result": "exact_once_deployed_app_stopped",
        "submit_boundary": "no_app_ref_handle_or_submit_intent",
    }


def test_stopped_before_submit_closes_and_reuses_existing_one_use_permit_without_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from spireagent.json_boundary import BoundaryError
    from stpd.workers.token_remote_update import prepare_token_remote_update

    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, owner, producer, run, _, op, dataset_id = case
    try:
        request, spec, settings = _real_prepare_intent(case)
        state = _stopped_state(store)
        controller = _controller(service, state, settings)
        closed = controller.reconcile(op, wait_seconds=0)
        assert closed["status"] == "failed" and closed["stage"] == "remote_failed"
        assert closed["error_code"] == "provider_app_stopped_before_submit"
        latest = closed["remote"]["attempts"][-1]
        receipt = latest["recovery_receipt"]
        expected = _stopped_receipt(spec)
        expected["observed_at_unix_ns"] = receipt["observed_at_unix_ns"]
        assert receipt == expected
        assert latest["phase"] == "preflight_failed" and latest["app_phase"] == "prepare_intent"
        assert latest["provider_terminal"] is False
        assert latest["stop_confirmation"] == receipt["stop_confirmation"]
        evidence = service.remote_provider_evidence(op)
        assert evidence["attempt_spec_bytes"] == spec.to_bytes()
        assert evidence["app_ref_bytes"] is evidence["handle_bytes"] is None
        journal, _ = service._paths(owner)
        closed_bytes = journal.read_bytes()
        assert controller.reconcile(op, wait_seconds=0) == closed
        assert journal.read_bytes() == closed_bytes
        assert state.lookups == state.stop_inspections == 1
        assert state.prepares == state.submits == state.stops == 0
        with pytest.raises(BoundaryError, match="remote_prepare_stop_unavailable"):
            service.record_remote_prepare_stopped(
                op, expected_attempt_spec_sha256=receipt["attempt_spec_sha256"],
                recovery_receipt=receipt,
            )
        assert journal.read_bytes() == closed_bytes
        retired = controller.retire_preflight_failure(op)
        assert retired["error_code"] == "provider_app_stopped_before_submit"
        assert store.blobs.get(retired["archive_ref"]) == closed_bytes
        assert controller.retire_preflight_failure(op) == retired
        context = service.remote_preflight_retry_context()
        assert context["attempt_spec_bytes"] == spec.to_bytes()
        assert context["request_bytes"] == request.to_bytes()
        assert context["canonical_account_id"] == settings.account_id
        assert not journal.exists()
        new_request = prepare_token_remote_update(
            store, owner, run.artifact_id, producer, op, 1, attempt_id="a" * 32,
        )
        new_spec = controller._spec(new_request, settings)
        reserved = service.reserve_remote_m0(
            dataset_id, input_profile="public_lite", request_bytes=new_request.to_bytes(),
            target=controller._target(new_spec, 1), target_step=1,
            attempt_spec_bytes=new_spec.to_bytes(),
        )["operation"]
        assert reserved["previous_remote_failure"]["archive_ref"] == retired["archive_ref"]
        assert reserved["previous_remote_failure"]["error_code"] == retired["error_code"]
        assert service.remote_preflight_retry_context() is None
        assert state.prepares == state.submits == 0
        with pytest.raises(BoundaryError):
            controller.retire_preflight_failure(op)
    finally:
        torch.set_num_threads(previous_threads)


@pytest.mark.parametrize("field,value", [
    ("submit_intent_at_unix_ns", 0), ("submit_intent_at_unix_ns", False),
    ("submit_intent_at_unix_ns", 1), ("submit_intent_at_unix_ns", "intent"),
    ("handle_ref", "present"), ("app_ref_ref", "present"),
    ("provider_terminal", True), ("provider_terminal_observed_at_unix_ns", 1),
    ("provider_result_ref", "present"), ("provider_result_sha256", "f" * 64),
    ("checkpoint_candidate_sha256", "f" * 64), ("validated_checkpoint_id", "f" * 64),
    ("resume_checkpoint_id", "f" * 64), ("runtime_evidence_ref", "present"),
    ("local_acceptance_started_at_unix_ns", 1),
    ("operation.checkpoint_id", "f" * 64), ("operation.checkpoint_step", 1),
    ("operation.result_id", "f" * 64), ("operation.model_id", "f" * 64),
    ("operation.evaluation_id", "f" * 64), ("operation.completed_at_unix_ns", 1),
])
def test_owner_locked_stopped_recovery_rejects_any_submitted_or_result_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value,
):
    from spireagent.json_boundary import BoundaryError

    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, _, owner, _, _, _, op, _ = case
    try:
        _, spec, _ = _real_prepare_intent(case)
        journal, _ = service._paths(owner)
        before = journal.read_bytes()
        current = json.loads(before)
        if field.startswith("operation."):
            current[field.removeprefix("operation.")] = value
        else:
            current["remote"]["attempts"][-1][field] = value
        # Inject the reread under the owner lock to exercise the transition guard
        # even when the outer journal parser would also reject a malformed state.
        monkeypatch.setattr(service, "_read", lambda _path, _identity: current)
        with pytest.raises(BoundaryError, match="remote_prepare_stop_unproven"):
            service.record_remote_prepare_stopped(
                op, expected_attempt_spec_sha256=hashlib.sha256(spec.to_bytes()).hexdigest(),
                recovery_receipt=_stopped_receipt(spec),
            )
        assert journal.read_bytes() == before
    finally:
        torch.set_num_threads(previous_threads)


@pytest.mark.parametrize("field,value", [
    ("attempt_spec_sha256", "f" * 64), ("request_sha256", "f" * 64),
    ("app_id", "ap-foreign"), ("app_name", "foreign"),
    ("deployment_tag", "wrong"), ("app_version", 2),
    ("canonical_account_id", "ac-foreign"), ("pinned_account_id", "ac-foreign"),
    ("environment_name", "foreign"), ("function_id", "not-a-function"),
    ("stop_confirmation", {"confirmed": False}),
])
def test_owner_stopped_receipt_requires_complete_same_spec_identity_and_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value,
):
    from spireagent.json_boundary import BoundaryError

    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, _, owner, _, _, _, op, _ = case
    try:
        _, spec, _ = _real_prepare_intent(case)
        journal, _ = service._paths(owner)
        before = journal.read_bytes()
        receipt = _stopped_receipt(spec)
        receipt[field] = value
        with pytest.raises(BoundaryError, match="remote_prepare_stop_unproven"):
            service.record_remote_prepare_stopped(
                op, expected_attempt_spec_sha256=hashlib.sha256(spec.to_bytes()).hexdigest(),
                recovery_receipt=receipt,
            )
        assert journal.read_bytes() == before
    finally:
        torch.set_num_threads(previous_threads)


def test_controller_rejects_foreign_stop_spec_and_keeps_unknown_without_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from spireagent.json_boundary import BoundaryError

    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, owner, _, _, _, op, _ = case
    try:
        _, _, settings = _real_prepare_intent(case)
        state = _stopped_state(store)
        state.stop_proof_transform = lambda proof: replace(
            proof, spec=replace(proof.spec, request_sha256="0" * 64),
        )
        journal, _ = service._paths(owner)
        before = journal.read_bytes()
        controller = _controller(service, state, settings)
        assert controller.reconcile(op, wait_seconds=0)["status"] == "interrupted_unknown"
        assert journal.read_bytes() == before
        assert state.submits == state.prepares == state.stops == 0
        with pytest.raises(BoundaryError, match="remote_preflight_retirement_unavailable"):
            controller.retire_preflight_failure(op)
    finally:
        torch.set_num_threads(previous_threads)


def test_submitted_call_failure_cannot_use_stopped_prepare_recovery_or_retry_permit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.local_m0_remote import ModalM0Settings
    from stpd.cloud_jobs.m0_modal import M0AttemptSpec

    case = _case(tmp_path, monkeypatch, steps=1)
    torch, previous_threads, service, store, owner, _, run, _, _, _ = case
    try:
        state = _FakeModalState(store, ready=False)
        controller = _controller(
            service, state, ModalM0Settings("ac-account01", "test-env", "im-test-image"),
        )
        unknown = controller.start(run.artifact_id, 1, wait_seconds=0)
        op = unknown["operation_id"]
        assert state.submits == 1 and unknown["status"] == "interrupted_unknown"
        evidence = service.remote_provider_evidence(op)
        spec = M0AttemptSpec.from_bytes(evidence["attempt_spec_bytes"])
        journal, _ = service._paths(owner)
        before = journal.read_bytes()
        with pytest.raises(BoundaryError, match="remote_prepare_stop_unproven"):
            service.record_remote_prepare_stopped(
                op, expected_attempt_spec_sha256=hashlib.sha256(spec.to_bytes()).hexdigest(),
                recovery_receipt=_stopped_receipt(spec),
            )
        assert journal.read_bytes() == before
        service.record_remote_observation(
            op, state="failed", provider_terminal=True, error_code="call_terminal_failure",
        )
        failed = service.record_remote_stop_confirmation(op, {
            "app_id": "ap-test-app", "app_state": "stopped", "active_tasks": 0,
            "active_containers": 0, "confirmed": True,
        })
        assert failed["status"] == "failed"
        failed_bytes = journal.read_bytes()
        with pytest.raises(BoundaryError, match="remote_preflight_retirement_unavailable"):
            controller.retire_preflight_failure(op)
        assert journal.read_bytes() == failed_bytes
        assert service.remote_preflight_retry_context() is None
    finally:
        torch.set_num_threads(previous_threads)
