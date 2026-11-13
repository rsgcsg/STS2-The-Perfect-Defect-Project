"""W1 semantic counterexamples and real CPU MStd/Adam/CP continuation.

Synthetic public transitions exercise engineering contracts, not native gameplay,
Human origin or model quality. No tolerance/same-members speedup equivalence gate.
"""

from __future__ import annotations

import copy
import dataclasses
import random
from functools import lru_cache
from pathlib import Path

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_p1_input_v2 import public_state
from test_rl_loop import PRODUCER

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.rl_contracts import HotPayloadRef, RunSequence
from spireagent.rl_exchange import Exchange
from spireagent.storage.store import copy_artifact
from spireagent.workbench.global_evidence_store import publish_detached_p1_model
from stpd.p1.data import digest, json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import adapter_identity, export_model, input_spec, runtime_projection
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.model import MStd
from stpd.p1.sampling import distribution
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.batch_preparation import assemble, prepare_source
from stpd.rl.checkpoint import metadata_tensor
from stpd.rl.checkpoint_state import (
    SCHEMA,
    CP16ConversionInput,
    LearningCounters,
    ModelConstruction,
    convert_cp16,
    decode_state,
    encode_state,
    text_digest,
)
from stpd.rl.learner import LearnerConfig
from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig
from stpd.rl.prepared_batch import BatchMember, PrepareRequest
from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO
from stpd.rl.segment_targets import recorded_targets
from stpd.rl.segments import SegmentConfig


@pytest.fixture
def model():
    torch.set_num_threads(1)
    torch.manual_seed(37)
    value = MStd(
        public_vocabulary(),
        MStdExecution(
            implementation="experimental-packed-v1",
            microbatch_size=4,
            scene_attention_area=67108864,
            text_attention_area=67108864,
            cpu_threads=1,
        ),
    )
    value.enable_rl4_heads(7)
    return value.eval()


@lru_cache(maxsize=128)
def training_seed(name):
    from stpd.evaluation.suites import derive_game_seed, seed_membership
    from stpd.rl.critic import held_out

    return next(
        seed
        for i in range(1, 100)
        if not held_out(seed := derive_game_seed("rl41-w1-" + name, i))
        and not seed_membership(seed)
    )


def segment(
    model,
    *,
    name="actor/0",
    run_id="episode",
    rows=4,
    generation="g-one",
    mu=None,
    terminal=False,
    forced=True,
    start=0,
):
    compiler = FrameCompiler(model.vocabulary)
    spec = input_spec(model.vocabulary, runtime_projection(model.vocabulary))
    mu = mu or {
        "run": "w1-unit",
        "training_generation": generation,
        "snapshot_sha256": "a" * 64,
        "version": 57,
        "updates": 362,
    }
    inputs = []
    for index in range(rows + 1):
        observation = public_state()
        if forced and index % 3 == 1:
            observation["catalog"] = observation["catalog"][:1]
        observation["run"] = {"content": {"floor": 3}}
        observation["combat"] = {"availability": "available"}
        if terminal and index == rows:
            observation["status"] = "terminal"
            observation["scene"] = {"native_kind": "game_over"}
            observation["player"] = {"content": {"hp": 0}}
        inputs.append(
            {
                "acquisition_id": f"{run_id}-{start + index}",
                "consumption_unit": {"id": f"{run_id}-u-{start + index}"},
                "input_spec": spec,
                "observation": observation,
                "catalog": observation["catalog"],
            }
        )
    frames = [compiler.compile(value["observation"]) for value in inputs]
    with torch.no_grad():
        scores, logits, _ = model.forward_rl4(frames, [3] * len(inputs))
    steps = []
    for index, value in enumerate(inputs[:-1]):
        score = scores[index].tolist()
        raw, probabilities = distribution(score, 1.0, 0.0)
        sampling = {
            "mode": "temperature_uniform",
            "temperature": 1.0,
            "uniform_mix": 0.0,
            "probabilities": probabilities,
            "model_probabilities": raw,
            "model_logits": score,
            "selected_probability": probabilities[0],
        }
        behavior = {
            "mu": copy.deepcopy(mu),
            "lease": name,
            "scores": score,
            "floor": 3,
            "outcome_probabilities": logits[index].softmax(-1).tolist(),
            "compiled_input": compiler.encode(value["observation"]),
            "acquisition_id": value["acquisition_id"],
            "input_sha256": digest(json_bytes(value)),
            "observation_sha256": digest(json_bytes(value["observation"])),
            "catalog_sha256": digest(json_bytes(value["catalog"])),
            "consumption_unit": value["consumption_unit"]["id"],
        }
        steps.append(
            {
                "schema": "sts2.policy-runtime/verified-global-transition-1",
                "run_id": run_id,
                "step_index": start + index,
                "input": value,
                "successor": inputs[index + 1],
                "result": {"status": "completed"},
                "directive": {
                    "directive": {
                        "selection": {"action_id": value["catalog"][0]["choice_id"]},
                        "scores": {"values": score},
                    }
                },
                "sampling": sampling,
                "behavior": behavior,
            }
        )
    from stpd.rl.heads import class_reward_values

    label = {"class": 3, "reward": float(class_reward_values()[3]), "typed_original": "c" * 64}
    return {
        "training_split": "TRAIN",
        "run": mu["run"],
        "training_generation": generation,
        "identity": {"intent_id": run_id, "seed": training_seed(run_id)},
        "run_id": run_id,
        "rows": rows,
        "segment_id": name,
        "mu": copy.deepcopy(mu),
        "steps": steps,
        "terminal": label if terminal else None,
        "bootstrap": None
        if terminal
        else {
            "mu_snapshot_sha256": mu["snapshot_sha256"],
            "successor_input_sha256": digest(json_bytes(inputs[-1])),
            "floor": 3,
            "outcome_probabilities": logits[-1].softmax(-1).tolist(),
        },
        "compiler_identity": compiler.identity,
        "compiled_boundary": None if terminal else compiler.encode(inputs[-1]["observation"]),
        "hp_battles": [],
        "previous_segment_artifact_id": None,
        "original_commit": {
            "run_id": run_id,
            "segment_id": name,
            "representation": "live-prefix",
            "journal_prefix_sha256": "d" * 64,
            "behavior_trace_prefix_sha256": "e" * 64,
        },
    }


