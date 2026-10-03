"""Synthetic M0 export and standalone-score seam; no Human data or game runtime."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row, snapshot

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.canonical import semantic_hash
from stpd.fullrun.light_action_inputs import load_light_action_inputs, publish_light_action_inputs
from stpd.fullrun.text_menu_data import publish_text_menu_bc_view, publish_text_menu_source
from stpd.fullrun.text_menu_inputs import project_text_menu_snapshot
from stpd.light_action_codec import SPEC_BYTES
from stpd.models.stage1a import recipe_for
from stpd.policy.token_decision import (
    LIGHT_ACTION_M0_EXPORT_SCHEMA,
    LIGHT_ACTION_M0_FILES,
    LightActionM0DecisionScorer,
    _m0_runtime_backbone_matches,
    export_light_action_m0_model,
    light_action_m0_runtime_config,
)
from stpd.workers.token_ranking import (
    LightActionM0Config,
    TokenRankingEngine,
    config_payload,
)
from stpd.workers.token_worker import execute_tokens, prepare_token_run


def _completed_m0(tmp_path):
    archive = store(tmp_path / "objects")
    source = publish_text_menu_source(
        archive, (row("m0-export-a"), row("m0-export-b", native=True),
                  row("m0-export-c")), PRODUCER,
    )
    view = publish_text_menu_bc_view(archive, source.artifact_id, PRODUCER)
    input_manifest = publish_light_action_inputs(
        archive, view.artifact_id, "s", PRODUCER,
    )
    inputs = load_light_action_inputs(archive, input_manifest.artifact_id)
    config = LightActionM0Config(steps=2, max_state_tokens=8192, max_action_bytes=8192)
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        run = prepare_token_run(archive, inputs, config, PRODUCER)
        paused = execute_tokens(
            archive, reporter, run.artifact_id, PRODUCER, stop_after=1,
        )
        assert paused.state == "paused" and paused.checkpoint_id
        completed = execute_tokens(
            archive, reporter, run.artifact_id, PRODUCER, resume=paused.checkpoint_id,
        )
    finally:
        torch.set_num_threads(original_threads)
    assert completed.state == "completed" and completed.result_id
    result = archive.get_manifest(completed.result_id)
    return archive, config, result.parent("model"), result.parent("offline_evaluation")


def test_m0_scratch_train_resume_dev_export_and_fresh_process_score(tmp_path):
    archive, _, model_id, evaluation_id = _completed_m0(tmp_path)
    evaluation = archive.get_manifest(evaluation_id)
    assert evaluation.parameters.value()["partition"] == "dev"
    destination = tmp_path / "export"
    receipt = export_light_action_m0_model(archive, model_id, destination)
    assert receipt == {
        "model_id": model_id,
        "schema": LIGHT_ACTION_M0_EXPORT_SCHEMA,
        "payload_bytes": sum((destination / filename).stat().st_size
                              for filename in LIGHT_ACTION_M0_FILES.values()),
    }
    assert {item.name for item in destination.iterdir()} == {
        "model.json", *LIGHT_ACTION_M0_FILES.values(),
    }
    assert (destination / LIGHT_ACTION_M0_FILES["action_codec"]).read_bytes() == SPEC_BYTES

    model_manifest = archive.get_manifest(model_id)
    inputs = load_light_action_inputs(archive, model_manifest.parent("training_input"))
    checkpoint = archive.get_manifest(model_manifest.parent("checkpoint"))
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        engine = TokenRankingEngine(inputs, LightActionM0Config(steps=2))
        engine.restore(b"".join(archive.read_payload(checkpoint.payload("checkpoint"))))
        standalone = LightActionM0DecisionScorer(destination)
        assert standalone.runtime_device == "cpu"
        assert standalone.config.device == "cpu"
        for index, sample in enumerate(inputs.samples):
            assert standalone.score_texts(sample.state_text, sample.action_texts) == pytest.approx(
                engine.scores(index), rel=0, abs=0,
            )
    finally:
        torch.set_num_threads(original_threads)

    source_snapshot = snapshot("m0-fresh-process")
    current = project_text_menu_snapshot(source_snapshot)
    model = LightActionM0DecisionScorer(destination)
    scores = model.score_snapshot(source_snapshot)
    assert list(scores) == list(current.action_ids)
    assert len(scores) == len(source_snapshot["menu_actions"]["actions"])
    ordered_scores = model.score_texts(current.state_text, current.action_texts)
    reversed_scores = model.score_texts(current.state_text, current.action_texts[::-1])
    assert reversed_scores == pytest.approx(ordered_scores[::-1], rel=5e-4, abs=1e-5)

    request = tmp_path / "snapshot.json"
    request.write_text(json.dumps(source_snapshot), encoding="utf-8")
    program = """
