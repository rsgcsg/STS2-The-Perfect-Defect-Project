"""WP8: SegmentLearner consumes fast_chain segments (admission, exclusion, capacity, switch)."""
from __future__ import annotations

import collections
import dataclasses
import multiprocessing
import random
import threading
import time
import types
from typing import Any, cast

import pytest
from test_rl41_w1 import configuration
from test_rl_fast_source import Game, _sender, closed_for, message, train_seed

from spireagent.json_boundary import BoundaryError
from stpd.p1.frame_compiler import FrameCompiler
from stpd.rl.fast_assembly import MuHandoff
from stpd.rl.fast_segment import FORMAT, FORMAT_2
from stpd.rl.fast_source import FAST_FORMAT, FastSegment, IntakeDecision
from stpd.rl.segment_learner import SegmentLearner


def fast_learner(tmp_path, monkeypatch, *, fast: Any = True,
                 aux_labels: list[str] | None = None,
                 dataplane_extra: dict[str, Any] | None = None,
                 **loss: float) -> SegmentLearner:
    from stpd.p1.local_inference import AUTH_ENV

    tmp_path.mkdir(parents=True, exist_ok=True)
    config, io = configuration(tmp_path, monkeypatch)
    if loss:
        config = dataclasses.replace(config, loss=dataclasses.replace(config.loss, **loss))
    monkeypatch.setenv(AUTH_ENV, "ab" * 32)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, steps=64, dataset_rows=64, episode_rows=128, capacity_rows=128))
    dataplane = {"lanes": 1, "root": str(tmp_path / "live"), "source_slots": 8,
                 "prepare_slots": 2, "source_bytes": 16 * 1024**2,
                 "preparation_workers": 1, "assembly_workers": 1}
    if fast is not None:
        dataplane["fast_chain"] = fast
    if aux_labels is not None:
        dataplane["fast_aux_labels"] = aux_labels
    dataplane.update(dataplane_extra or {})
    return SegmentLearner(config, tmp_path / "owner", dataclasses.replace(io, dataplane=dataplane))


def game(owner: SegmentLearner, name: str, *, terminal: bool = True,
         segment_format: str = FORMAT) -> list[Any]:
    """One real assembler game bound to this learner's generation and compiler."""
    g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
             assembler_id="node-" + name, handoff=learner_mu(owner),
             segment_format=segment_format)
    g.open(name)
    for _ in range(64 + 5 if terminal else 65):
        g.step(name)
    if terminal:
        g.step(name, terminal=True)
        g.close(name, "terminal")
    return g.sealed


def learner_mu(owner: SegmentLearner) -> MuHandoff:
    """The mu this learner itself published (its start snapshot)."""
    identity = owner.pool.snapshots[owner.pool.latest]
    return MuHandoff(identity["snapshot_sha256"], identity["training_generation"],
                     identity["version"], identity["updates"])


def admission(owner: SegmentLearner, name: str, index: int) -> dict[str, Any]:
    return owner.control({
        "operation": "fast_instance_admission", "actor": "actor-1", "intent_id": "i-" + name,
        "seed": train_seed(index), "game_instance_id": name, "training_split": "TRAIN",
        "run": owner.config.run, "training_generation": owner.generation})


def eventually(check: Any, seconds: float = 10.0) -> bool:
    """The source prefetch thread holds a short `source-prefetch` retain of its own."""
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def offer(owner: SegmentLearner, segment: Any) -> None:
    assert owner.fast_intake is not None
    owner._fast_apply(owner.fast_intake.offer(message(segment), segment.data))


def queued(owner: SegmentLearner) -> list[str]:
    return [record["segment_id"] for record in owner.queue.pending]


@pytest.mark.parametrize("segment_format", [FORMAT, FORMAT_2])
def test_handoff_connection_admits_attested_segments_and_prepares_a_batch(
        tmp_path, monkeypatch, segment_format):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1", segment_format=segment_format)
        assert admission(owner, "g1", 1) == {"admitted": True, "game_instance_id": "g1"}
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender,
                                          args=(theirs, sealed, closed_for(sealed)))
        process.start()
        theirs.close()  # only the sender holds that end, so its exit is EOF here
        reader = owner.attach_fast_handoff(ours)
        process.join(30)
        assert process.exitcode == 0
        reader.join(30)  # EOF after the sender closes its end
        assert not reader.is_alive() and not owner.input_fenced
        assert queued(owner) == [s.segment_id for s in sealed]
        status = owner.fast_status()["fast_chain"]
        assert status["learner"]["admitted"] == 2
        assert status["learner"]["admitted_rows"] == 70
        assert status["intake"]["open_instances"] == 0
        assert status["fast_source_segments"] == 2
        # The fast record goes through the same binary preparer as the old chain.
        assert owner._prepare()
        assert owner.active is not None
        assert [r["segment_id"] for r in owner.active["segments"]] == [sealed[0].segment_id]
        assert owner.active_batch is not None
        assert len(owner.active_batch.metadata) == 64
        # Forward/loss/backward run on the fast rows. The fixture's behaviour q
        # (0.25/0.75) is far from the fresh policy, so the method's own KL stop
        # usually fires; a 16-row random minibatch can fall under it (then it steps).
        result = owner._step()
        assert result["rows"] == 16 and result["approx_kl_mu_theta"] > 0
        assert result["kl_stopped"] is not result["stepped"]
        assert owner.updates == (1 if result["stepped"] else 0)
    finally:
        owner.close()


