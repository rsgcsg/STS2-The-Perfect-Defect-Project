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
        from stpd.rl.segment_learner import SegmentLearner
        from stpd.rl.segments import AuxiliaryPool

        owner = SegmentLearner.__new__(SegmentLearner)
        owner.terminal, owner.hp = AuxiliaryPool(256, 2), AuxiliaryPool(128, 1)
        # The provider deliberately rejects long-lived business pins; verified
        # Binary AuxPool snapshots own their bytes independently of that spool.
        owner._retain, owner._release = provider.retain, provider.release
        owner._add_auxiliary(groups)
        assert owner.terminal.groups and owner.hp.groups
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
        # Two rows of the same aux-only source: the worker unpacks those rows alone.
        rows = list({item.source["row"]: item for item in items}.values())[:2]
        assert len(rows) == 2
        selections = tuple(AuxiliarySelection(item.source, "terminal", 3, 0, 2) for item in rows)
        req = dc.replace(
            request([current]),
            members=(member_current,),
            auxiliary=selections,
            auxiliary_minibatches=(0, 0),
        )
        provider.packets.clear()  # only the verified AuxPool body remains after retirement
        preparer.barrier(generation=late["training_generation"])
        # Current PPO is re-admitted separately; old aux snapshots supply their own bytes.
        current_encoded = enc.encode(current)
        provider.packets[member_current.payload] = current_encoded.packet
        preparer.prefetch(member_current.payload, current_encoded.envelope)
        reserve = preparer._reserve
        def evicted_between_prefetch_and_reservation(refs, snapshots=None):
            # Reproduce the cache-eviction boundary after an aux upload has
            # already retired. Its immutable AuxPool bytes must still suffice.
            for ref in snapshots or {}:
                preparer.sources.pop(ref, None)
                preparer.source_jobs.pop(ref, None)
            return reserve(refs, snapshots)
        preparer._reserve = evicted_between_prefetch_and_reservation
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


@pytest.mark.skipif(not torch.cuda.is_available(), reason="requires an authorized CUDA allocation")
def test_pinned_slab_stage_matches_unpack_pin_stage(model, tmp_path):
    """WP8 stage thread: pinning the file's slabs and mapping the tree once gives
    the same device batch as unpack + pin + stage_batch (three tree walks)."""
    import mmap

    from stpd.rl.binary_preparation import _PinnedBuffers, _write_batch
    from stpd.rl.sgd_process import mapped_arrays

    value = segment(model)
    batch = bp.assemble(request([value]), (source(model, value),))
    path = tmp_path / "ready.bin"
    with path.open("wb") as stream:
        stream.truncate(1 << 24)
    packed = _write_batch(batch, str(path))
    specs = [(v.offset, v.count, v.dtype) for v in packed.arrays]
    with path.open("rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    device = torch.device("cuda")
    pool = _PinnedBuffers(64 * 1024**2)
    old, _ = bp.stage_batch(pool.pin(bp.unpack_transport(
        (packed.tree, mapped_arrays(mapping, specs))), pool.epoch), device)
    slabs, lease = pool.pin_slabs(mapped_arrays(mapping, specs), pool.epoch)
    new, copies = bp.stage_pinned_slabs(packed.tree, slabs, lease, device)
    del slabs, lease
    torch.cuda.synchronize()
    assert copies == len(specs)

    def tensors(value):
        found = []
        shape = bp._map_structure(value, lambda node: found.append(node) or (
            "tensor", node.dtype, tuple(node.shape), node.device.type)
            if isinstance(node, torch.Tensor) else node)
        return shape, found

    (old_shape, old_tensors), (new_shape, new_tensors) = tensors(old), tensors(new)
    assert old_shape == new_shape and old_tensors
    for left, right in zip(old_tensors, new_tensors, strict=True):
        assert right.is_cuda and torch.equal(left, right)
    assert torch.equal(new.actions.cpu(), batch.actions)
    del old, new, old_tensors, new_tensors
    pool.close()
    mapping.close()


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


def test_process_pool_callbacks_never_wait_for_preparer_lock(model, tmp_path):
    # Submitters hold preparer.condition across pool.submit. A pool manager
    # thread that waits for it stops draining results (Friday R5 hang).
    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(2)]
    packets = [enc.encode(v) for v in values]
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    preparer = prepare(model, tmp_path / "prepare", provider)
    try:
        with preparer.condition:
            for ref, packet in zip(refs, packets, strict=True):
                preparer.prefetch(ref, packet.envelope)
                preparer.source_jobs[ref].result(timeout=20)
        preparer.callbacks.submit(lambda: None).result(timeout=20)
        assert all(arena.users == 0 for arena in preparer.arenas)
    finally:
        preparer.shutdown()


