"""Component lifecycle tests; the backend fake does not qualify numerical M2 work."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tools.m2_campaign import (
    Accepted,
    Attempt,
    Campaign,
    CampaignConfig,
    CampaignError,
    CampaignJournal,
    JournalError,
    NextSlice,
    PendingApproval,
    RunSpec,
)


class SyntheticBackend:
    def __init__(self) -> None:
        self.accepted: dict[str, Accepted] = {}
        self.partial: set[str] = set()
        self.revoked = False
        self.in_session = False
        self.sessions = 0
        self.builds = 0
        self.accept_calls = 0
        self.before_accept = None
        self.events: list[str] = []

    @contextmanager
    def session(self):
        self.in_session = True
        self.sessions += 1
        try:
            yield
        finally:
            self.in_session = False

    def admit(self, run_id: str) -> None:
        assert self.in_session
        self.events.append("admit")
        if self.revoked:
            raise PermissionError("owner_revoked")

    def plan_next(self, run_id: str) -> NextSlice | None:
        self.admit(run_id)
        previous = self.accepted.get(run_id)
        if previous and previous.completed:
            return None
        resume = previous.checkpoint_id if previous else None
        epoch = previous.completed_epochs + 1 if previous else 1
        return NextSlice(
            resume, 3 if run_id not in self.partial else 7, epoch, run_id in self.partial
        )

    def build(self, run_id: str, attempt_id: str, slice: NextSlice) -> bytes:
        self.builds += 1
        self.events.append("build")
        return json.dumps(
            {
                "run": run_id,
                "attempt": attempt_id,
                "resume": slice.resume_id,
                "epoch": slice.expected_epoch,
                "full": slice.completes_epoch,
            }
        ).encode()

    def accept(self, run_id: str, request: bytes, result: bytes) -> Accepted:
        self.accept_calls += 1
        self.events.append("accept")
        assert result == b"result:" + request
        obj = json.loads(request)
        completed = obj["epoch"] if obj["full"] else obj["epoch"] - 1
        updates = completed * 10 + (0 if obj["full"] else 3)
        accepted = Accepted(
            obj["attempt"],
            completed,
            updates,
            completed == 5,
            tuple(stage for stage in (1, 3, 5) if stage <= completed),
        )
        self.accepted[run_id] = accepted
        self.partial.add(run_id)
        if self.before_accept:
            self.before_accept()
        return accepted


class SyntheticProvider:
    def __init__(self, backend: SyntheticBackend) -> None:
        self.backend = backend
        self.prepares = 0
        self.submits = 0
        self.polls: list[tuple[bytes, bytes]] = []
        self.stops = 0
        self.requests: dict[bytes, bytes] = {}
        self.pending = False
        self.prepare_unknown = False
        self.submit_unknown = False
        self.stop_failure = False
        self.after_prepare = None
        self.after_stop = None
        self.active = 0
        self.max_active = 0

    def prepare(self, attempt: Attempt, request: bytes, approval: bytes) -> bytes:
        self.prepares += 1
        self.backend.events.append("prepare")
        if self.prepare_unknown:
            raise RuntimeError("unknown_deploy")
        if self.after_prepare:
            self.after_prepare()
        return b"target:" + attempt.attempt_id.encode()

    def submit(self, attempt: Attempt, target: bytes, request: bytes) -> bytes:
        self.submits += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.backend.events.append("submit")
        if self.submit_unknown:
            raise RuntimeError("unknown_submit")
        handle = b"handle:" + attempt.attempt_id.encode()
        self.requests[handle] = request
        return handle

    def poll(self, attempt: Attempt, target: bytes, handle: bytes) -> bytes | None:
        self.polls.append((target, handle))
        if self.pending:
            return None
        return b"result:" + self.requests[handle]

    def stop_and_confirm(self, attempt: Attempt, target: bytes, handle: bytes | None) -> bytes:
        self.stops += 1
        self.backend.events.append("stop")
        if self.stop_failure:
            raise RuntimeError("stop_unconfirmed")
        self.active = 0
        if self.after_stop:
            self.after_stop()
        return b"stopped:" + target + (handle or b"")


class SyntheticAuthority:
    def __init__(self) -> None:
        self.pending = False
        self.stale = False
        self.authorizations: list[tuple[Attempt, str]] = []
        self.validations = 0

    def authorize(self, attempt: Attempt, request_sha256: str) -> bytes:
        self.authorizations.append((attempt, request_sha256))
        if self.pending:
            raise PendingApproval("root_approval_required")
        return b"approval:" + request_sha256.encode()

    def validate_before_submit(
        self, attempt: Attempt, request_sha256: str, approval: bytes
    ) -> None:
        self.validations += 1
        if self.stale or approval != b"approval:" + request_sha256.encode():
            raise PermissionError("approval_stale")


class CampaignCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "campaign"
        self.backend = SyntheticBackend()
        self.provider = SyntheticProvider(self.backend)
        self.authority = SyntheticAuthority()
        self.config = CampaignConfig((RunSpec("synthetic-run", 6),))

    def campaign(self, config: CampaignConfig | None = None) -> Campaign:
        return Campaign(
            config or self.config,
            CampaignJournal(self.root),
            self.backend,
            self.provider,
            self.authority,
        )

    def state(self) -> dict[str, Any]:
        return json.loads((self.root / "state.json").read_bytes())

    def test_partial_checkpoint_restart_same_run_five_epochs(self) -> None:
        with self.campaign().session() as campaign:
            first = campaign.tick(pause_after_accepted=1)
            self.assertEqual(first.status, "paused")
            self.assertEqual(first.accepted.completed_epochs, 0)
            self.assertEqual(campaign.tick(pause_after_accepted=1).status, "paused")
        with self.campaign().session() as campaign:
            outcomes = [campaign.tick() for _ in range(5)]
            self.assertEqual(campaign.tick().status, "completed")
            self.assertEqual(outcomes[-1].accepted.stages, (1, 3, 5))
            self.assertTrue(outcomes[-1].accepted.completed)
            self.assertEqual(outcomes[0].attempt.slice.resume_id, first.accepted.checkpoint_id)
        self.assertEqual(self.provider.submits, 6)
        self.assertEqual(self.provider.max_active, 1)
        self.assertEqual(self.backend.sessions, 2)
        self.assertEqual(self.backend.events.count("accept"), 6)
        for index, event in enumerate(self.backend.events):
            if event == "accept":
                self.assertEqual(self.backend.events[index - 2 : index], ["stop", "admit"])

    def test_waiting_approval_retains_request_and_session_cache(self) -> None:
        self.authority.pending = True
        with self.campaign().session() as campaign:
            first = campaign.tick()
            second = campaign.tick()
            self.assertEqual(first.status, "waiting_approval")
            self.assertEqual(first.attempt, second.attempt)
            self.assertEqual(self.backend.builds, 1)
            self.assertTrue(self.backend.in_session)
            self.assertEqual(self.provider.prepares, 0)
            self.authority.pending = False
            self.assertEqual(campaign.tick().status, "accepted")
        self.assertEqual(self.backend.sessions, 1)
        self.assertEqual(len(set(sha for _, sha in self.authority.authorizations)), 1)

    def test_waiting_approval_restart_preserves_attempt(self) -> None:
        self.authority.pending = True
        with self.campaign().session() as campaign:
            attempt = campaign.tick().attempt
        self.authority.pending = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().attempt, attempt)
        self.assertEqual(self.backend.builds, 1)

    def test_saved_handle_restart_polls_exact_handle_without_resubmit(self) -> None:
        self.provider.pending = True
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "running")
            saved = self.provider.polls[-1]
        self.provider.pending = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "accepted")
        self.assertEqual(self.provider.polls[-1], saved)
        self.assertEqual((self.provider.prepares, self.provider.submits), (1, 1))

    def test_unknown_submit_is_never_retried(self) -> None:
        self.provider.submit_unknown = True
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "blocked")
        with self.campaign().session() as campaign:
            result = campaign.tick()
            self.assertEqual(result.reason, "submission_needs_reconciliation")
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.backend.accept_calls, 0)

    def test_prepare_failure_is_never_retried(self) -> None:
        self.provider.prepare_unknown = True
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "blocked")
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().reason, "deployment_needs_reconciliation")
        self.assertEqual(self.provider.prepares, 1)
        self.assertEqual(self.provider.submits, 0)

    def test_returned_target_persistence_failure_cleans_up_known_target(self) -> None:
        class FaultJournal(CampaignJournal):
            failed = False

            def put(self, raw: bytes, *, limit: int = 256 * 1024 * 1024):
                if raw.startswith(b"target:") and not self.failed:
                    self.failed = True
                    raise OSError("target_persistence_failed")
                return super().put(raw, limit=limit)

        campaign = Campaign(
            self.config, FaultJournal(self.root), self.backend, self.provider, self.authority
        )
        with campaign.session():
            self.assertEqual(campaign.tick().status, "blocked")
        with self.campaign().session() as recovered:
            self.assertEqual(recovered.tick().reason, "stopped_before_submit")
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.provider.submits, 0)

    def test_returned_handle_persistence_failure_stops_exact_known_handle(self) -> None:
        class FaultJournal(CampaignJournal):
            failed = False

            def put(self, raw: bytes, *, limit: int = 256 * 1024 * 1024):
                if raw.startswith(b"handle:") and not self.failed:
                    self.failed = True
                    raise OSError("handle_persistence_failed")
                return super().put(raw, limit=limit)

        campaign = Campaign(
            self.config, FaultJournal(self.root), self.backend, self.provider, self.authority
        )
        with campaign.session():
            self.assertEqual(campaign.tick().status, "blocked")
        saved_handle = self.state()["attempts"][0]["handle"]
        raw_handle = (self.root / "objects" / saved_handle["sha256"]).read_bytes()
        self.assertIn(raw_handle, self.provider.requests)
        with self.campaign().session() as recovered:
            self.assertEqual(recovered.tick().reason, "stopped_without_durable_result")
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.provider.submits, 1)

    def test_stop_failure_keeps_result_for_recovery(self) -> None:
        self.provider.stop_failure = True
        with (
            self.campaign().session() as campaign,
            self.assertRaisesRegex(RuntimeError, "stop_unconfirmed"),
        ):
            campaign.tick()
        self.assertEqual(self.backend.accept_calls, 0)
        self.provider.stop_failure = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "accepted")
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(len(self.provider.polls), 1)

    def test_interrupt_cleanup_stops_saved_target_handle_and_blocks_resubmit(self) -> None:
        self.provider.pending = True
        with self.campaign().session() as campaign:
            campaign.tick()
            self.assertEqual(campaign.stop_active().reason, "stopped_without_durable_result")
            self.assertEqual(campaign.tick().reason, "stopped_without_durable_result")
            campaign.stop_active()
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "blocked")
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.provider.submits, 1)

    def test_interrupt_cleanup_failure_retains_occupied_slot(self) -> None:
        self.provider.pending = True
        self.provider.stop_failure = True
        with self.campaign().session() as campaign:
            campaign.tick()
            with self.assertRaisesRegex(RuntimeError, "stop_unconfirmed"):
                campaign.stop_active()
        self.provider.stop_failure = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.stop_active().status, "blocked")
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.provider.max_active, 1)

    def test_owner_revocation_before_acceptance_does_not_accept(self) -> None:
        self.provider.after_stop = lambda: setattr(self.backend, "revoked", True)
        with (
            self.campaign().session() as campaign,
            self.assertRaisesRegex(PermissionError, "owner_revoked"),
        ):
            campaign.tick()
        self.assertEqual(self.backend.accept_calls, 0)
        self.provider.after_stop = None
        self.backend.revoked = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "accepted")
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.provider.submits, 1)

    def test_crash_after_backend_acceptance_replays_idempotently(self) -> None:
        def crash():
            raise RuntimeError("after_backend_accept_before_marker")

        self.backend.before_accept = crash
        with (
            self.campaign().session() as campaign,
            self.assertRaisesRegex(RuntimeError, "after_backend"),
        ):
            campaign.tick()
        self.backend.before_accept = None
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().status, "accepted")
        self.assertEqual(self.backend.accept_calls, 2)
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.provider.stops, 1)

    def test_stale_approval_before_prepare_has_no_paid_side_effect(self) -> None:
        self.authority.stale = True
        with (
            self.campaign().session() as campaign,
            self.assertRaisesRegex(PermissionError, "approval_stale"),
        ):
            campaign.tick()
        self.assertEqual(self.provider.prepares, 0)
        self.assertEqual(self.provider.submits, 0)

    def test_approval_expiring_during_prepare_stops_without_submit(self) -> None:
        self.provider.after_prepare = lambda: setattr(self.authority, "stale", True)
        with (
            self.campaign().session() as campaign,
            self.assertRaisesRegex(PermissionError, "approval_stale"),
        ):
            campaign.tick()
        self.authority.stale = False
        with self.campaign().session() as campaign:
            self.assertEqual(campaign.tick().reason, "stopped_before_submit")
        self.assertEqual(self.provider.stops, 1)
        self.assertEqual(self.provider.submits, 0)

    def test_request_limit_and_window_limit_prevent_new_dispatch(self) -> None:
        bounded = CampaignConfig((RunSpec("synthetic-run", 1),))
        with self.campaign(bounded).session() as campaign:
            campaign.tick()
            self.assertEqual(campaign.tick().reason, "request_limit")
        self.assertEqual(self.provider.submits, 1)
        with self.assertRaises(CampaignError):
            CampaignConfig(self.config.runs, max_windows=513)

    def test_multiple_runs_are_strictly_serial(self) -> None:
        config = CampaignConfig((RunSpec("first", 6), RunSpec("second", 6)))
        with self.campaign(config).session() as campaign:
            attempts = [campaign.tick().attempt for _ in range(12)]
            self.assertEqual(campaign.tick().status, "completed")
        self.assertEqual([attempt.run_id for attempt in attempts], ["first"] * 6 + ["second"] * 6)
        self.assertEqual(self.provider.max_active, 1)

    def test_config_mismatch_writer_lock_and_lock_scope(self) -> None:
        campaign = self.campaign()
        with (
            campaign.session(),
            self.assertRaisesRegex(JournalError, "campaign_writer_busy"),
            self.campaign().session(),
        ):
            pass
        with self.assertRaisesRegex(CampaignError, "campaign_session_required"):
            campaign.tick()
        changed = CampaignConfig((RunSpec("other-run", 6),))
        with self.assertRaises(JournalError), self.campaign(changed).session():
            pass

    def test_corrupt_request_and_rolled_back_metadata_fail_closed(self) -> None:
        self.authority.pending = True
        with self.campaign().session() as campaign:
            campaign.tick()
        old_state = (self.root / "state.json").read_bytes()
        request = self.state()["attempts"][0]["request"]
        (self.root / "objects" / request["sha256"]).write_bytes(b"corrupt")
        with self.assertRaises(JournalError), self.campaign().session():
            pass
        self.assertEqual(self.provider.prepares, 0)
        # Restore the exact request, then complete acceptance and roll back metadata.
        attempt = self.authority.authorizations[0][0]
        raw = self.backend.build(attempt.run_id, attempt.attempt_id, attempt.slice)
        (self.root / "objects" / request["sha256"]).write_bytes(raw)
        self.authority.pending = False
        with self.campaign().session() as campaign:
            campaign.tick()
        (self.root / "state.json").write_bytes(old_state)
        with (
            self.assertRaisesRegex(JournalError, "state_history_conflict"),
            self.campaign().session(),
        ):
            pass
        self.assertEqual(self.provider.submits, 1)


if __name__ == "__main__":
    unittest.main()
