"""Faults at the actual TCP/Store/CP boundaries; no World or GPU claim here."""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import replace

import pytest
from test_rl_segment_learner import cleanup, configuration, deliver

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from spireagent.rl_exchange import Exchange
from spireagent.storage.config import open_peer
from spireagent.storage.store import copy_artifact
from stpd.p1.local_inference import SegmentConnection
from stpd.rl.segment_learner import SegmentLearner


@pytest.mark.parametrize("operation", ["commit", "episode_end"])
@pytest.mark.parametrize("fault", ["before_accept", "after_accept"])
def test_lost_control_reply_resolves_original_input_once(
        tmp_path, monkeypatch, operation, fault):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    seen = []
    actual = owner.pool.control

    def observe(request):
        seen.append(request["operation"])
        return actual(request)

    owner.pool.control = observe

    class LostReply(SegmentConnection):
        injected = False

        def _request_once(self, kind, fields):
            if kind != operation or self.injected:
                return super()._request_once(kind, fields)
            self.injected = True
            if fault == "after_accept":
                super()._request_once(kind, fields)
            # The caller receives no application result. The reconnection and
            # query below are real TCP, rather than a fake receipt dictionary.
            raise ConnectionResetError("injected lost original control reply")

    try:
        records = deliver(owner, tmp_path, "fault", monkeypatch, connection_type=LostReply)
        assert owner.queue.rows == 2 and not owner.queue.leases and not owner.actor_episodes
        assert owner.input_sequence == 4 and len(owner.exchange.input_records(0)) == 4
        assert seen.count(operation) == 1 and "receipt" in seen
        before = copy.deepcopy(owner.queue.state())
        assert owner.control({"operation": "commit", **records[0]}) == {
            "segment_id": records[0]["segment_id"]}
        assert owner.queue.state() == before and owner.input_sequence == 4
        with pytest.raises(BoundaryError, match="input_key_reused_with_different_request"):
            owner.control({"operation": "commit", **records[0], "rows": 1})
        assert not owner.input_fenced
    finally:
        cleanup(owner)


