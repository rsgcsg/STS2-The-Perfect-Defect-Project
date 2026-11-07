"""RL5 S3 I1: explicit SGD-process alternative against the default in-process E4.

Real socket/WAL/preparation workers and a real spawned SGD child on CPU; zero
World. CPU is single-threaded and deterministic, so learning state is compared
bit for bit. GPU BF16/TF32 equality is a tolerance claim for the Friday plan.
"""
from __future__ import annotations

import dataclasses as dc
import json
import os
import signal
import socket
import time
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path

import pytest
import torch
from test_rl41_w1 import assert_tree, configuration, segment
from test_rl_pipeline_checkpoint import SGD_PROCESS
from test_rl_segment_learner import read_cp

from spireagent.json_boundary import BoundaryError
from spireagent.rl_socket_transport import upload
from stpd.p1.local_inference import SegmentConnection
from stpd.p1.local_mu import LocalMuScorer, NodeMuCache
from stpd.rl.segment_learner import SegmentLearner
from stpd.rl.sgd_process import SgdOptions, SgdProcessFailed, memory_budget, sgd_options
from stpd.rl.tensor_segment import TensorSegmentEncoder

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")


def terminal_owner(tmp_path, monkeypatch, *, episodes=3, sgd=True, segments=None,
                   dataplane=None, generation="g-sgd-equal"):
    """Each episode: two 2-row segments, the second terminal, so terminal aux rows
    are verified, sampled and charged exactly like the in-process owner."""
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor

    tmp_path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20261107)  # the fixture's start package draws from global torch RNG
    config, io = configuration(tmp_path, monkeypatch, generation=generation)
    config = dc.replace(config, segments=dc.replace(
        config.segments, minibatches=2, episode_rows=8, archive_min_free_bytes=0,
        archive_min_free_inodes=0, **(segments or {})))
    io = dc.replace(io, dataplane={
        "lanes": 1, "root": str(tmp_path / "live"), "source_slots": 32,
        "prepare_slots": 6, "source_bytes": 64 * 1024**2, "preparation_workers": 2,
        "assembly_workers": 2, "prepared_batches": 3,
        **(SGD_PROCESS if sgd else {}), **(dataplane or {})})
    owner = SegmentLearner(config, tmp_path / "owner", io)
    for key, value in owner.pool.environment().items():
        monkeypatch.setenv(key, value)
    endpoint = owner.pool.attachment()
    connection = SegmentConnection(endpoint)
    cache = NodeMuCache(endpoint, tmp_path / "node")
    scorer = LocalMuScorer(owner.exchange, tmp_path / "mu")
    try:
        for e in range(episodes):
            actor, episode = f"p{e}", f"episode-{e}"
            connection.request("episode_open", actor=actor, episode=episode)
            encoder = TensorSegmentEncoder(
                vocabulary=owner.input_preparer.vocabulary,
                compiler_identity=owner.compiler_identity,
                recipe={**owner.pool.binary_profile["binary_recipe"], "actor": actor},
                spool=tmp_path / f"spool-{e}")
            values, records = [], []
            for i in range(2):
                with cache.latest() as identity:
                    grant = connection.request(
                        "begin", actor=actor, episode=episode,
                        previous_lease=records[-1]["segment_id"] if records else None,
                        node_id=socket.gethostname(),
                        node_cached_mu_snapshot=identity["snapshot_sha256"])
                scorer.adopt(grant["mu"])
                value = segment(scorer.models[grant["mu"]["snapshot_sha256"]],
                                name=grant["lease"], run_id=episode, rows=2, start=2 * i,
                                generation=owner.generation, mu=grant["mu"], terminal=i == 1)
                encoded = encoder.encode(value)
                ref = upload(endpoint, encoded)
                record = {"segment_id": grant["lease"], "payload": asdict(ref), "rows": 2,
                          "training_generation": owner.generation,
                          "segment_artifact_id": "tensor:" + ref.slot}
                connection.request("commit", **record, run=config.run, run_id=episode,
                                   mu=grant["mu"], envelope=encoded.envelope)
                values.append(value)
                records.append(record)
            labels = {
                "run": config.run, "training_generation": owner.generation, "actor": actor,
                "episode": episode, "run_id": episode, "terminal": values[-1]["terminal"],
                "typed_original": values[-1]["terminal"]["typed_original"],
                "unqualified": False, "hp_battles": [], "segments": records,
                "outcome_states": [
                    {"source": {"run_id": episode, "segment_id": records[0]["segment_id"],
                                "training_generation": owner.generation, "row": r,
                                "key": f"{episode}:{r}"}, "outcome": 3}
                    for r in range(2)]}
            producer = SegmentManagedExecutor.__new__(SegmentManagedExecutor)
            producer.attachment = endpoint
            producer.binary_encoders = {episode: encoder}
            auxiliary = producer._upload_auxiliary(labels)
            assert connection.request("episode_end", **labels,
                                      binary_aux=auxiliary) == {"run_id": episode}
    except BaseException:
        owner.close()
        raise
    finally:
        connection.close()
        scorer.close()
    return owner, config, io


