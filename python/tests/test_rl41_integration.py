"""Cross-line CPU process/socket integration; synthetic public transitions, no World claim."""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest
import torch
from test_rl41_w1 import (
    assert_tree,
    configuration,
    construction,
    deliver,
    legacy_cp16,
    segment,
)
from test_rl41_w1 import (
    model as model,
)
from test_rl_loop import PRODUCER

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import FrozenObject
from spireagent.rl_exchange import Exchange
from spireagent.workbench.rl_segment_producer import SegmentManagedExecutor
from stpd.p1.local_inference import AUTH_ENV, CentralInferencePool, SegmentConnection
from stpd.p1.local_mu import (
    ActorControlConnection,
    ActorInferencePool,
    RemoteMuScorer,
)
from stpd.rl.checkpoint_state import SCHEMA, CP16ConversionInput, convert_cp16, encode_state
from stpd.rl.segment_learner import SegmentLearner


@pytest.mark.parametrize("central", [False, True], ids=["local", "central"])
def test_CP16_update_publish_adopt_CP_replace_only_learner_continue(
    tmp_path, monkeypatch, model, central
):
    config, io = configuration(tmp_path, monkeypatch, generation="g-first")
    rendezvous = tmp_path / "shared" / "inference.json"
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, rendezvous_path=str(rendezvous), central_scoring=central))
    state, names = legacy_cp16(model)
    cp = convert_cp16(CP16ConversionInput(state, construction(model), names, "1" * 64,
        "g-legacy", "g-converted", dataclasses.asdict(config), PRODUCER.to_dict()))
    exchange = Exchange(config.peer, config.run)
    original = Manifest("checkpoint", PRODUCER,
        payloads=(exchange.store.put_bytes("checkpoint", encode_state(cp)),),
        parameters=FrozenObject.of({"schema": SCHEMA}))
    exchange.store.publish(original)
    owner = SegmentLearner(dataclasses.replace(config, start_package=None,
        resume_checkpoint=original.artifact_id), tmp_path / "learner-1", io)
    monkeypatch.setenv(AUTH_ENV, owner.pool.environment()[AUTH_ENV])
    remote = {**owner.pool.attachment(), "rendezvous": str(rendezvous)}
    from spireagent.workbench.rl_actor import Actor

    execution = tmp_path / "execution.json"
    execution.write_text(json.dumps({"peer": config.peer,
        "retention": {"tier": "trajectory", "purpose": "training", "audit_every": 100}}))
    actual_actor = Actor({"schema": "spireagent/rl-actor-config-1", "actor": "integration",
        "execution": str(execution), "peer": config.peer, "run": config.run,
        "segments": {"inference": remote, "rendezvous": str(rendezvous)}}, tmp_path / "actor")
    actual_actor.prefetch.refresh()
    assert actual_actor.prefetch.newest()[0]["training_generation"] == owner.generation
    assert exchange.latest_model() is None  # legacy namespace is deliberately not selected
    actor = actual_actor.actor_pool
    class LostBeforeRestoredCommit(SegmentConnection):
        injected = False

        def _request_once(self, operation, fields):
            if (operation == "commit" and fields.get("training_generation") == "g-second"
                    and not self.injected):
                self.injected = True
                raise ConnectionResetError("lost before loopback control delivery")
            return super()._request_once(operation, fields)

    client = LostBeforeRestoredCommit(actor.attachment())
    replacement = None
    owner_closed = False
    try:
        assert owner.updates == 362 and owner.version == 58
        deliver(owner, actor="a", connection=client, terminal=True)
        deliver(owner, actor="b", connection=client, terminal=True)
        assert owner._prepare()
        while owner.active is not None:
            owner._step()
        owner._collect_publication(wait=True)
        assert owner.updates == 366
        assert client.request("episode_open", actor="live", episode="same-world")["admitted"]
        grant = client.request("begin", actor="live", episode="same-world", previous_lease=None)
        assert grant["mu"]["version"] == 59 and grant["mu"]["updates"] == 366
        # Keep the same actor/socket/model owner and one in-flight old lease.
        old_key = grant["mu"]["snapshot_sha256"]
        frozen_mu = owner.pool.models[old_key] if central else actor.scorer.models[old_key]
        old_tail = segment(frozen_mu,
            name=grant["lease"], run_id="same-world", rows=2, generation=owner.generation,
            mu=grant["mu"])
        if central:
            # Leave a real score socket connected to the old learner. Adoption
            # after replacement must reconnect before sending a new-mu score.
            actor.scorer.score_compiled(old_key,
                old_tail["steps"][-1]["behavior"]["compiled_input"], 3, policy=False)
            old_score_connection = actor.scorer.remote.connection
        owner.checkpoint(final=True)
        record = exchange.latest_checkpoint(training_generation=owner.generation)
        assert record["checkpoint_sequence"] == 17
        previous = owner._learning_state()
        owner.close()
        owner_closed = True
        replacement = SegmentLearner(dataclasses.replace(config, start_package=None,
            resume_checkpoint=record["checkpoint_artifact_id"],
            segments=dataclasses.replace(config.segments, training_generation="g-second")),
            tmp_path / "learner-2", io)
        assert_tree(replacement.optimizer.state_dict(), previous.optimizer)
        assert_tree(replacement.ema, previous.ema)
        executor = SimpleNamespace(exchange=exchange, actor="live",
            admission=ActorControlConnection(actor))
        executor._discard_hot_segment = lambda value: SegmentManagedExecutor._discard_hot_segment(
            executor, value)
        # New grant reaches the actor before the observer sees its first old
        # row. The actor lease must preserve that old mu until explicit discard.
        new_grant = client.request("begin", actor="live", episode="same-world",
            previous_lease=grant["lease"])
        assert new_grant["mu"]["training_generation"] == "g-second"
        assert new_grant["lease"] == "live/0"
        # The producer's separate loopback channel retains its startup attachment.
        # A new-generation control input must reach the actual learner unchanged.
        actor.scorer.retain_mu(old_key)
        if central:
            from spireagent.json_boundary import BoundaryError

            assert old_score_connection.closed
            new_key = new_grant["mu"]["snapshot_sha256"]
            assert actor.scorer.score_compiled(new_key,
                old_tail["steps"][-1]["behavior"]["compiled_input"], 3,
                policy=True).snapshot_sha256 == new_key
            with pytest.raises(BoundaryError, match="generation_changed"):
                actor.scorer.score_compiled(old_key,
                    old_tail["steps"][-1]["behavior"]["compiled_input"], 3, policy=False)
        discarded = SegmentManagedExecutor._commit_hot_segment(
            executor, {"intent_id": "same-world"}, client, old_tail)
        assert discarded["discarded_on_restore"] and "payload" not in discarded
        assert actor.scorer.references[old_key] == 1
        actor.scorer.release_mu(old_key)
        assert all(generation == "g-second" for generation, _lease in actor.leases)
        assert exchange.hot_status("g-first") == {"payloads": 0, "bytes": 0, "references": 0}
        # Other episodes provide fresh training data after the same live actor
        # reconnects. Old input receipts remain queryable, without new uses.
        old_receipts = list(exchange.input_records(0, training_generation="g-first"))
        assert old_receipts
        assert exchange.read_input(old_receipts[0])["response"]
        deliver(replacement, actor="c", connection=client)
        deliver(replacement, actor="d", connection=client)
        assert replacement._prepare()
        while replacement.active is not None:
            replacement._step()
        assert client.injected
        assert replacement.updates == 370
        assert replacement.first_use_multi > cp.counters.first_use_multi
        assert json.loads(rendezvous.read_bytes())["training_generation"] == "g-second"
    finally:
        client.close()
        actor.close()
        if replacement is not None:
            replacement.close()
        elif not owner_closed:
            owner.close()


