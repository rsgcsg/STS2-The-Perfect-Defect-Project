"""RL5 S3 I1: explicit SGD-process alternative against the default in-process E4.

Real socket/WAL/preparation workers and a real spawned SGD child on CPU; zero
World. CPU is single-threaded and deterministic, so learning state is compared
bit for bit. GPU BF16/TF32 equality is a tolerance claim for the Friday plan.
"""
from __future__ import annotations

import dataclasses as dc
import json
import math
import os
import signal
import socket
import time
from concurrent.futures import Future
from dataclasses import asdict
from pathlib import Path

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
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
                   dataplane=None, generation="g-sgd-equal", device="cpu"):
    """Each episode: two 2-row segments, the second terminal, so terminal aux rows
    are verified, sampled and charged exactly like the in-process owner."""
    from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor

    tmp_path.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20261107)  # the fixture's start package draws from global torch RNG
    config, io = configuration(tmp_path, monkeypatch, generation=generation)
    config = dc.replace(config, device=device, segments=dc.replace(
        config.segments, minibatches=2, episode_rows=8, archive_min_free_bytes=0,
        archive_min_free_inodes=0, **(segments or {})))
    io = dc.replace(io, dataplane={
        "lanes": 1, "root": str(tmp_path / "live"), "source_slots": 32,
        "prepare_slots": 6, "source_bytes": 64 * 1024**2, "preparation_workers": 2,
        "assembly_workers": 2, "prepared_batches": 3,
        # Helpers here step the lockstep protocol; prestart is tested explicitly.
        **({**SGD_PROCESS, "sgd_prestart": False} if sgd else {}), **(dataplane or {})})
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
        pinned_bytes=8 * GiB, wal_bytes=4 * GiB, workers=15,
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


def _first_handoff(owner):
    handoff = owner.pending_batch[1].result(timeout=60) if owner.pending_batch else None
    if handoff is None:
        active = owner._select()
        owner.pending_batch = active, owner._submit_batch(active)
        handoff = owner.pending_batch[1].result(timeout=60)
    return handoff


@pytest.mark.parametrize("threaded", [False, True])
@pytest.mark.parametrize("damage", ["deleted", "changed"])
def test_prepared_file_must_survive_unchanged_until_staged(tmp_path, monkeypatch, damage,
                                                           threaded):
    """Off: staging fails inline. On: it fails on the stage thread, and the child
    still fails loudly with the same error (#456 review: stage-thread exception)."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1,
                                 dataplane={"sgd_stage_thread": threaded})
    try:
        handoff = _first_handoff(owner)
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
        assert child["stage"] == ("stage_wait" if threaded else "stage")
        assert (child["error_type"] == "FileNotFoundError" if damage == "deleted"
                else child["error_code"] == "sgd_prepared_file_integrity_changed")
        assert caught.value.facts["exitcode"] == 1
        assert owner.updates == 0
    finally:
        owner.close()


def test_close_while_a_plan_is_staging_on_the_stage_thread(tmp_path, monkeypatch):
    """#456 review: a shutdown that arrives while the side thread stages a plan
    waits for that staging, then exits 0 (no kill, no failure, no hang)."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=1,
                                 dataplane={"sgd_stage_thread": True})
    try:
        owner._sgd_plan(_first_handoff(owner))  # sent; not waited for
    finally:
        owner.close()
    exit_facts = owner.sgd.exit
    assert exit_facts["exitcode"] == 0 and not exit_facts["shutdown_killed"]
    assert exit_facts["failure"] is None


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


