"""Real spawn/IPC boundary and immutable selection/late-label counterexamples."""

from __future__ import annotations

import copy
import dataclasses as dc
import hashlib
import random
from contextlib import contextmanager
from pathlib import Path

import pytest
import torch
from test_rl41_w1 import model as model
from test_rl41_w1 import request, segment, source
from test_rl_tensor_segment import batch_digest, encoder

from spireagent.rl_dataplane_ports import TENSOR_SEGMENT_FORMAT, BinaryRef
from stpd.rl import batch_preparation as bp
from stpd.rl.binary_preparation import BinaryBatchPreparer
from stpd.rl.prepared_batch import AuxiliarySelection
from stpd.rl.tensor_segment import decode_packet


class BytesProvider:
    def __init__(self):
        self.packets = {}
        self.borrows = 0
        self.active = 0

    def __getstate__(self):
        raise AssertionError("provider must never cross spawn boundary")

    def add(self, encoded, slot):
        ref = BinaryRef(
            TENSOR_SEGMENT_FORMAT,
            slot,
            hashlib.sha256(encoded.packet).hexdigest(),
            len(encoded.packet),
        )
        self.packets[ref] = encoded.packet
        return ref

    @contextmanager
    def borrow(self, ref):
        self.borrows += 1
        self.active += 1
        try:
            assert hashlib.sha256(self.packets[ref]).hexdigest() == ref.sha256
            yield memoryview(self.packets[ref])
        finally:
            self.active -= 1

    def retain(self, ref, owner):
        raise AssertionError("borrow is the complete copy lifetime")

    def release(self, ref, owner):
        raise AssertionError("provider business ownership belongs to P2")


def prepare(model, tmp_path, provider, **options):
    return BinaryBatchPreparer(
        provider=provider,
        vocabulary=model.vocabulary,
        workers=2,
        threads=1,
        scratch=tmp_path,
        source_slots=4,
        max_source_bytes=32 * 1024**2,
        **options,
    )


def admit(enc, preparer, provider, value, slot):
    encoded = enc.encode(value)
    ref = provider.add(encoded, f"{value['run']}/{value['training_generation']}/{slot}")
    preparer.prefetch(ref, encoded.envelope)
    member = dc.replace(request([value]).members[0], payload=ref)
    return member, encoded


def labels_for(value, payload):
    source, _ = decode_packet(payload.packet)
    refs = source.row_refs
    return {
        "run_id": value["run_id"],
        "episode": value["identity"]["intent_id"],
        "training_generation": value["training_generation"],
        "typed_original": "c" * 64,
        "terminal": value["terminal"],
        "segments": [],
        "outcome_states": [{"source": ref, "outcome": 3} for ref in refs],
        "hp_battles": [
            {
                "battle_id": "public-terminal-battle",
                "post_hp": 0,
                "states": [{"source": ref, "hp": 0.0} for ref in refs],
            }
        ],
    }


