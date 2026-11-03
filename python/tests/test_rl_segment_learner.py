"""Actual TCP/CAS/MStd SGD, frozen dataset and empty-work-directory restore.

The public inputs and completed transitions are fixtures, not native gameplay.
No GPU, World or quality qualification is established by this engineering test.
"""

from __future__ import annotations

import base64
import copy
from dataclasses import replace

import torch
from test_p1_input_v2 import public_state
from test_rl_loop import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import FrozenObject, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.storage.store import copy_artifact
from spireagent.workbench.global_evidence_store import publish_detached_p1_model
from spireagent.workbench.managed_segment_store import publish_segment, read_segment
from spireagent.workbench.rl_segment_producer import OriginalBarrier, SegmentAssembler
from stpd.evaluation.suites import derive_game_seed
from stpd.p1.export import export_model, input_spec, runtime_projection
from stpd.p1.local_inference import SegmentConnection, SegmentInferenceClient
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.critic import held_out
from stpd.rl.learner import LearnerConfig
from stpd.rl.loss import LossConfig
from stpd.rl.performance import PerformanceConfig
from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO
from stpd.rl.segments import SegmentConfig
from stpd.workers.checkpoint_codec import encode_checkpoint


def configuration(tmp_path, monkeypatch):
    import stpd.rl.segment_learner as module

    monkeypatch.setattr(module, "source_identity", lambda _: PRODUCER)
    torch.set_num_threads(2)
    torch.manual_seed(41)
    model = MStd(public_vocabulary()).eval()
    model.enable_rl4_heads(9)
    package = tmp_path / "start"
    cp = tmp_path / "source-checkpoint"
    cp.write_bytes(b"synthetic engineering input")
    export_model(model, cp, package, {})
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, "short-loop")
    start = publish_detached_p1_model(exchange.store, package, PRODUCER)
    warmup = Manifest(
        "checkpoint",
        PRODUCER,
        parents=(Parent("model", start.artifact_id),),
        payloads=(
            exchange.store.put_bytes(
                "checkpoint",
                encode_checkpoint(
                    {
                        "schema": "stpd/rl4-head-warmup-1",
                        "model_package_sha256": start.payload("weights").sha256,
                        "heads": model.rl4_heads.state_dict(),
                    }
                ),
            ),
        ),
        parameters=FrozenObject.of({"schema": "stpd/rl4-head-warmup-1"}),
    )
    exchange.store.publish(warmup)
    config = LearnerConfig(
        run="short-loop",
        peer=peer,
        start_artifact_id=start.artifact_id,
        start_model_sha256=start.payload("weights").sha256,
        device="cpu",
        threads=2,
        lr=4e-5,
        wall_seconds=10,
        loss=LossConfig(mode="ppo", value_coef=0.2),
        performance=PerformanceConfig(
            packed=True,
            tensor_cache=True,
            precompile_inputs=True,
            microbatch_size=2,
            scene_attention_area=67108864,
            text_attention_area=67108864,
        ),
        segments=SegmentConfig(
            steps=2,
            dataset_rows=4,
            minibatches=2,
            epochs=2,
            episode_rows=2,
            capacity_rows=16,
            inference_batch=2,
            diagnostics_every=8,
            warmup_artifact_id=warmup.artifact_id,
        ),
    )
    return config, SegmentLearnerIO(read_segment, publish_detached_p1_model)


