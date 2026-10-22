from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from test_cloud_jobs import job_fixture

from spireagent.compute import ComputeSubmissionUnknown, ComputeTerminated, ComputeTermination
from spireagent.hub.database import CURRENT_SCHEMA, Operations
from spireagent.hub.scheduler import Scheduler
from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.contracts import ComputeReceipt
from stpd.cloud_jobs.execution import dispatch, prepare_feature_run
from stpd.cloud_jobs.modal import ModalCall, ModalTarget


class Provider:
    def __init__(self, ops, store, backend, producer):
        self.target = ModalTarget(
            producer, "registry.example/stpd@sha256:" + "d" * 64, timeout_seconds=300
        )
        self.ops, self.store, self.backend = ops, store, backend
        self.calls, self.polls, self.cancels = [], [], []
        self.ready = False
        self.ambiguous = False
        self.cancel_error = False
        self.poll_error = False
        self.altered_receipt = False
        self.missing_output = False

    def submit(self, request):
        rows = self.ops.jobs()
        row = next(row for row in rows if row["attempt_id"] == request.attempt_id)
        durable = self.ops.compute_state(row["id"])
        assert durable["phase"] == "submitting"
        assert durable["request"] == request.to_dict()
        assert durable["handle"] is None  # Publication precedes the external mutation.
        self.calls.append(request)
        if self.ambiguous:
            raise TimeoutError("remote may have accepted; no automatic resubmit")
        return ModalCall(self.target.target_id, request, "fc-" + request.attempt_id)

    def restore_handle(self, value):
        return ModalCall.decode(value)

    def poll(self, handle):
        self.polls.append(handle.call_id)
        if self.poll_error:
            raise BoundaryError("provider", "result_unavailable")
        if not self.ready:
            return None
        receipt = dispatch(
            self.store,
            handle.request,
            self.target.producer,
            backend=self.backend if handle.request.kind == "features" else None,
        )
        if self.missing_output:
            return replace(receipt, output_id="e" * 64)
        return replace(receipt, attempt_id="wrong") if self.altered_receipt else receipt

    def cancel(self, handle):
        self.cancels.append(handle.call_id)
        if self.cancel_error:
            raise TimeoutError("cancellation acknowledgement lost")


@pytest.fixture
def pipeline(tmp_path: Path):
    store, backend, _, job, request = job_fixture(tmp_path)
    ops = Operations(tmp_path / "operations.sqlite")
    job_id = ops.enqueue(
        "feature", job.artifact_id, "feature-1", max_seconds=300, reserved_units=1, budget_limit=10
    )
    provider = Provider(ops, store, backend, request.producer)
    return ops, store, provider, job_id


def test_default_disabled_then_durable_features_request_and_validated_completion(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider) as scheduler:
        assert scheduler.tick(1)["state"] == "launch_disabled"
        assert not provider.calls
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        state = ops.compute_state(job)
        assert state["request"]["kind"] == "features"
        assert state["handle"]["call_id"] == ops.jobs()[0]["provider_ref"]
        provider.ready = True
        assert scheduler.tick(2)["state"] == "completed"
        assert ops.jobs()[0]["status"] == "completed"
        assert scheduler.tick(3)["state"] == "idle"
        assert len(provider.calls) == 1
        assert "lease_token" not in json.dumps(state)


@pytest.mark.parametrize("outcome,cancelled", [("failed", False), ("timeout", False),
                                               ("cancelled", True)])
def test_confirmed_original_process_exit_releases_active_slot_retains_budget_after_restart(
    pipeline, monkeypatch, outcome, cancelled,
):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        old_token = scheduler._tokens[job]
        original = ops.compute_state(job)
        row = ops.jobs()[0]
        handle = provider.restore_handle(original["handle"])
        terminal = ComputeTermination(handle.target_id, handle.request.request_id,
                                      handle.request.attempt_id, handle.call_id, outcome, True)
        if cancelled:
            ops.cancel(job)
        # Neither elapsed time nor cancel acknowledgement is terminal evidence.
        assert scheduler.tick(2)["state"] in {"pending", "cancelling"}
        assert ops.jobs()[0]["status"] in {"running", "cancelling"}

    def ended(_handle):
        raise ComputeTerminated(terminal)

    monkeypatch.setattr(provider, "poll", ended)
    with Scheduler(Operations(ops.path), store, provider, budget_limit=10) as recovered:
        expected = "cancelled" if cancelled else "failed"
        assert recovered.tick(3)["state"] == expected
        current = ops.jobs()[0]
        assert current["reserved_units"] == row["reserved_units"] == 1
        assert current["provider_ref"] == row["provider_ref"]
        assert json.loads(current["result"]) == terminal.to_dict()
        assert ops.compute_state(job)["handle"] == original["handle"]
        assert ops.compute_state(job)["receipt"] is None
        with pytest.raises(BoundaryError, match="stale_attempt"):
            ops.accept_compute_termination(job, old_token, row["attempt_id"], row["fence"],
                                           terminal.to_dict())
        # The ended attempt never retries, and its reservation is not refunded.
        with pytest.raises(BoundaryError, match="budget_exhausted"):
            ops.enqueue("feature", row["input_id"], "over-budget", max_seconds=300,
                        reserved_units=1, budget_limit=1)
        second = ops.enqueue("feature", row["input_id"], "next-budgeted", max_seconds=300,
                             reserved_units=1, budget_limit=10)
        assert recovered.tick(4)["state"] == "submitted"
        assert next(value for value in ops.jobs() if value["id"] == second)["status"] == "running"
    assert len(provider.calls) == 2