def test_env_crash_close_drops_unused_segments_and_counts_used_rows(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        for segment in sealed:
            offer(owner, segment)
        assert owner.fast_intake is not None
        owner._fast_apply(owner.fast_intake.closed(closed_for(
            sealed, close_reason="env_crash")))
        assert queued(owner) == []
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_instances"] == 1
        assert counters["excluded_dropped_rows"] == 70 and counters["excluded_used_rows"] == 0
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"] == 0)
        # The actor's later evidence report for the same instance is answered, not re-applied.
        reply = owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                               "game_instance_id": "g1", "reason": "evidence_failed",
                               "run": owner.config.run, "training_generation": owner.generation})
        assert reply == {"excluded": True, "game_instance_id": "g1"}
        assert owner.fast_status()["fast_chain"]["learner"]["excluded_instances"] == 1
    finally:
        owner.close()


def test_full_queue_drops_the_oldest_unprepared_segment_counted(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        capacity = owner.settings.capacity_rows  # 1 lane x 64 + 4 x dataset_rows
        assert capacity % 64 == 0
        first = []
        for index in range(1, capacity // 64 + 2):
            name = f"g{index}"
            bootstrap = game(owner, name, terminal=False)[0]
            admission(owner, name, index)
            offer(owner, bootstrap)
            first.append(bootstrap.segment_id)
        assert queued(owner) == first[1:]
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["capacity_dropped"] == 1 and counters["capacity_dropped_rows"] == 64
        assert counters["capacity_evicted"] == 1 and counters["capacity_refused"] == 0
        assert owner.queue.occupied == capacity
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"]
                          == capacity // 64)
    finally:
        owner.close()


def test_fast_chain_off_keeps_the_old_provider_and_refuses_fast_controls(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch, fast=None)
    try:
        assert owner.input_preparer.provider is owner.binary_sources
        assert owner.fast_status() == {}
        with pytest.raises(BoundaryError, match="fast_chain_not_enabled"):
            admission(owner, "g1", 1)
    finally:
        owner.close()
    with pytest.raises(BoundaryError, match="explicit_bool"):
        (tmp_path / "bad").mkdir()
        fast_learner(tmp_path / "bad", monkeypatch, fast="yes")


def test_fast_ref_format_is_routed_to_fast_sources(tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1", terminal=False)
        admission(owner, "g1", 1)
        offer(owner, sealed[0])
        record = owner.queue.pending[0]
        member = owner._member(record)
        assert member.payload.format == FAST_FORMAT
        assert owner._binary_owner(member.payload) is owner.fast_sources
    finally:
        owner.close()


def test_learner_trains_only_segments_under_its_own_published_mu(tmp_path, monkeypatch):
    from test_rl_fast_source import mu

    from stpd.rl.fast_source import FastIntakeError

    owner = fast_learner(tmp_path, monkeypatch)
    try:
        assert owner.fast_intake is not None
        start = learner_mu(owner)
        assert owner.fast_intake.published[start.snapshot_sha256]["version"] == start.version
        g = Game(owner.generation, compiler=FrameCompiler(owner.model.vocabulary),
                 assembler_id="node-x", handoff=mu(5, owner.generation))
        g.open("gx")
        for _ in range(65):
            g.step("gx")
        admission(owner, "gx", 1)
        with pytest.raises(FastIntakeError, match="fast_segment_mu_not_published"):
            offer(owner, g.sealed[0])
        assert queued(owner) == []
    finally:
        owner.close()


def test_labels_over_the_handoff_fill_the_aux_pools_and_reach_a_batch(tmp_path, monkeypatch):
    from test_rl_fast_source import combat_game

    owner = fast_learner(tmp_path, monkeypatch, aux_labels=["terminal", "hp"])
    try:
        sealed = combat_game(handoff=learner_mu(owner),
                             compiler=FrameCompiler(owner.model.vocabulary),
                             assembler_id="node-g1")
        admission(owner, "g1", 1)
        ours, theirs = multiprocessing.Pipe()
        process = multiprocessing.Process(target=_sender,
                                          args=(theirs, sealed, closed_for(sealed), True))
        process.start()
        theirs.close()
        reader = owner.attach_fast_handoff(ours)
        process.join(30)
        assert process.exitcode == 0
        reader.join(30)
        assert not reader.is_alive() and not owner.input_fenced
        assert list(owner.terminal.groups) == ["g1"] and len(owner.terminal.groups["g1"]) == 64
        assert list(owner.hp.groups) == ["g1:0", "g1:20"]
        assert {row["hp"] for row in owner.hp.groups["g1:0"]} == {0.4}
        intake = owner.fast_status()["fast_chain"]["intake"]
        assert intake["labels_terminal_rows"] == 64 and intake["labels_hp_rows"] == 20
        # The aux rows go through the same binary preparer as the old chain's.
        assert owner._prepare()
        assert owner.active is not None and owner.active["terminal"] and owner.active["hp"]
        assert owner.active_batch is not None
        batch = owner.active_batch
        assert batch.kinds.count("terminal") == len(owner.active["terminal"]) > 0
        assert batch.kinds.count("hp") == len(owner.active["hp"]) > 0
        result = owner._step()
        assert result["rows"] > 0
        # A later exclusion of the instance also removes its label rows.
        owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                       "game_instance_id": "g1", "reason": "evidence_failed",
                       "run": owner.config.run, "training_generation": owner.generation})
        assert not owner.terminal.groups and not owner.hp.groups
        assert owner.fast_status()["fast_chain"]["learner"]["excluded_aux_rows"] > 0
    finally:
        owner.close()


def test_fast_sources_cross_ipc_as_offsets_into_a_derived_file(tmp_path, monkeypatch):
    """Decoded fast rows live in the source's derived file, removed with the slot."""
    owner = fast_learner(tmp_path, monkeypatch)
    preparer: Any = owner.input_preparer
    scratch = preparer.scratch
    closed = False
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        for segment in sealed:
            offer(owner, segment)
        assert owner._prepare() and owner.active_batch is not None
        ready = [job.result().value for job in preparer.source_jobs.values() if job.done()]
        assert ready and all(type(a).__name__ == "_SharedArray" for r in ready for a in r.arrays)
        assert {a.name for r in ready for a in r.arrays} <= {
            str(p) for p in scratch.glob("rl-source-*.d*")}
        owner.close()
        closed = True
        assert not list(scratch.glob("rl-source-*"))
    finally:
        if not closed:
            owner.close()


def test_a_full_queue_admission_never_drops_a_read_ahead_selection_in_flight(
        tmp_path, monkeypatch):
    """h100c 11-12: a read-ahead selection (take=False) stays queued; an admission
    that overflowed the queue between the selection and its retain dropped it and
    released its bytes, and the learner died with fast_source_already_released."""

    owner = fast_learner(tmp_path, monkeypatch)
    try:
        capacity = owner.settings.capacity_rows
        for index in range(1, capacity // 64 + 1):
            name = f"g{index}"
            admission(owner, name, index)
            offer(owner, game(owner, name, terminal=False)[0])
        assert owner.queue.occupied == capacity
        name = f"g{capacity // 64 + 1}"
        admission(owner, name, capacity // 64 + 1)
        late = game(owner, name, terminal=False)[0]
        submit = owner._submit_batch
        racer = threading.Thread(target=offer, args=(owner, late))

        def submit_after_a_racing_admission(active: dict[str, Any]) -> Any:
            racer.start()
            racer.join(0.5)  # it must wait for the selection to be registered
            assert racer.is_alive()
            return submit(active)

        monkeypatch.setattr(owner, "settings",
                            dataclasses.replace(owner.settings, prepare_ahead=True))
        monkeypatch.setattr(owner, "prepared_batches", 1)
        monkeypatch.setattr(owner, "_submit_batch", submit_after_a_racing_admission)
        owner._prepare_ahead()
        racer.join(10)
        assert not racer.is_alive() and owner.pending_batch is not None
        selected = owner.pending_batch[0]["segments"][0]["segment_id"]
        assert selected in queued(owner) and late.segment_id in queued(owner)
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["capacity_dropped"] == 1 and counters["capacity_dropped_rows"] == 64
        assert counters["capacity_evicted"] == 1 and counters["capacity_refused"] == 0
    finally:
        owner.close()


def exclude(owner: SegmentLearner, name: str) -> dict[str, Any]:
    return owner.control({"operation": "fast_instance_excluded", "actor": "actor-1",
                          "game_instance_id": name, "reason": "evidence_failed",
                          "run": owner.config.run, "training_generation": owner.generation})


def test_exclusion_counts_trained_and_prefetched_rows_apart(tmp_path, monkeypatch):
    """Lead ruling 11-10: used = selected into a prepared batch, never voided or
    rebuilt; rows handed to SGD and rows only read ahead are counted apart."""
    from concurrent.futures import Future

    owner = fast_learner(tmp_path, monkeypatch)
    try:
        for index, name in enumerate(("g1", "g2"), start=1):
            sealed = game(owner, name)
            admission(owner, name, index)
            for segment in sealed:
                offer(owner, segment)
            if name == "g1":
                # The next dataset is taken for SGD: it leaves the queue.
                active = owner._select()
            else:
                # A read-ahead batch (take=False): still queued, held by the batch.
                active = owner._select(take=False)
                owner.ahead_batches.append((active, Future()))
            assert active is not None
            assert [r["segment_id"] for r in active["segments"]] == [sealed[0].segment_id]
            assert exclude(owner, name) == {"excluded": True, "game_instance_id": name}
            assert queued(owner) == ([] if name == "g1" else [sealed[0].segment_id])
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_instances"] == 2
        assert counters["excluded_dropped_rows"] == 12  # each game's 6-row terminal tail
        assert counters["excluded_trained_rows"] == 64  # g1's dataset, handed to SGD
        assert counters["excluded_prefetched_rows"] == 64  # g2's, read ahead, kept
        assert counters["excluded_used_rows"] == 128
    finally:
        owner.ahead_batches.clear()
        owner.close()


def test_an_exclusion_between_the_admit_decision_and_its_use_drops_the_segment(
        tmp_path, monkeypatch):
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        sealed = game(owner, "g1")
        admission(owner, "g1", 1)
        assert owner.fast_intake is not None
        # The receive thread has the intake's decision (admit) ...
        decision = owner.fast_intake.offer(message(sealed[0]), sealed[0].data)
        assert decision.admit
        # ... the control thread applies the actor's exclusion first ...
        assert exclude(owner, "g1") == {"excluded": True, "game_instance_id": "g1"}
        # ... then the receive thread applies its decision: dropped, counted.
        owner._fast_apply(decision)
        assert queued(owner) == []
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["excluded_admit_raced"] == 1
        assert counters["excluded_admit_raced_rows"] == 64
        assert counters["excluded_dropped_rows"] == 64 and counters["admitted"] == 0
        assert eventually(lambda: owner.fast_status()["fast_chain"]["fast_source_segments"] == 0)
    finally:
        owner.close()


def test_fast_aux_pools_follow_the_loss_coefficients(tmp_path, monkeypatch):
    """Lead ruling 11-10: every pool with coef > 0 takes fast labels by default;
    an explicit list leaving one out is refused at start, never a silent 0."""
    owner = fast_learner(tmp_path / "a", monkeypatch)
    try:
        assert owner.config.loss.terminal_coef > 0 and owner.config.loss.hp_coef > 0
        assert owner.fast_intake is not None
        assert owner.fast_intake.aux_labels == {"terminal", "hp"}
    finally:
        owner.close()
    owner = fast_learner(tmp_path / "b", monkeypatch, hp_coef=0.0)
    try:
        assert owner.fast_intake is not None and owner.fast_intake.aux_labels == {"terminal"}
    finally:
        owner.close()
    with pytest.raises(BoundaryError, match="omit_a_pool_with_positive_coef"):
        fast_learner(tmp_path / "c", monkeypatch, aux_labels=["terminal"])
    with pytest.raises(BoundaryError, match="omit_a_pool_with_positive_coef"):
        fast_learner(tmp_path / "d", monkeypatch, aux_labels=[], terminal_coef=0.1)
    # A pool the loss does not weight may still be listed (harmless).
    owner = fast_learner(tmp_path / "e", monkeypatch, aux_labels=["terminal", "hp"],
                         hp_coef=0.0)
    owner.close()


def test_capacity_drop_never_takes_an_old_chain_segment(tmp_path, monkeypatch):
    """Review S4: an old-chain lease's segment queued ahead of fast segments keeps
    its place (and its episode accounting); the oldest fast segment is dropped."""
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        capacity = owner.settings.capacity_rows
        # The placeholder below has no source to read: park the source prefetch
        # (a read-ahead hint only) so it never touches it.
        owner.source_prefetch_stopping.set()
        owner.source_prefetch_changed.set()
        with owner.queue.condition:  # stands in for a committed old-chain lease
            owner.queue.pending.append({"segment_id": "old-chain-1", "run_id": "old", "rows": 64})
            owner.queue.rows += 64
        fast = []
        for index in range(1, capacity // 64 + 1):
            name = f"g{index}"
            bootstrap = game(owner, name, terminal=False)[0]
            admission(owner, name, index)
            offer(owner, bootstrap)
            fast.append(bootstrap.segment_id)
        assert queued(owner) == ["old-chain-1", *fast[1:]]
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["capacity_dropped"] == 1 and counters["capacity_dropped_rows"] == 64
        assert counters["capacity_evicted"] == 1 and counters["capacity_refused"] == 0
        assert not owner.input_fenced
    finally:
        with owner.queue.condition:
            owner.queue.discard({"old-chain-1"})
        owner.close()


def test_a_bad_buffered_segment_released_by_an_attest_fences_input(tmp_path, monkeypatch):
    """Review record 3: the control thread fences exactly as the hand-off thread."""
    owner = fast_learner(tmp_path, monkeypatch)
    try:
        bootstrap = game(owner, "g1", terminal=False)[0]
        offer(owner, bootstrap)  # not attested yet: buffered by the intake
        assert queued(owner) == [] and not owner.input_fenced
        monkeypatch.setattr(owner, "compiler_identity", "another-compiler")
        with pytest.raises(ValueError, match="fast_segment_compiler_identity_changed"):
            admission(owner, "g1", 1)
        assert owner.input_fenced and owner.newlease_stopped
        assert owner.input_failure["operation"] == "fast_control"
    finally:
        owner.close()


# --- Capacity admission against the pre-change algorithm (int 9a3925aa) -------

def _old_capacity_admit(pending: list[dict[str, Any]], referenced: set[str],
                        excluded: set[str], capacity: int, new: dict[str, Any],
                        counters: collections.Counter[str]) -> list[dict[str, Any]]:
    """Reference: `_fast_admit`'s selection as it was before the counter split."""
    if new["run_id"] in excluded:
        counters["raced"] += 1
        return pending
    overflow = sum(r["rows"] for r in pending) + new["rows"] - capacity
    victims: set[str] = set()
    for record in pending:
        if overflow <= 0:
            break
        if record.get("fast") and record["segment_id"] not in referenced:
            victims.add(record["segment_id"])
            overflow -= record["rows"]
    if overflow > 0:
        counters["dropped"] += 1
        counters["dropped_rows"] += new["rows"]
        counters["refused"] += 1
        return pending
    kept = []
    for record in pending:
        if record["segment_id"] in victims:
            counters["dropped"] += 1
            counters["dropped_rows"] += record["rows"]
            counters["evicted"] += 1
        else:
            kept.append(record)
    return [*kept, {"segment_id": new["segment_id"], "fast": True, "rows": new["rows"],
                    "run_id": new["run_id"]}]


class _CountingCondition(threading.Condition):
    def __init__(self) -> None:
        super().__init__(threading.RLock())
        self.notified = 0

    def notify_all(self) -> None:
        self.notified += 1
        super().notify_all()


def _stub_owner(capacity: int, excluded: set[str]) -> SegmentLearner:
    """Only what `_fast_apply` / `_fast_admit` touch on the admission path."""
    from stpd.rl.segments import SegmentQueue

    owner = SegmentLearner.__new__(SegmentLearner)
    owner.queue = SegmentQueue(cast(Any, types.SimpleNamespace(
        steps=64, capacity_rows=capacity, episode_rows=10**9, dataset_rows=64)))
    owner.condition = owner.queue.condition = _CountingCondition()
    owner.settings = cast(Any, types.SimpleNamespace(capacity_rows=capacity))
    owner.config = cast(Any, types.SimpleNamespace(run="run"))
    owner.generation = "gen"
    owner.compiler_identity = {"compiler": 1}
    owner.newlease_stopped = owner.input_fenced = False
    owner.training_discarded_rows = 0
    owner.fast_intake = cast(Any, types.SimpleNamespace(excluded=lambda run: run in excluded))
    owner.fast_sources = cast(Any, types.SimpleNamespace(put=lambda *args: None))
    owner.hot_references = {}
    owner.fast_counters = collections.defaultdict(int)
    owner.fast_admitted_rows = collections.OrderedDict()
    released: list[str] = []
    owner._release = lambda payload, who: released.append(who)  # type: ignore[method-assign]
    cast(Any, owner).released = released
    owner.pending_batch = None
    owner.ahead_batches = collections.deque()
    owner.source_prefetch_changed = threading.Event()
    return owner


def _stub_segment(sid: str, run: str, rows: int, *, compiler: Any = None
                  ) -> tuple[FastSegment, dict[str, Any]]:
    header = {"segment_id": sid, "game_instance_id": run, "rows": [{}] * rows}
    envelope = {"compiler_identity": compiler or {"compiler": 1}, "run_id": run,
                "rows": rows, "packet_sha256": "ab" * 32, "packet_size": 100,
                "mu": {"snapshot_sha256": "cd" * 32}, "episode": "e-" + run,
                "actor": "actor-1"}
    return FastSegment({}, header, b"x"), envelope


def _stub_queue(owner: SegmentLearner, records: list[dict[str, Any]]) -> None:
    for record in records:
        if record.get("fast"):
            record["payload"] = {"format": FAST_FORMAT, "slot": "s/" + record["segment_id"],
                                 "sha256": "ab" * 32, "size": 100}
            record["mu"] = {"snapshot_sha256": "cd" * 32}
            record["episode"] = "e"
        owner.queue.pending.append(record)
        owner.queue.rows += record["rows"]


def test_capacity_admission_matches_the_pre_change_selection_record_by_record():
    """Full-queue eviction, read-ahead selections at the head of the queue (held,
    never evicted), old-chain records (never evicted) and an exclusion applied
    between the admit decision and its use: queue, counters and releases match
    the old algorithm in every random case; the split counters sum to the old one."""
    rng = random.Random(20261113)
    for case in range(400):
        capacity = rng.choice((128, 256, 640))
        records: list[dict[str, Any]] = []
        while True:
            rows = rng.randint(1, 64)
            if sum(r["rows"] for r in records) + rows > capacity:
                break
            fast = rng.random() > 0.1
            records.append({"segment_id": f"q{case}-{len(records)}", "rows": rows,
                            "run_id": f"r{rng.randint(0, 5)}", **({"fast": True} if fast
                            else {"actor": "a", "episode": "e"})})
        # Read-ahead (take=False) selections come from the head of the queue.
        head = rng.randint(0, len(records))
        referenced = {r["segment_id"] for r in records[:head]
                      if r.get("fast") and rng.random() > 0.1}
        excluded = {f"r{i}" for i in range(6) if rng.random() < 0.15}
        owner = _stub_owner(capacity, excluded)
        _stub_queue(owner, [dict(r) for r in records])
        if referenced:
            owner.ahead_batches.append(({"segments": [
                {"segment_id": sid} for sid in sorted(referenced)]}, cast(Any, None)))
        admits = [_stub_segment(f"n{case}-{i}", f"r{rng.randint(0, 5)}", rng.randint(1, 64))
                  for i in range(rng.randint(1, 3))]
        expected = [dict(r) for r in records]
        counters: collections.Counter[str] = collections.Counter()
        for segment, envelope in admits:
            expected = _old_capacity_admit(expected, referenced, excluded, capacity,
                                           {"segment_id": segment.segment_id,
                                            "run_id": envelope["run_id"],
                                            "rows": segment.rows}, counters)
        owner._fast_apply(IntakeDecision(admit=admits))
        assert queued(owner) == [r["segment_id"] for r in expected], case
        got = owner.fast_counters
        assert got["capacity_dropped"] == counters["dropped"], case
        assert got["capacity_dropped_rows"] == counters["dropped_rows"], case
        assert got["capacity_evicted"] == counters["evicted"], case
        assert got["capacity_refused"] == counters["refused"], case
        assert got["capacity_dropped"] == got["capacity_evicted"] + got["capacity_refused"]
        assert got["capacity_dropped_rows"] == (got["capacity_evicted_rows"]
                                                + got["capacity_refused_rows"])
        assert got["excluded_admit_raced"] == counters["raced"], case
        assert len(cast(Any, owner).released) == counters["evicted"], case
        assert not referenced & {r["segment_id"] for r in records
                                 if r["segment_id"] not in queued(owner)}, case
        assert owner.queue.rows == sum(r["rows"] for r in expected) <= capacity


def test_one_decision_notifies_once_and_wakes_every_waiter():
    owner = _stub_owner(256, set())
    _stub_queue(owner, [{"segment_id": f"old{i}", "fast": True, "rows": 64,
                         "run_id": "r0"} for i in range(4)])
    condition = cast(_CountingCondition, owner.condition)
    woke: list[bool] = []
    ready = [0]

    def wait_for_the_new_segments() -> None:
        with condition:
            ready[0] += 1
            woke.append(condition.wait_for(
                lambda: {"n1", "n2"} <= set(queued(owner)), timeout=10))

    waiters = [threading.Thread(target=wait_for_the_new_segments) for _ in range(3)]
    for waiter in waiters:
        waiter.start()
    deadline = time.monotonic() + 10
    while True:  # holding the lock with ready == 3: all three are inside `wait_for`
        with condition:
            if ready[0] == 3:
                break
        assert time.monotonic() < deadline
        time.sleep(0.01)
    # Two evictions and two admissions in one decision: one notify_all.
    applied = time.monotonic()
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("n1", "r1", 64),
                                            _stub_segment("n2", "r1", 64)]))
    for waiter in waiters:
        waiter.join(10)
    # Woken by the notify, not by their 10 s `wait_for` timeout.
    assert time.monotonic() - applied < 5
    assert woke == [True, True, True]
    assert condition.notified == 1 and owner.source_prefetch_changed.is_set()
    assert owner.fast_counters["capacity_evicted"] == 2
    # A refused admission changes nothing: no wake-up, no prefetch hint.
    owner.source_prefetch_changed.clear()
    owner.ahead_batches.append(({"segments": [{"segment_id": s} for s in queued(owner)]},
                                cast(Any, None)))
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("n3", "r1", 64)]))
    assert condition.notified == 1 and not owner.source_prefetch_changed.is_set()
    assert owner.fast_counters["capacity_refused"] == 1