def test_spawn_full_aux_hash_request_rng_and_provider_lifetime(model, tmp_path):
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "prepare", provider, assembly_workers=3)
    enc = encoder(model, tmp_path / "spool")
    value = segment(model, terminal=True)
    try:
        member, encoded = admit(enc, preparer, provider, value, "current")
        labels = labels_for(value, encoded)
        labels["segments"] = [
            {"segment_id": member.segment_id, "payload": dc.asdict(member.payload)}
        ]
        groups = preparer.verify_labels(labels, (member,)).result(timeout=30)
        snapshots = tuple(row["prepared_input"] for _, _, rows in groups for row in rows)
        selections = tuple(
            AuxiliarySelection(
                row["source"],
                kind,
                row["outcome"] if kind == "terminal" else row["hp"],
                0,
                2 if kind == "terminal" else 1,
            )
            for kind, _, rows in groups
            for row in rows
        )
        req = dc.replace(
            request([value]),
            members=(member,),
            auxiliary=selections,
            auxiliary_minibatches=(0,) * len(selections),
        )
        rng, torch_rng = random.getstate(), torch.get_rng_state().clone()
        batch = preparer.submit(req, snapshots).result(timeout=30)
        old = bp.assemble(req, (source(model, value),))
        assert batch_digest(batch) == batch_digest(old)
        assert batch.request == req and batch.request.selection_rng_state == req.selection_rng_state
        assert batch.terminal_mask.sum() == batch.hp_mask.sum() == value["rows"]
        assert batch.worker_pids and provider.borrows == 1 and provider.active == 0
        assert random.getstate() == rng and torch.equal(torch.get_rng_state(), torch_rng)
        assert all(selection.uses_before == 0 for selection in req.auxiliary)
        summary = preparer.summary()
        assert summary["source_workers"] == 2 and summary["assembly_workers"] == 3
        assert 1 <= summary["actual_assembly_workers"] <= 3
        assert sum(row["tasks"] for row in summary["workers"]) >= 2
        assert all(0 <= row["busy_fraction"] <= 1 for row in summary["workers"])
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_late_aux_packet_survives_PPO_retirement_and_verifies_original_public_hp(model, tmp_path):
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "prepare", provider)
    enc = encoder(model, tmp_path / "spool")
    late = segment(model, name="late/0", run_id="late-episode", terminal=True)
    current = segment(model, name="current/0", run_id="current-episode")
    try:
        old_member, encoded = admit(enc, preparer, provider, late, "late")
        labels = labels_for(late, encoded)
        labels["segments"] = [
            {"segment_id": old_member.segment_id, "payload": dc.asdict(old_member.payload)}
        ]
        (aux_packet,) = enc.auxiliary(labels)
        ref = provider.add(aux_packet, f"{late['run']}/{late['training_generation']}/aux")
        preparer.prefetch(ref, aux_packet.envelope)
        member = dc.replace(old_member, payload=ref)
        # Canonical close still names the original PPO ref, not the later aux
        # upload. Only physical snapshots handed to the pool move to the aux ref.
        groups = preparer.verify_labels(labels, (member,)).result(timeout=30)
        assert all(
            row["prepared_input"].member.payload == ref for _, _, rows in groups for row in rows
        )
        wrong_ref = copy.deepcopy(labels)
        wrong_ref["segments"][-1]["payload"]["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="original_PPO_reference"):
            preparer.verify_labels(wrong_ref, (member,)).result(timeout=30)
        items = tuple(row["prepared_input"] for _, _, rows in groups for row in rows)
        bad = copy.deepcopy(labels)
        bad["hp_battles"][0]["post_hp"] = 99
        with pytest.raises(ValueError, match="public_hp_scale"):
            preparer.verify_labels(bad, (member,)).result(timeout=30)
        member_current, _ = admit(enc, preparer, provider, current, "current")
        selection = AuxiliarySelection(items[0].source, "terminal", 3, 0, 2)
        req = dc.replace(
            request([current]),
            members=(member_current,),
            auxiliary=(selection,),
            auxiliary_minibatches=(0,),
        )
        provider.packets.clear()  # only the verified AuxPool body remains after retirement
        preparer.barrier(generation=late["training_generation"])
        # Current PPO is re-admitted separately; old aux snapshots supply their own bytes.
        current_encoded = enc.encode(current)
        provider.packets[member_current.payload] = current_encoded.packet
        preparer.prefetch(member_current.payload, current_encoded.envelope)
        batch = preparer.submit(req, items).result(timeout=30)
        original_late = source(model, late)
        aux_frames = tuple(
            bp.AuxiliaryFrame(r, f, floor)
            for r, f, floor in zip(
                original_late.row_refs, original_late.frames, original_late.floors, strict=True
            )
        )
        old = bp.assemble(req, (source(model, current),), auxiliary_frames=aux_frames)
        assert batch_digest(batch) == batch_digest(old)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_bounds_identity_output_partition_and_barrier(model, tmp_path):
    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "prepare", provider)
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    try:
        member, encoded = admit(enc, preparer, provider, value, "current")
        with pytest.raises(ValueError, match="same_reference_changed"):
            preparer.prefetch(member.payload, dict(encoded.envelope, actor="other"))
        req = dc.replace(request([value]), members=(member,))
        tiny = dc.replace(req, max_output_bytes=1)
        with pytest.raises(ValueError, match="byte_budget"):
            preparer.submit(tiny).result(timeout=30)
        with pytest.raises(ValueError, match="partition"):
            preparer.submit(dc.replace(req, minibatch_rows=((0,),))).result(timeout=30)
        names = [a.name for a in preparer.arenas]
        preparer.barrier(generation="g-two")
        assert preparer.last_barrier_failures
        assert not preparer.sources and not preparer.arenas
        for name in names:
            assert not Path(name).exists()
        with pytest.raises(ValueError, match="prefetch_generation"):
            preparer.prefetch(member.payload, encoded.envelope)
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