def test_foreign_or_unconfirmed_terminal_fact_cannot_release_original_attempt(
    pipeline, monkeypatch,
):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        saved = ops.compute_state(job)
        handle = provider.restore_handle(saved["handle"])
        terminal = ComputeTermination(handle.target_id, handle.request.request_id,
                                      handle.request.attempt_id, handle.call_id, "failed", True)
        with pytest.raises(BoundaryError, match="confirmed_terminal_process_required"):
            ComputeTermination.decode({**terminal.to_dict(), "process_exited": False})

        def foreign(_handle):
            raise ComputeTerminated(replace(terminal, call_id="fc-foreign"))

        monkeypatch.setattr(provider, "poll", foreign)
        assert scheduler.tick(2)["state"] == "result_uncertain"
        assert ops.jobs()[0]["status"] == "uncertain"
        assert ops.jobs()[0]["result"] is None
        assert len(provider.calls) == 1
        row = ops.jobs()[0]
        with pytest.raises(BoundaryError, match="original_handle_binding_mismatch"):
            ops.accept_compute_termination(
                job, scheduler._tokens[job], row["attempt_id"], row["fence"],
                replace(terminal, target_id="f" * 64).to_dict())


def test_known_original_handle_after_lost_submit_ack_is_observed_across_restart(
    pipeline, monkeypatch,
):
    ops, store, provider, job = pipeline
    submit = provider.submit

    def lost(request):
        raise ComputeSubmissionUnknown(submit(request))

    monkeypatch.setattr(provider, "submit", lost)
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submission_unknown"
        assert ops.compute_state(job)["handle"] is not None
        assert ops.jobs()[0]["status"] == "uncertain"
    with Scheduler(Operations(ops.path), store, provider, budget_limit=10) as recovered:
        assert recovered.tick(2)["state"] == "pending"
        assert len(provider.calls) == 1
        provider.ready = True
        assert recovered.tick(3)["state"] == "completed"
        assert len(provider.calls) == 1