def test_source_copy_runs_outside_preparer_lock_and_barrier_waits_for_it(model, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    class SlowProvider(BytesProvider):
        def __init__(self):
            super().__init__()
            self.entered, self.go = threading.Event(), threading.Event()

        @contextmanager
        def borrow(self, ref):
            with super().borrow(ref) as raw:
                if ref.slot == "slow":
                    self.entered.set()
                    assert self.go.wait(10)
                yield raw

    provider = SlowProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(2)]
    packets = [enc.encode(v) for v in values]
    slow, fast = (provider.add(p, s) for p, s in zip(packets, ("slow", "fast"), strict=True))
    preparer = prepare(model, tmp_path / "prepare", provider)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            copying = pool.submit(preparer.prefetch, slow, packets[0].envelope)
            assert provider.entered.wait(10)
            # Another source copies and the preparer lock is free meanwhile.
            preparer.prefetch(fast, packets[1].envelope)
            assert fast in preparer.sources and slow in preparer.copying
            fencing = pool.submit(preparer.barrier, generation="next")
            with pytest.raises(TimeoutError):
                fencing.result(timeout=.3)
            provider.go.set()
            with pytest.raises(RuntimeError, match="fenced_or_closed"):
                copying.result(timeout=10)
            fencing.result(timeout=30)
        assert not preparer.copying and not preparer.sources and not preparer.arenas
    finally:
        provider.go.set()
        preparer.shutdown()


def test_persistent_pools_warm_every_worker_and_survive_barriers(model, tmp_path):
    with pytest.raises(ValueError, match="persistent_pools"):
        prepare(model, tmp_path / "bad", BytesProvider(), persistent_pools=1)
    provider = BytesProvider()
    spools = {}
    plain = prepare(model, tmp_path / "plain", provider)
    kept = prepare(model, tmp_path / "kept", provider, persistent_pools=True)
    try:
        # Every worker of the three pools is started before the first batch.
        assert len(kept.pool._processes) == kept.workers
        assert len(kept.assembly_pool._processes) == kept.assembly_workers
        assert kept.summary()["pool_warmups"][0]["workers"]["source"] >= 1
        assert not plain.pool._processes  # the original pools spawn lazily

        def run(prep, value, slot):
            # One spool per (preparer, generation): a spool never re-encodes a source.
            key = (id(prep), value["training_generation"], value["run_id"])
            enc = spools.setdefault(key, encoder(model, tmp_path / f"spool-{len(spools)}"))
            member, _ = admit(enc, prep, provider, value, slot)
            req = dc.replace(request([value]), members=(member,))
            return batch_digest(prep.submit(req).result(timeout=60))

        first = segment(model)
        assert run(kept, first, "k1") == run(plain, first, "p1")
        pools = (kept.pool, kept.assembly_pool, kept.label_pool)
        pids = set(kept.pool._processes) | set(kept.assembly_pool._processes)
        names = [Path(a.name) for a in kept.arenas]
        kept.barrier(generation="g-two")
        plain.barrier(generation="g-two")
        # Same executors, same worker processes; the old arenas are gone.
        assert (kept.pool, kept.assembly_pool, kept.label_pool) == pools
        assert set(kept.pool._processes) | set(kept.assembly_pool._processes) == pids
        assert not kept.arenas and names and all(not name.exists() for name in names)
        second = segment(model, generation="g-two")
        assert run(kept, second, "k2") == run(plain, second, "p2")
        # A broken pool is replaced (and warmed), never reused.
        next(iter(kept.assembly_pool._processes.values())).kill()
        with pytest.raises(Exception):  # noqa: B017 - BrokenProcessPool or its wrapper
            run(kept, segment(model, generation="g-two", run_id="other"), "k3")
        kept.barrier(generation="g-three")
        assert kept.assembly_pool is not pools[1] and kept.pool is pools[0]
        assert len(kept.assembly_pool._processes) == kept.assembly_workers
        third = segment(model, generation="g-three")
        plain.barrier(generation="g-three")
        assert run(kept, third, "k4") == run(plain, third, "p4")
    finally:
        kept.shutdown(wait=True, cancel_futures=True)
        plain.shutdown(wait=True, cancel_futures=True)
    assert not kept.pool._processes and not kept.assembly_pool._processes