import json, sys, torch
from pathlib import Path
from stpd.policy.token_decision import LightActionM0DecisionScorer
torch.set_num_threads(1)
scorer = LightActionM0DecisionScorer(Path(sys.argv[1]))
snapshot = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
print(json.dumps({'model_id': scorer.artifact.artifact_id,
                  'scores': scorer.score_snapshot(snapshot)}))
"""
    output = subprocess.check_output(
        [sys.executable, "-c", program, str(destination), str(request)],
        cwd=Path(__file__).resolve().parents[1], text=True,
    )
    fresh = json.loads(output.strip().splitlines()[-1])
    assert fresh == {"model_id": model_id, "scores": scores}


def test_m0_runtime_device_is_separate_from_training_identity():
    training = LightActionM0Config(device="cuda")
    runtime = light_action_m0_runtime_config(training)
    assert training.device == config_payload(training)["device"] == "cuda"
    assert runtime.device == "cpu"
    with pytest.raises(BoundaryError, match="unsupported_runtime_device"):
        light_action_m0_runtime_config(training, "tpu")
    with pytest.raises(BoundaryError, match="cuda_scratch_m0_only"):
        light_action_m0_runtime_config(
            LightActionM0Config(
                recipe="stage1a.dsimple.light-action.m0.pf.v1", device="cpu",
            ),
            "cuda",
        )


def test_m0_pf_runtime_identity_normalizes_only_the_recorded_training_device():
    training_config = LightActionM0Config(
        recipe="stage1a.dsimple.light-action.m0.pf.v1", device="mps",
    )
    recipe = recipe_for(training_config.recipe)
    state_codec = {"family": "pinned-qwen3", "sha256": "a" * 64}

    def backbone(device: str, *, weights_sha256: str = "b" * 64) -> dict:
        identity = {
            "kind": "pf",
            "qwen": {"device": device, "weights_sha256": weights_sha256},
            "state_codec": state_codec,
        }
        identity["core_fingerprint"] = semantic_hash({
            "core": identity,
            "graph": recipe.graph,
            "vocabulary_size": 128,
            "max_state_tokens": training_config.max_state_tokens,
        })
        return identity

    runtime = backbone("cpu")
    training = backbone("mps")
    assert _m0_runtime_backbone_matches(runtime, training, training_config, 128)
    assert not _m0_runtime_backbone_matches(
        runtime, backbone("cpu"), training_config, 128,
    )
    forged_runtime_fingerprint = backbone("cpu")
    forged_runtime_fingerprint["core_fingerprint"] = "0" * 64
    assert not _m0_runtime_backbone_matches(
        forged_runtime_fingerprint, training, training_config, 128,
    )
    forged_training_fingerprint = backbone("mps")
    forged_training_fingerprint["core_fingerprint"] = "0" * 64
    assert not _m0_runtime_backbone_matches(
        training, forged_training_fingerprint, training_config, 128,
    )
    assert not _m0_runtime_backbone_matches(
        backbone("cpu", weights_sha256="c" * 64), training, training_config, 128,
    )


def test_m0_standalone_scorer_rejects_action_overflow_and_tampered_codec(tmp_path):
    archive, _, model_id, _ = _completed_m0(tmp_path)
    destination = tmp_path / "export"
    export_light_action_m0_model(archive, model_id, destination)
    scorer = LightActionM0DecisionScorer(destination)
    with pytest.raises(BoundaryError, match="byte_limit_exceeded"):
        scorer.score_texts("current state", ("x" * 8193,))

    for role in ("action_codec", "state_tokenizer", "weights"):
        payload = destination / LIGHT_ACTION_M0_FILES[role]
        raw = payload.read_bytes()
        payload.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        with pytest.raises(BoundaryError, match="payload_digest_mismatch"):
            LightActionM0DecisionScorer(destination)
        payload.write_bytes(raw)


def test_m0_export_rejects_legacy_artifact(tmp_path):
    from test_stage1a_training import tiny_config, token_inputs

    owner, inputs = token_inputs(tmp_path)
    run = prepare_token_run(owner.store, inputs, tiny_config(), owner.producer)
    completed = execute_tokens(
        owner.store, ObjectStoreRunReporter(owner.store, owner.store.blobs),
        run.artifact_id, owner.producer,
    )
    result = owner.store.get_manifest(completed.result_id)
    with pytest.raises(BoundaryError, match="unsupported_light_action_model"):
        export_light_action_m0_model(owner.store, result.parent("model"), tmp_path / "bad-export")


@pytest.mark.parametrize("recipe", [
    "stage1a.dsimple.light-action.m0.pf.v1",
    "stage1a.dsimple.light-action.m0.pl.v1",
])
def test_m0_qwen_backbone_exports_and_scores_from_pinned_tiny_fixture(
    tmp_path, monkeypatch, recipe,
):
    if recipe.endswith(".pl.v1"):
        pytest.importorskip("peft")
    pytest.importorskip("transformers")
    from test_light_action_m0 import _inputs, _qwen_family_inputs, _tiny_backend

    archive, _, scratch_inputs = _inputs(tmp_path)
    inputs = _qwen_family_inputs(scratch_inputs)
    archive.publish(inputs.manifest)
    state_codec = inputs.manifest.parameters.value()["state_codec"]
    special_ids = state_codec["special_token_ids"]
    special_tokens = tuple(
        SimpleNamespace(token_id=token_id, roles=(role,))
        for role, token_id in (("bos_token", special_ids["bos"]),
                               ("eos_token", special_ids["eos"]),
                               ("pad_token", special_ids["pad"]))
    )
    pin = SimpleNamespace(
        model_id=state_codec["model_id"], repo_revision=state_codec["revision"],
        file_by_name={"tokenizer.json": SimpleNamespace(
            sha256=state_codec["sha256"],
            size_bytes=inputs.manifest.payload("state_tokenizer").size,
        )},
        tokenizer_bundle_sha256=state_codec["tokenizer_bundle_sha256"],
        special_tokens=special_tokens, hard_limit=8192,
    )
    monkeypatch.setattr("stpd.policy.token_decision.load_pin", lambda: pin)

    def backend_factory(_snapshot, *, device):
        assert device == "cpu"
        return _tiny_backend(inputs.state_tokenizer.get_vocab_size(), device=device)

    monkeypatch.setattr("stpd.workers.token_ranking.PortableQwenBackend", backend_factory)
    monkeypatch.setattr("stpd.qwen.readout_backend.validate_engineering_identity", lambda _id: None)
    monkeypatch.setattr(
        "stpd.workers.token_worker.load_light_action_inputs",
        lambda _store, _identity: inputs,
    )
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        config = LightActionM0Config(recipe=recipe, steps=1)
        run = prepare_token_run(archive, inputs, config, PRODUCER)
        result = execute_tokens(
            archive, ObjectStoreRunReporter(archive, archive.blobs), run.artifact_id,
            PRODUCER, snapshot=tmp_path,
        )
        assert result.result_id
        completed = archive.get_manifest(result.result_id)
        destination = tmp_path / ("export-" + recipe.rsplit(".", 2)[-2])
        export_light_action_m0_model(archive, completed.parent("model"), destination)
        model = archive.get_manifest(completed.parent("model"))
        for key, bad_value in (
            ("model_id", "untrusted/tokenizer"),
            ("revision", "f" * 40),
            ("sha256", "f" * 64),
            ("tokenizer_bundle_sha256", "f" * 64),
            ("special_token_ids", {"bos": 9, "eos": 10, "pad": 11,
                                    "additional": []}),
        ):
            info = model.parameters.value()
            forged_codec = dict(info["state_codec"])
            forged_codec[key] = bad_value
            forged_info = {**info, "state_codec": forged_codec}
            forged = replace(model, parameters=FrozenObject.of(forged_info))
            with pytest.raises(BoundaryError) as error:
                from stpd.policy.token_decision import check_light_action_m0_model

                check_light_action_m0_model(forged)
            expected_code = (
                "light_action_codec_identity_mismatch" if key == "sha256"
                else "light_action_state_codec_pin_mismatch"
            )
            assert error.value.code == expected_code, key
        scorer = LightActionM0DecisionScorer(destination, snapshot=tmp_path)
        scores = scorer.score_snapshot(snapshot("tiny-qwen-" + recipe[-5:]))
        assert list(scores) == ["opaque-nav", "opaque-play"]
        assert all(torch.isfinite(torch.tensor(value)) for value in scores.values())
    finally:
        torch.set_num_threads(original_threads)