# Spawned children run this before any test code: it wraps the child's first
# import of `stpd.rl.loss` so attempt N raises a CUDA OOM, the way a real
# allocator failure surfaces from `prepared_update`. Only the SGD child calls
# `prepared_update` on this path; the test process itself never imports this.
_RAISE_IN_CHILD = """
import importlib.machinery, os, sys
class _RaiseAtAttempt:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != "stpd.rl.loss":
            return None
        sys.meta_path.remove(_RaiseAtAttempt)
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        original = spec.loader.exec_module
        def exec_module(module):
            original(module)
            inner, calls = module.prepared_update, [0]
            def prepared_update(*args, **kwargs):
                calls[0] += 1
                if calls[0] == int(os.environ["RL_TEST_SGD_OOM_AT_ATTEMPT"]):
                    import torch
                    raise torch.cuda.OutOfMemoryError("CUDA out of memory (test injection)")
                return inner(*args, **kwargs)
            module.prepared_update = prepared_update
            child = sys.modules.get("stpd.rl.sgd_process")  # absent in preparation workers
            if os.environ.get("RL_TEST_SGD_BROKEN_ALLOCATOR") and child is not None:
                worker = child._SgdWorker
                plain = worker.memory
                def memory(self, *, allocator=False):
                    if allocator:
                        raise RuntimeError("CUDA error: an illegal memory access (test injection)")
                    return plain(self)
                worker.memory = memory
        spec.loader.exec_module = exec_module
        return spec
if os.environ.get("RL_TEST_SGD_OOM_AT_ATTEMPT"):
    sys.meta_path.insert(0, _RaiseAtAttempt)
"""


@pytest.mark.parametrize("broken_allocator", [False, True])
def test_child_reported_cuda_oom_fences_without_charging_the_failed_attempt(
        tmp_path, monkeypatch, broken_allocator):
    """F4 on CPU: the child's own `failed` (OOM type, stage) fences the generation.

    Nothing is retried or run in-process; the failed attempt charges no update,
    first use or aux use; no new CP; OPS restores the last verified CP. The real
    CUDA allocator failure is the GPU check in the S3 report. A broken CUDA
    context (allocator stats raise) still reports the failure facts.
    """
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(_RAISE_IN_CHILD)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        [str(hook), *filter(None, [os.environ.get("PYTHONPATH")])]))
    monkeypatch.setenv("RL_TEST_SGD_OOM_AT_ATTEMPT", "2")
    if broken_allocator:
        monkeypatch.setenv("RL_TEST_SGD_BROKEN_ALLOCATOR", "1")
    owner, _, _ = terminal_owner(tmp_path / "run", monkeypatch, episodes=2)
    owner.checkpoint()
    owner._collect_checkpoint(wait=True)
    record, _ = read_cp(owner)
    charged = []
    _run_with(owner, lambda learner: charged.append(
        (learner.updates, learner.first_use, aux_uses(learner))))
    with pytest.raises(BoundaryError, match="input_owner_fenced_restore_last_checkpoint"):
        owner.run()
    assert owner.input_fenced and len(charged) == 1
    assert (owner.updates, owner.first_use, aux_uses(owner)) == charged[0]
    final = json.loads((owner.work / "control-wal-final.json").read_bytes())
    failed = [e["request"] for e in final["events"] if e["request"]["kind"] == "failed"]
    assert len(failed) == 1 and failed[0]["updates"] == 1
    facts = failed[0]["sgd_failure"]
    assert facts["code"] == "sgd_process_failed" and facts["last_update"] == 1
    assert facts["child"]["error_type"] == "OutOfMemoryError"
    assert facts["child"]["stage"] == "update" and facts["child"]["updates"] == 1
    assert (facts["child"].get("memory_error") == "RuntimeError") is broken_allocator
    assert ("memory" in facts["child"]) is not broken_allocator
    assert facts["exitcode"] == 1 and not owner.sgd.process.is_alive()
    latest, cp = read_cp(owner)
    assert latest["checkpoint_artifact_id"] == record["checkpoint_artifact_id"]
    assert cp.counters.updates == 0


