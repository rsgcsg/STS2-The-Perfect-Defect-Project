"""Synthetic coverage for the explicit stateless light-action M0 slice."""

from __future__ import annotations

import hashlib
import io
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row
from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.light_action_inputs import (
    LoadedLightActionInputs,
    _compile,
    fit_state_bpe,
    load_light_action_inputs,
    publish_light_action_inputs,
)
from stpd.fullrun.text_menu_data import publish_text_menu_bc_view, publish_text_menu_source
from stpd.light_action_codec import (
    BOS_ACT,
    EOS_ACT,
    SPEC_SHA256,
    decode_action,
    encode_action,
)
from stpd.models.dsimple_memory import _LightActionEncoder
from stpd.models.light_action_encoder import LightActionEncoder
from stpd.models.stage1a import LightActionM0Scorer, build_scorer
from stpd.models.token_core import ScratchShape, ScratchTokenCore
from stpd.qwen.portable_backend import PortableQwenBackend
from stpd.qwen.readout_backend import FrozenQwenTokenCore, LoRAQwenTokenCore
from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH, RECIPES, recipe_for
from stpd.workers.token_ranking import (
    CHECKPOINT_SCHEMA,
    LIGHT_ACTION_M0_CHECKPOINT_SCHEMA,
    LightActionM0Config,
    TokenRankingEngine,
    model_weights,
    restore_weights,
)
from stpd.workers.token_worker import (
    LIGHT_ACTION_M0_MODEL_SCHEMA,
    execute_tokens,
    prepare_token_run,
)


def _inputs(tmp_path):
    archive = store(tmp_path / "objects")
    source = publish_text_menu_source(
        archive, (row("m0-a"), row("m0-b", native=True), row("m0-c")), PRODUCER,
    )
    view = publish_text_menu_bc_view(archive, source.artifact_id, PRODUCER)
    item = publish_light_action_inputs(archive, view.artifact_id, "s", PRODUCER)
    return archive, view, load_light_action_inputs(archive, item.artifact_id)


def _small_bpe() -> bytes:
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(
        ("OBS\nstate\n", "ACT\nfirst\n", "ACT\nsecond\n"),
        trainers.BpeTrainer(
            vocab_size=512, min_frequency=1, show_progress=False,
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=[],
        ),
    )
    return tokenizer.to_str().encode("utf-8")


def test_recipes_are_additive_and_action_byte_codec_is_lossless():
    assert recipe_for("stage1a.dsimple.s.v1").graph == "dsimple.vector.v1"
    assert recipe_for("stage1a.dsimple.pf.v1").backbone == "pf"
    expected = {
        f"stage1a.dsimple.light-action.m0.{backbone}.v1"
        for backbone in ("s", "pf", "pl")
    }
    assert expected <= set(RECIPES)
    assert {recipe_for(item).graph for item in expected} == {LIGHT_ACTION_M0_GRAPH}

    composed = "cafe\u0301: 龍\U0001f9ed"
    precomposed = "café: 龍\U0001f9ed"
    encoded = encode_action(composed, max_bytes=64)
    assert encoded[0] == BOS_ACT and encoded[-1] == EOS_ACT
    assert all(0 <= token <= 255 for token in encoded[1:-1])
    assert decode_action(encoded) == composed
    assert encode_action(composed, max_bytes=64) != encode_action(precomposed, max_bytes=64)
    with pytest.raises(BoundaryError, match="byte_limit"):
        encode_action("龍", max_bytes=2)
    with pytest.raises(BoundaryError, match="invalid_utf8_action"):
        decode_action((BOS_ACT, 0xFF, EOS_ACT))