def train(owner, datasets):
    """The same controller call order for both implementations."""
    events = []
    for _ in range(datasets):
        assert owner._prepare()
        owner._prepare_ahead()
        while owner.active is not None:
            events.append(owner._step())
            owner._prepare_ahead()
        owner._collect_publication(wait=True)
    return events


def aux_uses(owner):
    return {row["key"]: row["uses"] for pool in (owner.terminal, owner.hp)
            for rows in pool.groups.values() for row in rows}


def strip_slots(value):
    if isinstance(value, dict):
        return {k: strip_slots(v) for k, v in value.items() if k not in {"slot", "created_at"}}
    if isinstance(value, (list, tuple)):
        return [strip_slots(v) for v in value]
    return value


@pytest.mark.parametrize("target_kl", [0.03, 1e-9])
def test_split_CP_learning_state_is_bitwise_equal_to_in_process_on_CPU(
        tmp_path, monkeypatch, target_kl):
    """Same seed, same inputs and call order -> same CP learning state, bit for bit."""
    results = {}
    for sgd in (False, True):
        root = tmp_path / ("split" if sgd else "inproc")
        owner, config, _ = terminal_owner(root, monkeypatch, sgd=sgd,
                                          segments={"target_kl": target_kl})
        try:
            assert aux_uses(owner) and set(aux_uses(owner).values()) == {0}
            events = train(owner, 3)
            owner.checkpoint(final=True)
            _, cp = read_cp(owner)
            results[sgd] = cp, events, aux_uses(owner), owner.version
            if sgd:
                # Controller never touched CUDA; theta lives only in the child.
                assert not torch.cuda.is_initialized()
                assert not hasattr(owner, "model") and not hasattr(owner, "optimizer")
                assert all("sgd_timing" in event for event in events)
                timing = events[0]["sgd_timing"]
                assert timing["optimizer_seconds"] > 0 and timing["step_wall_seconds"] > 0
                assert "host_timing" in events[0]
                # Prepared-batch files are gone once staged; init/state files too.
                assert not list(owner.live_work.glob("rl-ready-*"))
                assert not list((owner.live_work / "sgd").glob("*.tree"))
        finally:
            owner.close()
    (base, base_events, base_aux, base_version), (split, events, aux, version) = (
        results[False], results[True])
    stopped = [event["kl_stopped"] for event in base_events]
    assert [event["kl_stopped"] for event in events] == stopped
    if target_kl < 1e-6:
        assert any(stopped) and base.counters.updates < 6  # brake mid- and first-minibatch
    else:
        assert not any(stopped) and base.counters.updates == 6
    assert base.counters == split.counters and base_version == version
    assert base_aux == aux and any(aux.values())  # successful aux uses, one ledger
    for name in ("weights", "optimizer", "ema"):
        assert_tree(getattr(base, name), getattr(split, name))
    assert base.optimizer_parameter_names == split.optimizer_parameter_names
    assert base.construction == split.construction and base.conversion == split.conversion
    assert base.rng.learner_random == split.rng.learner_random
    assert torch.equal(base.rng.torch_rng, split.rng.torch_rng)
    assert_tree(base.rng.cuda_rng, split.rng.cuda_rng)
    # The SGD process did not consume torch RNG: it is still the seeded state.
    generator = torch.Generator().manual_seed(base.config["seed"])
    assert torch.equal(split.rng.torch_rng, generator.get_state())
    for key in ("committed_selection_rng", "speculative_selection_rng", "pending_labels"):
        assert base.input_pipeline[key] == split.input_pipeline[key]
    assert strip_slots(base.input_pipeline["pending_requests"]) == strip_slots(
        split.input_pipeline["pending_requests"])
    for left, right in zip(base_events, events, strict=True):
        for key in ("update", "first_use", "first_use_multi", "td_loss", "policy_loss",
                    "approx_kl_mu_theta", "gradient_norm", "stepped"):
            assert left[key] == right[key], key