def test_binary_PPO_and_aux_keep_original_seed_exclusion_and_run_checks(model, tmp_path):
    value = segment(model)
    enc = encoder(model, tmp_path / "spool")
    encoded = enc.encode(value)
    for mode in ("excluded", "foreign-run"):
        provider = BytesProvider()
        options = (
            {"excluded_seeds": frozenset((value["identity"]["seed"],))}
            if mode == "excluded"
            else {}
        )
        preparer = prepare(model, tmp_path / mode, provider, **options)
        try:
            ref = provider.add(encoded, "opaque-binary-locator")
            preparer.prefetch(ref, encoded.envelope)
            req = request([value])
            req = dc.replace(req, members=(dc.replace(req.members[0], payload=ref),))
            if mode == "foreign-run":
                req = dc.replace(req, identity=dc.replace(req.identity, run="another-run"))
            with pytest.raises(ValueError, match="held_out_or_not_TRAIN|source_run_changed"):
                preparer.submit(req).result(timeout=30)
        finally:
            preparer.shutdown(wait=True, cancel_futures=True)


def test_evicted_source_refills_from_immutable_packet_without_run_long_metadata(model, tmp_path):
    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(3)]
    packets = [enc.encode(v) for v in values]
    cap = 2 * max(len(p.packet) for p in packets) + 8
    preparer = BinaryBatchPreparer(
        provider=provider,
        vocabulary=model.vocabulary,
        workers=1,
        threads=1,
        scratch=tmp_path / "prepare",
        source_slots=1,
        max_source_bytes=cap,
    )
    try:
        refs = []
        for i, packet in enumerate(packets):
            ref = provider.add(packet, f"opaque-{i}")
            refs.append(ref)
            preparer.prefetch(ref, packet.envelope)
            preparer.source_jobs[ref].result(timeout=30)
            # Allow the completion callback to release the decode pin before
            # forcing an arena eviction. The future result alone can race it.
            with preparer.condition:
                while preparer.arenas[0].users:
                    preparer.condition.wait(0.01)
        assert refs[0] not in preparer.sources
        req = request([values[0]])
        req = dc.replace(req, members=(dc.replace(req.members[0], payload=refs[0]),))
        batch = preparer.submit(req).result(timeout=30)
        assert batch_digest(batch) == batch_digest(bp.assemble(req, (source(model, values[0]),)))
        assert provider.borrows == 4
        assert len(preparer.sources) <= 2
    finally:
        preparer.shutdown(wait=True, cancel_futures=True)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires an authorized CUDA allocation")
def test_pinned_double_buffer_event_lifetime_and_barrier_cancels_waiting_pin(model, tmp_path):
    from concurrent.futures import CancelledError, ThreadPoolExecutor

    from stpd.rl.binary_preparation import _PinnedBuffers

    value = segment(model)
    batch = bp.assemble(request([value]), (source(model, value),))
    pool = _PinnedBuffers(64 * 1024**2)
    one = pool.pin(batch, pool.epoch)
    two = pool.pin(batch, pool.epoch)
    assert one.floors.is_pinned() and two.floors.is_pinned()
    assert one.floors.untyped_storage().data_ptr() != two.floors.untyped_storage().data_ptr()
    device_batch, copies = bp.stage_batch(one, torch.device("cuda"))
    assert copies == 3
    assert torch.equal(device_batch.actions.cpu(), batch.actions)
    owner = one.actions._rl_pinned_lease
    assert owner.events and all(event.query() for event in owner.events)
    with ThreadPoolExecutor(max_workers=1) as worker:
        blocked = worker.submit(pool.pin, batch, pool.epoch)
        pool.fence()
        with pytest.raises(CancelledError, match="fenced"):
            blocked.result(timeout=10)
    pool.close()