def test_array_table_crosses_ipc_as_one_buffer_with_the_same_views(tmp_path):
    import mmap
    import pickle

    import numpy as np

    from stpd.rl.binary_preparation import _ArrayTable, _SharedArray

    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(bytes(range(256)) * 4)
    b.write_bytes(bytes(reversed(range(256))) * 4)
    arrays = [_SharedArray(str(a if i % 3 else b), 8 * (i % 50), 1 + i % 7,
                           ("<f4", "<i8", "|u1")[i % 3]) for i in range(3000)]
    table = _ArrayTable.of(arrays)
    assert list(table) == arrays and len(table) == 3000
    # One int64 buffer plus the distinct names/dtypes, not 3,000 objects.
    assert len(pickle.dumps(table, protocol=5)) < table.rows.nbytes + 4096
    handles = {}
    for path in (a, b):
        with path.open("rb") as stream:
            handles[str(path)] = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    views = table.views(handles)
    for view, item in zip(views, arrays, strict=True):
        expected = np.frombuffer(handles[item.name], dtype=np.dtype(item.dtype),
                                 count=item.count, offset=item.offset)
        assert view.dtype == expected.dtype and np.array_equal(view, expected)
    del views, expected, view
    for handle in handles.values():
        handle.close()


@pytest.mark.parametrize("place", [(1016, 2, "<i8"), (1024, 1, "|u1"), (-8, 1, "<i8")])
def test_array_table_view_past_the_mapping_is_refused(tmp_path, place):
    """#456 review: a slab beyond its mapped file fails loudly, never comes back short."""
    import mmap

    from stpd.rl.binary_preparation import _ArrayTable, _SharedArray

    path = tmp_path / "a"
    path.write_bytes(bytes(1024))
    offset, count, dtype = place
    table = _ArrayTable.of([_SharedArray(str(path), 0, 4, "<i8"),
                            _SharedArray(str(path), offset, count, dtype)])
    with path.open("rb") as stream:
        handle = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        with pytest.raises(ValueError, match="binary_shared_array_out_of_bounds"):
            table.views({str(path): handle})
    finally:
        handle.close()