def test_explicit_central_learner_publication_actor_and_tail(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, segments=dataclasses.replace(
        config.segments, central_scoring=True))
    owner = SegmentLearner(config, tmp_path / "central", io)
    monkeypatch.setenv(AUTH_ENV, owner.pool.environment()[AUTH_ENV])
    assert isinstance(owner.pool, CentralInferencePool)
    actor = ActorInferencePool(SegmentConnection(owner.pool.attachment()),
                               RemoteMuScorer(owner.pool.attachment()))
    try:
        with SegmentConnection(actor.attachment()) as client:
            deliver(owner, actor="a", connection=client)
            deliver(owner, actor="b", connection=client)
            assert owner._prepare()
            while owner.active is not None:
                owner._step()
            before = torch.get_rng_state().clone()
            owner._collect_publication(wait=True)
            assert torch.equal(before, torch.get_rng_state())
            assert client.request("episode_open", actor="tail", episode="tail")["admitted"]
            grant = client.request("begin", actor="tail", episode="tail", previous_lease=None)
            from test_p1_input_v2 import public_state

            from stpd.p1.frame_compiler import FrameCompiler

            # Explicit central helper also supplies forced/tail values under the
            # granted immutable mu, independently of the learner's new theta.
            wire = FrameCompiler(owner.model.vocabulary).encode(public_state())
            score = actor.scorer.score_compiled(
                grant["mu"]["snapshot_sha256"], wire, 3, policy=False)
            assert score.scores is None and len(score.outcome_probabilities) == 51
            assert score.snapshot_sha256 == grant["mu"]["snapshot_sha256"]
            connected = actor.scorer.remote.connection
            assert connected is not None
            assert actor.scorer.score_compiled(
                grant["mu"]["snapshot_sha256"], wire, 3, policy=True).scores is not None
            assert actor.scorer.remote.connection is connected
    finally:
        actor.close()
        owner.close()
    assert connected.closed