def test_an_admission_that_raises_after_a_queue_change_still_wakes_waiters():
    owner = _stub_owner(128, set())
    _stub_queue(owner, [{"segment_id": "old0", "fast": True, "rows": 64, "run_id": "r0"}])
    condition = cast(_CountingCondition, owner.condition)
    with pytest.raises(ValueError, match="fast_segment_compiler_identity_changed"):
        owner._fast_apply(IntakeDecision(admit=[
            _stub_segment("n1", "r1", 64),
            _stub_segment("n2", "r1", 64, compiler={"compiler": 2})]))
    assert queued(owner) == ["old0", "n1"]
    assert condition.notified == 1 and owner.source_prefetch_changed.is_set()


# --- Short `_fast_apply` hold (V3 午) -----------------------------------------

def _old_discard(pending: list[dict[str, Any]], ids: set[str]) -> tuple[list[str], list[str]]:
    """Reference: `SegmentQueue.discard` before the C-level scan."""
    removed = [s["segment_id"] for s in pending if s["segment_id"] in ids]
    return removed, [s["segment_id"] for s in pending if s["segment_id"] not in ids]


def test_queue_discard_removes_exactly_what_the_old_passes_removed():
    """Every occurrence, in queue order, ids absent from the queue ignored, rows
    adjusted; also for duplicate ids (a restored state is not re-validated)."""
    from stpd.rl.segments import SegmentQueue

    rng = random.Random(1113)
    for case in range(300):
        queue = SegmentQueue(cast(Any, types.SimpleNamespace(
            steps=64, capacity_rows=10**9, episode_rows=10**9, dataset_rows=64)))
        names = [f"s{rng.randint(0, 40)}" for _ in range(rng.randint(0, 60))]
        for name in names:
            queue.pending.append({"segment_id": name, "rows": 1 + len(name), "run_id": "r"})
            queue.rows += 1 + len(name)
        ids = {f"s{rng.randint(0, 50)}" for _ in range(rng.randint(0, 6))}
        before = list(queue.pending)
        expected_removed, expected_kept = _old_discard(before, ids)
        removed = queue.discard(ids, notify=False)
        assert [s["segment_id"] for s in removed] == expected_removed, case
        assert [s["segment_id"] for s in queue.pending] == expected_kept, case
        assert all(any(r is b for b in before) for r in removed), case
        assert queue.rows == sum(s["rows"] for s in queue.pending), case