def test_controller_end_closed_makes_child_exit_without_writing(tmp_path, monkeypatch):
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=0)
    try:
        before = set((owner.live_work / "sgd").iterdir())
        # What a dead controller looks like to the child: the socket ends. On
        # Linux close() alone does not wake our own reader blocked in recv on
        # the same fd, so the peer would never see EOF; shutdown() ends it.
        connection = owner.sgd.connection
        with socket.socket(fileno=os.dup(connection.fileno())) as duplicate:
            duplicate.shutdown(socket.SHUT_RDWR)
        connection.close()
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
    assert not list((tmp_path / "live").rglob("init.tree"))


def test_failure_after_child_start_kills_the_child_before_raising(tmp_path, monkeypatch):
    """Review H1: a listener/attachment failure must not leave a non-daemon child."""
    import stpd.rl.segment_learner as module
    from stpd.rl.sgd_process import SgdProcess

    started = []
    original = SgdProcess.start

    def record(self, init):
        started.append(self)
        return original(self, init)

    def refuse(*_args, **_kwargs):
        raise OSError("injected_listener_failure")

    monkeypatch.setattr(SgdProcess, "start", record)
    monkeypatch.setattr(module, "create_segment_inference_pool", refuse)
    with pytest.raises(OSError, match="injected_listener_failure"):
        terminal_owner(tmp_path, monkeypatch, episodes=0)
    assert len(started) == 1 and not started[0].process.is_alive()
    assert started[0].exit is not None