def test_CP_restores_across_implementations_both_directions(tmp_path, monkeypatch):
    """A split CP resumes in-process and split; the split resume equals the in-process one."""
    owner, config, io = terminal_owner(tmp_path / "a", monkeypatch, sgd=True, episodes=1)
    try:
        train(owner, 1)
        owner.checkpoint(final=True)
        record, cp = read_cp(owner)
    finally:
        owner.close()
    states = []
    for index, sgd in enumerate((False, True)):
        dataplane = {key: value for key, value in io.dataplane.items()
                     if sgd or key not in SGD_PROCESS}
        request = dc.replace(
            config, start_package=None, resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dc.replace(config.segments, training_generation=f"g-restore-{index}"))
        restored = SegmentLearner(request, tmp_path / f"restored-{index}", dc.replace(
            io, dataplane={**dataplane, "root": str(tmp_path / f"live-{index}")}))
        try:
            assert restored.updates == cp.counters.updates
            assert (restored.sgd is not None) is sgd
            state = restored._learning_state()
            for name in ("weights", "optimizer", "ema"):
                assert_tree(getattr(cp, name), getattr(state, name))
            assert torch.equal(cp.rng.torch_rng, state.rng.torch_rng)
            assert state.rng.learner_random == cp.rng.learner_random
            assert state.counters.mu_version == cp.counters.mu_version + 1
            assert dc.replace(state.counters, mu_version=0) == dc.replace(
                cp.counters, mu_version=0)
            states.append(state)
        finally:
            restored.close()
    for name in ("weights", "optimizer", "ema"):
        assert_tree(getattr(states[0], name), getattr(states[1], name))


def test_controller_with_CUDA_context_is_refused_before_any_resource(tmp_path, monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: True)
    with pytest.raises(BoundaryError, match="sgd_process_controller_has_CUDA_context"):
        terminal_owner(tmp_path, monkeypatch, episodes=0)
    assert not (tmp_path / "owner" / "inference.json").exists()


def test_options_require_explicit_selection_and_binary_profile():
    assert sgd_options(None) is None and sgd_options({"lanes": 1}) is None
    with pytest.raises(ValueError, match="without_explicit_selection"):
        sgd_options({"sgd_stall_seconds": 5})
    with pytest.raises(ValueError, match="must_be_boolean"):
        sgd_options({"sgd_process": "yes"})
    with pytest.raises(ValueError, match="invalid_memory_budget"):
        sgd_options({"sgd_process": True, "memory_budget": {"invented": 1}})
    assert sgd_options({"sgd_process": True}).stall_seconds == 120


def test_memory_budget_counts_tmpfs_and_pinned_and_refuses_without_shrinking(tmp_path,
                                                                             monkeypatch):
    import stpd.rl.sgd_process as module

    shm = {"sources": "tmpfs", "arenas": "tmpfs", "wal": "xfs"}
    monkeypatch.setattr(module, "_filesystem", lambda path: shm.get(path.name, "apfs"))
    GiB = 1024**3
    # Friday-like profile: 20 GiB spool, 30 GiB arena, 8 GiB prepared, 64 GiB cgroup.
    budget = memory_budget(
        options=SgdOptions(memory_limit_bytes=64 * GiB), device="cuda", model_bytes=10**8,
        source_bytes=20 * GiB, prepare_source_bytes=30 * GiB, prepared_bytes=8 * GiB,
        per_batch_bytes=8 * GiB // 5, wal_bytes=4 * GiB, workers=15,
        paths={"sources": Path("sources"), "arenas": Path("arenas"), "wal": Path("wal")})
    rows = {row["item"]: row for row in budget["items"]}
    assert rows["source_spool"]["counted"] and rows["prepare_arenas"]["counted"]
    assert not rows["wal"]["counted"] and rows["pinned_slots"]["kind"] == "pinned"
    assert budget["usable_bytes"] == int(64 * GiB * 0.8) and not budget["fits"]
    assert rows["prepare_arenas"]["bytes"] == 30 * GiB  # reported as configured
    with pytest.raises(BoundaryError, match="sgd_process_memory_budget_exceeded"):
        terminal_owner(tmp_path, monkeypatch, episodes=0,
                       dataplane={"memory_limit_bytes": 1024**3})
    refused = json.loads((tmp_path / "owner" / "sgd-memory-budget.json").read_bytes())
    assert not refused["fits"] and refused["limit_source"] == "profile_memory_limit_bytes"
    # Refused before any SGD process, listener or attachment.
    assert not (tmp_path / "owner" / "inference.json").exists()