def deliver(learner, tmp_path, actor, monkeypatch, *, battle=False):
    errors = []
    actual_control = learner.pool.control

    def observed_control(request):
        try:
            return actual_control(request)
        except Exception as error:
            errors.append(error)
            raise

    learner.pool.control = observed_control
    for key, token in learner.pool.environment().items():
        monkeypatch.setenv(key, token)
    episode = f"episode-{actor}"
    attachment = {**learner.pool.attachment(), "actor": actor, "episode": episode}
    directory = tmp_path / actor
    directory.mkdir()
    trace = directory / "behavior.jsonl"
    projection = runtime_projection(learner.model.vocabulary)
    spec = input_spec(learner.model.vocabulary, projection)
    client = SegmentInferenceClient(attachment, projection, trace)
    control = SegmentConnection(attachment)
    inputs = []
    count = 4 if battle else 2
    for index in range(count + 1):
        observation = public_state()
        if index == 1:
            observation["catalog"] = observation["catalog"][:1]
        observation["run"] = {"content": {"floor": 3}}
        if battle:
            observation["combat"] = {
                "availability": "available" if index < count else "unavailable"
            }
            if index == count:
                observation["scene"] = {"native_kind": "reward_card"}
                observation["player"] = {"content": {"hp": 57}}
        inputs.append(
            {
                "acquisition_id": f"{actor}-{index}",
                "consumption_unit": {"id": f"{actor}-unit-{index}"},
                "input_spec": spec,
                "observation": observation,
                "catalog": observation["catalog"],
            }
        )
    scores = [client.consume(value) for value in inputs[:count]]
    client.close()
    for name in ("manifest.json", "agent-manifest.json", "adapter-attestation.json"):
        (directory / name).write_bytes(b"{}\n")
    (directory / "events.jsonl").write_bytes(
        b"".join(json_bytes({"sequence": i}) for i in range(1, count + 2))
    )
    (directory / "global-source.json").write_bytes(b"verified-source fixture")
    seed = next(
        derive_game_seed(f"synthetic-engine-{actor}", i)
        for i in range(1, 100)
        if not held_out(derive_game_seed(f"synthetic-engine-{actor}", i))
    )
    records = []

    def commit(segment, originals):
        artifact = publish_segment(learner.exchange.store, segment, PRODUCER, originals=originals)
        record = {key: segment[key] for key in ("segment_id", "run_id", "rows", "mu")}
        record["segment_artifact_id"] = artifact.artifact_id
        learner.exchange.publish_segment(actor, len(records), record)
        try:
            assert control.request("commit", **record)["segment_id"] == segment["segment_id"]
        except ValueError:
            if errors:
                raise errors[-1] from None
            raise
        records.append(record)
        return {"segment_artifact_id": artifact.artifact_id}

    assembler = SegmentAssembler(
        {
            "intent_id": episode,
            "seed": seed,
            "behavior_policy": {"temperature": 1.0, "uniform_mix": 0.0},
        },
        OriginalBarrier(directory, trace),
        commit,
        step_limit=2,
    )
    try:
        for index in range(count):
            assembler.transition(
                {
                    "schema": "sts2.policy-runtime/verified-global-transition-1",
                    "manifest_original_base64": base64.b64encode(b"{}\n").decode(),
                    "run_id": episode,
                    "step_index": index,
                    "verified_through_sequence": index + 2,
                    "result_sequence": index + 1,
                    "input": inputs[index],
                    "successor": inputs[index + 1],
                    "directive": {
                        "directive": {
                            "selection": {"action_id": inputs[index]["catalog"][0]["choice_id"]},
                            "scores": {"values": scores[index]},
                        }
                    },
                    "result": {"status": "completed"},
                    "original_paths": ["global-source.json"],
                }
            )
        assert (
            control.request(
                "episode_end",
                actor=actor,
                episode=episode,
                run_id=episode,
                terminal=None,
                outcome_states=[],
                hp_battles=[],
            )["run_id"]
            == episode
        )
    finally:
        control.close()
        assembler.close()
    return records


def cleanup(learner):
    learner._collect_publication(wait=True)
    learner.pool.close()
    learner.publisher.shutdown(wait=True)
    learner.checkpoints.__exit__(None)