def test_final_state_capture_failure_reports_sgd_failure(tmp_path, monkeypatch):
    """Review M3: outside the loop fence the failed event still carries the child facts."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=2)
    original = owner.sgd.expect

    def die_on_state(kind, **kwargs):
        if kind == "state":
            os.kill(owner.sgd.pid, signal.SIGKILL)
        return original(kind, **kwargs)

    def stop(learner):
        (learner.work / "STOP").touch()

    monkeypatch.setattr(owner.sgd, "expect", die_on_state)
    _run_with(owner, stop)
    with pytest.raises(SgdProcessFailed):
        owner.run()
    final = json.loads((owner.work / "control-wal-final.json").read_bytes())
    failed = [e["request"] for e in final["events"] if e["request"]["kind"] == "failed"]
    assert len(failed) == 1 and failed[0]["sgd_failure"]["code"] == "sgd_process_exited"
    assert failed[0]["sgd_failure"]["signal"] == "SIGKILL"


def test_handoff_registered_before_its_future_completes_counts_once():
    """Review M1: one batch is never counted in both `preparing` and `handoffs`."""
    from stpd.rl.binary_preparation import BinaryBatchPreparer

    preparer = BinaryBatchPreparer.__new__(BinaryBatchPreparer)
    running, finished = Future(), Future()
    finished.set_result(None)
    preparer.preparing = {running}
    preparer.handoffs = {"a": object(), "b": object()}
    preparer.handoff_owners = {"a": [running], "b": [finished]}
    assert preparer._handed_off() == 1  # "a" is still counted by `preparing`
    preparer.preparing = set()
    assert preparer._handed_off() == 2


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


def test_empty_tensor_sharing_a_file_offset_never_aliases_the_next_array(tmp_path,
                                                                         monkeypatch):
    """Found by the GPU replay (job 1444102): an empty array sits at the same file
    offset as the next same-dtype array; pinning re-packs the mapped batch by
    storage pointer and must not alias that array to 0 bytes."""
    import dataclasses
    import mmap

    import torch

    from stpd.rl import batch_preparation as bp
    from stpd.rl.binary_preparation import _PinnedBuffers, _write_batch
    from stpd.rl.sgd_process import mapped_arrays

    @dataclasses.dataclass(frozen=True)
    class Batch:
        tensors: dict

    batch = Batch({"empty": torch.zeros(0, dtype=torch.int64), "full": torch.arange(5)})
    path = tmp_path / "ready.bin"
    with path.open("wb") as stream:
        stream.truncate(1 << 16)
    packed = _write_batch(batch, str(path))
    offsets = [view.offset for view in packed.arrays]
    assert offsets[0] == offsets[1]  # the layout that used to collide
    with path.open("rb") as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
    arrays = mapped_arrays(mapping, [(v.offset, v.count, v.dtype) for v in packed.arrays])
    mapped = bp.unpack_transport((packed.tree, arrays))
    empty = torch.empty
    monkeypatch.setattr(torch, "empty", lambda *a, **k: empty(
        *a, **{key: value for key, value in k.items() if key != "pin_memory"}))
    pinned = _PinnedBuffers(1 << 20, slots=2)._copy(mapped, 0, None)
    assert pinned.tensors["full"].tolist() == [0, 1, 2, 3, 4]
    assert pinned.tensors["empty"].shape == (0,)
    del mapped, arrays, pinned
    mapping.close()


def test_read_ahead_while_waiting_keeps_the_same_learning_and_fills_the_window(
        tmp_path, monkeypatch):
    """WP8 starvation: `ahead_while_waiting` only submits the same read-ahead
    selections earlier (while the next batch is still being prepared)."""
    results = {}
    for ahead in (False, True):
        owner, _, _ = terminal_owner(tmp_path / f"ahead-{ahead}", monkeypatch,
                                     dataplane={"ahead_while_waiting": ahead})
        calls = {"inside": 0, "depth": 0}
        fill, prepare = owner._prepare_ahead, owner._prepare

        def counted(fill=fill, calls=calls):
            calls["inside"] += calls["depth"]  # a call from the wait inside _prepare
            fill()

        def preparing(prepare=prepare, calls=calls):
            calls["depth"] += 1
            try:
                return prepare()
            finally:
                calls["depth"] -= 1

        monkeypatch.setattr(owner, "_prepare_ahead", counted)
        monkeypatch.setattr(owner, "_prepare", preparing)
        try:
            events = train(owner, 3)
            owner.checkpoint(final=True)
            _, cp = read_cp(owner)
            results[ahead] = cp, events, aux_uses(owner), calls["inside"]
        finally:
            owner.close()
    (base, base_events, base_aux, base_inside), (cp, events, aux, inside) = (
        results[False], results[True])
    assert base_inside == 0 and inside > 0
    assert base.counters == cp.counters and base_aux == aux
    for name in ("weights", "optimizer", "ema"):
        assert_tree(getattr(base, name), getattr(cp, name))
    assert base.input_pipeline["committed_selection_rng"] == cp.input_pipeline[
        "committed_selection_rng"]
    for left, right in zip(base_events, events, strict=True):
        for key in ("update", "first_use", "td_loss", "policy_loss", "stepped"):
            assert left[key] == right[key], key
    with pytest.raises(ValueError, match="ahead_while_waiting_must_be_explicit"):
        terminal_owner(tmp_path / "bad", monkeypatch, dataplane={"ahead_while_waiting": 1})


def assert_close_tree(left, right):
    """`assert_tree` with float tensors equal to a few float32 ulps (CUDA layouts)."""
    if isinstance(left, torch.Tensor) and left.is_floating_point():
        torch.testing.assert_close(left.cpu(), right.cpu(), rtol=1e-4, atol=1e-9)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_close_tree(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for one, two in zip(left, right, strict=True):
            assert_close_tree(one, two)
    else:
        assert_tree(left, right)


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda", marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason="requires an authorized CUDA allocation"))])
def test_stage_thread_keeps_the_same_learning(tmp_path, monkeypatch, device):
    """WP8 starvation: `sgd_stage_thread` moves verify/pin/H2D of the next plan to
    one side thread; batches, order and every update stay the same.

    On CUDA this is the production path: pinned slabs H2D-copied from the side
    thread (`direct`), the default stream waiting on the copy's event. CUDA
    training is not bit-deterministic run to run here (1460164: the same path
    twice differs by ~1 float32 ulp), so on CUDA the updates must agree to a few
    ulps; a batch read before its copy landed would be far off.
    """
    results = {}
    for threaded in (False, True):
        owner, _, _ = terminal_owner(tmp_path / f"stage-{threaded}", monkeypatch,
                                     dataplane={"sgd_stage_thread": threaded,
                                                "ahead_while_waiting": True}, device=device)
        threads: list[str] = []
        staged = owner.sgd.on_staged

        def seen(message, staged=staged, threads=threads):
            threads.append(message["stage_thread"])
            staged(message)

        monkeypatch.setattr(owner.sgd, "on_staged", seen)
        try:
            events = train(owner, 3)
            owner.checkpoint(final=True)
            _, cp = read_cp(owner)
            results[threaded] = cp, events, aux_uses(owner), threads
        finally:
            owner.close()
    (base, base_events, base_aux, base_threads), (cp, events, aux, threads) = (
        results[False], results[True])
    assert base_threads and threads and len(base_threads) == len(threads)
    assert not any(name.startswith("rl-sgd-stage") for name in base_threads)
    assert all(name.startswith("rl-sgd-stage") for name in threads)
    assert base.counters == cp.counters and base_aux == aux
    if device == "cuda":
        for name in ("weights", "optimizer", "ema"):
            assert_close_tree(getattr(base, name), getattr(cp, name))
        for left, right in zip(base_events, events, strict=True):
            for key in ("update", "first_use", "stepped"):
                assert left[key] == right[key], key
            for key in ("td_loss", "policy_loss"):
                assert math.isclose(left[key], right[key], rel_tol=1e-4, abs_tol=1e-9), key
    else:
        for name in ("weights", "optimizer", "ema"):
            assert_tree(getattr(base, name), getattr(cp, name))
        for left, right in zip(base_events, events, strict=True):
            for key in ("update", "first_use", "td_loss", "policy_loss", "stepped"):
                assert left[key] == right[key], key
    with pytest.raises(ValueError, match="invalid_sgd_stage_thread"):
        terminal_owner(tmp_path / "bad", monkeypatch, dataplane={"sgd_stage_thread": 1})


def _hold_plan_ahead(owner, monkeypatch):
    """Only explicit `_sgd_plan` calls plan: no early or boundary plan-ahead."""
    monkeypatch.setattr(owner, "_sgd_plan_ahead", lambda: None)
    monkeypatch.setattr(owner, "_sgd_prepared", lambda future: None)


def test_stage_overlap_plans_next_dataset_ahead_and_releases_only_after_staged(
        tmp_path, monkeypatch):
    """V3 stage overlap: the next prepared dataset is planned right after the current
    one starts (or when its preparation completes), staged by the side thread while
    the current one trains, and taken without a stage wait. Its derived file and
    PPO bytes are released no earlier than its own `staged` answer, in plan order."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3,
                                 dataplane={"sgd_stage_thread": True,
                                            "ahead_while_waiting": True})
    order: list[tuple[str, int]] = []
    staged = owner.sgd.on_staged
    release = owner.input_preparer.release_handoff

    def seen(message):
        order.append(("staged", message["dataset"]))
        staged(message)

    def released(handoff):
        order.append(("released", handoff.request.identity.sequence))
        assert ("staged", handoff.request.identity.sequence) in order
        release(handoff)

    monkeypatch.setattr(owner.sgd, "on_staged", seen)
    monkeypatch.setattr(owner.input_preparer, "release_handoff", released)
    try:
        events = train(owner, 3)
    finally:
        owner.close()
    datasets = [event["dataset"] for event in events]
    assert datasets == sorted(datasets) and len(set(datasets)) == 3
    staged_order = [dataset for kind, dataset in order if kind == "staged"]
    assert staged_order == sorted(set(datasets))
    assert [d for kind, d in order if kind == "released"] == staged_order
    # Datasets 2 and 3 were staged before they were taken (no blocking stage wait).
    assert owner.timing["sgd_staged_ahead_batches"] >= 1
    assert owner.timing["sgd_stage_wait_seconds"] >= 0
    assert owner.sgd.exit["exitcode"] == 0 and owner.sgd.exit["failure"] is None


