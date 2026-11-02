"""Actual TCP/CAS/MStd SGD, frozen dataset and empty-work-directory restore.

The public inputs and completed transitions are fixtures, not native gameplay.
No GPU, World or quality qualification is established by this engineering test.
"""

from __future__ import annotations

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
    for name in ("manifest.json", "agent-manifest.json"):
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

    def commit(segment):
        artifact = publish_segment(learner.exchange.store, segment, PRODUCER)
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
        final = read_segment(learner.exchange.store, manifest.artifact_id, scratch=tmp_path)
        final["hp_battles"][0]["hp"] = 0.42
        import pytest

        from spireagent.json_boundary import BoundaryError

        with pytest.raises(BoundaryError, match="battle_hp_boundary_changed"):
            publish_segment(learner.exchange.store, final, PRODUCER)
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
    exchange.request_stop('permanent user stop')
    import pytest

    from spireagent.json_boundary import BoundaryError

    with pytest.raises(BoundaryError, match='cannot_resume_a_permanently_stopped_run'):
        SegmentLearner(config, tmp_path/'stopped-work', io)
    assert exchange.latest_model() is None
    assert not (tmp_path/'stopped-work/inference.json').exists()