def test_queue_admit_still_refuses_a_pending_duplicate_and_owned_skips_the_copy():
    from stpd.rl.segments import SegmentQueue

    queue = SegmentQueue(cast(Any, types.SimpleNamespace(
        steps=64, capacity_rows=1000, episode_rows=10**9, dataset_rows=64)))
    record = {"segment_id": "a", "rows": 8, "run_id": "r"}
    queue.admit(record)
    assert queue.pending[0] is not record and queue.pending[0] == record
    with pytest.raises(ValueError, match="fast_segment_admission_binding_mismatch"):
        queue.admit({"segment_id": "a", "rows": 8, "run_id": "r"})
    owned = {"segment_id": "b", "rows": 8, "run_id": "r"}
    queue.admit(owned, owned=True)
    assert queue.pending[1] is owned and queue.rows == 16


def test_fast_record_and_its_copy_are_built_outside_the_condition_and_put_keeps_the_bytes():
    """The hold only decides and publishes: each admission's queue record (and
    its deep copy) is made before `condition` is taken, and the source store
    keeps the hand-off's own bytes object (no payload copy under the lock)."""
    from stpd.rl.fast_source import FastSources

    owner = _stub_owner(256, set())
    sources = owner.fast_sources = FastSources()
    built: list[bool] = []
    original = owner._fast_record

    def record(segment: Any, envelope: Any) -> Any:
        # `_is_owned` on a Condition: whether this thread holds its lock.
        built.append(cast(Any, owner.condition)._is_owned())
        return original(segment, envelope)

    owner._fast_record = record  # type: ignore[method-assign]
    put_owned: list[bool] = []
    real_put = sources.put

    def put(*args: Any) -> None:
        put_owned.append(cast(Any, owner.condition)._is_owned())
        real_put(*args)

    sources.put = put  # type: ignore[method-assign, assignment]
    admits = [_stub_segment(f"n{i}", "r1", 64) for i in range(3)]
    for segment, envelope in admits:
        envelope["packet_size"] = len(segment.data)  # a real store checks it
    owner._fast_apply(IntakeDecision(admit=admits))
    assert built == [False, False, False]
    # Publishing into the store stays under the condition (as before).
    assert put_owned == [True, True, True]
    for (segment, envelope), queued_record in zip(admits, owner.queue.pending, strict=True):
        ref = owner.hot_references[queued_record["payload"]["slot"],
                                   "ppo:" + segment.segment_id]
        assert sources.entries[ref]["data"] is segment.data
        # The queued record is a private copy: not the envelope object handed in.
        assert queued_record["envelope"] is not envelope
        assert queued_record["envelope"] == envelope
    assert queued(owner) == ["n0", "n1", "n2"]