def test_submission_uncertainty_survives_restart_without_duplicate_spawn(pipeline):
    ops, store, provider, job = pipeline
    provider.ambiguous = True
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submission_unknown"
    ops.enqueue(
        "feature",
        provider.calls[0].input_id,
        "feature-2",
        max_seconds=300,
        reserved_units=1,
        budget_limit=10,
    )
    with Scheduler(Operations(ops.path), store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(2)["state"] == "submission_unknown"
        assert scheduler.tick(500)["state"] == "submission_unknown"
    assert len(provider.calls) == 1
    assert ops.jobs()[0]["status"] == "submission_unknown"
    assert ops.jobs()[1]["status"] == "queued"
    assert ops.compute_state(job)["handle"] is None


def test_crash_after_remote_accept_before_handle_does_not_allow_retry(pipeline, monkeypatch):
    ops, store, provider, job = pipeline

    class ProcessDeath(BaseException):
        pass

    def death(*args, **kwargs):
        raise ProcessDeath()

    with (
        Scheduler(ops, store, provider, budget_limit=10) as scheduler,
        monkeypatch.context() as patch,
    ):
        patch.setattr(ops, "bind_compute_handle", death)
        with pytest.raises(ProcessDeath):
            scheduler.tick(1)
    assert ops.compute_state(job)["phase"] == "submitting"
    with Scheduler(Operations(ops.path), store, provider, budget_limit=10) as replacement:
        assert replacement.tick(2)["state"] == "submission_unknown"
    assert len(provider.calls) == 1


def test_restart_rotates_token_and_accepts_exact_late_receipt_after_lease_expiry(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as first:
        first.tick(1)
        old_token = first._tokens[job]
        old_row = ops.jobs()[0]
    provider.ready = True
    with Scheduler(Operations(ops.path), store, provider, budget_limit=0) as replacement:
        assert replacement.tick(200)["state"] == "completed"
        new_token = replacement._tokens[job]
        assert new_token != old_token
        receipt = ops.compute_state(job)["receipt"]
        with pytest.raises(BoundaryError, match="stale_attempt"):
            ops.accept_compute(job, old_token, old_row["attempt_id"], old_row["fence"], receipt)
        ops.accept_compute(job, new_token, old_row["attempt_id"], old_row["fence"], receipt)
    assert len(provider.calls) == 1
    assert ops.jobs()[0]["fence"] == old_row["fence"]


def test_bad_receipt_is_not_selected_or_relabelled_success(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        provider.ready = provider.altered_receipt = True
        assert scheduler.tick(2)["state"] == "result_uncertain"
        row = ops.jobs()[0]
        assert row["status"] == "uncertain" and row["result"] is None
        assert ops.compute_state(job)["last_error"] == "request_binding_mismatch"
        assert scheduler.tick(3)["state"] == "result_uncertain"
    assert len(provider.calls) == 1


def test_late_observation_after_deadline_accepts_already_returned_exact_receipt(pipeline):
    ops, store, provider, _ = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        provider.ready = True
        assert scheduler.tick(1000)["state"] == "completed"
    assert not provider.cancels and len(provider.calls) == 1


def test_cancel_ack_is_not_terminal_and_recovery_does_not_repeat_cancel(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        ops.cancel(job)
        assert scheduler.tick(2)["state"] == "cancelling"
        assert ops.jobs()[0]["status"] == "cancelling"
        assert ops.compute_state(job)["cancel_state"] == "acknowledged"
    with Scheduler(Operations(ops.path), store, provider, budget_limit=10) as replacement:
        assert replacement.tick(3)["state"] == "cancelling"
        assert len(provider.cancels) == 1
        provider.ready = True
        assert replacement.tick(4)["state"] == "cancelled"
        selected = json.loads(ops.jobs()[0]["result"])
        assert "output_id" not in selected
        assert ops.compute_state(job)["receipt"]["output_id"]
    assert len(provider.calls) == 1


def test_deadline_cancels_once_even_when_poll_fails_and_does_not_requeue(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        provider.poll_error = provider.cancel_error = True
        assert scheduler.tick(301)["state"] == "result_uncertain"
        assert ops.jobs()[0]["status"] == "cancelling"
        assert ops.compute_state(job)["cancel_state"] == "unknown"
        scheduler.tick(1000)
        assert len(provider.cancels) == 1
        assert ops.claim("another", now=1001) is None
    assert len(provider.calls) == 1


def test_singleton_lock_and_changed_target_fail_closed(pipeline):
    ops, store, provider, _ = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        with pytest.raises(BoundaryError, match="another_scheduler"):
            Scheduler(Operations(ops.path), store, provider, budget_limit=10)
        scheduler.tick(1)
    provider.target = replace(provider.target, timeout_seconds=299)
    with Scheduler(ops, store, provider, budget_limit=10) as replacement:
        assert replacement.tick(2)["state"] == "target_or_handle_mismatch"
    assert len(provider.calls) == 1 and not provider.polls


def test_restored_paused_database_never_calls_provider(pipeline, tmp_path):
    ops, store, provider, _ = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
    backup = tmp_path / "backup.sqlite"
    ops.backup(backup)
    with Scheduler(Operations(backup), store, provider, budget_limit=10) as restored:
        assert restored.tick(2)["state"] == "paused_by_operator"
    assert len(provider.calls) == 1 and not provider.polls and not provider.cancels


def test_training_pause_requires_explicit_new_budgeted_resume_job(pipeline):
    ops, store, provider, job = pipeline
    provider.ready = True
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        scheduler.tick(2)
        receipt = ComputeReceipt.decode(ops.compute_state(job)["receipt"])
        prepared = prepare_feature_run(
            store, receipt.input_id, receipt.output_id, provider.target.producer
        )
        training = ops.enqueue(
            "training",
            prepared.run_id,
            "training-pause",
            max_seconds=300,
            reserved_units=1,
            budget_limit=10,
            options={"stop_after": 1},
        )
        assert scheduler.tick(3)["state"] == "submitted"
        assert scheduler.tick(4)["state"] == "paused"
        assert scheduler.tick(5)["state"] == "idle"
        checkpoint = ops.compute_state(training)["receipt"]["checkpoint_id"]
        resumed = ops.enqueue(
            "training",
            prepared.run_id,
            "training-resume",
            max_seconds=300,
            reserved_units=1,
            budget_limit=10,
            options={"resume": checkpoint},
        )
        assert scheduler.tick(6)["state"] == "submitted"
        assert scheduler.tick(7)["state"] == "completed"
        assert ops.compute_state(resumed)["request"]["resume"] == checkpoint
    assert len(provider.calls) == 3


def test_schema_migration_keeps_requests_and_rejects_future_schema(pipeline, tmp_path):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
    with sqlite3.connect(ops.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA
        connection.execute("PRAGMA user_version=2")
    assert Operations(ops.path).compute_state(job) == ops.compute_state(job)
    future = tmp_path / "future.sqlite"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(BoundaryError, match="unsupported_operations_schema"):
        Operations(future)
    with sqlite3.connect(future) as connection:
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()


def test_schema_migration_failure_rolls_back_all_ddl(tmp_path):
    database = tmp_path / "broken-v1.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE VIEW jobs AS SELECT 'broken' AS id")
        connection.execute("PRAGMA user_version=1")
    with pytest.raises(sqlite3.OperationalError, match="view"):
        Operations(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("SELECT type,name FROM sqlite_master").fetchall() == [
            ("view", "jobs")
        ]


def test_valid_receipt_binding_still_requires_artifact_integrity(pipeline):
    ops, store, provider, job = pipeline
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        provider.ready = provider.missing_output = True
        assert scheduler.tick(2)["state"] == "result_uncertain"
        assert ops.jobs()[0]["result"] is None
        assert ops.jobs()[0]["status"] == "uncertain"
        assert ops.compute_state(job)["last_error"] == "object_not_found"


def test_cancel_racing_after_validation_prevents_final_selection(pipeline, monkeypatch):
    import spireagent.hub.scheduler as module

    ops, store, provider, job = pipeline
    validate = module.validate_receipt

    def racing_cancel(*args):
        validate(*args)
        ops.cancel(job)

    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        scheduler.tick(1)
        provider.ready = True
        monkeypatch.setattr(module, "validate_receipt", racing_cancel)
        assert scheduler.tick(2)["state"] == "result_uncertain"
        assert ops.jobs()[0]["status"] == "cancelling"
        assert ops.jobs()[0]["result"] is None
        assert scheduler.tick(3)["state"] == "cancelled"
        assert "output_id" not in json.loads(ops.jobs()[0]["result"])


def test_provider_timeout_cannot_exceed_authorized_job_duration(pipeline):
    ops, store, provider, job = pipeline
    provider.target = replace(provider.target, timeout_seconds=301)
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        report = scheduler.tick(1)
        assert report["state"] == "preflight_failed"
        assert report["code"] == "provider_timeout_exceeds_job_budget"
    assert not provider.calls and ops.compute_state(job) is None
    assert ops.jobs()[0]["status"] == "failed"


def test_console_index_failure_never_reopens_selected_compute(pipeline, monkeypatch):
    from spireagent.hub.console_index import ConsoleIndex

    ops, store, provider, job = pipeline

    def broken(*args):
        raise OSError("projection unavailable")

    monkeypatch.setattr(ConsoleIndex, "artifact_closure", broken)
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        provider.ready = True
        assert scheduler.tick(2)["state"] == "completed"
        assert ops.jobs()[0]["status"] == "completed"
        assert scheduler.tick(3)["state"] == "idle"
        assert len(provider.calls) == 1
    assert ConsoleIndex(ops).health()["last_artifact_index_issue_at"] is not None


def test_index_and_secondary_telemetry_failures_cannot_change_completed_compute(
    pipeline,
    monkeypatch,
):
    from spireagent.hub.console_index import ConsoleIndex

    ops, store, provider, job = pipeline
    event = ops._event

    def index_broken(*args):
        raise BoundaryError("console", "projection_failed")

    def event_broken(db, actor, operation, subject, detail):
        if operation == "artifact_index_unavailable":
            raise BoundaryError("console", "failure_event_failed")
        event(db, actor, operation, subject, detail)

    monkeypatch.setattr(ConsoleIndex, "artifact_closure", index_broken)
    monkeypatch.setattr(ops, "_event", event_broken)
    with Scheduler(ops, store, provider, budget_limit=10) as scheduler:
        assert scheduler.tick(1)["state"] == "submitted"
        provider.ready = True
        result = scheduler.tick(2)
        assert result["state"] == "completed"
        assert result["console_index_status"] == "unavailable"
        assert ops.jobs()[0]["status"] == "completed"
        assert scheduler.tick(3)["state"] == "idle"
        assert len(provider.calls) == 1