def test_state_bpe_only_uses_train_observations_and_actions_keep_catalog_binding(tmp_path):
    archive, _, inputs = _inputs(tmp_path)
    samples = inputs.samples
    altered = tuple(
        replace(sample, state_text=("dev-only-secret" if sample.split == "dev"
                                    else sample.state_text),
                action_texts=tuple(f"different-action-{i}" for i in sample.action_texts))
        for sample in samples
    )
    assert fit_state_bpe(samples) == fit_state_bpe(altered)
    assert inputs.manifest.parameters.value()["action_codec"]["sha256"] == SPEC_SHA256
    for sample, token_row in zip(inputs.samples, inputs.rows, strict=True):
        assert token_row.action_ids == sample.action_keys
        assert tuple(decode_action(action) for action in token_row.actions) == sample.action_texts
        assert len(token_row.actions) == len(sample.action_keys)

    with pytest.raises(BoundaryError, match="byte_limit_exceeded"):
        _compile(samples, fit_state_bpe(samples), "s", max_state_tokens=8192,
                 max_action_bytes=1)

    original_rows = b"".join(archive.read_payload(inputs.manifest.payload("rows")))
    forged_rows = archive.put_payload("rows", io.BytesIO(original_rows + b"{}\n"),
                                      "application/x-ndjson")
    forged = replace(
        inputs.manifest,
        payloads=tuple(forged_rows if payload.role == "rows" else payload
                       for payload in inputs.manifest.payloads),
    )
    archive.publish(forged)
    with pytest.raises(BoundaryError, match="source_projection_mismatch"):
        load_light_action_inputs(archive, forged.artifact_id)


def test_pinned_state_codec_is_shared_by_pf_pl_and_actions_do_not_depend_on_it(
    tmp_path, monkeypatch,
):
    from stpd.fullrun import light_action_inputs

    _, _, inputs = _inputs(tmp_path)
    samples = inputs.samples
    raw = _small_bpe()
    digest = hashlib.sha256(raw).hexdigest()
    pin = SimpleNamespace(
        file_by_name={"tokenizer.json": SimpleNamespace(sha256=digest,
                                                        size_bytes=len(raw))},
        hard_limit=8192,
        model_id="Qwen/Qwen3-0.6B-Base",
        repo_revision="d" * 40,
        tokenizer_bundle_sha256="e" * 64,
        special_tokens=(
            SimpleNamespace(token_id=1, roles=("bos_token",)),
            SimpleNamespace(token_id=2, roles=("eos_token",)),
            SimpleNamespace(token_id=3, roles=("pad_token",)),
        ),
    )
    monkeypatch.setattr(light_action_inputs, "load_pin", lambda: pin)
    _, pf_rows, pf_info = _compile(samples, raw, "qwen3", max_state_tokens=8192,
                                   max_action_bytes=8192)
    _, pl_rows, pl_info = _compile(samples, raw, "qwen3", max_state_tokens=8192,
                                   max_action_bytes=8192)
    assert pf_info["state_codec"] == pl_info["state_codec"]
    assert pf_rows == pl_rows
    assert pf_info["action_codec"]["sha256"] == pl_info["action_codec"]["sha256"]


def test_m0_encodes_state_once_preflights_full_catalog_and_is_candidate_equivariant():
    core = ScratchTokenCore(ScratchShape(512, 16, 1, 2, 32, 0.0, 64))
    model = build_scorer(
        "stage1a.dsimple.light-action.m0.s.v1", core,
        max_action_bytes=8, scoring_seed=994,
    ).eval()
    assert isinstance(model, LightActionM0Scorer)
    state = torch.tensor([1, 4, 8], dtype=torch.long)
    actions = tuple(torch.tensor(encode_action(text, max_bytes=8))
                    for text in ("a", "bb", "ccc"))
    with (
        patch.object(core, "contextualize", wraps=core.contextualize) as encode_state,
        patch.object(model.action_encoder, "forward",
                     wraps=model.action_encoder.forward) as encode_action_row,
    ):
        scores = model(state, actions)
        assert encode_state.call_count == 1
        assert encode_action_row.call_count == len(actions)
        torch.testing.assert_close(model(state, actions[::-1]), scores.flip(0))
        torch.testing.assert_close(model(state, (actions[0],))[0], scores[0])
    assert scores.shape == (3,) and torch.isfinite(scores).all()

    too_long = torch.tensor(encode_action("over-limit", max_bytes=32))
    with patch.object(core, "contextualize", wraps=core.contextualize) as encode_state:
        with pytest.raises(ValueError, match="truncation is forbidden"):
            model(state, (actions[0], too_long))
        encode_state.assert_not_called()