def test_forged_step_update_is_fatal_and_never_charged(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1)
    try:
        assert owner._prepare()
        original = owner.sgd.expect

        def forged(kind, **kwargs):
            message = original(kind, **kwargs)
            if kind == "step":
                message = {**message, "update": message["update"] + 1}
            return message

        monkeypatch.setattr(owner.sgd, "expect", forged)
        before = owner.updates, owner.first_use, aux_uses(owner)
        with pytest.raises(SgdProcessFailed) as caught:
            owner._step()
        assert caught.value.facts["code"] == "sgd_step_fence_mismatch"
        assert (owner.updates, owner.first_use, aux_uses(owner)) == before
        assert not owner.sgd.process.is_alive()  # both sides fatal
    finally:
        owner.close()


def test_state_with_other_counters_cannot_pair_into_a_CP(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1)
    try:
        train(owner, 1)
        original = owner.sgd.expect

        def forged(kind, **kwargs):
            message = original(kind, **kwargs)
            return {**message, "updates": message["updates"] - 1} if kind == "state" else message

        monkeypatch.setattr(owner.sgd, "expect", forged)
        with pytest.raises(SgdProcessFailed) as caught:
            owner.checkpoint(final=True)
        assert caught.value.facts["code"] == "sgd_state_counters_changed"
        assert owner.exchange.latest_checkpoint(training_generation=owner.generation) is None
    finally:
        owner.close()


def test_child_rejects_a_start_without_its_staged_plan(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1)
    try:
        sgd = owner.sgd
        sgd.sent += 1  # bypass only the controller-side check; same wire format
        sgd.connection.send({"protocol": "stpd/rl-sgd-process-1", "kind": "start",
                             "sequence": sgd.sent, "run": sgd.run,
                             "training_generation": sgd.generation, "dataset": 99,
                             "sent_at": time.time()})
        with pytest.raises(SgdProcessFailed) as caught:
            sgd.expect("step", timeout=30)
        facts = caught.value.facts
        assert facts["code"] == "sgd_process_failed"
        assert facts["child"]["error_code"] == "sgd_start_without_staged_plan"
        assert facts["exitcode"] == 1
    finally:
        owner.close()


@pytest.mark.parametrize("damage", ["deleted", "changed"])
def test_prepared_file_must_survive_unchanged_until_staged(tmp_path, monkeypatch, damage):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1)
    try:
        handoff = owner.pending_batch[1].result(timeout=60) if owner.pending_batch else None
        if handoff is None:
            active = owner._select()
            owner.pending_batch = active, owner._submit_batch(active)
            handoff = owner.pending_batch[1].result(timeout=60)
        path = Path(handoff.path)
        assert path.exists() and handoff.sha256
        if damage == "deleted":
            path.unlink()
        else:
            with path.open("r+b") as stream:
                first = stream.read(1)
                stream.seek(0)
                stream.write(bytes([first[0] ^ 1]))
        with pytest.raises(SgdProcessFailed) as caught:
            owner._prepare()
        child = caught.value.facts["child"]
        assert child["stage"] == "stage"
        assert (child["error_type"] == "FileNotFoundError" if damage == "deleted"
                else child["error_code"] == "sgd_prepared_file_integrity_changed")
        assert owner.updates == 0
    finally:
        owner.close()


def test_unsent_mu_snapshots_are_replaced_and_versions_stay_contiguous(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3)
    held: list[tuple[Future, Future]] = []
    original = owner._submit_publication

    def hold(snapshot, version, updates):
        real, outer = original(snapshot, version, updates), Future()
        held.append((real, outer))
        return outer

    try:
        monkeypatch.setattr(owner, "_submit_publication", hold)
        for _ in range(3):
            assert owner._prepare()
            while owner.active is not None:
                owner._step()
        # Dataset 1 is in flight; dataset 2's file was replaced by dataset 3's.
        assert len(held) == 1 and owner.latest_snapshot["updates"] == owner.updates
        files = sorted((owner.live_work / "sgd").glob("mu-*.tree"))
        assert [path.name for path in files] == [
            f"mu-{2:09d}.tree", f"mu-{owner.updates:09d}.tree"]
        real, outer = held[0]
        outer.set_result(real.result(timeout=60))
        owner._collect_publication(wait=False)
        assert len(held) == 2
        real, outer = held[1]
        outer.set_result(real.result(timeout=60))
        owner._collect_publication(wait=True)
        assert owner.version == 2 and owner.published_updates == owner.updates
        versions = [owner.exchange.model(v, training_generation=owner.generation)
                    for v in (1, 2)]
        assert [record["training"]["updates"] for record in versions] == [2, owner.updates]
        assert not list((owner.live_work / "sgd").glob("mu-*.tree"))
    finally:
        owner.close()