def _same(a, b):
    if isinstance(a, torch.Tensor):
        return isinstance(b, torch.Tensor) and a.dtype == b.dtype and torch.equal(a, b)
    if dc.is_dataclass(a) and not isinstance(a, type):
        return type(a) is type(b) and all(
            _same(getattr(a, f.name), getattr(b, f.name)) for f in dc.fields(a))
    if isinstance(a, (tuple, list)):
        return type(a) is type(b) and len(a) == len(b) and all(map(_same, a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def test_aux_only_unpack_keeps_selected_rows_and_every_other_field(model):
    """The aux-only transport unpack is the full unpack minus unselected frames."""
    import pickle

    from stpd.rl.binary_preparation import _only_rows
    from stpd.rl.tensor_segment import auxiliary_frame

    full_source = source(model, segment(model, rows=4, terminal=True))
    tree, slabs = bp.pack_transport(full_source)
    full = bp.unpack_transport((pickle.loads(pickle.dumps(tree)), slabs))
    assert _only_rows(tree, None) is tree
    part = bp.unpack_transport((_only_rows(pickle.loads(pickle.dumps(tree)), frozenset({1, 3})),
                                slabs))
    assert [f is None for f in part.frames] == [True, False, True, False]
    for field in dc.fields(full):
        if field.name != "frames":
            assert _same(getattr(part, field.name), getattr(full, field.name)), field.name
    for row in (1, 3):
        ref = full.row_refs[row]
        assert _same(auxiliary_frame(part, ref), auxiliary_frame(full, ref))
    with pytest.raises(ValueError, match="binary_auxiliary_row_binding_changed"):
        auxiliary_frame(part, full.row_refs[0])  # an unselected row is never usable


def test_worker_task_pauses_only_the_cyclic_collector():
    import gc

    from stpd.rl.binary_preparation import _collector_paused

    assert gc.isenabled()
    with _collector_paused():
        assert not gc.isenabled()
    assert gc.isenabled()
    gc.disable()
    try:
        with _collector_paused():
            assert not gc.isenabled()
        assert not gc.isenabled()  # a caller's own choice is kept
    finally:
        gc.enable()
    with pytest.raises(RuntimeError), _collector_paused():
        raise RuntimeError("task failed")
    assert gc.isenabled()



def test_worker_initializer_freezes_import_time_objects(model):
    """Per-task gc.collect must not rescan torch/stpd/compiler objects each time."""
    import gc
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    from stpd.rl.binary_preparation import _initialize

    with ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                             initializer=_initialize, initargs=(model.vocabulary, 1)) as pool:
        assert pool.submit(gc.get_freeze_count).result(timeout=120) > 10_000


def test_prepared_digest_binds_every_byte_and_the_length_on_any_thread_count():
    import hashlib
    import os

    from stpd.rl.binary_preparation import PREPARED_DIGEST, PREPARED_DIGEST_CHUNK, prepared_digest

    data = bytearray(os.urandom(2 * PREPARED_DIGEST_CHUNK + 12345))
    chunks = [bytes(data[i:i + PREPARED_DIGEST_CHUNK])
              for i in range(0, len(data), PREPARED_DIGEST_CHUNK)]
    expected = hashlib.sha256(PREPARED_DIGEST.encode() + len(data).to_bytes(8, "big")
                              + b"".join(hashlib.sha256(c).digest() for c in chunks))
    assert prepared_digest(memoryview(data)) == expected.hexdigest()
    assert prepared_digest(memoryview(data), threads=4) == expected.hexdigest()
    for position in (0, PREPARED_DIGEST_CHUNK - 1, PREPARED_DIGEST_CHUNK, len(data) - 1):
        changed = bytearray(data)
        changed[position] ^= 1
        assert prepared_digest(memoryview(changed), threads=4) != expected.hexdigest()
    assert prepared_digest(memoryview(data + b"\0")) != expected.hexdigest()
    assert prepared_digest(memoryview(b"")) != prepared_digest(memoryview(b"\0"))
    assert PREPARED_DIGEST.endswith("/16MiB")
    with pytest.raises(ValueError, match="flat_byte_view"):
        prepared_digest(memoryview(bytearray(8)).cast("I"))


def test_parallel_prefetches_under_one_arena_credit_finish_without_deadlock(model, tmp_path):
    # Several dispatch threads copy at once. A held request reservation plus
    # concurrent partial copies must only wait, then all finish exactly.
    import threading
    from concurrent.futures import ThreadPoolExecutor

    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(5)]
    packets = [enc.encode(v) for v in values]
    preparer = BinaryBatchPreparer(provider=provider, vocabulary=model.vocabulary,
        workers=1, threads=1, scratch=tmp_path / "prepare", source_slots=1,
        max_source_bytes=max(len(p.packet) for p in packets) + 8, source_wait_seconds=30)
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    held = ()
    try:
        preparer.prefetch(refs[0], packets[0].envelope)
        preparer.source_jobs[refs[0]].result(timeout=30)
        with preparer.condition:
            held, _ = preparer._reserve((refs[0],))
        waiting = threading.Semaphore(0)
        real_wait = preparer._wait_for_source

        def observed(deadline):
            waiting.release()
            return real_wait(deadline)

        preparer._wait_for_source = observed
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(preparer.prefetch, ref, packet.envelope)
                       for ref, packet in zip(refs[1:], packets[1:], strict=True)]
            assert waiting.acquire(timeout=5) and not any(f.done() for f in futures)
            preparer._release(held)
            held = ()
            timings = [f.result(timeout=60) for f in futures]
        assert all(wait >= 0 and copy >= 0 for wait, copy in timings)
        assert sum(wait for wait, _ in timings) > 0
        # Exactly one source fits; the last copied one is byte-identical.
        (ref, (index, descriptor)), = preparer.sources.items()
        arena = preparer.arenas[index]
        assert bytes(arena.mapping[descriptor.offset:descriptor.offset + ref.size]) \
            == provider.packets[ref]
        preparer.source_jobs[ref].result(timeout=30)
        preparer.callbacks.submit(lambda: None).result(timeout=20)
        assert all(a.users == 0 for a in preparer.arenas) and not preparer.copying
    finally:
        if held:
            preparer._release(held)
        preparer.shutdown()