def test_a_segment_not_admitted_never_enters_the_source_store():
    """Stopped, raced exclusion, capacity refusal and a compiler change: the
    pre-built record is just dropped; nothing reaches `fast_sources`, counters
    exactly as before, and the compiler change still raises."""
    from stpd.rl.fast_source import FastSources

    def fresh(capacity: int, excluded: set[str]) -> tuple[SegmentLearner, FastSources]:
        owner = _stub_owner(capacity, excluded)
        sources = owner.fast_sources = FastSources()
        return owner, sources

    owner, sources = fresh(256, set())
    owner.newlease_stopped = True
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("n1", "r1", 64)]))
    assert owner.training_discarded_rows == 64 and not sources.entries

    owner, sources = fresh(256, {"r1"})
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("n1", "r1", 64)]))
    assert owner.fast_counters["excluded_admit_raced_rows"] == 64
    assert not sources.entries and not queued(owner)

    owner, sources = fresh(128, set())
    _stub_queue(owner, [{"segment_id": "old0", "fast": True, "rows": 64, "run_id": "r0"},
                        {"segment_id": "old1", "rows": 64, "run_id": "r0",
                         "actor": "a", "episode": "e"}])
    owner.ahead_batches.append(({"segments": [{"segment_id": "old0"}]}, cast(Any, None)))
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("n1", "r1", 64)]))
    assert owner.fast_counters["capacity_refused"] == 1
    assert owner.fast_counters["capacity_refused_rows"] == 64
    assert not sources.entries and queued(owner) == ["old0", "old1"]

    owner, sources = fresh(256, set())
    with pytest.raises(ValueError, match="fast_segment_compiler_identity_changed"):
        owner._fast_apply(IntakeDecision(admit=[
            _stub_segment("n1", "r1", 64, compiler={"compiler": 2})]))
    assert not sources.entries and not queued(owner)