def test_staged_ahead_failure_fails_the_running_child_loudly(tmp_path, monkeypatch):
    """A plan staged ahead while a dataset trains fails on the stage thread; the
    child fails at once (stage `stage_wait`), never trains that dataset, and the
    controller gets `SgdProcessFailed`, not a silent skip or a retry."""
    owner, _, _ = terminal_owner(tmp_path, monkeypatch, episodes=3,
                                 dataplane={"sgd_stage_thread": True})
    _hold_plan_ahead(owner, monkeypatch)
    try:
        assert owner._prepare()
        owner._prepare_ahead()
        handoff = owner.pending_batch[1].result(timeout=60)
        with Path(handoff.path).open("r+b") as stream:
            first = stream.read(1)
            stream.seek(0)
            stream.write(bytes([first[0] ^ 1]))
        owner._sgd_plan(handoff)
        with pytest.raises(SgdProcessFailed) as caught:
            for _ in range(20):
                if owner.active is not None:
                    owner._step()
                else:
                    owner._prepare()
        child = caught.value.facts["child"]
        assert child["stage"] == "stage_wait"
        assert child["error_code"] == "sgd_prepared_file_integrity_changed"
        assert handoff.request.identity.sequence not in owner.sgd.staged
        assert Path(handoff.path).exists()  # never released: the child never owned it
    finally:
        owner.close()