def test_m0_shared_scorer_initialization_is_width_independent_and_rng_isolated():
    def make(width: int, *, global_seed: int):
        torch.manual_seed(global_seed)
        core = ScratchTokenCore(ScratchShape(512, width, 1, 2, 32, 0.0, 64))
        before = torch.get_rng_state().clone()
        model = build_scorer(
            "stage1a.dsimple.light-action.m0.s.v1", core,
            max_action_bytes=8, scoring_seed=994,
        )
        assert torch.equal(torch.get_rng_state(), before)
        return model

    narrow = make(16, global_seed=1)
    wide = make(32, global_seed=2)
    same_shape = make(16, global_seed=3)
    for name, value in narrow.state_dict().items():
        if name.startswith("core."):
            continue
        torch.testing.assert_close(value, same_shape.state_dict()[name], rtol=0, atol=0)
    for branch in ("action_encoder", "transition", "score_head"):
        narrow_state = getattr(narrow, branch).state_dict()
        wide_state = getattr(wide, branch).state_dict()
        assert narrow_state.keys() == wide_state.keys()
        for name in narrow_state:
            torch.testing.assert_close(narrow_state[name], wide_state[name], rtol=0, atol=0)

def test_shared_m2_encoder_keeps_its_original_parameter_layout():
    old_boundary = _LightActionEncoder(258, 24)
    shared_implementation = LightActionEncoder(258, 24)
    assert type(old_boundary).__name__ == "_LightActionEncoder"
    assert set(old_boundary.state_dict()) == set(shared_implementation.state_dict())
    assert {name: value.shape for name, value in old_boundary.state_dict().items()} == {
        name: value.shape for name, value in shared_implementation.state_dict().items()
    }


def test_scratch_engine_resume_and_worker_preserve_m0_identity(tmp_path):
    torch.set_num_threads(2)
    archive, view, inputs = _inputs(tmp_path)
    config = LightActionM0Config(steps=2, max_state_tokens=8192, max_action_bytes=8192)
    baseline = TokenRankingEngine(inputs, config)
    losses = [baseline.advance(), baseline.advance()]
    paused = TokenRankingEngine(inputs, config)
    assert paused.advance() == losses[0]
    restored = TokenRankingEngine(inputs, config)
    restored.restore(paused.checkpoint())
    assert restored.advance() == losses[1]
    assert restored.model_bytes() == baseline.model_bytes()
    assert restored.scores(0) == baseline.scores(0)

    run = prepare_token_run(archive, inputs, config, PRODUCER)
    assert "checkpoint_interval" not in run.parameters.value()
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    first = execute_tokens(archive, reporter, run.artifact_id, PRODUCER, stop_after=1)
    assert first.state == "paused" and first.checkpoint_id
    final = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                           resume=first.checkpoint_id)
    assert final.state == "completed" and final.result_id
    result = archive.get_manifest(final.result_id)
    model = archive.get_manifest(result.parent("model"))
    checkpoint = archive.get_manifest(final.checkpoint_id)
    events = [item.parameters.value() for item in reporter.events(run.artifact_id)]
    assert [item["step"] for item in events if item["kind"] == "step"] == [1, 2]
    assert [archive.get_manifest(item["details"]["checkpoint_id"]).parameters.value()["step"]
            for item in events if item["kind"] == "checkpoint"] == [1, 2]
    assert all("checkpoint_interval" not in item["details"] for item in events
               if item["kind"] in {"started", "resumed"})
    assert all(type(item["details"]["loss"]) is float for item in events
               if item["kind"] == "step")
    info = model.parameters.value()
    assert info["schema"] == LIGHT_ACTION_M0_MODEL_SCHEMA
    assert info["graph"] == LIGHT_ACTION_M0_GRAPH
    assert info["source_view_schema"] == view.parameters.value()["schema"]
    assert info["state_codec"] == inputs.manifest.parameters.value()["state_codec"]
    assert info["action_codec"]["sha256"] == SPEC_SHA256
    assert checkpoint.parameters.value()["schema"] == LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
    assert checkpoint.parameters.value()["graph"] == LIGHT_ACTION_M0_GRAPH
    repeated = execute_tokens(archive, reporter, run.artifact_id, PRODUCER)
    assert repeated.result_id == final.result_id

    with pytest.raises(BoundaryError, match="light_action_m0_recipe_required"):
        replace(config, recipe="stage1a.dsimple.s.v1")
    assert CHECKPOINT_SCHEMA != LIGHT_ACTION_M0_CHECKPOINT_SCHEMA