def test_one_hold_computes_the_referenced_set_at_most_once():
    owner = _stub_owner(256, set())
    _stub_queue(owner, [{"segment_id": f"old{i}", "fast": True, "rows": 64,
                         "run_id": "r0"} for i in range(4)])
    owner.ahead_batches.append(({"segments": [{"segment_id": "old0"}]}, cast(Any, None)))
    calls = [0]
    original = owner._fast_referenced

    def referenced() -> set[str]:
        calls[0] += 1
        return original()

    owner._fast_referenced = referenced  # type: ignore[method-assign]
    # Room for nothing: three overflowing admissions in one decision.
    owner._fast_apply(IntakeDecision(admit=[_stub_segment(f"n{i}", "r1", 64)
                                            for i in range(3)]))
    assert calls[0] == 1
    assert queued(owner) == ["old0", "n0", "n1", "n2"]
    assert owner.fast_counters["capacity_evicted"] == 3
    # A decision that never overflows never builds it.
    owner = _stub_owner(1024, set())
    owner._fast_referenced = referenced  # type: ignore[method-assign]  # counts only
    calls[0] = 0
    owner._fast_apply(IntakeDecision(admit=[_stub_segment("m0", "r1", 64)]))
    assert calls[0] == 0


def _ref_learner(tmp_path: Any, monkeypatch: Any, value: Any) -> SegmentLearner:
    return fast_learner(tmp_path, monkeypatch, dataplane_extra=(
        {} if value is None else {"prefetch_referenced_only": value}))