def test_checkpoint_then_acked_tail_cold_store_restore_and_later_offset(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    archive = {"kind": "local", "directory": str(tmp_path / "archive")}
    config = replace(config, peer={**config.peer, "archive": archive},
        segments=replace(config.segments, archive_min_free_bytes=0, archive_poll_seconds=.02))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = second = None
    closed = False
    try:
        owner.checkpoint(final=True)
        cp0 = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        deliver(owner, tmp_path, "first", monkeypatch)
        deliver(owner, tmp_path, "second", monkeypatch)
        expected_queue = owner.queue.state()
        assert owner.input_sequence == 8 and owner.updates == owner.first_use == 0
        assert owner.exchange.drain_archive(20)["full_readback"]
        cleanup(owner)
        closed = True  # Kill/replacement boundary: CP predates every ACK above.

        peer = {"kind": "local", "directory": str(tmp_path / "empty-store"), "archive": archive}
        open_peer(peer).archive.seed_exchange(config.run)
        replacement = SegmentLearner(replace(config, peer=peer, resume_checkpoint=cp0),
                                     tmp_path / "empty-work", io)
        assert replacement.queue.state() == expected_queue
        assert replacement.input_sequence == replacement.replayed_inputs == 8
        assert replacement.updates == replacement.first_use == 0
        assert not replacement.queue.leases and not replacement.actor_episodes
        for name, value in owner.model.state_dict().items():
            assert value.equal(replacement.model.state_dict()[name]), name
        replacement.checkpoint(final=True)
        cp1 = replacement.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        deliver(replacement, tmp_path, "third", monkeypatch)
        assert replacement.input_sequence == 12
        assert replacement.exchange.drain_archive(20)["full_readback"]
        cleanup(replacement)
        replacement = None

        peer2 = {**peer, "directory": str(tmp_path / "second-empty-store")}
        store2 = open_peer(peer2)
        store2.archive.seed_exchange(config.run)
        original_get = store2.blobs.get

        def reject_old_input_bodies(key):
            if key.startswith(f"exchange/rl/{config.run}/inputs/"):
                assert int(key.rpartition("/")[2][:-5]) >= 8  # CP head + its tail only.
            return original_get(key)

        # The shared ArchiveBlobs instance isn't retained across open_peer calls;
        # hook the underlying class while leaving all other root reads intact.
        from spireagent.storage.async_archive import ArchiveBlobs

        get = ArchiveBlobs.get

        def checked_get(blobs, key):
            if blobs.root == store2.blobs.root:
                return reject_old_input_bodies(key)
            return get(blobs, key)

        monkeypatch.setattr(ArchiveBlobs, "get", checked_get)
        # original_get was bound before replacing the method.
        second = SegmentLearner(replace(config, peer=peer2, resume_checkpoint=cp1),
                                tmp_path / "second-empty-work", io)
        assert second.replayed_inputs == 4 and second.input_sequence == 12
        assert second.queue.rows == 6  # Every original accepted row, exactly once.
        assert second._prepare()
        second._step()
        assert second.updates == 1 and second.first_use == 2
    finally:
        if not closed:
            cleanup(owner)
        if replacement is not None:
            cleanup(replacement)
        if second is not None:
            cleanup(second)


def test_unreceived_producer_end_reconciles_from_original_input(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = None
    try:
        for key, value in owner.pool.environment().items():
            monkeypatch.setenv(key, value)
        client = SegmentConnection(owner.pool.attachment())
        client.request("episode_open", actor="original", episode="original-episode")
        grant = client.request("begin", actor="original", episode="original-episode",
                               previous_lease=None)
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        original_end = owner.exchange.persist_episode_end({"actor": "original",
            "episode": "original-episode", "run_id": None, "terminal": None,
            "outcome_states": [], "hp_battles": [], "unqualified": True}, owner.producer)
        assert owner.input_sequence == 2 and grant["lease"] in owner.queue.leases
        client.close()
        replacement = SegmentLearner(replace(config, resume_checkpoint=cp),
                                     tmp_path / "replacement", io)
        assert replacement.replayed_inputs == 0 and replacement.input_sequence == 3
        assert not replacement.queue.leases and not replacement.actor_episodes
        assert replacement.exchange.input_receipt(original_end)["response"] == {"run_id": None}
        replacement.checkpoint(final=True)
        assert replacement.exchange.latest_checkpoint()["checkpoint_sequence"] == 2
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)


@pytest.mark.parametrize("point", ["uncertain_write", "partial_apply"])
def test_uncertain_input_or_partial_apply_fences_before_next_input_or_checkpoint(
        tmp_path, monkeypatch, point):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = None
    try:
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        if point == "uncertain_write":
            actual = owner.exchange.blobs.put_if_absent

            def fail_after_write(key, raw):
                result = actual(key, raw)
                if "/inputs/" in key:
                    raise OSError("injected ambiguous durable input write")
                return result

            monkeypatch.setattr(owner.exchange.blobs, "put_if_absent", fail_after_write)
        else:
            from stpd.rl.segment_learner import SegmentLearner as Owner

            actual_accept = Owner._accept

            def half_apply(self, request, response, apply):
                def fail_after_apply():
                    apply()
                    raise OSError("injected half-apply before consumer offset")

                return actual_accept(self, request, response, fail_after_apply)

            monkeypatch.setattr(owner, "_accept", half_apply.__get__(owner))
        with pytest.raises(OSError, match="injected"):
            owner.control({"operation": "episode_open", "actor": "a", "episode": "e"})
        assert owner.input_fenced and owner.input_sequence == 0
        with pytest.raises(BoundaryError, match="input_owner_fenced"):
            owner.checkpoint(final=True)
        with pytest.raises(BoundaryError, match="input_owner_fenced"):
            owner.control({"operation": "episode_open", "actor": "b", "episode": "f"})
        assert len(owner.exchange.input_records(0)) == 1
        replacement = SegmentLearner(replace(config, resume_checkpoint=cp),
                                     tmp_path / "replacement", io)
        assert replacement.input_sequence == replacement.replayed_inputs == 1
        assert replacement.actor_episodes == {"a:e": "e"} and not replacement.input_fenced
        began = time.monotonic()
        with pytest.raises(BoundaryError, match="input_owner_fenced"):
            owner.run(initialize_only=True)
        assert time.monotonic() - began < 3  # No impossible 600s input drain after fencing.
        assert owner.exchange.latest_checkpoint()["checkpoint_artifact_id"] == cp
        assert owner.pool.stopping.is_set()
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)