def test_worker_checkpoint_interval_forces_pause_and_final_and_resumes_exactly(tmp_path):
    torch.set_num_threads(2)
    archive, _, inputs = _inputs(tmp_path)
    config = LightActionM0Config(steps=5, max_state_tokens=8192, max_action_bytes=8192)
    baseline = TokenRankingEngine(inputs, config)
    baseline_losses = [baseline.advance() for _ in range(config.steps)]
    baseline_weights = baseline.model_bytes()

    run = prepare_token_run(archive, inputs, config, PRODUCER, replicate="interval-3")
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    for invalid in (0, -1, True, 1.5, "3"):
        with pytest.raises(BoundaryError, match="invalid_checkpoint_interval"):
            execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                           checkpoint_interval=invalid)
    assert reporter.events(run.artifact_id) == ()

    paused = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                            stop_after=2, checkpoint_interval=3)
    assert paused.state == "paused" and paused.checkpoint_id
    assert archive.get_manifest(paused.checkpoint_id).parameters.value()["step"] == 2
    completed = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                               resume=paused.checkpoint_id, checkpoint_interval=3)
    assert completed.state == "completed" and completed.checkpoint_id

    events = [item.parameters.value() for item in reporter.events(run.artifact_id)]
    step_events = [item for item in events if item["kind"] == "step"]
    checkpoint_events = [item for item in events if item["kind"] == "checkpoint"]
    assert [item["step"] for item in step_events] == [1, 2, 3, 4, 5]
    assert [item["details"]["loss"] for item in step_events] == baseline_losses
    assert [item["step"] for item in checkpoint_events] == [2, 3, 5]
    starts = [item for item in events if item["kind"] in {"started", "resumed"}]
    assert [item["kind"] for item in starts] == ["started", "resumed"]
    assert all(item["details"]["checkpoint_interval"] == 3 for item in starts)

    final_checkpoint = archive.get_manifest(completed.checkpoint_id)
    model = archive.get_manifest(archive.get_manifest(completed.result_id).parent("model"))
    assert final_checkpoint.parameters.value()["step"] == 5
    assert b"".join(archive.read_payload(model.payload("weights"))) == baseline_weights


def test_worker_checkpoint_write_failure_keeps_last_checkpoint_without_retry(tmp_path,
                                                                              monkeypatch):
    torch.set_num_threads(2)
    archive, _, inputs = _inputs(tmp_path)
    config = LightActionM0Config(steps=3, max_state_tokens=8192, max_action_bytes=8192)
    run = prepare_token_run(archive, inputs, config, PRODUCER, replicate="failure-2")
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    original_put_payload = archive.put_payload
    checkpoint_writes = 0

    def fail_second_checkpoint(role, *args, **kwargs):
        nonlocal checkpoint_writes
        if role == "checkpoint":
            checkpoint_writes += 1
            if checkpoint_writes == 2:
                raise OSError("injected checkpoint payload failure")
        return original_put_payload(role, *args, **kwargs)

    monkeypatch.setattr(archive, "put_payload", fail_second_checkpoint)
    with pytest.raises(OSError, match="injected checkpoint payload failure"):
        execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                       checkpoint_interval=2)

    assert checkpoint_writes == 2
    events = [item.parameters.value() for item in reporter.events(run.artifact_id)]
    checkpoint_events = [item for item in events if item["kind"] == "checkpoint"]
    step_events = [item for item in events if item["kind"] == "step"]
    failed = next(item for item in events if item["kind"] == "failed")
    assert [item["step"] for item in checkpoint_events] == [2]
    assert [item["step"] for item in step_events] == [1, 2, 3]
    assert failed["details"]["last_checkpoint"] == checkpoint_events[0]["details"][
        "checkpoint_id"]
    assert reporter.completed(run.artifact_id) is None


