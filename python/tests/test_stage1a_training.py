"""Synthetic token-worker lifecycle; no Human or native runtime qualification."""
import json
import subprocess
import sys
from dataclasses import replace

import pytest
import torch
from test_decision_training import prepared
from test_stage1a_models import tiny_qwen_core

from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation, publish_decision_view
from stpd.fullrun.representation import FullRunSerializer
from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs
from stpd.models.stage1a import BTokenScorer
from stpd.policy.token_decision import TokenDecisionScorer, export_token_model
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.token_ranking import TokenConfig, TokenRankingEngine, model_weights
from stpd.workers.token_worker import execute_tokens, prepare_token_run


def token_inputs(tmp_path):
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset,
                                     AllocationSpec(max_train=2, max_dev=1), owner.producer)
    view = publish_decision_view(owner.store, allocation.artifact_id,
                                 FullRunSerializer("lite"), owner.producer)
    item = publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer)
    return owner, load_token_inputs(owner.store, item.artifact_id)


def tiny_config(recipe="stage1a.dsimple.s.v1"):
    return TokenConfig(recipe=recipe, steps=3, width=16, heads=2, layers=2,
                       feedforward=32, dropout=0.2)


def test_token_budget_is_explicit_and_old_configs_keep_original_limit():
    from dataclasses import asdict

    config = replace(tiny_config(), max_tokens=16384)
    assert TokenConfig.decode(asdict(config)).shape(256).max_tokens == 16384
    legacy = {k: v for k, v in asdict(config).items() if k != "max_tokens"}
    assert TokenConfig.decode(legacy).max_tokens == 8192


@pytest.mark.parametrize("recipe", ["stage1a.b.s.v1", "stage1a.b.s.v2", "stage1a.dsimple.s.v1"])
def test_engine_resume_keeps_dropout_plan_parameters_and_scores(tmp_path, recipe):
    _, inputs = token_inputs(tmp_path)
    config = tiny_config(recipe)
    baseline = TokenRankingEngine(inputs, config)
    losses = [baseline.advance() for _ in range(3)]
    paused = TokenRankingEngine(inputs, config)
    assert paused.advance() == losses[0]
    restored = TokenRankingEngine(inputs, config)
    restored.restore(paused.checkpoint())
    assert [restored.advance(), restored.advance()] == losses[1:]
    assert restored.model_bytes() == baseline.model_bytes()
    assert restored.scores(0) == baseline.scores(0)
    wrong = TokenRankingEngine(inputs, replace(config, seed=config.seed + 1))
    with pytest.raises(BoundaryError, match="identity"):
        wrong.restore(paused.checkpoint())
    with pytest.raises(BoundaryError, match="exhausted"):
        restored.advance()
    altered = decode_checkpoint(paused.checkpoint())
    altered["optimizer"]["param_groups"][0]["lr"] *= 2
    with pytest.raises(BoundaryError, match="optimizer_config"):
        restored.restore(encode_checkpoint(altered))


def test_fixed_qwen_weights_are_not_saved_in_small_checkpoint_model():
    core = tiny_qwen_core()
    model = BTokenScorer(core, readout_initial=core.readout_initial())
    assert set(model_weights(model, frozen=True)) == {"readout", "head.weight", "head.bias"}


@pytest.mark.parametrize("recipe", ["stage1a.dsimple.s.v1", "stage1a.b.s.v2"])
def test_worker_new_process_resume_export_and_standalone_score(tmp_path, recipe):
    owner, inputs = token_inputs(tmp_path)
    config = tiny_config(recipe)
    run = prepare_token_run(owner.store, inputs, config, owner.producer)
    reporter = ObjectStoreRunReporter(owner.store, owner.store.blobs)
    paused = execute_tokens(owner.store, reporter, run.artifact_id, owner.producer, stop_after=1)
    assert paused.state == "paused"
    with pytest.raises(BoundaryError, match="explicit_resume"):
        execute_tokens(owner.store, reporter, run.artifact_id, owner.producer)
    with pytest.raises(BoundaryError, match="source_or_contract"):
        execute_tokens(owner.store, reporter, run.artifact_id,
                       replace(owner.producer, source_revision="b" * 40),
                       resume=paused.checkpoint_id)
    program = '''
import json, sys, torch
from dataclasses import asdict
from spireagent.storage.config import open_store
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.workers.token_worker import execute_tokens
torch.set_num_threads(int(sys.argv[4]))
store = open_store(sys.argv[1])
runtime = store.get_manifest(sys.argv[2]).producer
result = execute_tokens(store, ObjectStoreRunReporter(store, store.blobs),
                        sys.argv[2], runtime, resume=sys.argv[3])
print(json.dumps(asdict(result)))
'''
    output = subprocess.check_output([
        sys.executable, "-c", program, str(owner.store.blobs.root), run.artifact_id,
        paused.checkpoint_id, str(torch.get_num_threads()),
    ], text=True)
    result = json.loads(output.strip().splitlines()[-1])
    assert result["state"] == "completed"
    final = owner.store.get_manifest(result["result_id"])
    model = owner.store.get_manifest(final.parent("model"))
    checkpoint = owner.store.get_manifest(result["checkpoint_id"])
    engine = TokenRankingEngine(inputs, config)
    engine.restore(b"".join(owner.store.read_payload(checkpoint.payload("checkpoint"))))
    reference = TokenRankingEngine(inputs, config)
    for _ in range(3):
        reference.advance()
    assert engine.model_bytes() == reference.model_bytes()
    assert b"".join(owner.store.read_payload(model.payload("weights"))) == engine.model_bytes()
    repeated = execute_tokens(owner.store, reporter, run.artifact_id, owner.producer)
    assert repeated.result_id == result["result_id"]
    destination = tmp_path / "export"
    export_token_model(owner.store, model.artifact_id, destination)
    assert {p.name for p in destination.iterdir()} == {
        "model.json", "tokenizer.json", "weights.safetensors",
    }
    scorer = TokenDecisionScorer(destination)
    for index, sample in enumerate(inputs.samples):
        assert scorer.score_texts(sample.state_text, sample.action_texts) == engine.scores(index)
    sample = inputs.samples[0]
    scores = scorer.score_texts(sample.state_text, sample.action_texts)
    assert scorer.score_texts(sample.state_text, sample.action_texts[::-1]) == scores[::-1]
    # A fresh scoring process gets only the export and an unlabelled input, no store path.
    scoring_input = tmp_path / "input.json"
    scoring_input.write_text(json.dumps({"state": sample.state_text,
                                         "actions": sample.action_texts}))
    scoring_program = '''
import json, sys, torch
from pathlib import Path
from stpd.policy.token_decision import TokenDecisionScorer
torch.set_num_threads(int(sys.argv[3]))
value = json.loads(Path(sys.argv[2]).read_text())
model = TokenDecisionScorer(Path(sys.argv[1]))
print(json.dumps(model.score_texts(value['state'], tuple(value['actions']))))
'''
    output = subprocess.check_output([sys.executable, "-c", scoring_program, str(destination),
                                      str(scoring_input), str(torch.get_num_threads())], text=True)
    assert tuple(json.loads(output)) == scores
    raw = (destination / "weights.safetensors").read_bytes()
    (destination / "weights.safetensors").write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(BoundaryError, match="digest_mismatch"):
        TokenDecisionScorer(destination)
