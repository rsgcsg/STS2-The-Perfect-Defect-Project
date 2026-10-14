"""Original application journal lifecycle with pure provider/domain seams.

These fixtures execute no tensor code, real data, provider SDK or paid allocation.
They do not establish numeric validity or cloud stop truth; those are separate gates.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    NotStarted,
    PreparedTrainingAttempt,
    StopInspection,
    TrainingAppRef,
    TrainingCallHandle,
    TrainingClaim,
    TrainingPlacement,
    TrainingSliceLimits,
    Unknown,
)
from spireagent.training_transport import (
    CandidateArtifact,
    ProjectionPart,
    encode_candidate,
    encode_projection,
)
from spireagent.workbench import local_training as module
from spireagent.workbench.developer import ProjectConfig, RemoteTrainingConfiguration, combination
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.remote_training_operation import (
    ApplicationTrainingAttemptOwner,
    ConfiguredTrainingTarget,
    RemotePreparedWorkload,
    RemoteTrainingBinding,
)
from spireagent.workbench.trusted_recipes import STRUCTURED_SCOPED_RECIPE

PRODUCER = Producer("https://example.test/source", "a" * 40, "b" * 64)


def target():
    return ConfiguredTrainingTarget(
        "modal-cpu",
        STRUCTURED_SCOPED_RECIPE,
        "structured-m2-slice-v1",
        TrainingPlacement("modal", "ac-test", "test", "im-test", "none", "cpu"),
        FrozenObject.of(
            {"device": "cpu", "dtype": "float32", "torch_version": "test", "python_version": "test"}
        ),
        PRODUCER,
        "c" * 64,
        "d" * 64,
        TrainingSliceLimits(2, 2, 2, 1, 4096, 4096, 4096, 4, 2000, 8192, "e" * 64, 100),
        200,
        1,
    )


class Domain:
    accepted = 0
    reject = False

    def preflight_request(self, store, owner, request):
        assert request.source_id == "1" * 64

    def preflight_remote(self, request, selected_target):
        assert request.placement_id == selected_target.placement_id

    def training_plan(self, store, owner, request, prepared_dataset=None):
        value = {
            "schema": "stpd/structured-training-plan-v1",
            "updates_per_epoch": 2,
            "epochs": 2,
            "update_cap": 1000,
            "planned_optimizer_updates": 4,
            "chunk_count_per_epoch": 2,
            "planned_boundaries": 4,
            "source_sha256": "9" * 64,
            "tbptt_advances": 4,
            "distinct_N_basis": "qualified_original_train_run_and_capture_identity",
            "progress_basis": (
                "actual_cumulative_optimizer_updates_label_uses_observations_boundaries"
            ),
            "data_counts": {
                "distinct_supervised_N": 3,
                "contexts": 5,
                "unlabelled_contexts": 2,
                "excluded_N": None,
                "captured_readiness_dispositions": 0,
            },
            "whole_cohort_target": {
                "scope": "one_full_frozen_selected_TRAIN_pass",
                "planned": True,
            },
        }
        value["plan_sha256"] = hashlib.sha256(json_bytes(value)).hexdigest()
        return value

    def prepare_remote(self, service, path, identity, owner, store, selected_target, *, budget):
        budget.assert_preparation_budget()
        operation = service._read(path, owner.identity)
        service._advance(path, identity, use_state="reserved", stage="preparing_run")
        run_id = (
            operation.get("run_id")
            or hashlib.sha256(("run:" + operation["operation_id"]).encode()).hexdigest()
        )
        input_id = operation.get("input_id") or "3" * 64
        service._advance(path, identity, run_id=run_id, input_id=input_id, stage="training")
        ids = {
            "run_id": run_id,
            "training_input_id": input_id,
            "operation_id": identity,
            "attempt_id": operation["attempt_id"],
        }
        request = json_bytes({**ids, "device": selected_target.placement.device})
        projection = encode_projection(
            ids,
            (ProjectionPart("training_projection", b"fixture"),),
            selected_target.limits.max_projection_bytes,
        )
        prepared = PreparedTrainingAttempt(
            **ids,
            request_sha256=hashlib.sha256(request).hexdigest(),
            projection_sha256=hashlib.sha256(projection).hexdigest(),
            producer=selected_target.producer,
            code_closure_sha256=selected_target.code_closure_sha256,
            worker_capability=selected_target.worker_capability,
            runtime=selected_target.runtime,
            placement=selected_target.placement,
            limits=selected_target.limits,
        )
        return RemotePreparedWorkload(prepared, request, projection)

    def accept_remote_candidate(self, store, operation, candidate, workload, *, budget):
        assert budget.remaining_seconds(workload.prepared.identity) > 0
        proof = operation["remote"]["stop_inspection"]
        assert operation["remote"]["provider_terminal"] is True
        assert operation["writer_terminal"] is True
        assert proof["state"] == "stopped" and proof["running_tasks"] == 0
        assert proof["running_containers"] == 0
        workload.prepared.bind_payloads(workload.request_bytes, workload.projection_bytes)
        if self.reject:
            raise BoundaryError("domain", "checkpoint_invalid")
        self.accepted += 1
        updates = operation.get("progress", {}).get("completed", 0) + 2
        return {
            "state": "paused",
            "run_id": operation["run_id"],
            "attempt_id": operation["attempt_id"],
            "checkpoint_id": "4" * 64,
            "result_id": None,
            "model_id": None,
            "optimizer_updates": updates,
            "accepted_counters": {
                "optimizer_updates": updates,
                "label_uses": updates // 2 * 3,
                "observations": updates // 2 * 5,
                "boundaries": updates,
                "epoch": updates // 2,
                "whole_cohort_passes": updates // 2,
            },
            "checkpoint_progress": {
                "checkpoint_id": "4" * 64,
                "optimizer_updates": updates,
                "boundaries": updates,
                "epoch": updates // 2,
            },
        }

    def verify_remote_resume(self, store, operation, checkpoint_id):
        assert operation["writer_terminal"] is True
        assert checkpoint_id == operation["checkpoint_id"]


class Provider:
    def __init__(self):
        self.prepare_calls = 0
        self.submit_calls = 0
        self.stop_confirmed = True
        self.unknown_submit = False
        self.ref = self.handle = self.attempt = None
        self.cancel_entered = threading.Event()
        self.release_poll = threading.Event()
        self.hold_poll = False

    def bind(self, owner):
        self.owner = owner
        return self

    def prepare(self, attempt):
        claim = self.owner.claim_preparation(attempt)
        if claim is TrainingClaim.WITHHELD:
            return NotStarted(attempt.identity, "prepare", "owner_control_withheld")
        assert claim is TrainingClaim.CLAIMED
        self.prepare_calls += 1
        self.attempt = attempt
        self.ref = TrainingAppRef(
            attempt.identity,
            "ap-test" + str(self.prepare_calls),
            "fu-test" + str(self.prepare_calls),
            attempt.app_name,
            attempt.placement.account_id,
            attempt.placement.environment,
            attempt.placement.image_object_id,
        )
        self.owner.persist_app_ref(attempt, self.ref)
        return self.ref

    def recover_preparation(self, attempt):
        return self.ref or Unknown(attempt.identity, "original_app_not_resolved")

    def submit_once(self, attempt, request, projection, ref):
        claim = self.owner.claim_submission_once(attempt, ref)
        if claim is TrainingClaim.WITHHELD:
            return NotStarted(attempt.identity, "submit", "owner_control_withheld")
        assert claim is TrainingClaim.CLAIMED
        attempt.bind_payloads(request, projection)
        self.submit_calls += 1
        self.handle = TrainingCallHandle(ref, "fc-test" + str(self.submit_calls))
        if self.unknown_submit:
            return Unknown(attempt.identity, "submit_response_lost", ref)
        self.owner.persist_call_handle(attempt, self.handle)
        return self.handle

    def candidate(self, attempt):
        manifest = Manifest(
            "checkpoint", attempt.producer, parents=(Parent("run", attempt.run_id),)
        )
        raw = encode_candidate(attempt, (CandidateArtifact(manifest, ()),))
        return CandidateResult(
            self.handle,
            raw,
            "slice_boundary",
            attempt.runtime,
            FrozenObject.of({"optimizer_updates": 2, "boundaries": 2}),
        )

    def poll(self, attempt, handle):
        if self.hold_poll:
            self.cancel_entered.set()
            assert self.release_poll.wait(3)
        return self.candidate(attempt)

    def reconcile(self, attempt, ref, handle=None):
        assert ref == self.ref
        if handle is None:
            self.owner.persist_call_handle(attempt, self.handle)
        return self.candidate(attempt)

    def cancel(self, attempt, handle):
        return True

    def stop_and_inspect(self, attempt, ref):
        return StopInspection(
            ref,
            "stopped" if self.stop_confirmed else "not_stopped",
            0 if self.stop_confirmed else 1,
            0 if self.stop_confirmed else 1,
        )


def ready(tmp_path, monkeypatch):
    owner = SimpleNamespace(path=tmp_path / "curation.sqlite", identity=("fixture",))
    config = ProjectConfig(tmp_path, "", "", None, {})
    domain, provider = Domain(), Provider()
    binding = RemoteTrainingBinding(target(), provider.bind)
    service = LocalTrainingService(config, remote_bindings=(binding,))
    monkeypatch.setattr(service, "_selected", lambda: (owner, object(), None))
    monkeypatch.setattr(module, "source_identity", lambda _: PRODUCER)
    import spireagent.workbench.trusted_recipes as registry

    monkeypatch.setattr(registry, "recipe_adapter", lambda _: domain)
    request = TrainingRequest(
        "5" * 32, STRUCTURED_SCOPED_RECIPE, "1" * 64, {}, "modal-cpu", {"wall_seconds": 60}
    )
    return service, request, owner, provider, domain, binding


def settle(service):
    service._thread.join(5)
    assert not service._thread.is_alive()
    return service.status()["operation"]


def restart(service, owner, binding, monkeypatch):
    new = LocalTrainingService(service.config, remote_bindings=(binding,))
    monkeypatch.setattr(new, "_selected", service._selected)
    return new


def test_default_unavailable_and_legacy_schema_unchanged(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    unconfigured = LocalTrainingService(service.config)
    monkeypatch.setattr(unconfigured, "_selected", service._selected)
    with pytest.raises(BoundaryError, match="remote_placement_unavailable"):
        unconfigured.start(request)
    assert not (tmp_path / OPERATION_FILE).exists()
    assert provider.submit_calls == 0
    assert request.to_dict()["schema"] == "spireagent/training-request-v2"
    assert replace(request, placement_id="local-cpu").to_dict()["schema"].endswith("-v1")


def test_slice_through_original_owner_and_idempotency(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    started = service.start(request)["operation"]
    paused = settle(service)
    assert paused["status"] == "paused" and paused["worker_state"] == "terminal"
    assert paused["attempt_id"] == started["attempt_id"]
    assert domain.accepted == 1 and provider.submit_calls == 1
    duplicate = service.start(request)["operation"]
    assert duplicate["attempt_id"] == paused["attempt_id"] and provider.submit_calls == 1
    assert paused["remote_execution"]["cost_reserved_microusd"] == 100


def test_mutex_recovery_cannot_replace_unstopped_remote_writer(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    provider.stop_confirmed = False
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown" and domain.accepted == 0
    assert list(tmp_path.glob("*.remote-candidate"))
    new = restart(service, owner, binding, monkeypatch)
    reconciled = new.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert reconciled["attempt_id"] == unknown["attempt_id"]
    assert reconciled["worker_state"] == "unknown" and provider.submit_calls == 1
    with pytest.raises(BoundaryError, match="remote_prior_terminal_proof_required"):
        new.resume(
            unknown["operation_id"], unknown["attempt_id"], "4" * 64, "6" * 32, request.limits
        )
    provider.stop_confirmed = True
    paused = new.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert paused["status"] == "paused" and domain.accepted == 1
    assert paused["attempt_id"] == unknown["attempt_id"] and provider.submit_calls == 1


def test_unknown_submit_retains_original_ref_and_recovers_exact_handle(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    provider.unknown_submit = True
    service.start(request)
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown"
    new = restart(service, owner, binding, monkeypatch)
    before = new._read(tmp_path / OPERATION_FILE, owner.identity)
    assert before["remote"]["submit_claimed"] is True
    assert before["remote"]["app_ref"] == provider.ref.to_dict()
    assert "call_handle" not in before["remote"]
    paused = new.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert paused["status"] == "paused" and provider.submit_calls == 1
    after = new._read(tmp_path / OPERATION_FILE, owner.identity)
    assert after["remote"]["call_handle"] == provider.handle.to_dict()


def test_pause_and_cancel_ack_wait_for_slice_and_stop(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    provider.hold_poll = True
    started = service.start(request)["operation"]
    assert provider.cancel_entered.wait(3)
    pause = service.pause(started["operation_id"], started["attempt_id"])["operation"]
    assert pause["status"] == "pending" and domain.accepted == 0
    cancel = service.cancel(started["operation_id"], started["attempt_id"])["operation"]
    assert cancel["status"] == "pending" and cancel["selected_result"] is False
    provider.release_poll.set()
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled" and cancelled["worker_state"] == "terminal"
    assert cancelled["selected_result"] is False


def test_domain_rejection_keeps_candidate_unselected(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    domain.reject = True
    service.start(request)
    failed = settle(service)
    assert failed["status"] == "interrupted_unknown"
    assert failed["worker_state"] == "terminal" and domain.accepted == 0
    assert failed["validation_state"] == "not_completed" and "checkpoint_id" not in failed


def test_durable_claim_failure_prevents_provider_side_effect(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    real_write = ApplicationTrainingAttemptOwner.write

    def fail_claim(port, value):
        if value["remote"]["prepare_claimed"]:
            raise OSError("synthetic durable failure")
        real_write(port, value)

    monkeypatch.setattr(ApplicationTrainingAttemptOwner, "write", fail_claim)
    service.start(request)
    failed = settle(service)
    assert failed["status"] == "interrupted_unknown"
    assert provider.prepare_calls == provider.submit_calls == 0


def test_unbound_failure_and_foreign_stop_do_not_prove_terminal(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    provider.unknown_submit = True
    service.start(request)
    unknown = settle(service)
    monkeypatch.setattr(
        provider,
        "reconcile",
        lambda attempt, ref, handle=None: KnownFailure(attempt.identity, "worker_failed"),
    )
    new = restart(service, owner, binding, monkeypatch)
    with pytest.raises(BoundaryError, match="remote_failure_binding_mismatch"):
        new.reconcile(unknown["operation_id"], unknown["attempt_id"])
    assert domain.accepted == 0 and provider.submit_calls == 1


def test_explicit_resume_preserves_run_and_cumulative_cost_without_new_allowance(
    tmp_path, monkeypatch
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    service.start(request)
    paused = settle(service)
    resumed = service.resume(
        paused["operation_id"],
        paused["attempt_id"],
        paused["checkpoint_id"],
        "6" * 32,
        request.limits,
    )["operation"]
    assert resumed["operation_id"] == paused["operation_id"]
    assert resumed["attempt_id"] != paused["attempt_id"]
    second = settle(service)
    assert second["run_id"] == paused["run_id"]
    assert second["progress"]["completed"] == 4
    assert second["remote_execution"]["cost_reserved_microusd"] == 200
    assert "resume" not in second["supported_actions"]
    journal = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert (
        journal["attempts"][0]["remote"]["app_ref"]["prepared_id"]
        != journal["remote"]["app_ref"]["prepared_id"]
    )
    with pytest.raises(BoundaryError, match="cumulative_remote_cost_exhausted"):
        service.resume(
            second["operation_id"],
            second["attempt_id"],
            second["checkpoint_id"],
            "7" * 32,
            request.limits,
        )
    assert provider.submit_calls == 2


def test_stopped_app_without_call_terminal_never_enables_resume(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    provider.unknown_submit = True
    service.start(request)
    unknown = settle(service)
    monkeypatch.setattr(
        provider,
        "reconcile",
        lambda attempt, ref, handle=None: Unknown(
            attempt.identity, "call_outcome_unknown", ref, handle
        ),
    )
    new = restart(service, owner, binding, monkeypatch)
    kept = new.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    assert kept["remote_execution"]["stop_inspection"]["state"] == "stopped"
    assert kept["remote_execution"]["provider_terminal"] is False
    assert kept["worker_state"] == "unknown" and domain.accepted == 0
    assert "resume" not in kept["supported_actions"] and provider.submit_calls == 1


def test_foreign_stop_receipt_never_enables_acceptance(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)

    def foreign_stop(attempt, ref):
        foreign = replace(ref, app_id="ap-other")
        return StopInspection(foreign, "stopped", 0, 0)

    monkeypatch.setattr(provider, "stop_and_inspect", foreign_stop)
    service.start(request)
    rejected = settle(service)
    assert rejected["worker_state"] == "unknown" and domain.accepted == 0
    assert rejected["error"]["code"] == "remote_stop_binding_mismatch"
    assert provider.submit_calls == 1


def test_remote_runtime_mismatch_and_cross_version_request_reject_before_side_effect(
    tmp_path, monkeypatch
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError, match="remote_runtime_binding_mismatch"):
        replace(binding.target, runtime=FrozenObject.of({"device": "cuda:0", "dtype": "float32"}))
    mismatched = request.to_dict()
    mismatched["schema"] = "spireagent/training-request-v1"
    with pytest.raises(BoundaryError, match="invalid_training_request"):
        service.start(mismatched)
    assert not (tmp_path / OPERATION_FILE).exists() and provider.submit_calls == 0


def test_monitor_reads_preserve_plan_progress_timestamps_ids_and_unknown_meter(
    tmp_path, monkeypatch
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    service.start(request)
    paused = settle(service)
    assert paused["progress"] == {"unit": "optimizer_update", "completed": 2, "total": 4}
    assert paused["training_plan"]["update_cap"] == 1000
    assert paused["training_plan"]["data_counts"]["distinct_supervised_N"] == 3
    assert paused["accepted_counters"]["label_uses"] == 3
    assert paused["accepted_counters"]["observations"] == 5
    assert paused["checkpoint_progress"]["optimizer_updates"] == 2
    remote = paused["remote_execution"]
    assert "qualified_target" not in remote and remote["provider_phase"] == "accepted"
    assert remote["observed_runtime"] == binding.target.runtime.value()
    assert remote["qualification"]["state"] == "not_established_by_configuration"
    assert remote["cost"]["metered_microusd"] is None and remote["cost"]["meter_state"] == "unknown"
    assert remote["cost"]["reserved_maximum_microusd"] == 100
    path = tmp_path / OPERATION_FILE
    original = path.read_bytes()
    new = restart(service, owner, binding, monkeypatch)
    for _ in range(3):
        observed = new.status(paused["operation_id"])["operation"]
        new.capabilities()
        assert observed["remote_execution"]["provider_refs"] == remote["provider_refs"]
        assert observed["last_progress_at"] == paused["last_progress_at"]
        assert observed["last_checkpoint_at"] == paused["last_checkpoint_at"]
        assert observed["observed_at"] >= paused["observed_at"]
    reconciled = new.reconcile(paused["operation_id"], paused["attempt_id"])["operation"]
    assert reconciled["last_progress_at"] == paused["last_progress_at"]
    assert reconciled["last_checkpoint_at"] == paused["last_checkpoint_at"]
    assert domain.accepted == 1
    assert path.read_bytes() == original and provider.submit_calls == 1


def test_running_slice_has_original_call_refs_but_no_invented_live_progress(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    provider.hold_poll = True
    started = service.start(request)["operation"]
    assert provider.cancel_entered.wait(3)
    try:
        observed = service.status(started["operation_id"])["operation"]
        remote = observed["remote_execution"]
        assert remote["provider_phase"] == "call_bound"
        assert remote["provider_refs"]["call_id"] == provider.handle.call_id
        assert remote["within_slice_progress"] == {
            "state": "unknown",
            "optimizer_updates": None,
            "boundaries": None,
        }
        assert remote["observed_runtime"] is None and observed["last_progress_at"] is None
        assert observed["progress"]["completed"] == 0 and observed["progress"]["total"] == 4
    finally:
        provider.release_poll.set()
        settle(service)


def test_operator_configuration_builds_ordinary_application_without_provider_calls(
    tmp_path, monkeypatch
):
    import json

    from spireagent.workbench.developer_server import Application

    config = ProjectConfig(
        tmp_path,
        "",
        "",
        None,
        combination(),
        remote_training=RemoteTrainingConfiguration((target(),)),
    )
    path = tmp_path / "project.json"
    path.write_bytes(json_bytes(config.to_dict()))
    loaded = ProjectConfig.load(path)
    assert loaded == config
    import builtins

    original_import = builtins.__import__

    def metadata_import(name, *args, **kwargs):
        if (
            name == "torch"
            or name.startswith("torch.")
            or name == "modal"
            or name.startswith("modal.")
        ):
            raise AssertionError("runtime import during application monitor construction")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", metadata_import)
    app = Application(loaded, config_path=path)
    capabilities = app.local_training.capabilities()
    remote = next(item for item in capabilities["placements"] if item["remote"])
    assert remote["placement_id"] == "modal-cpu"
    assert remote["readiness"] in {
        "configured_requires_exact_runtime_receipt",
        "remote_recipe_unavailable",
    }
    assert remote["available"] is (remote["readiness"] != "remote_recipe_unavailable")
    assert len(app.local_training._remote_bindings) == 1
    assert not list(tmp_path.glob("*.remote-*")) and not (tmp_path / OPERATION_FILE).exists()
    raw = json.loads(path.read_bytes())
    raw["remote_training"]["factory"] = "arbitrary.module"
    path.write_bytes(json_bytes(raw))
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        ProjectConfig.load(path)
    raw = config.to_dict()
    raw["remote_training"]["targets"][0]["runtime"]["token"] = "forbidden"
    path.write_bytes(json_bytes(raw))
    with pytest.raises(BoundaryError, match="secret_metadata"):
        ProjectConfig.load(path)


def test_http_monitor_gets_share_original_ids_without_writes_or_provider_calls(
    tmp_path, monkeypatch
):
    import json
    from urllib.request import Request, urlopen

    from spireagent.workbench.developer_server import create_server

    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    service.start(request)
    paused = settle(service)
    path = tmp_path / OPERATION_FILE
    original = path.read_bytes()
    # Only account/browser authentication is synthetic; the real server and training owner are used.
    app = SimpleNamespace(
        local_training=service,
        account=SimpleNamespace(cookie_name="fixture", cookie="session", csrf="csrf"),
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/local-training/"
    try:
        for route in ("capabilities", "status?operation_id=" + paused["operation_id"], "status"):
            with urlopen(
                Request(url + route, headers={"Cookie": "fixture=session"}), timeout=3
            ) as reply:
                result = json.load(reply)
            if route != "capabilities":
                observed = result["operation"]
                assert observed["attempt_id"] == paused["attempt_id"]
                assert observed["training_plan"] == paused["training_plan"]
                assert observed["accepted_counters"] == paused["accepted_counters"]
                assert (
                    observed["remote_execution"]["provider_refs"]
                    == paused["remote_execution"]["provider_refs"]
                )
                assert observed["last_progress_at"] == paused["last_progress_at"]
        assert path.read_bytes() == original and provider.submit_calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_stale_runtime_binding_rejects_before_pending_or_use(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)

    def reject(request, target):
        raise BoundaryError("local_training", "remote_runtime_binding_mismatch")

    monkeypatch.setattr(domain, "preflight_remote", reject)
    with pytest.raises(BoundaryError, match="remote_runtime_binding_mismatch"):
        service.start(request)
    assert not (tmp_path / OPERATION_FILE).exists()
    assert provider.prepare_calls == provider.submit_calls == 0


def test_frozen_plan_tamper_and_untrusted_request_binding_reject_before_start(
    tmp_path, monkeypatch
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    real_plan = domain.training_plan

    def invalid_plan(*args, **kwargs):
        value = real_plan(*args, **kwargs)
        value["planned_optimizer_updates"] = value["update_cap"]
        return value

    monkeypatch.setattr(domain, "training_plan", invalid_plan)
    with pytest.raises(BoundaryError, match="training_plan_digest_mismatch"):
        service.start(request)
    payload = request.to_dict()
    payload["remote_training"] = {"targets": [binding.target.to_dict()]}
    with pytest.raises(BoundaryError, match="invalid_training_request"):
        service.start(payload)
    assert not (tmp_path / OPERATION_FILE).exists() and provider.submit_calls == 0


def test_missing_remote_recipe_hooks_and_configuration_remain_unavailable(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    monkeypatch.setattr(domain, "prepare_remote", None)
    capability = next(item for item in service.capabilities()["placements"] if item["remote"])
    assert (
        capability["readiness"] == "remote_recipe_unavailable" and capability["available"] is False
    )
    with pytest.raises(BoundaryError, match="remote_recipe_unavailable"):
        service.start(request)
    assert not (tmp_path / OPERATION_FILE).exists() and provider.submit_calls == 0


class FakeClock:
    """Deterministic owner/provider wall, not a real timing or performance claim."""

    def __init__(self):
        self.seconds = 0.0

    def monotonic(self):
        return 1000.0 + self.seconds

    def time(self):
        return 1700000000.0 + self.seconds

    def advance(self, seconds):
        self.seconds += seconds


def use_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "time", clock.time)
    return clock


def test_original_wallet_requires_explicit_cleanup_before_any_provider_effect(
    tmp_path, monkeypatch
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError, match="remote_grant_exceeds_operation_budget"):
        service.start(replace(request, limits={"wall_seconds": 3}))
    assert provider.prepare_calls == provider.submit_calls == 0
    assert not (tmp_path / OPERATION_FILE).exists()
    raw = binding.target.to_dict()
    raw.pop("cleanup_seconds")
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        ConfiguredTrainingTarget.from_dict(raw)


def test_input_preparation_consumes_original_wallet_before_provider_claim(tmp_path, monkeypatch):
    clock = use_clock(monkeypatch)
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    original = domain.prepare_remote

    def expensive_input(*args, budget, **kwargs):
        budget.assert_preparation_budget()
        clock.advance(58)
        budget.assert_preparation_budget()
        return original(*args, budget=budget, **kwargs)

    monkeypatch.setattr(domain, "prepare_remote", expensive_input)
    service.start(request)
    failed = settle(service)
    assert failed["status"] == "failed" and failed["worker_state"] == "terminal"
    assert failed["elapsed_seconds"] == 58
    assert failed["remote_execution"]["provider_phase"] == "not_started"
    assert provider.prepare_calls == provider.submit_calls == 0


def test_deployment_consumes_original_wallet_and_leaves_only_app_cleanup(tmp_path, monkeypatch):
    clock = use_clock(monkeypatch)
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    original_prepare = provider.prepare
    budgets = []

    def delayed_deploy(attempt):
        budgets.append(provider.owner.remaining_seconds(attempt.identity))
        ref = original_prepare(attempt)
        clock.advance(57)
        return ref

    monkeypatch.setattr(provider, "prepare", delayed_deploy)
    service.start(request)
    failed = settle(service)
    assert budgets == [59]
    assert provider.prepare_calls == 1 and provider.submit_calls == 0
    assert failed["elapsed_seconds"] == 57
    assert failed["remote_execution"]["provider_phase"] == "not_started"
    assert failed["remote_execution"]["stop_inspection"]["state"] == "stopped"
    assert failed["remote_execution"]["original_deadline_at"] == 1700000060
    assert failed["remote_execution"]["cost"]["reserved_maximum_microusd"] == 100


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_preprepare_control_withholds_first_claim_without_provider_lookup(
    tmp_path, monkeypatch, action
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()
    original_prepare = provider.prepare

    def barrier(attempt):
        entered.set()
        assert released.wait(3)
        return original_prepare(attempt)

    monkeypatch.setattr(provider, "prepare", barrier)
    service.start(request)
    assert entered.wait(3)
    current = service.status()["operation"]
    ack = getattr(service, action)(current["operation_id"], current["attempt_id"])["operation"]
    assert ack["status"] == "pending"
    released.set()
    stopped = settle(service)
    assert stopped["status"] == ("paused" if action == "pause" else "cancelled")
    assert stopped["worker_state"] == "terminal"
    assert stopped["remote_execution"]["provider_refs"]["app_id"] is None
    assert stopped["remote_execution"]["provider_phase"] == "not_started"
    assert provider.prepare_calls == provider.submit_calls == 0
    journal = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert journal["remote"]["prepare_claimed"] is False
    assert journal["cost_reserved_microusd"] == 0


@pytest.mark.parametrize("action", ["pause", "cancel"])
def test_presubmit_control_stops_original_app_without_fictional_call(tmp_path, monkeypatch, action):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()
    original_submit = provider.submit_once

    def barrier(attempt, raw, projection, ref):
        entered.set()
        assert released.wait(3)
        return original_submit(attempt, raw, projection, ref)

    monkeypatch.setattr(provider, "submit_once", barrier)
    service.start(request)
    assert entered.wait(3)
    current = service.status()["operation"]
    ack = getattr(service, action)(current["operation_id"], current["attempt_id"])["operation"]
    assert ack["status"] == "pending"
    released.set()
    stopped = settle(service)
    assert stopped["status"] == ("paused" if action == "pause" else "cancelled")
    assert stopped["worker_state"] == "terminal"
    assert stopped["remote_execution"]["provider_refs"]["app_id"] == provider.ref.app_id
    assert stopped["remote_execution"]["provider_refs"]["call_id"] is None
    assert stopped["remote_execution"]["stop_inspection"]["running_tasks"] == 0
    assert provider.prepare_calls == 1 and provider.submit_calls == 0
    journal = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert journal["remote"]["submit_claimed"] is False
    assert journal["remote"]["provider_terminal"] is False


def test_preexisting_submit_claim_is_prior_even_after_cancel_control(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    entered, released = threading.Event(), threading.Event()

    def claimed_barrier(attempt, raw, projection, ref):
        assert provider.owner.claim_submission_once(attempt, ref) is TrainingClaim.CLAIMED
        provider.submit_calls += 1
        provider.handle = TrainingCallHandle(ref, "fc-test")
        entered.set()
        assert released.wait(3)
        assert provider.owner.claim_submission_once(attempt, ref) is TrainingClaim.PRIOR
        return Unknown(attempt.identity, "submission_response_lost", ref)

    monkeypatch.setattr(provider, "submit_once", claimed_barrier)
    service.start(request)
    assert entered.wait(3)
    current = service.status()["operation"]
    service.cancel(current["operation_id"], current["attempt_id"])
    released.set()
    unknown = settle(service)
    assert unknown["status"] == "interrupted_unknown" and unknown["worker_state"] == "unknown"
    assert provider.submit_calls == 1
    journal = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert journal["remote"]["submit_claimed"] is True
    assert "not_started_receipt" not in journal["remote"]


def test_deploy_submit_poll_and_cleanup_share_original_clock_then_explicit_control_only(
    tmp_path, monkeypatch
):
    clock = use_clock(monkeypatch)
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    original_prepare, original_submit = provider.prepare, provider.submit_once
    observed = []

    def deployment(attempt):
        ref = original_prepare(attempt)
        clock.advance(55)
        return ref

    def submission(attempt, raw, projection, ref):
        handle = original_submit(attempt, raw, projection, ref)
        clock.advance(1)
        return handle

    def running_poll(attempt, handle):
        observed.append(provider.owner.remaining_seconds(attempt.identity))
        clock.advance(3)
        return None

    def stop(attempt, ref):
        observed.append(provider.owner.remaining_seconds(attempt.identity))
        clock.advance(0.5)
        return StopInspection(ref, "stopped", 0, 0)

    monkeypatch.setattr(provider, "prepare", deployment)
    monkeypatch.setattr(provider, "submit_once", submission)
    monkeypatch.setattr(provider, "poll", running_poll)
    monkeypatch.setattr(provider, "stop_and_inspect", stop)
    service.start(request)
    unknown = settle(service)
    assert observed == [3, 1]
    assert unknown["elapsed_seconds"] == 59.5
    assert unknown["status"] == "interrupted_unknown" and unknown["worker_state"] == "unknown"
    deadline = unknown["remote_execution"]["original_deadline_at"]
    clock.advance(2)
    new = restart(service, owner, binding, monkeypatch)
    finished = new.reconcile(unknown["operation_id"], unknown["attempt_id"])["operation"]
    control = finished["remote_execution"]["control_observation"]
    assert control["allowance_seconds"] == 1
    assert control["training_budget_extended"] is False
    assert control["state"] == "completed" and control["remaining_seconds"] == 0.5
    assert finished["remote_execution"]["original_deadline_at"] == deadline
    assert finished["attempt_id"] == unknown["attempt_id"] and provider.submit_calls == 1
    assert finished["remote_execution"]["cost"]["reserved_maximum_microusd"] == 100
    assert finished["budget_state"]["remaining_wall_seconds"] == 0


def stop_before_submit(service, request, provider, monkeypatch):
    entered, released = threading.Event(), threading.Event()
    original = provider.submit_once

    def barrier(attempt, raw, projection, ref):
        entered.set()
        assert released.wait(3)
        return original(attempt, raw, projection, ref)

    monkeypatch.setattr(provider, "submit_once", barrier)
    service.start(request)
    assert entered.wait(3)
    pending = service.status()["operation"]
    service.cancel(pending["operation_id"], pending["attempt_id"])
    released.set()
    stopped = settle(service)
    monkeypatch.setattr(provider, "submit_once", original)
    return stopped


def test_explicit_new_request_can_retire_only_exact_sealed_no_effect_predecessor(
    tmp_path, monkeypatch
):
    import json

    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    stopped = stop_before_submit(service, request, provider, monkeypatch)
    assert stopped["next_start_precondition"] == {
        "after_completed_operation_id": stopped["operation_id"],
        "explicit_new_intent_required": True,
    }
    assert (
        "start_new" in stopped["supported_actions"] and "resume" not in stopped["supported_actions"]
    )
    path = tmp_path / OPERATION_FILE
    original = path.read_bytes()
    submitted = service.start(
        replace(
            request,
            intent_id="6" * 32,
            after_completed_operation_id=stopped["next_start_precondition"][
                "after_completed_operation_id"
            ],
        )
    )
    assert submitted["operation"]["operation_id"] != stopped["operation_id"]
    successor = settle(service)
    pointer = successor["previous_known_not_started"]
    assert pointer["operation_id"] == stopped["operation_id"]
    assert pointer["run_id"] == stopped["run_id"] and successor["run_id"] != stopped["run_id"]
    archives = list(tmp_path.glob("*.remote-operation-" + stopped["operation_id"]))
    assert len(archives) == 1 and archives[0].read_bytes() == original
    old = json.loads(original)
    assert old["remote"]["not_started"] is True and old["remote"]["submit_claimed"] is False
    assert old["cost_reserved_microusd"] == 100
    assert old["use_state"] == "reserved" and old["writer_terminal"] is True
    assert provider.prepare_calls == 2 and provider.submit_calls == 1


def test_no_effect_successor_requires_exact_predecessor_and_new_intent(tmp_path, monkeypatch):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    stopped = stop_before_submit(service, request, provider, monkeypatch)
    assert service.start(request)["operation"]["operation_id"] == stopped["operation_id"]
    for after in (None, "0" * 32):
        with pytest.raises(BoundaryError, match="explicit_known_no_effect_predecessor_required"):
            service.start(replace(request, intent_id="6" * 32, after_completed_operation_id=after))
    assert provider.prepare_calls == 1 and provider.submit_calls == 0


@pytest.mark.parametrize(
    "defect", ["missing_receipt", "foreign_app", "not_stopped", "unknown", "checkpoint"]
)
def test_unsafe_no_effect_predecessor_cannot_authorize_new_request(tmp_path, monkeypatch, defect):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    stopped = stop_before_submit(service, request, provider, monkeypatch)
    path = tmp_path / OPERATION_FILE
    value = service._read(path, owner.identity)
    port = ApplicationTrainingAttemptOwner(service, path, owner, value)
    if defect == "missing_receipt":
        value["remote"].pop("no_effect_receipt")
    elif defect == "foreign_app":
        value["remote"]["no_effect_receipt"]["record"]["app_ref"]["app_id"] = "ap-other"
    elif defect == "not_stopped":
        value["remote"]["stop_inspection"]["running_tasks"] = 1
    elif defect == "unknown":
        value["status"] = "interrupted_unknown"
    else:
        value["checkpoint_id"] = "4" * 64
    port.write(value)
    with pytest.raises(BoundaryError):
        service.start(
            replace(
                request, intent_id="6" * 32, after_completed_operation_id=stopped["operation_id"]
            )
        )
    assert provider.prepare_calls == 1 and provider.submit_calls == 0


def test_no_effect_successor_requires_released_original_writer(tmp_path, monkeypatch):
    from spireagent.workbench.instance_lock import instance_lock

    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    stopped = stop_before_submit(service, request, provider, monkeypatch)
    _, lock_path = service._paths(owner)
    with instance_lock(lock_path, create=False):
        snapshot = service.status(stopped["operation_id"])["operation"]
        assert (
            snapshot["next_start_precondition"] is None
            and "start_new" not in snapshot["supported_actions"]
        )
        with pytest.raises(BoundaryError, match="operation_in_progress"):
            service.start(
                replace(
                    request,
                    intent_id="6" * 32,
                    after_completed_operation_id=stopped["operation_id"],
                )
            )
    assert provider.prepare_calls == 1 and provider.submit_calls == 0


@pytest.mark.parametrize("marked_not_started", [False, True])
def test_unsealed_failed_remote_without_run_blocks_until_explicit_reconcile(
    tmp_path, monkeypatch, marked_not_started
):
    service, request, owner, provider, domain, binding = ready(tmp_path, monkeypatch)
    original_launch = service._launch_attempt
    # Retain the real admitted journal at the crash boundary before launch.
    monkeypatch.setattr(
        service, "_launch_attempt", lambda held, *args: held.__exit__(None, None, None)
    )
    started = service.start(request)["operation"]
    path = tmp_path / OPERATION_FILE
    value = service._read(path, owner.identity)
    assert value.get("run_id") is None and value["remote"].get("prepared") is None
    value.update(status="failed", writer_terminal=marked_not_started)
    value["remote"]["not_started"] = marked_not_started
    assert value["remote"].get("no_effect_receipt") is None
    ApplicationTrainingAttemptOwner(service, path, owner, value).write(value)
    before = path.read_bytes()
    for after in (None, started["operation_id"]):
        with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
            service.start(replace(request, intent_id="6" * 32, after_completed_operation_id=after))
        assert path.read_bytes() == before
    assert provider.prepare_calls == provider.submit_calls == 0
    recovered = service.reconcile(started["operation_id"], started["attempt_id"])["operation"]
    assert recovered["operation_id"] == started["operation_id"]
    # The command snapshot is taken while holding the writer lock. A later
    # read may offer start_new only after that original lock is released.
    assert recovered["next_start_precondition"] is None
    recovered = service.status(started["operation_id"])["operation"]
    assert recovered["next_start_precondition"] == {
        "after_completed_operation_id": started["operation_id"],
        "explicit_new_intent_required": True,
    }
    sealed = path.read_bytes()
    assert service._read(path, owner.identity)["remote"]["no_effect_receipt"]
    service.reconcile(started["operation_id"], started["attempt_id"])
    assert path.read_bytes() == sealed
    assert provider.prepare_calls == provider.submit_calls == 0
    with pytest.raises(BoundaryError, match="explicit_known_no_effect_predecessor_required"):
        service.start(replace(request, intent_id="6" * 32))
    monkeypatch.setattr(service, "_launch_attempt", original_launch)
    successor = service.start(replace(
        request, intent_id="6" * 32, after_completed_operation_id=started["operation_id"]
    ))["operation"]
    assert successor["operation_id"] != started["operation_id"]
    settle(service)
    archives = list(tmp_path.glob("*.remote-operation-" + started["operation_id"]))
    assert len(archives) == 1 and archives[0].read_bytes() == sealed
    assert provider.prepare_calls == provider.submit_calls == 1


def test_submit_unknown_stops_original_known_app_without_promoting_result(tmp_path, monkeypatch):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    provider.unknown_submit = True
    calls = []
    original_stop = provider.stop_and_inspect
    def stop(attempt, ref):
        calls.append(ref)
        return original_stop(attempt, ref)
    monkeypatch.setattr(provider, "stop_and_inspect", stop)
    service.start(request)
    outcome = settle(service)
    saved = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert calls == [provider.ref]
    assert saved["remote"]["stop_inspection"]["state"] == "stopped"
    assert saved["remote"]["stop_inspection"]["running_tasks"] == 0
    assert saved["remote"]["stop_inspection"]["running_containers"] == 0
    assert saved["remote"]["provider_terminal"] is False and saved["writer_terminal"] is False
    assert outcome["status"] == "interrupted_unknown"
    assert provider.submit_calls == 1 and domain.accepted == 0


@pytest.mark.parametrize("wallet_expired", [False, True])
def test_preparation_unknown_only_stops_durable_original_app_with_existing_wallet(
    tmp_path, monkeypatch, wallet_expired
):
    service, request, owner, provider, domain, _ = ready(tmp_path, monkeypatch)
    calls = []
    original_prepare, original_stop = provider.prepare, provider.stop_and_inspect
    def prepare(attempt):
        ref = original_prepare(attempt)
        if wallet_expired:
            monkeypatch.setattr(provider.owner, "remaining_wallet_seconds", lambda: 0.0)
        return Unknown(attempt.identity, "prepare_response_lost", ref)
    def stop(attempt, ref):
        calls.append(ref)
        return original_stop(attempt, ref)
    monkeypatch.setattr(provider, "prepare", prepare)
    monkeypatch.setattr(provider, "stop_and_inspect", stop)
    service.start(request)
    outcome = settle(service)
    saved = service._read(tmp_path / OPERATION_FILE, owner.identity)
    assert calls == ([] if wallet_expired else [provider.ref])
    assert outcome["status"] == "interrupted_unknown"
    assert saved["remote"]["provider_terminal"] is False and saved["writer_terminal"] is False
    assert provider.submit_calls == 0 and domain.accepted == 0
