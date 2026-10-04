"""Bounded serial M2 lifecycle over research, provider and root-policy seams.

The backend owns data admission, checkpoint lineage and numerical acceptance.
The provider owns deployment, transport and verified cleanup. The authority owns
paid permission and exposure. This core owns only durable sequencing and bounds.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from .journal import MAX_METADATA_BYTES, CampaignJournal, sha256

MAX_REQUEST_BYTES = 256 * 1024 * 1024
MAX_RESULT_BYTES = 192 * 1024 * 1024


class CampaignError(RuntimeError):
    """Invalid campaign configuration or violated lifecycle contract."""


class PendingApproval(RuntimeError):
    """Exact request awaits external approval; no paid side effect occurred."""


@dataclass(frozen=True)
class RunSpec:
    run_id: str
    max_requests: int


@dataclass(frozen=True)
class NextSlice:
    resume_id: str | None
    max_windows: int
    expected_epoch: int
    completes_epoch: bool


@dataclass(frozen=True)
class Accepted:
    checkpoint_id: str
    completed_epochs: int
    optimizer_updates: int
    completed: bool
    stages: tuple[int, ...]


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    run_id: str
    ordinal: int
    slice: NextSlice


@dataclass(frozen=True)
class CampaignConfig:
    runs: tuple[RunSpec, ...]
    max_windows: int = 512
    goal_epochs: int = 5

    def __post_init__(self) -> None:
        if (
            type(self.runs) is not tuple
            or not self.runs
            or type(self.max_windows) is not int
            or not 1 <= self.max_windows <= 512
            or self.goal_epochs != 5
            or type(self.goal_epochs) is not int
        ):
            raise CampaignError("campaign_config_invalid")
        ids = set()
        for spec in self.runs:
            if (
                not isinstance(spec, RunSpec)
                or type(spec.run_id) is not str
                or not spec.run_id
                or len(spec.run_id) > 128
                or spec.run_id in ids
                or type(spec.max_requests) is not int
                or spec.max_requests < 1
            ):
                raise CampaignError("run_spec_invalid")
            ids.add(spec.run_id)


class Backend(Protocol):
    def session(self) -> AbstractContextManager[Any]: ...
    def plan_next(self, run_id: str) -> NextSlice | None: ...
    def admit(self, run_id: str) -> None: ...
    def build(self, run_id: str, attempt_id: str, slice: NextSlice) -> bytes: ...
    def accept(self, run_id: str, request: bytes, result: bytes) -> Accepted: ...


class Provider(Protocol):
    def prepare(self, attempt: Attempt, request: bytes, approval: bytes) -> bytes: ...
    def submit(self, attempt: Attempt, target: bytes, request: bytes) -> bytes: ...
    def poll(self, attempt: Attempt, target: bytes, handle: bytes) -> bytes | None: ...
    def stop_and_confirm(self, attempt: Attempt, target: bytes, handle: bytes | None) -> bytes: ...


class Authority(Protocol):
    def authorize(self, attempt: Attempt, request_sha256: str) -> bytes: ...
    def validate_before_submit(
        self, attempt: Attempt, request_sha256: str, approval: bytes
    ) -> None: ...


@dataclass(frozen=True)
class TickResult:
    status: Literal["waiting_approval", "running", "accepted", "paused", "completed", "blocked"]
    attempt: Attempt | None = None
    accepted: Accepted | None = None
    reason: str | None = None


_FIELDS = {
    "attempt_id",
    "run_id",
    "ordinal",
    "slice",
    "phase",
    "request",
    "approval",
    "target",
    "handle",
    "result",
    "stop",
}
_PHASES = (
    "waiting_approval",
    "deploy_intent",
    "prepared",
    "submit_intent",
    "submitted",
    "result",
    "stopped",
)


class Campaign:
    def __init__(
        self,
        config: CampaignConfig,
        journal: CampaignJournal,
        backend: Backend,
        provider: Provider,
        authority: Authority,
        *, execution_settings: bytes | None = None,
    ) -> None:
        self.config, self.journal = config, journal
        self.backend, self.provider, self.authority = backend, provider, authority
        self._active = False
        self._session_accepted = 0
        self.execution_settings = execution_settings

    @contextmanager
    def session(self) -> Iterator[Campaign]:
        """Retain command-scoped immutable verification and the cross-run writer lock."""
        if self._active:
            raise CampaignError("session_already_active")
        with self.journal.session(asdict(self.config), settings_bytes=self.execution_settings):
            if self.execution_settings is not None:
                from .recovery import validate_execution
                validate_execution(self.journal)
            with self.backend.session():
                self._active = True
                self._session_accepted = 0
                try:
                    self._records()
                    yield self
                finally:
                    self._active = False

    def _slice(self, value: object) -> NextSlice:
        if isinstance(value, NextSlice):
            slice = value
        elif type(value) is dict and set(value) == {
            "resume_id",
            "max_windows",
            "expected_epoch",
            "completes_epoch",
        }:
            slice = NextSlice(**value)
        else:
            raise CampaignError("slice_invalid")
        if (
            slice.resume_id is not None
            and (type(slice.resume_id) is not str or not slice.resume_id)
            or type(slice.max_windows) is not int
            or not 1 <= slice.max_windows <= self.config.max_windows
            or type(slice.expected_epoch) is not int
            or not 1 <= slice.expected_epoch <= self.config.goal_epochs
            or type(slice.completes_epoch) is not bool
        ):
            raise CampaignError("slice_invalid")
        return slice

    def _accepted(self, value: object, slice: NextSlice) -> Accepted:
        if isinstance(value, Accepted):
            accepted = value
        elif type(value) is dict and set(value) == {
            "checkpoint_id",
            "completed_epochs",
            "optimizer_updates",
            "completed",
            "stages",
        }:
            fields = dict(value)
            if type(fields["stages"]) is not list:
                raise CampaignError("accepted_invalid")
            fields["stages"] = tuple(fields["stages"])
            accepted = Accepted(**fields)
        else:
            raise CampaignError("accepted_invalid")
        epochs = slice.expected_epoch if slice.completes_epoch else slice.expected_epoch - 1
        if (
            type(accepted.checkpoint_id) is not str
            or not accepted.checkpoint_id
            or type(accepted.completed_epochs) is not int
            or accepted.completed_epochs != epochs
            or type(accepted.optimizer_updates) is not int
            or accepted.optimizer_updates < 1
            or type(accepted.completed) is not bool
            or accepted.completed != (epochs == self.config.goal_epochs)
            or type(accepted.stages) is not tuple
            or any(type(stage) is not int for stage in accepted.stages)
            or accepted.stages != tuple(stage for stage in (1, 3, 5) if stage <= epochs)
        ):
            raise CampaignError("accepted_invalid")
        return accepted

    def _attempt(self, record: dict[str, Any]) -> Attempt:
        return Attempt(
            record["attempt_id"], record["run_id"], record["ordinal"], self._slice(record["slice"])
        )

    def _records(self) -> list[dict[str, Any]]:
        records = self.journal.records()
        specs = {spec.run_id: spec for spec in self.config.runs}
        counts: dict[str, int] = {}
        logical: dict[str, int] = {}
        previous: dict[str, Accepted] = {}
        unresolved = False
        last_run_index = -1
        for record in records:
            if (
                set(record) not in (_FIELDS, _FIELDS | {"replacement_for"})
                or record["phase"] not in _PHASES
                or type(record["run_id"]) is not str
                or record["run_id"] not in specs
                or type(record["ordinal"]) is not int
            ):
                raise CampaignError("attempt_record_invalid")
            attempt = self._attempt(record)
            run_index = tuple(specs).index(attempt.run_id)
            if unresolved or run_index < last_run_index:
                raise CampaignError("serial_attempt_order_invalid")
            if run_index > last_run_index:
                for prior_run in tuple(specs)[:run_index]:
                    if prior_run in previous and not previous[prior_run].completed:
                        raise CampaignError("run_order_invalid")
                last_run_index = run_index
            counts[attempt.run_id] = counts.get(attempt.run_id, 0) + 1
            resolved = self.journal.resolution(record) is not None
            logical[attempt.run_id] = logical.get(attempt.run_id, 0) + (0 if resolved else 1)
            if (counts[attempt.run_id] != attempt.ordinal
                    or logical[attempt.run_id] > specs[attempt.run_id].max_requests):
                raise CampaignError("request_limit_or_ordinal_invalid")
            phase = _PHASES.index(record["phase"])
            required = {
                "request": 0,
                "approval": 1,
                "target": 2,
                "handle": 4,
                "result": 5,
                "stop": 6,
            }
            for key, minimum in required.items():
                if (phase >= minimum) != (record[key] is not None) and not (
                    key == "stop" and phase in {2, 3, 4}
                ):
                    raise CampaignError("attempt_phase_invalid")
                if record[key] is not None:
                    limit = (
                        MAX_REQUEST_BYTES
                        if key == "request"
                        else MAX_RESULT_BYTES
                        if key == "result"
                        else MAX_METADATA_BYTES
                    )
                    if record[key]["size"] > limit:
                        raise CampaignError("attempt_object_size_limit")
            prior = previous.get(attempt.run_id)
            if prior is not None and (
                prior.completed
                or attempt.slice.resume_id != prior.checkpoint_id
                or attempt.slice.expected_epoch != prior.completed_epochs + 1
            ):
                raise CampaignError("same_run_resume_invalid")
            marker = self.journal.accepted(record)
            if marker is None:
                unresolved = not resolved
            else:
                accepted = self._accepted(marker, attempt.slice)
                if prior is not None and accepted.optimizer_updates <= prior.optimizer_updates:
                    raise CampaignError("optimizer_progress_invalid")
                previous[attempt.run_id] = accepted
        return records

    def _save(self, records: list[dict[str, Any]], record: dict[str, Any], **fields: Any) -> None:
        record.update(fields)
        self.journal.save(records)

    def _stop(
        self, records: list[dict[str, Any]], record: dict[str, Any], attempt: Attempt
    ) -> None:
        if record["stop"] is None:
            target = self.journal.read(record["target"], limit=MAX_METADATA_BYTES)
            handle = (
                None
                if record["handle"] is None
                else self.journal.read(record["handle"], limit=MAX_METADATA_BYTES)
            )
            stop = self.provider.stop_and_confirm(attempt, target, handle)
            self._save(
                records,
                record,
                stop=self.journal.put(stop, limit=MAX_METADATA_BYTES),
                phase="stopped" if record["result"] is not None else record["phase"],
            )

    def _cleanup_known(
        self,
        records: list[dict[str, Any]],
        record: dict[str, Any],
        attempt: Attempt,
        target: bytes,
        handle: bytes | None,
    ) -> None:
        """Stop a returned call even when persisting its target/handle failed."""
        stop = self.provider.stop_and_confirm(attempt, target, handle)
        self._save(
            records,
            record,
            target=self.journal.put(target, limit=MAX_METADATA_BYTES),
            handle=(None if handle is None else self.journal.put(handle, limit=MAX_METADATA_BYTES)),
            stop=self.journal.put(stop, limit=MAX_METADATA_BYTES),
            phase="prepared" if handle is None else "submitted",
        )

    def tick(self, pause_after_accepted: int | None = None) -> TickResult:
        """Advance at most one request, returning promptly for approval or pending poll.

        A pause count is local to this command session. Durable accepted progress
        survives restart; an intent with an unknown paid outcome never dispatches.
        """
        if not self._active:
            raise CampaignError("campaign_session_required")
        if pause_after_accepted is not None and (
            type(pause_after_accepted) is not int or pause_after_accepted < 1
        ):
            raise CampaignError("pause_bound_invalid")
        if pause_after_accepted is not None and self._session_accepted >= pause_after_accepted:
            return TickResult("paused")
        records = self._records()
        record = next((item for item in records if self.journal.accepted(item) is None
                       and self.journal.resolution(item) is None), None)
        if record is None:
            for spec in self.config.runs:
                owned = [item for item in records if item["run_id"] == spec.run_id]
                accepted_rows = [item for item in owned if self.journal.accepted(item) is not None]
                if accepted_rows:
                    latest = self._accepted(
                        self.journal.accepted(accepted_rows[-1]),
                        self._slice(accepted_rows[-1]["slice"]),
                    )
                    if latest.completed:
                        continue
                slice = self.backend.plan_next(spec.run_id)
                if slice is None:
                    if accepted_rows:
                        raise CampaignError("backend_completed_without_campaign_acceptance")
                    continue  # An already-completed prepared run belongs to the backend.
                logical_count = sum(self.journal.resolution(item) is None for item in owned)
                if logical_count >= spec.max_requests:
                    return TickResult("blocked", reason="request_limit")
                slice = self._slice(slice)
                if accepted_rows and (
                    slice.resume_id != latest.checkpoint_id
                    or slice.expected_epoch != latest.completed_epochs + 1
                ):
                    raise CampaignError("same_run_resume_invalid")
                attempt = Attempt(uuid.uuid4().hex, spec.run_id, len(owned) + 1, slice)
                replacement = self.journal.replacement_for(spec.run_id, records)
                if replacement is not None:
                    old = next(item for item in owned if item["attempt_id"] == replacement)
                    if asdict(slice) != old["slice"]:
                        raise CampaignError("replacement_must_repeat_same_slice")
                self.backend.admit(spec.run_id)
                request = self.backend.build(spec.run_id, attempt.attempt_id, slice)
                record = {
                    **asdict(attempt),
                    "phase": "waiting_approval",
                    "request": self.journal.put(request, limit=MAX_REQUEST_BYTES),
                    "approval": None,
                    "target": None,
                    "handle": None,
                    "result": None,
                    "stop": None,
                }
                if replacement is not None:
                    record["replacement_for"] = replacement
                records.append(record)
                self.journal.save(records)
                break
            if record is None:
                return TickResult("completed")
        attempt = self._attempt(record)
        request = self.journal.read(record["request"])
        request_sha = sha256(request)
        if record["phase"] == "waiting_approval":
            try:
                approval = self.authority.authorize(attempt, request_sha)
            except PendingApproval as error:
                return TickResult("waiting_approval", attempt, reason=str(error))
            self.authority.validate_before_submit(attempt, request_sha, approval)
            self.backend.admit(attempt.run_id)
            self._save(
                records,
                record,
                approval=self.journal.put(approval, limit=MAX_METADATA_BYTES),
                phase="deploy_intent",
            )
            target = None
            try:
                target = self.provider.prepare(attempt, request, approval)
                self._save(
                    records,
                    record,
                    target=self.journal.put(target, limit=MAX_METADATA_BYTES),
                    phase="prepared",
                )
            except Exception as error:
                if target is not None:
                    try:
                        self._cleanup_known(records, record, attempt, target, None)
                    except Exception:
                        return TickResult(
                            "blocked", attempt, reason="prepare_persistence_stop_unconfirmed"
                        )
                return TickResult(
                    "blocked", attempt, reason="prepare_unknown:" + type(error).__name__
                )
        elif record["phase"] == "deploy_intent":
            return TickResult("blocked", attempt, reason="deployment_needs_reconciliation")
        if record["phase"] == "prepared":
            if record["stop"] is not None:
                return TickResult("blocked", attempt, reason="stopped_before_submit")
            try:
                self.backend.admit(attempt.run_id)
                self.authority.validate_before_submit(
                    attempt,
                    request_sha,
                    self.journal.read(record["approval"], limit=MAX_METADATA_BYTES),
                )
            except Exception:
                self._stop(records, record, attempt)
                raise
            target = self.journal.read(record["target"], limit=MAX_METADATA_BYTES)
            self._save(records, record, phase="submit_intent")
            handle = None
            try:
                handle = self.provider.submit(attempt, target, request)
                self._save(
                    records,
                    record,
                    handle=self.journal.put(handle, limit=MAX_METADATA_BYTES),
                    phase="submitted",
                )
            except Exception as error:
                try:
                    if handle is not None and record["handle"] is None:
                        self._cleanup_known(records, record, attempt, target, handle)
                    else:
                        self._stop(records, record, attempt)
                except Exception:
                    return TickResult("blocked", attempt, reason="submit_unknown_stop_unconfirmed")
                return TickResult(
                    "blocked", attempt, reason="submit_unknown:" + type(error).__name__
                )
        elif record["phase"] == "submit_intent":
            return TickResult("blocked", attempt, reason="submission_needs_reconciliation")
        if record["phase"] == "submitted":
            if record["stop"] is not None:
                return TickResult("blocked", attempt, reason="stopped_without_durable_result")
            result = self.provider.poll(
                attempt,
                self.journal.read(record["target"], limit=MAX_METADATA_BYTES),
                self.journal.read(record["handle"], limit=MAX_METADATA_BYTES),
            )
            if result is None:
                return TickResult("running", attempt)
            self._save(
                records,
                record,
                result=self.journal.put(result, limit=MAX_RESULT_BYTES),
                phase="result",
            )
        if record["phase"] == "result":
            self._stop(records, record, attempt)
            self._save(records, record, phase="stopped")
        self.backend.admit(attempt.run_id)
        accepted = self._accepted(
            self.backend.accept(attempt.run_id, request, self.journal.read(record["result"])),
            attempt.slice,
        )
        prior_records = [item for item in records[:-1] if item["run_id"] == attempt.run_id
                         and self.journal.accepted(item) is not None]
        if prior_records:
            prior = self._accepted(
                self.journal.accepted(prior_records[-1]), self._slice(prior_records[-1]["slice"])
            )
            if accepted.optimizer_updates <= prior.optimizer_updates:
                raise CampaignError("optimizer_progress_invalid")
        self.journal.mark_accepted(record, asdict(accepted))
        self._session_accepted += 1
        status: Literal["accepted", "paused"] = "accepted"
        if pause_after_accepted is not None and self._session_accepted >= pause_after_accepted:
            status = "paused"
        return TickResult(status, attempt, accepted)

    def stop_active(self) -> TickResult:
        """CLI error/interrupt cleanup while the session still holds the writer lock.

        Unknown deployments lack a target and require external reconciliation.
        A stopped call without a saved result stays blocked; cleanup never makes
        a fresh submission permissible. Cleanup failure leaves the occupied slot.
        """
        if not self._active:
            raise CampaignError("campaign_session_required")
        records = self._records()
        record = next((item for item in records if self.journal.accepted(item) is None
                       and self.journal.resolution(item) is None), None)
        if record is None:
            return TickResult("paused", reason="no_active_attempt")
        attempt = self._attempt(record)
        if record["target"] is None:
            if record["phase"] == "deploy_intent":
                return TickResult("blocked", attempt, reason="deployment_needs_reconciliation")
            return TickResult("paused", attempt, reason="not_deployed")
        self._stop(records, record, attempt)
        return TickResult(
            "blocked",
            attempt,
            reason=(
                "stopped_pending_acceptance"
                if record["result"] is not None
                else "stopped_without_durable_result"
            ),
        )


__all__ = [
    "Accepted",
    "Attempt",
    "Authority",
    "Backend",
    "Campaign",
    "CampaignConfig",
    "CampaignError",
    "NextSlice",
    "PendingApproval",
    "Provider",
    "RunSpec",
    "TickResult",
]