def _tiny_backend(vocab_size: int, *, seed: int = 521):
    from transformers import Qwen3Config, Qwen3Model

    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        base = Qwen3Model(Qwen3Config(
            vocab_size=vocab_size, hidden_size=24, intermediate_size=48,
            num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=1,
            max_position_embeddings=8192, attention_dropout=0.0,
        )).eval().requires_grad_(False)
    backend = object.__new__(PortableQwenBackend)
    backend._base_model = base
    backend.hidden_size = 24
    backend.identity = SimpleNamespace(
        model_id="Qwen/Qwen3-0.6B-Base", model_revision="d" * 40,
        weights_sha256="a" * 64, config_sha256="b" * 64,
        tokenizer_revision="d" * 40, tokenizer_sha256="c" * 64,
    )
    backend.pin = SimpleNamespace(l1=SimpleNamespace(hard_limit=8192))
    backend._tokenizer = SimpleNamespace(eos_token_id=2)
    return backend


def _qwen_family_inputs(inputs):
    info = inputs.manifest.parameters.value()
    state = dict(info["state_codec"])
    state.update({"schema": "qwen3-tokenizer-json-v1", "family": "pinned-qwen3",
                  "model_id": "Qwen/Qwen3-0.6B-Base", "revision": "d" * 40,
                  "tokenizer_bundle_sha256": "e" * 64,
                  "special_token_ids": {"bos": 1, "eos": 2, "pad": 3, "additional": []}})
    state["sha256"] = inputs.manifest.payload("state_tokenizer").sha256
    action = dict(info["action_codec"])
    updated = {**info, "state_codec": state, "action_codec": action}
    manifest = replace(inputs.manifest, parameters=FrozenObject.of(updated))
    return LoadedLightActionInputs(manifest, inputs.samples, inputs.rows,
                                   inputs.state_tokenizer)


