"""Single-Hub scheduling over durable invocation intent and fenced result selection.

Provider calls are never the operations ledger. A lost submit response remains
unknown; only a saved handle is polled after restart. A lease is not proof that an
external worker stopped. This module starts no cloud work without a positive budget.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import threading
from collections.abc import Callable
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.compute import (
    ComputeHandle,
    ComputeProvider,
    ComputeSubmissionUnknown,
    ComputeTerminated,
)
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError
from spireagent.storage.store import ArtifactStore
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest, load_feature_job
from stpd.cloud_jobs.execution import preflight_training_request, validate_receipt

ACTIVE = frozenset({"running", "uncertain", "submission_unknown", "cancelling"})




class Scheduler:
    """Hold one OS lifetime lock per operations DB; call ``tick(now)`` from a loop.

    Use as a context manager or call close in the owning service's finally block.
    The lock survives neither process death nor explicit close. It is not deleted
    or stolen on a guessed timeout. SQLite CAS still fences every state mutation.
    """

    def __init__(
        self,
        operations: Operations,
        store: ArtifactStore,
        provider: ComputeProvider,
        *,
        budget_limit: int = 0,
        record_training_use: Callable[[Manifest], None] | None = None,
    ) -> None:
        if type(budget_limit) is not int or budget_limit < 0:
            raise BoundaryError("scheduler", "invalid_budget")
        self.operations, self.store, self.provider = operations, store, provider
        self.budget_limit = budget_limit
        self._record_training_use = record_training_use
        self._tokens: dict[str, str] = {}
        self._serial = threading.Lock()
        self._closed = False
        path = operations.path.resolve().with_suffix(".scheduler.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = path.open("a+b")
        self._lock.seek(0)
        # Windows byte locks may extend beyond EOF. Reading or initializing that
        # byte before acquisition instead fails against a live owner's lock.
        try:
            if os.name == "nt":
                api = importlib.import_module("msvcrt")
                api.locking(self._lock.fileno(), api.LK_NBLCK, 1)
            else:
                api = importlib.import_module("fcntl")
                api.flock(self._lock.fileno(), api.LOCK_EX | api.LOCK_NB)
        except OSError:
            self._lock.close()
            raise BoundaryError("scheduler", "another_scheduler_owns_database") from None

    def __enter__(self) -> Scheduler:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._serial:
            if not self._closed:
                self._closed = True
                self._lock.close()
                self._tokens.clear()

    def _reply(self, state: str, job: str | None = None, **detail: Any) -> dict[str, Any]:
        return {"schema": "stpd/scheduler-tick-v1", "state": state, "job_id": job, **detail}

    def _token(self, row: dict[str, Any], now: float) -> str:
        job = str(row["id"])
        if job not in self._tokens:
            recovered = self.operations.recover_compute(
                job,
                row["attempt_id"],
                row["fence"],
                now=now,
            )
            self._tokens[job] = recovered["lease_token"]
        return self._tokens[job]

    def _preflight(self, row: dict[str, Any]) -> ComputeRequest:
        kind = {"feature": "features", "training": "training"}.get(row["kind"])
        if kind is None:
            raise BoundaryError("scheduler", "unsupported_job_kind")
        target = self.provider.target
        if target.timeout_seconds > row["max_seconds"]:
            raise BoundaryError("scheduler", "provider_timeout_exceeds_job_budget")
        options = json.loads(row["options"])
        request = ComputeRequest(
            kind,
            row["input_id"],
            target.producer,
            row["attempt_id"],
            options.get("resume"),
            options.get("stop_after"),
        )
        if kind == "features":
            load_feature_job(self.store, request.input_id, target.producer)
        else:
            preflight_training_request(self.store, request)
        manifest = self.store.get_manifest(request.input_id)
        if self._record_training_use is None:
            from spireagent.hub.curation_access import record_use

            # The default Hub retains its own durable Curation authority.
            # Features disclose held-out input too, before numerical training.
            record_use(self.operations, self.store, manifest, "training")
        else:
            # Trusted application composition can retain its existing LocalLedger
            # (including host claim guards); this is never a request-supplied hook.
            self._record_training_use(manifest)
        return request

    def _submit(self, row: dict[str, Any], now: float) -> dict[str, Any]:
        job, token = row["id"], row["lease_token"]
        self._tokens[job] = token
        try:
            request = self._preflight(row)
        except (BoundaryError, OSError, ValueError) as error:
            code = error.code if isinstance(error, BoundaryError) else "input_unavailable"
            self.operations.fail_before_compute(job, token, code, now=now)
            return self._reply("preflight_failed", job, code=code)
        if not self.operations.prepare_compute(
            job,
            token,
            request.to_dict(),
            self.provider.target.target_id,
            now=now,
        ):
            return self._reply("already_prepared_no_resubmit", job)
        try:
            handle = self.provider.submit(request)
        except ComputeSubmissionUnknown as unknown:
            # A deterministic transport handle is observation capability, never
            # evidence of accepted submission or permission to invoke again.
            try:
                handle = self.provider.restore_handle(unknown.handle.to_dict())
                if (handle.request != request
                        or handle.target_id != self.provider.target.target_id):
                    raise BoundaryError("scheduler", "submission_handle_mismatch")
            except (BoundaryError, TypeError, ValueError):
                self.operations.compute_uncertain(job, token, row["attempt_id"], row["fence"],
                                                  "submission_handle_mismatch")
                return self._reply("submission_unknown", job)
            self.operations.bind_compute_handle(job, token, row["attempt_id"], row["fence"],
                                                handle.to_dict())
            self.operations.compute_uncertain(job, token, row["attempt_id"], row["fence"],
                                              "submission_unknown")
            return self._reply("submission_unknown", job)
        except Exception:
            self.operations.compute_uncertain(
                job,
                token,
                row["attempt_id"],
                row["fence"],
                "submission_unknown",
            )
            return self._reply("submission_unknown", job)
        # A crash before this transaction leaves durable 'submitting', never permission to retry.
        self.operations.bind_compute_handle(
            job,
            token,
            row["attempt_id"],
            row["fence"],
            handle.to_dict(),
        )
        return self._reply("submitted", job, request_id=request.request_id)

    def _cancel(self, row: dict[str, Any], token: str, handle: ComputeHandle) -> None:
        job, attempt, fence = row["id"], row["attempt_id"], row["fence"]
        if row["status"] != "cancelling":
            self.operations.cancel(job)
        if self.operations.begin_compute_cancel(job, token, attempt, fence):
            try:
                self.provider.cancel(handle)
            except Exception:
                self.operations.record_compute_cancel(
                    job, token, attempt, fence, acknowledged=False
                )
            else:
                self.operations.record_compute_cancel(job, token, attempt, fence, acknowledged=True)

    def _index_result(self, receipt: ComputeReceipt) -> bool:
        from spireagent.hub.console_index import ConsoleIndex

        try:
            ConsoleIndex(self.operations).artifact_closure(
                self.store,
                tuple(
                    identity for identity in (receipt.output_id, receipt.checkpoint_id) if identity
                ),
            )
            return True
        except Exception:
            # Only the rebuildable console projection failed, not selected compute truth.
            try:
                with self.operations.transaction() as db:
                    self.operations._event(
                        db, "console", "artifact_index_unavailable", receipt.request_id, {}
                    )
            except Exception:
                # Failure telemetry is also optional. Do not let its write escape into
                # the enclosing authoritative compute-state error handler.
                pass
            return False

    def _poll(self, row: dict[str, Any], now: float) -> dict[str, Any]:
        job, attempt, fence = row["id"], row["attempt_id"], row["fence"]
        token = self._token(row, now)
        saved = self.operations.compute_state(job)
        if saved is None or saved["handle"] is None:
            self.operations.compute_uncertain(job, token, attempt, fence, "handle_not_recorded")
            return self._reply(
                "submission_unknown", job, recovery="provider_reconciliation_required"
            )
        request = ComputeRequest.decode(saved["request"])
        handle = self.provider.restore_handle(saved["handle"])
        if (
            saved["target_id"] != self.provider.target.target_id
            or handle.target_id != saved["target_id"]
            or handle.request != request
            or row["provider_ref"] != handle.call_id
            or saved["attempt_id"] != attempt
            or saved["fence"] != fence
        ):
            self.operations.compute_uncertain(job, token, attempt, fence, "saved_handle_mismatch")
            return self._reply("target_or_handle_mismatch", job)
        cancelling = row["status"] == "cancelling"
        deadline_elapsed = now >= row["deadline"]
        if cancelling:
            self._cancel(row, token, handle)
        try:
            try:
                receipt = self.provider.poll(handle)
            except ComputeTerminated as terminal:
                terminal.termination.bind(handle)
                status = self.operations.accept_compute_termination(
                    job, token, attempt, fence, terminal.termination.to_dict())
                return self._reply(status, job, execution_outcome=terminal.termination.outcome)
            if receipt is not None:
                receipt.bind(request)
                if cancelling:
                    # Returning this exact call's receipt proves it ended, but does not select it.
                    self.operations.accept_compute(
                        job, token, attempt, fence, receipt.to_dict(), cancelled=True
                    )
                    return self._reply("cancelled", job)
                validate_receipt(self.store, request, receipt)
                self.operations.accept_compute(job, token, attempt, fence, receipt.to_dict())
                # Result selection is already durable. A disposable metadata index cannot
                # turn completed compute back into uncertain or resubmit an invocation.
                indexed = self._index_result(receipt)
                return self._reply(
                    "paused" if receipt.state == "paused" else "completed",
                    job,
                    request_id=request.request_id,
                    console_index_status="available" if indexed else "unavailable",
                )
        except (BoundaryError, OSError, ValueError) as error:
            code = error.code if isinstance(error, BoundaryError) else "result_unavailable"
            if deadline_elapsed and not cancelling:
                self._cancel(row, token, handle)
            # Retain the attempt and its handle. Poll/validation may be retried, never spawn.
            self.operations.compute_uncertain(job, token, attempt, fence, code)
            return self._reply("result_uncertain", job, code=code)
        if deadline_elapsed and not cancelling:
            # Observe an already returned exact receipt before requesting stop. The target's
            # native timeout bounds compute, not the Hub's observation time or lease clock.
            self._cancel(row, token, handle)
            cancelling = True
        if cancelling:
            return self._reply("cancelling", job, recovery="await_exact_provider_terminal_evidence")
        if row["status"] == "running":
            self.operations.heartbeat(job, token, now=now)
        return self._reply("pending", job)

    def tick(self, now: float) -> dict[str, Any]:
        if not math.isfinite(now):
            raise BoundaryError("scheduler", "invalid_clock")
        with self._serial:
            if self._closed:
                raise BoundaryError("scheduler", "scheduler_closed")
            if self.operations.paused():
                return self._reply("paused_by_operator")
            self.operations.expire(now=now)
            jobs = self.operations.jobs()
            active = [row for row in jobs if row["status"] in ACTIVE]
            active_ids = {row["id"] for row in active}
            self._tokens = {job: token for job, token in self._tokens.items() if job in active_ids}
            if len(active) > 1:
                raise BoundaryError("scheduler", "multiple_active_jobs")
            if active:
                return self._poll(active[0], now)
            if self.budget_limit == 0:
                return self._reply("launch_disabled")
            if sum(row["reserved_units"] for row in jobs) > self.budget_limit:
                return self._reply("budget_blocked")
            claimed = self.operations.claim("scheduler", now=now)
            return self._reply("idle") if claimed is None else self._submit(claimed, now)