def _cached(owner: SegmentLearner, segment_id: str) -> bool:
    record = next(r for r in owner.queue.pending if r["segment_id"] == segment_id)
    preparer = cast(Any, owner.input_preparer)
    return owner._member(record).payload in preparer.cached_sources()


@pytest.mark.parametrize("referenced_only", [None, False])
def test_source_prefetch_default_copies_the_whole_fifo_window(
        tmp_path, monkeypatch, referenced_only):
    """Flag absent or false: the old window, unreferenced segments included."""
    owner = _ref_learner(tmp_path, monkeypatch, referenced_only)
    try:
        assert owner.prefetch_referenced_only is False
        ids = []
        for index in (1, 2):
            name = f"g{index}"
            admission(owner, name, index)
            segment = game(owner, name, terminal=False)[0]
            offer(owner, segment)
            ids.append(segment.segment_id)
        assert eventually(lambda: all(_cached(owner, sid) for sid in ids))
    finally:
        owner.close()


def test_referenced_only_prefetch_never_copies_a_capacity_victim(tmp_path, monkeypatch):
    """Flag on, full queue: the evicted segment was never pre-copied, and the
    read-ahead batch is still copied (by its own dispatch) and prepared."""
    owner = _ref_learner(tmp_path, monkeypatch, True)
    try:
        capacity = owner.settings.capacity_rows
        ids = []
        for index in range(1, capacity // 64 + 1):
            name = f"g{index}"
            admission(owner, name, index)
            segment = game(owner, name, terminal=False)[0]
            offer(owner, segment)
            ids.append(segment.segment_id)
        assert owner.queue.occupied == capacity
        time.sleep(1.0)  # every chance for the prefetch thread to (wrongly) copy
        assert not any(_cached(owner, sid) for sid in ids)
        with owner.condition:  # one read-ahead selection, as `_prepare_ahead` makes it
            active = owner._select(take=False)
            assert active is not None
            owner.pending_batch = active, owner._submit_batch(active)
        selected = owner.pending_batch[0]["segments"][0]["segment_id"]
        assert selected == ids[0]
        name = f"g{capacity // 64 + 1}"
        admission(owner, name, capacity // 64 + 1)
        late = game(owner, name, terminal=False)[0]
        offer(owner, late)  # evicts the oldest unreferenced segment
        counters = owner.fast_status()["fast_chain"]["learner"]
        assert counters["capacity_evicted"] == 1 and counters["capacity_refused"] == 0
        victims = [sid for sid in ids[1:] if sid not in queued(owner)]
        assert len(victims) == 1
        cached = cast(Any, owner.input_preparer).cached_sources()
        assert not any(ref.slot.endswith("/" + victims[0]) for ref in cached)
        time.sleep(0.5)
        assert not _cached(owner, late.segment_id)
        assert owner._prepare()  # the dispatch's own prefetch copied the batch
        assert owner.active is not None
        assert [r["segment_id"] for r in owner.active["segments"]] == [selected]
    finally:
        owner.close()


def test_prefetch_referenced_only_must_be_a_bool(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="prefetch_referenced_only_must_be_explicit"):
        _ref_learner(tmp_path, monkeypatch, "yes")