def source(model, value):
    compiler = FrameCompiler(model.vocabulary)
    return prepare_source(
        value, compiler, input_spec(model.vocabulary, runtime_projection(model.vocabulary))
    )


def request(values, *, auxiliary=(), owners=(), parts=None, number=1):
    count = sum(value["rows"] for value in values)
    if parts is None:
        parts = (tuple(range(count)),)
    return PrepareRequest(
        RunSequence(values[0]["run"], values[0]["training_generation"], number),
        tuple(
            BatchMember(
                v["segment_id"],
                HotPayloadRef(f"{v['run']}/{v['training_generation']}/{i}", "f" * 64, 1),
                v["run_id"],
                v["rows"],
                v["mu"]["snapshot_sha256"],
                v["identity"]["intent_id"],
            )
            for i, v in enumerate(values)
        ),
        tuple(auxiliary),
        tuple(parts),
        tuple(owners),
        values[0]["compiler_identity"],
        random.Random(1).getstate(),
        128 * 1024**2,
    )


def ready(model, values, **kwargs):
    return assemble(
        request(values, **kwargs),
        tuple(source(model, value) for value in values),
        microbatch_size=2,
        deduplicate_text=True,
    )


@pytest.mark.parametrize("where", ["forced", "tail", "missing_forced", "missing_bootstrap"])
def test_same_mu_forced_and_confirmed_bootstrap_cannot_be_substituted(model, where):
    value = segment(model)
    if where == "forced":
        value["steps"][1]["behavior"]["mu"]["snapshot_sha256"] = "b" * 64
    elif where == "tail":
        value["bootstrap"]["mu_snapshot_sha256"] = "b" * 64
    elif where == "missing_forced":
        value["steps"][1]["behavior"]["outcome_probabilities"] = None
    else:
        value["bootstrap"] = None
    with pytest.raises(ValueError, match="mu|forced|bootstrap"):
        recorded_targets(value)


def test_nonterminal_bootstraps_and_real_terminal_uses_class(model):
    value = segment(model, rows=2)
    tail = [0.0] * 51
    tail[49] = 1.0
    value["bootstrap"]["outcome_probabilities"] = tail
    _, target, _ = recorded_targets(value)
    assert torch.equal(target[-1], torch.tensor(tail))
    assert target[0, 49] > 0.99  # lambda=.99, and real next-state mu mass remains
    ended = segment(model, rows=2, terminal=True)
    _, target, _ = recorded_targets(ended)
    assert target[-1, 3] == 1 and target[-1].sum() == 1
    ended["bootstrap"] = value["bootstrap"]
    with pytest.raises(ValueError, match="terminal_has_no_bootstrap"):
        recorded_targets(ended)