def test_tiny_qwen3_lora_is_trainable_frozen_base_and_checkpointable(tmp_path, monkeypatch):
    pytest.importorskip("peft")
    pytest.importorskip("transformers")
    torch.set_num_threads(2)
    archive, _, scratch_inputs = _inputs(tmp_path)
    inputs = _qwen_family_inputs(scratch_inputs)
    archive.publish(inputs.manifest)
    vocab_size = inputs.state_tokenizer.get_vocab_size()

    def backend_factory(snapshot, *, device):
        assert device == "cpu"
        return _tiny_backend(vocab_size)

    monkeypatch.setattr("stpd.workers.token_ranking.PortableQwenBackend", backend_factory)
    monkeypatch.setattr("stpd.qwen.readout_backend.validate_engineering_identity", lambda _id: None)

    pf = TokenRankingEngine(
        inputs, LightActionM0Config(recipe="stage1a.dsimple.light-action.m0.pf.v1",
                                    steps=2), snapshot=tmp_path,
    )
    pl_config = LightActionM0Config(recipe="stage1a.dsimple.light-action.m0.pl.v1", steps=2)
    pl = TokenRankingEngine(inputs, pl_config, snapshot=tmp_path)
    assert isinstance(pf.model.core, FrozenQwenTokenCore)
    assert isinstance(pl.model.core, LoRAQwenTokenCore)
    assert pl.model.core.model.config.use_cache is False
    assert all(not parameter.requires_grad for parameter in pf.model.core.parameters())
    torch.testing.assert_close(torch.tensor(pf.scores(0)), torch.tensor(pl.scores(0)),
                               rtol=0, atol=0)
    pf.advance()
    pf_restored = TokenRankingEngine(
        inputs, LightActionM0Config(recipe="stage1a.dsimple.light-action.m0.pf.v1",
                                    steps=2), snapshot=tmp_path,
    )
    pf_restored.restore(pf.checkpoint())
    assert pf_restored.model_bytes() == pf.model_bytes()

    adapter_before = {name: parameter.detach().clone()
                      for name, parameter in pl.model.core.model.named_parameters()
                      if parameter.requires_grad}
    base_before = {name: parameter.detach().clone()
                   for name, parameter in pl.model.core.model.named_parameters()
                   if not parameter.requires_grad}
    loss = pl.advance()
    assert torch.isfinite(torch.tensor(loss))
    assert any(parameter.grad is not None and parameter.grad.abs().sum() > 0
               for parameter in pl.model.core.model.parameters()
               if parameter.requires_grad)
    assert all(parameter.grad is None for parameter in pl.model.core.model.parameters()
               if not parameter.requires_grad)
    assert any(not torch.equal(adapter_before[name], parameter)
               for name, parameter in pl.model.core.model.named_parameters()
               if parameter.requires_grad)
    assert all(torch.equal(base_before[name], parameter)
               for name, parameter in pl.model.core.model.named_parameters()
               if not parameter.requires_grad)

    checkpoint = pl.checkpoint()
    restored = TokenRankingEngine(inputs, pl_config, snapshot=tmp_path)
    restored.restore(checkpoint)
    assert restored.advance() == pl.advance()
    assert restored.model_bytes() == pl.model_bytes()
    assert restored.scores(0) == pl.scores(0)

    saved = model_weights(pl.model, frozen=False,
                          adapter_tensor_names=pl.adapter_tensor_names)
    assert pl.adapter_tensor_names
    raw = __import__("safetensors.torch", fromlist=["save"]).save(saved)
    restore_weights(restored.model, raw, frozen=False,
                    adapter_tensor_names=restored.adapter_tensor_names,
                    strict_frozen_core=True)
    damaged = dict(saved)
    damaged.pop(next(iter(pl.adapter_tensor_names)))
    with pytest.raises(BoundaryError, match="invalid_weights"):
        restore_weights(pl.model, __import__("safetensors.torch", fromlist=["save"]).save(damaged),
                        frozen=False, adapter_tensor_names=pl.adapter_tensor_names,
                        strict_frozen_core=True)

    # Exercise the worker's M0 artifact/checkpoint identity chain using this synthetic
    # Qwen-family fixture; only the tokenizer projection loader is substituted here.
    monkeypatch.setattr(
        "stpd.workers.token_worker.load_light_action_inputs",
        lambda _store, _identity: inputs,
    )
    run = prepare_token_run(archive, inputs, pl_config, PRODUCER)
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    first = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                           snapshot=tmp_path, stop_after=1)
    assert first.state == "paused" and first.checkpoint_id
    checkpoint_manifest = archive.get_manifest(first.checkpoint_id)
    tamper = (
        ("graph", "dsimple.other.v1"),
        ("state_codec", {**checkpoint_manifest.parameters.value()["state_codec"],
                          "sha256": "0" * 64}),
        ("backbone", {**checkpoint_manifest.parameters.value()["backbone"],
                       "core_fingerprint": "0" * 64}),
        ("adapter_config", {**checkpoint_manifest.parameters.value()["adapter_config"],
                            "r": 4}),
    )
    for field, value in tamper:
        info = dict(checkpoint_manifest.parameters.value())
        info[field] = value
        forged = replace(checkpoint_manifest, parameters=FrozenObject.of(info))
        archive.publish(forged)
        with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
            execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                           snapshot=tmp_path, resume=forged.artifact_id)
    final = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                           snapshot=tmp_path, resume=first.checkpoint_id)
    assert final.state == "completed" and final.result_id
    result = archive.get_manifest(final.result_id)
    model_manifest = archive.get_manifest(result.parent("model"))
    metadata = model_manifest.parameters.value()
    assert metadata["schema"] == LIGHT_ACTION_M0_MODEL_SCHEMA
    assert metadata["graph"] == LIGHT_ACTION_M0_GRAPH
    assert metadata["recipe"] == pl_config.recipe
    assert metadata["backbone"]["core_fingerprint"] == pl.backbone["core_fingerprint"]
    assert metadata["state_codec"] == inputs.manifest.parameters.value()["state_codec"]
    assert metadata["action_codec"]["sha256"] == SPEC_SHA256
    assert metadata["adapter_config"]["target_modules"] == [
        "q_proj", "k_proj", "v_proj", "o_proj",
    ]
    assert metadata["adapter_tensor_names"] == sorted(pl.adapter_tensor_names)
    assert archive.get_manifest(final.checkpoint_id).parameters.value()["schema"] == (
        LIGHT_ACTION_M0_CHECKPOINT_SCHEMA
    )
    repeated = execute_tokens(archive, reporter, run.artifact_id, PRODUCER,
                              snapshot=tmp_path)
    assert repeated.result_id == final.result_id
