"""Synthetic M0 export and standalone-score seam; no Human data or game runtime."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_text_menu_data import row, snapshot

from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.light_action_inputs import load_light_action_inputs, publish_light_action_inputs
from stpd.fullrun.text_menu_data import publish_text_menu_bc_view, publish_text_menu_source
from stpd.fullrun.text_menu_inputs import project_text_menu_snapshot
from stpd.light_action_codec import SPEC_BYTES
from stpd.policy.token_decision import (
    LIGHT_ACTION_M0_EXPORT_SCHEMA,
    LIGHT_ACTION_M0_FILES,
    LightActionM0DecisionScorer,
    export_light_action_m0_model,
)
from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine
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

    def backend_factory(_snapshot, *, device):
        assert device == "cpu"
        return _tiny_backend(inputs.state_tokenizer.get_vocab_size())

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
        scorer = LightActionM0DecisionScorer(destination, snapshot=tmp_path)
        scores = scorer.score_snapshot(snapshot("tiny-qwen-" + recipe[-5:]))
        assert list(scores) == ["opaque-nav", "opaque-play"]
        assert all(torch.isfinite(torch.tensor(value)) for value in scores.values())
    finally:
        torch.set_num_threads(original_threads)