def test_normalization_is_across_dataset_multi_rows_not_each_segment(model):
    first = segment(model, rows=2, name="a/0", run_id="a", forced=False)
    second = segment(model, rows=2, name="b/0", run_id="b", forced=False)
    for value, label in ((first, 4), (second, 49)):
        tail = [0.0] * 51
        tail[label] = 1.0
        value["bootstrap"]["outcome_probabilities"] = tail
    batch = ready(model, [first, second])
    assert batch.normalization.multi_rows == 4
    assert float(batch.advantages[:2].mean()) < -0.9
    assert float(batch.advantages[2:].mean()) > 0.9
    assert abs(float(batch.advantages.mean())) < 1e-5


def test_cpu_preparation_contains_complete_metadata_and_no_learned_tensors(model):
    value = segment(model)
    batch = ready(model, [value])
    assert len(batch.frames) == 4 and batch.td_mask.all()
    assert batch.policy_mask.tolist() == [True, False, True, True]
    assert batch.advantages[1] == 0
    assert batch.payload_bytes > 0
    for group in batch.minibatches[0].groups:
        assert {
            "scene_gather",
            "scene_mask",
            "text_inverse",
            "outcome_valid",
        } <= group.tensors.keys()
        assert all(
            t.device.type == "cpu" and t.is_contiguous() and not t.requires_grad
            for t in group.tensors.values()
        )
    assert batch.behavior_outcomes.shape == batch.outcome_targets.shape == (4, 51)


def test_full_catalog_policy_gradients_and_text_train_on_cpu(model):
    # A fitted head has nonzero last-layer weights; the initializer deliberately
    # has zero trunk gradient on its very first TD pass.
    with torch.no_grad():
        model.rl4_heads.outcome.mlp[-1].weight.normal_(0, 0.01)
    value = segment(model, forced=False)
    value["bootstrap"]["outcome_probabilities"] = [0.0] * 49 + [1.0, 0.0]
    batch = ready(model, [value])
    before = {name: tensor.clone() for name, tensor in model.state_dict().items()}
    result = prepared_update(
        model,
        torch.optim.AdamW(model.parameters(), lr=4e-5, weight_decay=0),
        batch.minibatches[0],
        config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
        performance=PerformanceConfig(deduplicate_text=True),
        diagnostics=True,
    )
    assert result["stepped"] and result["multi_rows"] == 4
    assert any(
        not torch.equal(before[name], tensor)
        for name, tensor in model.state_dict().items()
        if name.startswith("text_blocks.")
    )
    assert result["trunk_gradients"]["weighted_head_norms"]["td"] > 0
    assert all(parameter.requires_grad for parameter in model.parameters())


def test_KL_brake_does_not_update_theta_or_adam(model):
    value = segment(model, forced=False)
    for step in value["steps"]:
        probabilities = step["sampling"]["probabilities"]
        probabilities[0], probabilities[1] = 0.00001, 1 - 0.00001
    batch = ready(model, [value])
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    before = copy.deepcopy(model.state_dict())
    result = prepared_update(
        model,
        optimizer,
        batch.minibatches[0],
        config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
        performance=PerformanceConfig(),
        target_kl=0.03,
    )
    assert result["kl_stopped"] and not result["stepped"]
    assert not optimizer.state
    assert all(torch.equal(tensor, before[name]) for name, tensor in model.state_dict().items())
    assert all(parameter.grad is None for parameter in model.parameters())


def construction(model):
    return ModelConstruction(
        model.architecture,
        model.vocabulary,
        input_spec(model.vocabulary, runtime_projection(model.vocabulary)),
        FrameCompiler(model.vocabulary).identity,
        adapter_identity(),
        {
            "execution": dataclasses.asdict(model.execution),
            "model_size": model.model_size,
            "precision": "fp32",
        },
        text_digest(model.state_dict()),
    )


def parameter_names(model, optimizer):
    names = {id(parameter): name for name, parameter in model.named_parameters()}
    return tuple(
        tuple(names[id(parameter)] for parameter in group["params"])
        for group in optimizer.param_groups
    )


def legacy_cp16(model):
    optimizer = torch.optim.AdamW(
        [
            {
                "params": [
                    p for name, p in model.named_parameters() if not name.startswith("rl4_heads.")
                ],
                "lr": 4e-5,
            },
            {"params": list(model.rl4_heads.parameters()), "lr": 1e-4},
        ],
        weight_decay=0,
    )
    batch = ready(model, [segment(model, forced=False)])
    assert prepared_update(
        model,
        optimizer,
        batch.minibatches[0],
        config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
        performance=PerformanceConfig(),
    )["stepped"]
    metadata = {
        "schema": "stpd/rl-segment-checkpoint-1",
        "checkpoint_sequence": 16,
        "updates": 362,
        "datasets": 20,
        "first_use": 48951,
        "first_use_multi": 42787,
        "version": 57,
        "published_updates": 362,
        "config": {"run": "w1-unit", "segments": {"epochs": 2}},
        "queue": {"pending": ["discarded"]},
        "active": {"minibatch": 1},
    }
    state = {
        "schema": metadata["schema"],
        "metadata": metadata_tensor(metadata),
        "weights": copy.deepcopy(model.state_dict()),
        "optimizer": copy.deepcopy(optimizer.state_dict()),
        "ema": {name: tensor.detach().clone() * 0.9 for name, tensor in model.state_dict().items()},
        "random": random.Random(98).getstate(),
        "torch_rng": torch.get_rng_state(),
        "cuda_rng": [torch.tensor([17, 23, 41], dtype=torch.uint8)],
    }
    return state, parameter_names(model, optimizer)