def test_checkpoint_with_a_staged_ahead_dataset_keeps_it_unreleased_and_learning_equal(
        tmp_path, monkeypatch):
    """A CP taken while the next dataset is staged in the SGD process but not
    started: that dataset is still in the queue (not taken, PPO bytes retained), so
    the CP restores it; training then takes it without restaging, and the learning
    state equals the inline-staging run with the same CP points, bit for bit."""
    results = {}
    for threaded in (False, True):
        owner, _, _ = terminal_owner(tmp_path / f"cp-{threaded}", monkeypatch, episodes=3,
                                     dataplane={"sgd_stage_thread": threaded})
        try:
            assert owner._prepare()
            owner._prepare_ahead()
            ahead = owner.pending_batch
            ahead_sequence = ahead[1].result(timeout=60).request.identity.sequence
            while owner.active is not None:
                owner._step()
            owner._collect_publication(wait=True)
            if threaded:
                # Between datasets only `staged` can still arrive (as in `_prepare`).
                owner.sgd.wait_staged(ahead_sequence)  # staged ahead, not started
                assert ahead_sequence in owner.sgd.staged
                assert owner.pending_batch is ahead and not ahead[0]["taken"]
                queued = {r["segment_id"] for r in owner.queue.pending}
                assert {r["segment_id"] for r in ahead[0]["segments"]} <= queued
            owner.checkpoint()
            owner._collect_checkpoint(wait=True)
            _, first = read_cp(owner)
            events = train(owner, 2)
            owner.checkpoint(final=True)
            _, last = read_cp(owner)
            results[threaded] = first, last, events, aux_uses(owner)
        finally:
            owner.close()
    (first, last, events, aux), (first_t, last_t, events_t, aux_t) = (
        results[False], results[True])
    assert aux == aux_t
    for left, right in ((first, first_t), (last, last_t)):
        assert left.counters == right.counters
        for name in ("weights", "optimizer", "ema"):
            assert_tree(getattr(left, name), getattr(right, name))
    for left, right in zip(events, events_t, strict=True):
        for key in ("dataset", "update", "first_use", "td_loss", "policy_loss", "stepped"):
            assert left[key] == right[key], key