def test_arena_copy_failure_releases_its_credit_and_fails_loudly(model, tmp_path):
    class ChangedProvider(BytesProvider):
        @contextmanager
        def borrow(self, ref):
            changed = bytearray(self.packets[ref])
            changed[-1] ^= 1
            yield memoryview(bytes(changed))

    provider = ChangedProvider()
    enc = encoder(model, tmp_path / "spool")
    packet = enc.encode(segment(model, name="actor/0", run_id="episode-0"))
    ref = provider.add(packet, "opaque-0")
    preparer = prepare(model, tmp_path / "prepare", provider)
    try:
        with pytest.raises(ValueError, match="binary_provider_integrity_changed"):
            preparer.prefetch(ref, packet.envelope)
        assert ref not in preparer.sources and not preparer.copying
        assert all(a.users == 0 for a in preparer.arenas)
    finally:
        preparer.shutdown()


def test_prefetch_many_copies_a_batch_in_order_with_two_lock_holds(model, tmp_path):
    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    values = [segment(model, name=f"actor/{i}", run_id=f"episode-{i}") for i in range(4)]
    packets = [enc.encode(v) for v in values]
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    preparer = prepare(model, tmp_path / "prepare", provider)
    holds = []
    real = preparer._reserve_copy

    def counted(ref, envelope):
        holds.append(preparer.condition._is_owned())
        return real(ref, envelope)

    preparer._reserve_copy = counted
    try:
        timing = preparer.prefetch_many(
            [(ref, p.envelope) for ref, p in zip(refs, packets, strict=True)])
        assert set(timing) == {"reserve_lock_wait", "reserve", "credit_wait", "copy",
                               "publish_lock_wait", "publish"}
        assert timing["credit_wait"] == 0 and all(holds) and len(holds) == 4
        offsets = [preparer.sources[ref][1].offset for ref in refs]
        assert offsets == sorted(offsets)  # reserved in member order
        for ref in refs:
            index, descriptor = preparer.sources[ref]
            assert bytes(preparer.arenas[index].mapping[
                descriptor.offset:descriptor.offset + ref.size]) == provider.packets[ref]
        # Cached refs are a no-op; changed envelopes still fail loudly.
        assert preparer.prefetch_many([(refs[0], packets[0].envelope)])["copy"] == 0
        with pytest.raises(ValueError, match="same_reference_changed_envelope"):
            preparer.prefetch_many([(refs[1], dict(packets[1].envelope, actor="other"))])
        for ref in refs:
            preparer.source_jobs[ref].result(timeout=30)
    finally:
        preparer.shutdown()