@pytest.mark.parametrize("training_group_size", [2, 512])
def test_application_CP16_conversion_uses_original_codec_without_optimizer_or_world(
    tmp_path, monkeypatch, model, training_group_size
):
    import spireagent.source
    from spireagent.workbench.rl_learner import convert_legacy_checkpoint
    from stpd.rl.checkpoint_state import decode_state
    from stpd.rl.learner import LearnerConfig
    from stpd.workers.checkpoint_codec import encode_checkpoint

    config, _io = configuration(tmp_path, monkeypatch)
    config = dataclasses.replace(config, performance=dataclasses.replace(config.performance,
        microbatch_size=training_group_size, scene_attention_area=2**32,
        text_attention_area=2**36))
    state, _names = legacy_cp16(model)
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.rl.checkpoint import metadata_tensor

    metadata = json.loads(state["metadata"].numpy().tobytes())
    metadata["config"].update(
        start_model_sha256=json.loads((tmp_path / "package/model.json").read_bytes())[
            "package_sha256"],
        segments={"input_compiler_identity": FrameCompiler(model.vocabulary).identity})
    state["metadata"] = metadata_tensor(metadata)
    exchange = Exchange(config.peer, config.run)
    original = Manifest("checkpoint", PRODUCER,
        payloads=(exchange.store.put_bytes("checkpoint", encode_checkpoint(state)),),
        parameters=FrozenObject.of({"schema": "stpd/rl-segment-checkpoint-1"}))
    exchange.store.publish(original)
    monkeypatch.setattr(spireagent.source, "source_identity", lambda _root: PRODUCER)

    def forbidden_step(*_args, **_kwargs):
        raise AssertionError("conversion must not call an optimizer")

    monkeypatch.setattr(torch.optim.AdamW, "step", forbidden_step)
    result = convert_legacy_checkpoint(dataclasses.replace(config,
        resume_checkpoint=original.artifact_id), tmp_path / "package", tmp_path / "conversion",
        source_generation="g-original", conversion_generation="g-converted")
    converted = exchange.store.get_manifest(result["converted_checkpoint"])
    assert not converted.parents
    payload = converted.payload("checkpoint")
    cp = decode_state(exchange.store.bytes(payload, maximum=payload.size))
    assert cp.config["performance"]["microbatch_size"] == training_group_size
    execution = cp.construction.numerical["execution"]
    assert execution["microbatch_size"] == min(64, config.segments.inference_batch)
    assert execution["scene_attention_area"] <= 2**28
    assert execution["text_attention_area"] <= 2**28
    assert_tree(cp.weights, state["weights"])
    assert_tree(cp.optimizer, state["optimizer"])
    assert_tree(cp.ema, state["ema"])
    assert result["optimizer_calls"] == result["World_calls"] == 0
    continuation = LearnerConfig.read(tmp_path / "conversion/converted-learner.json")
    assert continuation.resume_checkpoint == converted.artifact_id
    assert continuation.start_package is None

    # A valid same-topology model package is still the wrong construction if
    # it is not the exact original start package named by CP16 metadata.
    from stpd.p1.export import export_model

    wrong_package = tmp_path / "wrong-package"
    export_model(model, tmp_path / "fixture-source", wrong_package, {"wrong": True})
    with pytest.raises(ValueError, match="original_construction_package_changed"):
        convert_legacy_checkpoint(dataclasses.replace(config,
            resume_checkpoint=original.artifact_id), wrong_package, tmp_path / "wrong-conversion",
            source_generation="g-original", conversion_generation="g-converted")
    metadata["config"]["segments"]["input_compiler_identity"]["vocabulary_sha256"] = "0" * 64
    changed = {**state, "metadata": metadata_tensor(metadata)}
    wrong_identity = Manifest("checkpoint", PRODUCER,
        payloads=(exchange.store.put_bytes("checkpoint", encode_checkpoint(changed)),),
        parameters=FrozenObject.of({"schema": "stpd/rl-segment-checkpoint-1"}))
    exchange.store.publish(wrong_identity)
    with pytest.raises(ValueError, match="original_vocabulary_or_input_spec_changed"):
        convert_legacy_checkpoint(dataclasses.replace(config,
            resume_checkpoint=wrong_identity.artifact_id), tmp_path / "package",
            tmp_path / "wrong-vocabulary", source_generation="g-original",
            conversion_generation="g-converted")


def test_benchmark_uses_process_preparation_stage_and_complete_update(tmp_path, model):
    from test_p1_input_v2 import public_state

    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.rl.batch_preparation import stage_batch
    from stpd.rl.loss import LossConfig, prepared_update
    from stpd.rl.performance import PerformanceConfig
    from tools.benchmark_rl import prepare_segment_diagnostic

    observation = public_state()
    frame = FrameCompiler(model.vocabulary).compile(observation)
    records = [{"observation": observation, "reference": {"chosen": frame.choices[0]},
                "floor": 3, "outcome_class": 3, "hp": .57} for _ in range(8)]
    with torch.no_grad():
        scores, outcomes, _ = model.forward_rl4([frame] * 8, [3] * 8)
    performance = PerformanceConfig(microbatch_size=4)
    batch = prepare_segment_diagnostic(model, records,
        [value.softmax(-1).tolist() for value in scores], outcomes.softmax(-1).tolist(),
        tmp_path / "benchmark", performance)
    batch, copies = stage_batch(batch, torch.device("cpu"))
    assert len(batch.request.members) == 1 and len(batch.minibatches) == 4 and copies == 0
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    assert all(prepared_update(model, optimizer, minibatch,
        config=LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01),
        performance=performance)["stepped"] for minibatch in batch.minibatches)