@pytest.mark.parametrize("changed", ["outcome", "hp", None])
def test_terminal_labels_bind_to_original_terminal_segment_before_any_pool_apply(
        tmp_path, monkeypatch, changed):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)

    def alter(end):
        if changed == "outcome":
            from stpd.rl.heads import class_reward_values

            end["terminal"] = {**end["terminal"], "class": 4,
                               "reward": float(class_reward_values()[4])}
            for row in end["outcome_states"]:
                row["outcome"] = 4
        elif changed == "hp":
            for battle in end["hp_battles"]:
                battle["post_hp"] = 42
                for row in battle["states"]:
                    row["hp"] = .42

    try:
        if changed is None:
            deliver(owner, tmp_path, "natural", monkeypatch, battle=True, natural=True)
            assert len(owner.terminal.groups) == len(owner.hp.groups) == 1
            assert owner.input_sequence == 6 and not owner.actor_episodes
        else:
            with pytest.raises(ValueError, match="service episode_end failed"):
                deliver(owner, tmp_path, "natural", monkeypatch, battle=True, natural=True,
                        end_transform=alter)
            assert not owner.terminal.groups and not owner.hp.groups
            assert owner.input_sequence == 5 and not owner.input_fenced
            assert owner.queue.rows == 4  # Earlier independently accepted segments remain.
    finally:
        cleanup(owner)