def test_battle_hp_arrives_across_segments_before_natural_episode_end(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = replace(config, segments=replace(config.segments, episode_rows=4))
    learner = SegmentLearner(config, tmp_path / "work", io)
    try:
        records = deliver(learner, tmp_path, "actor-hp", monkeypatch, battle=True)
        # episode_end above has no terminal and no HP list: the only labels came
        # from the actual committed reward successor, including the earlier mu.
        states = learner.hp.groups["episode-actor-hp:0"]
        assert len(records) == 2 and len(states) == 4
        assert [state["row"] for state in states] == [0, 1, 0, 1]
        assert [state["segment_artifact_id"] for state in states] == [
            record["segment_artifact_id"] for record in records for _ in range(2)
        ]
        assert all(state["hp"] == 0.57 and state["uses"] == 0 for state in states)
        manifest = learner.exchange.store.get_manifest(records[1]["segment_artifact_id"])
        assert Parent("hp-segment-0000", records[0]["segment_artifact_id"]) in manifest.parents
        assert Parent("previous", records[0]["segment_artifact_id"]) in manifest.parents
        from spireagent.storage.archives import _extract

        journal, behavior = b"", b""
        for index, record in enumerate(records):
            artifact = learner.exchange.store.get_manifest(record["segment_artifact_id"])
            raw = b"".join(learner.exchange.store.read_payload(artifact.payload("originals")))
            directory = tmp_path / f"original-block-{index}"
            _extract(raw, directory)
            import json

            commit = json.loads((directory / "commit.json").read_bytes())
            assert commit["journal_start_bytes"] == len(journal)
            assert commit["behavior_trace_start_bytes"] == len(behavior)
            journal += (directory / "events-delta.jsonl").read_bytes()
            behavior += (directory / "behavior-delta.jsonl").read_bytes()
        assert journal == (tmp_path / "actor-hp/events.jsonl").read_bytes()
        assert behavior == (tmp_path / "actor-hp/behavior.jsonl").read_bytes()
        final = read_segment(learner.exchange.store, manifest.artifact_id, scratch=tmp_path)
        final["hp_battles"][0]["hp"] = 0.42
        import pytest

        from spireagent.json_boundary import BoundaryError

        with pytest.raises(BoundaryError, match="battle_hp_boundary_changed"):
            publish_segment(
                learner.exchange.store, final, PRODUCER, originals=b"invalid; not reached"
            )
    finally:
        cleanup(learner)


def test_real_delivery_sgd_and_mid_dataset_restore_keep_frozen_targets_and_counts(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    original = SegmentLearner(config, tmp_path / "original-work", io)
    restored = None
    try:
        deliver(original, tmp_path, "actor-a", monkeypatch)
        deliver(original, tmp_path, "actor-b", monkeypatch)
        assert not original.queue.leases and not original.actor_episodes
        assert original._prepare()
        frozen = copy.deepcopy(original.active["frozen"])
        original._step()
        assert original.updates == 1 and original.first_use == 2
        original.checkpoint(final=True)
        saved_rng = torch.get_rng_state().clone()
        record = original.exchange.latest_checkpoint()
        assert record["version"] == 0 and record["checkpoint_sequence"] == 1
        replica_peer = {"kind": "local", "directory": str(tmp_path / "cold-peer")}
        replica = Exchange(replica_peer, config.run)
        copy_artifact(
            original.exchange.store,
            replica.store,
            record["checkpoint_artifact_id"],
            scratch=tmp_path,
        )
        replica.publish_model(original.exchange.model(0))
        restored = SegmentLearner(
            replace(config, peer=replica_peer, resume_checkpoint=record["checkpoint_artifact_id"]),
            tmp_path / "empty-restored-work",
            io,
        )
        assert restored.active["minibatch"] == 1 and restored.first_use == 2
        assert torch.equal(torch.get_rng_state(), saved_rng)
        for key in ("targets", "probabilities", "advantages", "raw_advantages"):
            assert torch.equal(restored.active["frozen"][key], frozen[key])
        # Restored work materializes the real model/segment artifacts. Target
        # fitting is forbidden: continuing a frozen dataset must reuse its bytes.
        import stpd.rl.segment_learner as module

        monkeypatch.setattr(
            module,
            "freeze_targets",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("frozen targets were recomputed")),
        )
        assert restored._prepare()
        for learner in (original, restored):
            while learner.datasets == 0:
                learner._step()
            assert learner.updates == 4 and learner.first_use == 4
            assert learner.first_use_multi == 2
        for name, value in original.model.state_dict().items():
            torch.testing.assert_close(
                value, restored.model.state_dict()[name], atol=2e-7, rtol=2e-6
            )
    finally:
        cleanup(original)
        if restored is not None:
            cleanup(restored)


def test_permanent_remote_stop_rejects_before_publication_or_inference(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    exchange = Exchange(config.peer, config.run)
    exchange.request_stop("permanent user stop")
    import pytest

    from spireagent.json_boundary import BoundaryError

    with pytest.raises(BoundaryError, match="cannot_resume_a_permanently_stopped_run"):
        SegmentLearner(config, tmp_path / "stopped-work", io)
    assert exchange.latest_model() is None
    assert not (tmp_path / "stopped-work/inference.json").exists()


def test_budget_stop_publishes_partial_dataset_theta_and_keeps_resume_cursor(tmp_path, monkeypatch):
    from stpd.p1.export import action_weights, load_model
    from stpd.workers.checkpoint_codec import decode_checkpoint

    config, io = configuration(tmp_path, monkeypatch)
    learner = SegmentLearner(config, tmp_path / "work", io)
    deliver(learner, tmp_path, "first", monkeypatch)
    deliver(learner, tmp_path, "second", monkeypatch)
    assert learner._prepare()
    learner._step()
    assert learner.updates == 1 and learner.version == 0 and learner.published_updates == 0
    (learner.work / "STOP").touch()
    summary = learner.run()
    assert summary["updates"] == summary["published_updates"] == 1 and summary["versions"] == 1
    record = learner.exchange.model(1)
    checkpoint = learner.exchange.store.get_manifest(
        learner.exchange.latest_checkpoint()["checkpoint_artifact_id"]
    )
    state = decode_checkpoint(
        b"".join(learner.exchange.store.read_payload(checkpoint.payload("checkpoint")))
    )
    import json

    metadata = json.loads(state["metadata"].cpu().numpy().tobytes())
    assert metadata["active"]["minibatch"] == 1 and metadata["active"]["epoch"] == 0
    assert metadata["first_use"] == 2 and metadata["published_updates"] == 1
    package = learner.exchange.materialize_model(record, tmp_path / "readback")
    actual, _ = load_model(package / "model.tensor-tree", record["model_sha256"])
    for name, value in action_weights(state["weights"]).items():
        assert torch.equal(value.cpu(), actual.state_dict()[name].cpu())
    assert record["training"]["updates"] == 1


def test_prepare_only_publishes_a_restorable_start_with_zero_uses(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    learner = SegmentLearner(config, tmp_path / "work", io)
    summary = learner.run(initialize_only=True)
    assert summary["updates"] == summary["first_use"] == summary["first_use_multi"] == 0
    assert (
        summary["versions"] == summary["unresolved_inflight"] == summary["unresolved_episodes"] == 0
    )
    assert learner.exchange.latest_checkpoint()["checkpoint_sequence"] == 1
    assert learner.exchange.model(0)["training"]["updates"] == 0


def test_async_checkpoint_is_announced_while_idle_before_next_capture_interval(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    config = replace(config, wall_seconds=30)
    learner = SegmentLearner(config, tmp_path / "work", io)
    announced_after_stop = []
    original = learner.exchange.publish_checkpoint

    def published(*args, **kwargs):
        result = original(*args, **kwargs)
        announced_after_stop.append(learner.newlease_stopped)
        (learner.work / "STOP").touch()
        return result

    monkeypatch.setattr(learner.exchange, "publish_checkpoint", published)
    learner.checkpoint()  # Real asynchronous CAS publication; no data/actors.
    learner.run()
    assert announced_after_stop[0] is False
    assert learner.exchange.latest_checkpoint()["checkpoint_sequence"] == 2


def test_cold_restore_loads_three_mu_snapshots_without_republishing_latest(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    original = SegmentLearner(config, tmp_path / "three-mu-work", io)
    restored = None
    try:
        # Distinct test-only weights create real publications, not SGD evidence.
        # Keep all three protected versions as a live queued-data CP can do.
        for version in (1, 2):
            with torch.no_grad():
                original.model.head[2].weight.add_(0.001 * version)
            publication = original._publish_model(original._capture_model(), version, 0)
            frozen = publication["model"]
            original._configure_mu(frozen)
            original.pool.publish(frozen, publication["identity"])
            original.version = version
        identities = copy.deepcopy(original.pool.snapshots)
        latest = original.pool.latest
        assert len(identities) == 3
        original.checkpoint(final=True)
        checkpoint = original.exchange.latest_checkpoint()
        replica_peer = {"kind": "local", "directory": str(tmp_path / "three-mu-cold")}
        replica = Exchange(replica_peer, config.run)
        copy_artifact(original.exchange.store, replica.store,
                      checkpoint["checkpoint_artifact_id"], scratch=tmp_path)
        for version in (0, 1, 2):
            replica.publish_model(original.exchange.model(version))
        restored = SegmentLearner(
            replace(config, peer=replica_peer,
                    resume_checkpoint=checkpoint["checkpoint_artifact_id"]),
            tmp_path / "three-mu-replacement", io)
        assert restored.pool.snapshots == identities and restored.pool.latest == latest
        assert restored.updates == original.updates == 0
        assert restored.version == original.version == 2
    finally:
        cleanup(original)
        if restored is not None:
            cleanup(restored)