def test_prefetch_many_unwinds_partial_reservation_on_invalid_member(model, tmp_path):
    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    packets = [enc.encode(segment(model, name=f"actor/{i}", run_id=f"episode-{i}"))
               for i in range(2)]
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    preparer = prepare(model, tmp_path / "prepare", provider)
    try:
        bad = dict(packets[1].envelope, packet_size=packets[1].envelope["packet_size"] + 1)
        with pytest.raises(ValueError, match="binary_reference_or_slot_byte_limit"):
            preparer.prefetch_many([(refs[0], packets[0].envelope), (refs[1], bad)])
        assert not preparer.copying and not preparer.sources
        assert all(a.users == 0 for a in preparer.arenas)
    finally:
        preparer.shutdown()


def test_prefetch_many_publishes_the_rest_when_one_copy_fails(model, tmp_path):
    class OneChanged(BytesProvider):
        @contextmanager
        def borrow(self, ref):
            data = bytearray(self.packets[ref])
            if ref.slot == "bad":
                data[-1] ^= 1
            yield memoryview(bytes(data))

    provider = OneChanged()
    enc = encoder(model, tmp_path / "spool")
    packets = [enc.encode(segment(model, name=f"actor/{i}", run_id=f"episode-{i}"))
               for i in range(3)]
    refs = [provider.add(p, s) for p, s in zip(packets, ("a", "bad", "c"), strict=True)]
    preparer = prepare(model, tmp_path / "prepare", provider)
    try:
        with pytest.raises(ValueError, match="binary_provider_integrity_changed"):
            preparer.prefetch_many([(r, p.envelope) for r, p in zip(refs, packets, strict=True)])
        assert refs[0] in preparer.sources and refs[2] in preparer.sources
        assert refs[1] not in preparer.sources and not preparer.copying
        for ref in (refs[0], refs[2]):
            preparer.source_jobs[ref].result(timeout=30)
        preparer.callbacks.submit(lambda: None).result(timeout=20)
        assert all(a.users == 0 for a in preparer.arenas)
    finally:
        preparer.shutdown()


def test_concurrent_prefetch_many_under_one_arena_credit_never_deadlocks(model, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    provider = BytesProvider()
    enc = encoder(model, tmp_path / "spool")
    packets = [enc.encode(segment(model, name=f"actor/{i}", run_id=f"episode-{i}"))
               for i in range(9)]
    preparer = BinaryBatchPreparer(provider=provider, vocabulary=model.vocabulary,
        workers=1, threads=1, scratch=tmp_path / "prepare", source_slots=1,
        max_source_bytes=2 * max(len(p.packet) for p in packets) + 8, source_wait_seconds=60)
    refs = [provider.add(p, f"opaque-{i}") for i, p in enumerate(packets)]
    held = ()
    try:
        preparer.prefetch(refs[0], packets[0].envelope)
        preparer.source_jobs[refs[0]].result(timeout=30)
        with preparer.condition:
            held, _ = preparer._reserve((refs[0],))
        waiting = threading.Semaphore(0)
        real_wait = preparer._wait_for_source

        def observed(deadline):
            waiting.release()
            return real_wait(deadline)

        preparer._wait_for_source = observed
        batches = [list(zip(refs[1 + 2 * i:3 + 2 * i], packets[1 + 2 * i:3 + 2 * i],
                            strict=True)) for i in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(preparer.prefetch_many,
                                   [(r, p.envelope) for r, p in batch]) for batch in batches]
            assert waiting.acquire(timeout=10)
            preparer._release(held)
            held = ()
            timings = [f.result(timeout=120) for f in futures]
        assert sum(t["credit_wait"] for t in timings) > 0
        for ref, (index, descriptor) in preparer.sources.items():
            assert bytes(preparer.arenas[index].mapping[
                descriptor.offset:descriptor.offset + ref.size]) == provider.packets[ref]
            preparer.source_jobs[ref].result(timeout=30)
        preparer.callbacks.submit(lambda: None).result(timeout=20)
        assert all(a.users == 0 for a in preparer.arenas) and not preparer.copying
    finally:
        if held:
            preparer._release(held)
        preparer.shutdown()