def test_archive_outage_pauses_new_grants_and_actor_offer_but_allows_existing_close(
        tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    archive = {"kind": "local", "directory": str(tmp_path / "archive")}
    config = replace(config, peer={**config.peer, "archive": archive},
        segments=replace(config.segments, archive_pending_bytes=1,
                         archive_min_free_bytes=0, archive_poll_seconds=.02))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    blocked, entered = threading.Event(), threading.Event()
    client = waiting_client = None
    waiting_thread = offer_thread = None
    answers, offers, errors = [], [], []
    try:
        for key, value in owner.pool.environment().items():
            monkeypatch.setenv(key, value)
        client = SegmentConnection(owner.pool.attachment())
        waiting_client = SegmentConnection(owner.pool.attachment())
        for actor in ("current", "waiting"):
            owner.exchange.drain_archive(15)
            owner.archive._refresh_capacity()
            client.request("episode_open", actor=actor, episode=f"{actor}-e")
        owner.exchange.drain_archive(15)
        owner.archive._refresh_capacity()
        grant = client.request("begin", actor="current", episode="current-e",
                               previous_lease=None)
        owner.exchange.drain_archive(15)
        original_put = owner.archive.target.blobs.put_if_absent

        def unavailable(key, raw):
            entered.set()
            if not blocked.wait(15):
                raise OSError("injected archive outage expired")
            return original_put(key, raw)

        monkeypatch.setattr(owner.archive.target.blobs, "put_if_absent", unavailable)
        artifact = Manifest("analysis", owner.producer,
            payloads=(owner.exchange.store.put_bytes("evidence", b"x" * 1024**2),))
        owner.exchange.store.publish(artifact)
        assert entered.wait(2)
        owner.archive._refresh_capacity()
        state = client.request("status")
        assert state["accepting_new_segments"] and not state["archive_capacity"]["ready"]
        assert state["archive_capacity"]["pending_bytes_estimate"] >= 2 * 1024**2

        def wait_for_grant():
            try:
                answers.append(waiting_client.request("begin", actor="waiting",
                                                       episode="waiting-e", previous_lease=None))
            except Exception as error:
                errors.append(error)

        waiting_thread = threading.Thread(target=wait_for_grant, daemon=True)
        waiting_thread.start()
        from spireagent.workbench.rl_actor import Actor

        actor = Actor.__new__(Actor)
        actor.signalled, actor.segment_stop_reason = None, None
        actor.work, actor.service = tmp_path / "owner", client
        actor.run_deadline = time.monotonic() + 120
        # Actor uses a separate real connection from the close below.
        actor.service = SegmentConnection(owner.pool.attachment())
        offer_thread = threading.Thread(target=lambda: offers.append(actor._stop_before_offer()),
                                        daemon=True)
        offer_thread.start()
        time.sleep(.1)
        assert not answers and not offers and grant["lease"] in owner.queue.leases
        # Existing close uses local WAL, not the blocked R2 copy worker.
        assert client.request("episode_end", actor="current", episode="current-e", run_id=None,
            terminal=None, outcome_states=[], hp_battles=[])["run_id"] is None
        assert grant["lease"] not in owner.queue.leases and not answers
        blocked.set()
        owner.exchange.drain_archive(15)
        owner.archive._refresh_capacity()
        waiting_thread.join(3)
        offer_thread.join(3)
        assert not errors and answers[0]["lease"] == "waiting/0" and offers == [False]
        assert actor.segment_stop_reason is None
        actor.service.close()
        client.request("episode_end", actor="waiting", episode="waiting-e", run_id=None,
                       terminal=None, outcome_states=[], hp_battles=[])
    finally:
        blocked.set()
        with owner.condition:
            owner.newlease_stopped = True
            owner.condition.notify_all()
        if waiting_thread:
            waiting_thread.join(3)
        if offer_thread:
            offer_thread.join(3)
        if waiting_client:
            waiting_client.close()
        if client:
            client.close()
        cleanup(owner)


def test_restore_accepted_tail_keeps_rows_beyond_current_admission_threshold(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = replace(config, segments=replace(config.segments, capacity_rows=6))
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = again = None
    try:
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        for actor in ("first", "second"):
            deliver(owner, tmp_path, actor, monkeypatch)
        assert owner._prepare() and owner.queue.rows == 0
        for actor in ("third", "fourth", "fifth"):
            deliver(owner, tmp_path, actor, monkeypatch)
        # The original process freed its first frozen dataset after CP. A
        # rollback cannot pretend those rows were already optimized at that CP.
        assert owner.queue.rows == 6 and owner.active is not None
        replacement = SegmentLearner(replace(config, resume_checkpoint=cp),
                                     tmp_path / "replacement", io)
        assert replacement.queue.rows == 10 and replacement.replayed_inputs == 20
        assert not replacement.queue.reserve("fresh/0")  # Normal grants remain bounded.
        replacement.checkpoint(final=True)
        recovered_cp = replacement.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        again = SegmentLearner(replace(config, resume_checkpoint=recovered_cp),
                               tmp_path / "again", io)
        assert again.queue.rows == 10 and again.replayed_inputs == 0
        assert not again.queue.reserve("fresh/0")
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)
        if again is not None:
            cleanup(again)


def test_missing_exchange_log_fails_before_new_input_and_normal_exact_seed_recovers(
        tmp_path, monkeypatch):
    from test_rl_segment_learner import copy_input_exchange

    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = None
    try:
        deliver(owner, tmp_path, "first", monkeypatch)
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        peer = {"kind": "local", "directory": str(tmp_path / "empty-peer")}
        replica = Exchange(peer, config.run)
        copy_artifact(owner.exchange.store, replica.store, cp, scratch=tmp_path)
        replica.publish_model(owner.exchange.model(0))
        with pytest.raises(BoundaryError, match="input_log_missing_seed_original_exchange"):
            SegmentLearner(replace(config, peer=peer, resume_checkpoint=cp),
                           tmp_path / "incomplete-restore", io)
        copy_input_exchange(owner, replica)  # Original immutable records, byte-for-byte.
        replacement = SegmentLearner(replace(config, peer=peer, resume_checkpoint=cp),
                                     tmp_path / "complete-restore", io)
        assert replacement.input_sequence == 4 and replacement.replayed_inputs == 0
        assert replacement.control({"operation": "episode_open", "actor": "new",
                                    "episode": "new-e"})["admitted"]
        assert replacement.input_sequence == 5 and not replacement.input_fenced
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)


def test_restoring_old_theta_continues_publication_and_checkpoint_high_water(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    replacement = None
    try:
        owner.checkpoint(final=True)
        cp = owner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
        owner._schedule_publication()
        owner._collect_publication(wait=True)
        owner.checkpoint(final=True)
        original_version = owner.exchange.blobs.get(f"{owner.exchange.prefix}models/000001.json")
        assert owner.version == 1 and owner.checkpoint_sequence == 2
        replacement = SegmentLearner(replace(config, resume_checkpoint=cp,
                                             republish_on_resume=True),
                                     tmp_path / "replacement", io)
        assert replacement.version == 2 and replacement.updates == replacement.first_use == 0
        replacement.checkpoint(final=True)
        assert replacement.exchange.latest_checkpoint()["checkpoint_sequence"] == 3
        assert replacement.exchange.blobs.get(
            f"{replacement.exchange.prefix}models/000001.json") == original_version
    finally:
        cleanup(owner)
        if replacement is not None:
            cleanup(replacement)