def test_barrier_waits_actual_spawn_worker_and_releases_cancelled_public_future(model, tmp_path):
    import threading
    from concurrent.futures import CancelledError

    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "prepare", provider)
    enc = encoder(model, tmp_path / "spool")
    value = segment(model)
    unblock = threading.Event()
    blockers = []
    try:
        member, _ = admit(enc, preparer, provider, value, "cancelled")
        # Queue the public collector behind two independent waits, while the
        # real spawn worker owns the immutable source descriptor.
        started = [threading.Event(), threading.Event()]

        def hold(index):
            started[index].set()
            unblock.wait(10)

        blockers = [preparer.coordinator.submit(hold, i) for i in range(2)]
        assert all(event.wait(5) for event in started)
        req = dc.replace(request([value]), members=(member,))
        pending = preparer.submit(req)
        assert pending.cancel()
        paths = [Path(a.name) for a in preparer.arenas]
        preparer.barrier(generation="next-generation")
        assert pending.done()
        with pytest.raises(CancelledError):
            pending.result()
        assert all(not path.exists() for path in paths)
        assert not preparer.arenas
    finally:
        unblock.set()
        for blocker in blockers:
            blocker.result(timeout=10)
        preparer.shutdown(wait=True, cancel_futures=True)


def test_label_jobs_do_not_consume_current_plus_next_preparation_credit(model, tmp_path):
    import threading

    provider = BytesProvider()
    preparer = prepare(model, tmp_path / "prepare", provider)
    enc = encoder(model, tmp_path / "spool")
    value = segment(model, terminal=True)
    release = threading.Event()
    blockers = []
    try:
        member, encoded = admit(enc, preparer, provider, value, "current")
        started = [threading.Event(), threading.Event()]

        def hold(i):
            started[i].set()
            release.wait(10)

        blockers = [preparer.coordinator.submit(hold, i) for i in range(2)]
        assert all(event.wait(5) for event in started)
        labels = labels_for(value, encoded)
        labels["segments"] = [
            {"segment_id": member.segment_id, "payload": dc.asdict(member.payload)}
        ]
        label_jobs = [preparer.verify_labels(labels, (member,)) for _ in range(2)]
        req = dc.replace(request([value]), members=(member,))
        current, next_batch = preparer.submit(req), preparer.submit(req)
        with pytest.raises(ValueError, match="current_plus_next"):
            preparer.submit(req)
        release.set()
        assert all(job.result(timeout=30) for job in label_jobs)
        assert current.result(timeout=30).request == next_batch.result(timeout=30).request == req
    finally:
        release.set()
        for job in blockers:
            job.result(timeout=10)
        preparer.shutdown(wait=True, cancel_futures=True)


def test_binary_ref_never_enters_historical_JSON_reader():
    ref = BinaryRef(TENSOR_SEGMENT_FORMAT, "opaque", "a" * 64, 32)
    with pytest.raises(ValueError, match="requires_binary_preparer"):
        bp._read_source(ref, None)


def test_full_arena_waits_then_resumes_after_real_reservation_release(model, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(2)]
    packets = [enc.encode(v) for v in values]
    preparer = BinaryBatchPreparer(provider=provider, vocabulary=model.vocabulary,
        workers=1, threads=1, scratch=tmp_path / "prepare", source_slots=1,
        max_source_bytes=max(len(p.packet) for p in packets) + 8, source_wait_seconds=5)
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    held = ()
    try:
        preparer.prefetch(refs[0], packets[0].envelope)
        preparer.source_jobs[refs[0]].result(timeout=30)
        with preparer.condition:
            held, _ = preparer._reserve((refs[0],))
        from spireagent.json_boundary import BoundaryError

        preparer.source_wait_seconds = .01
        with pytest.raises(BoundaryError) as error:
            preparer.prefetch(refs[1], packets[1].envelope)
        assert error.value.code == "CAPACITY_WAIT" and refs[0] in preparer.sources
        preparer.source_wait_seconds = 5
        waiting = threading.Event()
        real_wait = preparer._wait_for_source
        def observed(deadline):
            waiting.set()
            return real_wait(deadline)
        preparer._wait_for_source = observed
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(preparer.prefetch, refs[1], packets[1].envelope)
            try:
                assert waiting.wait(3) and not future.done()
                preparer._release(held)
                held = ()
                future.result(timeout=3)
                assert refs[1] in preparer.sources
            finally:
                if held:
                    preparer._release(held)
                    held = ()
        assert preparer.summary()["source_capacity_wait_seconds"] > 0
    finally:
        if held:
            preparer._release(held)
        preparer.shutdown()