def _run_with(owner, after_first_step):
    original = owner._account_step
    calls = []

    def account(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            after_first_step(owner)
        return result

    owner._account_step = account
    return calls


@pytest.mark.parametrize("injection", ["kill", "hang"])
def test_child_kill_or_hang_fences_reports_failed_and_keeps_last_CP(
        tmp_path, monkeypatch, injection):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=2,
                                 dataplane={"sgd_stall_seconds": 3})
    owner.checkpoint()
    owner._collect_checkpoint(wait=True)
    record, _ = read_cp(owner)
    pid = owner.sgd.pid

    def inject(learner):
        os.kill(pid, signal.SIGKILL if injection == "kill" else signal.SIGSTOP)

    calls = _run_with(owner, inject)
    began = time.monotonic()
    with pytest.raises(BoundaryError, match="input_owner_fenced_restore_last_checkpoint"):
        owner.run()
    assert time.monotonic() - began < 60
    assert len(calls) == 1 and owner.input_fenced
    final = json.loads((owner.work / "control-wal-final.json").read_bytes())
    failed = [e["request"] for e in final["events"] if e["request"]["kind"] == "failed"]
    assert len(failed) == 1
    facts = failed[0]["sgd_failure"]
    assert facts["component"] == "sgd_process" and facts["last_update"] == 1
    if injection == "kill":
        assert facts["code"] == "sgd_process_exited" and facts["signal"] == "SIGKILL"
    else:
        assert facts["code"] == "sgd_process_stalled" and facts["timeout_seconds"] >= 3
    assert failed[0]["updates"] == 1
    summary = json.loads((owner.work / "learner-summary.json").read_bytes())
    assert summary["input_fenced"] and summary["input_failure"]["operation"] == "sgd_process"
    assert summary["sgd_process"]["failure"]["code"] == facts["code"]
    # No new CP: OPS restores the last verified one under a new generation.
    latest, cp = read_cp(owner)
    assert latest["checkpoint_artifact_id"] == record["checkpoint_artifact_id"]
    assert cp.counters.updates == 0
    assert not owner.sgd.process.is_alive()


def test_controller_end_closed_makes_child_exit_without_writing(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=0)
    try:
        before = set((owner.live_work / "sgd").iterdir())
        owner.sgd.connection.close()  # what a dead controller looks like to the child
        owner.sgd.process.join(5)
        assert owner.sgd.process.exitcode == 70
        assert set((owner.live_work / "sgd").iterdir()) == before
    finally:
        owner.sgd.failure = SgdProcessFailed("sgd_process_exited", {})
        owner.close()


def test_child_start_failure_refuses_learner_before_listener(tmp_path, monkeypatch):
    import stpd.rl.segment_learner as module

    original = module.write_tree

    def corrupt(path, value):
        file = original(path, value)
        return {**file, "sha256": "0" * 64} if path.name == "init.tree" else file

    monkeypatch.setattr(module, "write_tree", corrupt)
    with pytest.raises(SgdProcessFailed) as caught:
        terminal_owner(tmp_path, monkeypatch, episodes=0)
    child = caught.value.facts["child"]
    assert child["stage"] == "init" and child["error_code"] == "sgd_file_integrity_changed"
    assert not (tmp_path / "owner" / "inference.json").exists()
    final = json.loads((tmp_path / "owner" / "control-wal-final.json").read_bytes())
    assert final["events"][-1]["request"]["kind"] == "failed"
    assert final["events"][-1]["request"]["sgd_failure"]["code"] == "sgd_process_failed"


def test_stop_mid_dataset_finishes_only_the_started_dataset(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3)

    def stop(learner):
        (learner.work / "STOP").touch()

    calls = _run_with(owner, stop)
    summary = owner.run()
    # Dataset 1's two minibatches finish; the pre-staged dataset 2 never trains.
    assert len(calls) == 2 and summary["datasets"] == 1 and summary["updates"] == 2
    _, cp = read_cp(owner)
    assert cp.counters.updates == 2 and cp.counters.datasets == 1
    assert summary["sgd_process"]["exit"]["exitcode"] == 0
    assert not summary["sgd_process"]["exit"]["shutdown_killed"]
    assert not list(owner.live_work.glob("rl-ready-*"))