def assert_tree(left, right):
    if isinstance(left, torch.Tensor):
        assert torch.equal(left.cpu(), right.cpu())
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_tree(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for one, two in zip(left, right, strict=True):
            assert_tree(one, two)
    else:
        assert left == right


def test_explicit_cp16_conversion_is_zero_SGD_and_preserves_each_state(model, monkeypatch):
    state, names = legacy_cp16(model)
    before = copy.deepcopy(state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("CP16 conversion called SGD")

    monkeypatch.setattr(torch.optim.AdamW, "step", forbidden)
    source_input = CP16ConversionInput(
        state,
        construction(model),
        names,
        "1" * 64,
        "g-old",
        "g-new",
        {"run": "w1-unit", "segments": {"epochs": 1}},
        PRODUCER.to_dict(),
    )
    cp = convert_cp16(source_input)
    assert cp.identity == RunSequence("w1-unit", "g-new", 16)
    assert cp.counters == LearningCounters(362, 20, 48951, 42787, 57, 362)
    for field in ("weights", "optimizer", "ema"):
        assert_tree(state[field], getattr(cp, field))
    assert_tree(state, before)
    assert_tree(cp.rng.learner_random, state["random"])
    assert_tree(cp.rng.torch_rng, state["torch_rng"])
    assert_tree(cp.rng.cuda_rng, state["cuda_rng"])
    restored = decode_state(encode_state(cp))
    assert restored.conversion.source_sequence == 16
    assert_tree(restored.optimizer, state["optimizer"])
    assert not {"queue", "active", "leases", "input_head"} & dataclasses.asdict(restored).keys()


# A source-checkout-shaped producer: evaluation export refuses scratch producers.
SOURCE_PRODUCER = Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "0123456789abcdef" * 2
                           + "01234567", "c" * 64)


def configuration(tmp_path, monkeypatch, *, generation="g-source", producer=None):
    import stpd.rl.segment_learner as module

    monkeypatch.setattr(module, "source_identity", lambda _root: producer or PRODUCER)
    torch.set_num_threads(1)
    start = MStd(public_vocabulary()).eval()
    checkpoint = tmp_path / "fixture-source"
    checkpoint.write_bytes(b"engineering fixture")
    package = tmp_path / "package"
    export_model(start, checkpoint, package, {})
    config = LearnerConfig(
        run="w1-unit",
        peer={"kind": "local", "directory": str(tmp_path / "peer")},
        start_package=str(package),
        threads=1,
        device="cpu",
        tensorboard_steps=0,
        wall_seconds=10,
        loss=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
        performance=PerformanceConfig(
            packed=True,
            tensor_cache=True,
            microbatch_size=2,
            deduplicate_text=True,
            scene_attention_area=67108864,
            text_attention_area=67108864,
        ),
        segments=SegmentConfig(
            steps=2,
            preparation_workers=1,
            dataset_rows=4,
            episode_rows=2,
            capacity_rows=16,
            training_generation=generation,
            # The production free-space gate (20 GiB) pauses new leases; on a full
            # developer disk `begin` would then wait forever. Tests that exercise the
            # gate set their own thresholds.
            archive_min_free_bytes=0,
            archive_min_free_inodes=0,
        ),
    )
    return config, SegmentLearnerIO(None, publish_detached_p1_model)


def deliver(
    owner, *, actor="actor", terminal=False, bad_kl=False, connection=None, end_transform=None
):
    def control(request):
        if connection is None:
            return owner.control(request)
        return connection.request(
            request["operation"],
            **{key: value for key, value in request.items() if key != "operation"},
        )

    base = {
        "run": owner.config.run,
        "training_generation": owner.generation,
        "actor": actor,
        "episode": "episode-" + actor,
    }
    assert control({**base, "operation": "episode_open"})["admitted"]
    grant = control({**base, "operation": "begin", "previous_lease": None})
    from stpd.p1.local_mu import LocalMuScorer

    scorer = LocalMuScorer(owner.exchange, owner.work / ("actor-" + actor))
    scorer.adopt(grant["mu"])
    frozen = scorer.models[grant["mu"]["snapshot_sha256"]]
    value = segment(
        frozen,
        name=grant["lease"],
        run_id=base["episode"],
        rows=2,
        generation=owner.generation,
        mu=grant["mu"],
        terminal=terminal,
    )
    scorer.close()
    if bad_kl:
        step = value["steps"][0]
        score = [-12.0] + [0.0] * (len(step["behavior"]["scores"]) - 1)
        raw, probabilities = distribution(score, 1.0, 0.0)
        step["behavior"]["scores"] = score
        step["directive"]["directive"]["scores"]["values"] = score
        step["sampling"].update(
            probabilities=probabilities,
            model_probabilities=raw,
            model_logits=score,
            selected_probability=probabilities[0],
        )
    payload = owner.exchange.put_hot_segment(value, owner="episode:" + base["episode"])
    commit = {
        "operation": "commit",
        "run": owner.config.run,
        "training_generation": owner.generation,
        "segment_id": value["segment_id"],
        "run_id": value["run_id"],
        "rows": value["rows"],
        "mu": grant["mu"],
        "payload": dataclasses.asdict(payload),
    }
    assert control(commit)["segment_id"] == grant["lease"]
    refs = [
        {
            "source": {"run_id": value["run_id"], "segment_id": value["segment_id"],
                       "training_generation": owner.generation, "row": i,
                       "key": f"{value['run_id']}:{i}"},
            "outcome": 3,
        }
        for i in range(2)
    ]
    ended = {
        **base,
        "operation": "episode_end",
        "run_id": value["run_id"],
        "terminal": value["terminal"],
        "outcome_states": refs if terminal else [],
        "typed_original": "c" * 64,
        "hp_battles": [],
        "segments": [{"payload": dataclasses.asdict(payload)}],
    }
    if end_transform is not None:
        end_transform(ended)
    assert control(ended)["run_id"] == value["run_id"]
    owner.exchange.release_hot_payload(payload, "episode:" + base["episode"])
    return payload


def test_normal_CP_only_restore_then_successful_update_and_second_CP(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    restored = None
    try:
        deliver(owner, actor="a")
        deliver(owner, actor="b")
        assert owner._prepare()
        while owner.active is not None:
            owner._step()
        assert owner.updates == 4 and owner.first_use == 4 and owner.first_use_multi == 2
        owner._collect_publication(wait=True)
        owner.checkpoint(final=True)
        saved = owner.exchange.latest_checkpoint(training_generation=owner.generation)
        checkpoint_id = saved["checkpoint_artifact_id"]
        manifest = owner.exchange.store.get_manifest(checkpoint_id)
        assert manifest.parents == ()
        peer = {"kind": "local", "directory": str(tmp_path / "empty-peer")}
        cold = Exchange(peer, config.run)
        assert not any(path.is_file() for path in Path(peer["directory"]).rglob("*"))
        copy_artifact(owner.exchange.store, cold.store, checkpoint_id, scratch=tmp_path)
        saved_payload = cold.store.get_manifest(checkpoint_id).payload("checkpoint")
        saved_state = decode_state(cold.store.bytes(saved_payload, maximum=saved_payload.size))
        replacement = dataclasses.replace(
            config,
            peer=peer,
            start_package=None,
            start_artifact_id=None,
            start_model_sha256=None,
            resume_checkpoint=checkpoint_id,
            segments=dataclasses.replace(config.segments, training_generation="g-replaced"),
        )
        restored = SegmentLearner(replacement, tmp_path / "empty-work", io)
        assert (
            not restored.queue.pending and not restored.lease_owners and not restored.actor_episodes
        )
        assert restored.updates == 4 and restored.first_use == 4
        assert_tree(restored.model.state_dict(), saved_state.weights)
        assert_tree(restored.optimizer.state_dict(), saved_state.optimizer)
        assert_tree(restored.ema, saved_state.ema)
        deliver(restored, actor="c")
        deliver(restored, actor="d")
        assert restored._prepare()
        while restored.active is not None:
            restored._step()
        assert restored.updates > 4 and restored.first_use > 4
        restored._collect_publication(wait=True)
        restored.checkpoint(final=True)
        second = restored.exchange.latest_checkpoint(training_generation=restored.generation)
        assert second["checkpoint_sequence"] == saved["checkpoint_sequence"] + 1
        second_payload = restored.exchange.store.get_manifest(
            second["checkpoint_artifact_id"]
        ).payload("checkpoint")
        cp2 = decode_state(
            restored.exchange.store.bytes(second_payload, maximum=second_payload.size)
        )
        assert (
            cp2.identity.training_generation == "g-replaced"
            and cp2.counters.updates == restored.updates
        )
    finally:
        owner.close()
        if restored is not None:
            restored.close()


def test_KL_stopped_learner_never_charges_auxiliary_uses(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        deliver(owner, actor="a", terminal=True, bad_kl=True)
        deliver(owner, actor="b", terminal=True, bad_kl=True)
        assert owner._prepare()
        before = {
            row["key"]: row["uses"] for rows in owner.terminal.groups.values() for row in rows
        }
        # Four minibatches each have one PPO row: find the real multi-choice row,
        # whose forged old policy creates a guaranteed KL brake before its Adam.
        index = next(i for i, mb in enumerate(owner.active_batch.minibatches) if mb.policy_rows)
        owner.active["minibatch"] = index
        result = owner._step()
        assert (
            result["kl_stopped"] and owner.updates == owner.first_use == owner.first_use_multi == 0
        )
        assert {
            row["key"]: row["uses"] for rows in owner.terminal.groups.values() for row in rows
        } == before
    finally:
        owner.close()


def test_CP16_conversion_then_normal_learner_continues_mu58_CP17(tmp_path, monkeypatch, model):
    state, names = legacy_cp16(model)
    config, io = configuration(tmp_path, monkeypatch, generation="g-after-conversion")
    cp = convert_cp16(
        CP16ConversionInput(
            state,
            construction(model),
            names,
            "1" * 64,
            "g-legacy",
            "g-converted",
            dataclasses.asdict(config),
            PRODUCER.to_dict(),
        )
    )
    exchange = Exchange(config.peer, config.run)
    payload = exchange.store.put_bytes(
        "checkpoint", encode_state(cp), "application/vnd.stpd.tensor-tree"
    )
    manifest = Manifest(
        "checkpoint", PRODUCER, payloads=(payload,), parameters=FrozenObject.of({"schema": SCHEMA})
    )
    exchange.store.publish(manifest)
    actual_step = torch.optim.AdamW.step
    steps = []

    def counted_step(*args, **kwargs):
        steps.append(True)
        return actual_step(*args, **kwargs)

    monkeypatch.setattr(torch.optim.AdamW, "step", counted_step)
    owner = SegmentLearner(
        dataclasses.replace(config, start_package=None, resume_checkpoint=manifest.artifact_id),
        tmp_path / "converted-only",
        io,
    )
    try:
        assert steps == [] and owner.updates == 362 and owner.version == 58
        assert owner.first_use == 48951 and owner.first_use_multi == 42787
        assert owner.checkpoint_sequence == 16
        assert_tree(owner.model.state_dict(), cp.weights)
        assert_tree(owner.optimizer.state_dict(), cp.optimizer)
        assert_tree(owner.ema, cp.ema)
        assert_tree(torch.get_rng_state(), cp.rng.torch_rng)
        assert_tree(owner.rng.getstate(), cp.rng.learner_random)
        assert_tree(owner.cuda_rng, cp.rng.cuda_rng)
        deliver(owner, actor="new-a")
        deliver(owner, actor="new-b")
        assert owner._prepare()
        while owner.active is not None:
            owner._step()
        assert len(steps) == 4 and owner.updates == 366
        owner.checkpoint(final=True)
        record = owner.exchange.latest_checkpoint(training_generation=owner.generation)
        assert record["checkpoint_sequence"] == 17
        saved = owner.exchange.store.get_manifest(record["checkpoint_artifact_id"])
        restored = decode_state(
            owner.exchange.store.bytes(
                saved.payload("checkpoint"), maximum=saved.payload("checkpoint").size
            )
        )
        assert restored.identity.sequence == 17 and restored.counters.updates == 366
        assert_tree(restored.rng.cuda_rng, state["cuda_rng"])
        assert restored.conversion.source_checkpoint_artifact_id == "1" * 64
    finally:
        owner.close()


def test_snapshot_has_owned_storage_and_publication_stays_off_owner(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        snapshot = owner._capture_model()
        name = next(iter(snapshot["weights"]))
        before = snapshot["weights"][name].clone()
        with torch.no_grad():
            owner.model.state_dict()[name].add_(1)
        assert torch.equal(snapshot["weights"][name], before)
        # A deliberately pending IO future must not block another dataset's
        # short snapshot. Only the newest unsent immutable snapshot survives.
        from concurrent.futures import Future

        pending = Future()
        owner.pending_publication = pending
        owner._schedule_publication()
        first = owner.latest_snapshot
        owner.updates += 1
        owner._schedule_publication()
        assert owner.pending_publication is pending
        assert owner.latest_snapshot is not first and owner.latest_snapshot["updates"] == 1
        # Test future did not publish any Store asset and is not a live job.
        owner.pending_publication = None
        owner.latest_snapshot = None
        assert owner.version == 0
        # Owned CP weights/EMA are views into one slab per dtype. deepcopy of
        # every view clones the complete slab for every parameter (GiB per CP).
        checkpoint = owner._learning_state()

        def no_tensor_deepcopy(_tensor, _memo):
            raise AssertionError("checkpoint_encoding_must_not_clone_owned_tensor_storage")

        with monkeypatch.context() as boundary:
            boundary.setattr(torch.Tensor, "__deepcopy__", no_tensor_deepcopy)
            raw = encode_state(checkpoint)
        restored = decode_state(raw)
        for field in ("weights", "optimizer", "ema"):
            assert_tree(getattr(restored, field), getattr(checkpoint, field))
        assert_tree(restored.rng.torch_rng, checkpoint.rng.torch_rng)
        assert_tree(restored.rng.cuda_rng, checkpoint.rng.cuda_rng)
        assert restored.identity == checkpoint.identity
        assert restored.counters == checkpoint.counters
    finally:
        owner.close()


@pytest.mark.parametrize("kind", ["reserved", "permanent", "excluded", "not_TRAIN", "missing"])
def test_ready_rejects_every_heldout_or_nontraining_source(model, kind):
    from stpd.evaluation.suites import derive_game_seed, load_suite
    from stpd.rl.batch_preparation import validate_training_source
    from stpd.rl.critic import held_out

    value = segment(model)
    excluded = frozenset()
    if kind == "reserved":
        value["identity"]["seed"] = load_suite("test-1000")["seeds"][0]
    elif kind == "permanent":
        value["identity"]["seed"] = next(
            seed
            for i in range(1, 100)
            if held_out(seed := derive_game_seed("rl41-heldout-counterexample", i))
        )
    elif kind == "excluded":
        excluded = frozenset([value["identity"]["seed"]])
    elif kind == "not_TRAIN":
        value["training_split"] = "TEST"
    else:
        value["identity"].pop("seed")
    with pytest.raises(ValueError, match="held_out_or_not_TRAIN"):
        validate_training_source(value, excluded)


def test_complete_batch_stages_each_dtype_once_and_keeps_CPU_owner(model, monkeypatch):
    from stpd.rl.batch_preparation import stage_batch

    batch = ready(model, [segment(model)])
    actual = torch.Tensor.to
    copies = []

    def observed(self, *args, **kwargs):
        if args and isinstance(args[0], torch.device) and args[0].type == "meta":
            copies.append(self.dtype)
        return actual(self, *args, **kwargs)

    monkeypatch.setattr(torch.Tensor, "to", observed)
    staged, count = stage_batch(batch, torch.device("meta"))
    assert count == len(copies) == 3 and len(set(copies)) == 3
    assert staged.minibatches[0].tensors["targets"].device.type == "meta"
    assert batch.minibatches[0].tensors["targets"].device.type == "cpu"
    assert staged.minibatches[0].td_rows == batch.minibatches[0].td_rows == 4
    for group in staged.minibatches[0].groups:
        assert all(t.device.type == "meta" for t in group.tensors.values())
        assert all(t.device.type == "meta" for t in group.joint.tensors.values())


def test_prepared_compile_candidate_is_retired_after_G0(model):
    batch = ready(model, [segment(model)])
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    with pytest.raises(ValueError, match="compile_retired_after_G0"):
        prepared_update(model, optimizer, batch.minibatches[0],
            config=LossConfig(mode="ppo", value_coef=.2, entropy_coef=.01),
            performance=PerformanceConfig(compile_loss=True))
    assert not optimizer.state


def test_bf16_on_CPU_is_explicitly_rejected_without_a_precision_fallback(model):
    batch = ready(model, [segment(model)])
    optimizer = torch.optim.AdamW(model.parameters(), lr=4e-5)
    with pytest.raises(ValueError, match="bf16_requires_supported_cuda"):
        prepared_update(
            model,
            optimizer,
            batch.minibatches[0],
            config=LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01),
            performance=PerformanceConfig(precision="bf16"),
        )
    assert not optimizer.state


def test_late_auxiliary_snapshot_survives_source_retirement_and_rejects_wrong_binding(
    model, tmp_path
):
    from stpd.rl.batch_preparation import BatchPreparer, read_auxiliary_inputs
    from stpd.rl.prepared_batch import AuxiliarySelection

    current = segment(model, name="current/0", run_id="current-episode")
    late = segment(model, name="late/0", run_id="late-episode")
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, current["run"])
    current_ref = exchange.put_hot_segment(current, owner="test")
    late_ref = exchange.put_hot_segment(late, owner="test")
    req = request([current])
    req = dataclasses.replace(
        req, members=(dataclasses.replace(req.members[0], payload=current_ref),)
    )
    member = dataclasses.replace(request([late]).members[0], payload=late_ref)
    preparer = BatchPreparer(
        model.vocabulary, workers=1, threads=1, peer=peer, run=current["run"], scratch=tmp_path
    )
    try:
        inputs = preparer.pool.submit(read_auxiliary_inputs, member, (0,)).result()
        req = dataclasses.replace(
            req,
            auxiliary=(AuxiliarySelection(inputs[0].source, "hp", 0.57, 0, 1),),
            auxiliary_minibatches=(0,),
        )
        exchange.release_hot_payload(late_ref, "test")
        batch = preparer.submit(req, inputs).result()
        assert batch.row_refs[-1] == inputs[0].source
        assert batch.kinds[-1] == "hp" and batch.hp_targets[-1] == 0.57
        assert batch.frames[-1].choices == tuple(
            item["choice_id"] for item in late["steps"][0]["input"]["catalog"]
        )
        changed = dataclasses.replace(inputs[0], source={**inputs[0].source, "key": "wrong"})
        with pytest.raises(ValueError, match="auxiliary_snapshot_membership_changed"):
            preparer.submit(req, (changed,))
    finally:
        preparer.shutdown()
        exchange.release_hot_payload(current_ref, "test")


def test_configured_lambda_reaches_prepared_targets_from_recorded_mu(model):
    value = segment(model, rows=2)
    value["bootstrap"]["outcome_probabilities"] = [0.] * 49 + [1., 0.]
    compiler = FrameCompiler(model.vocabulary)
    spec = input_spec(model.vocabulary, runtime_projection(model.vocabulary))
    one_step = prepare_source(value, compiler, spec, gae_lambda=0)
    full_trace = prepare_source(value, compiler, spec, gae_lambda=1)
    assert torch.equal(one_step.targets[0], one_step.probabilities[1])
    assert full_trace.targets[:, 49].eq(1).all()
    assert not torch.equal(one_step.raw_advantages, full_trace.raw_advantages)


def test_verified_hot_preparation_preserves_tensors_and_rejects_changed_bytes(
    model, tmp_path, monkeypatch
):
    import stpd.rl.batch_preparation as preparation

    value = segment(model, rows=3)
    compiler = FrameCompiler(model.vocabulary)
    spec = input_spec(model.vocabulary, runtime_projection(model.vocabulary))
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, value["run"])
    payload = exchange.put_hot_segment(value, owner="test")
    member = dataclasses.replace(request([value]).members[0], payload=payload)
    monkeypatch.setattr(preparation, "_worker", dict(reader=None, exchange=exchange,
        compiler=compiler, expected_spec=spec, excluded_seeds=frozenset(), gae_lambda=.99))
    original = source(model, value)
    actual = preparation.unpack_transport(preparation._read_source(payload, member))
    for field in ("probabilities", "targets", "raw_advantages"):
        assert torch.equal(getattr(original, field), getattr(actual, field))
    for left, right in zip(original.frames, actual.frames, strict=True):
        assert_tree(left.state(), right.state())
    assert original.policy_probabilities == actual.policy_probabilities
    assert original.actions == actual.actions and original.row_refs == actual.row_refs
    assert actual.seconds["hot_read_wall_seconds"] >= 0
    assert actual.seconds["source_pack_seconds"] >= 0
    changed = copy.deepcopy(value)
    changed["steps"][0]["input"]["observation"]["run"]["content"]["floor"] = 4
    with pytest.raises(BoundaryError, match="compiled_public_input_binding_changed"):
        exchange.put_hot_segment(changed, owner="test")
    with pytest.raises(BoundaryError, match="public_input_hash_mismatch"):
        source(model, changed)


def test_verified_prepare_keeps_ordered_catalog_check(model):
    from stpd.rl.batch_preparation import _prepare_source

    value = segment(model, rows=2, forced=False)
    value["steps"][0]["input"]["catalog"] = list(
        reversed(value["steps"][0]["input"]["catalog"]))
    with pytest.raises(ValueError, match="prepared_catalog_membership_changed"):
        _prepare_source(value, FrameCompiler(model.vocabulary),
            input_spec(model.vocabulary, runtime_projection(model.vocabulary)),
            excluded_seeds=frozenset(), gae_lambda=.99, public_bindings_verified=True)
