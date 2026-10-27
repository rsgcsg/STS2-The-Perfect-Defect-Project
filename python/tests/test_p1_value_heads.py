"""T2 masks, faithful worker checkpoint and inference stripping regressions."""
from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_p1_formal_training import population
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at
from test_p1_t0 import observation

from stpd.cloud_jobs.contracts import ComputeRequest
from stpd.cloud_jobs.execution import dispatch
from stpd.p1.export import export_model, load_model
from stpd.p1.formal_training import MStdTrainer
from stpd.p1.input import V2_INPUT_SPEC, fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.p1.spec import MStdConfig
from stpd.p1.value_targets import VALUE_TARGET_SPEC, from_run_facts
from stpd.p1.workload import prepare_p1_run, verify_p1_run
from stpd.workers.p1_run import verify_p1_checkpoint


@pytest.mark.parametrize('facts', [
    {'result': None, 'native_end': False},
    {'result': 'loss', 'native_end': False},
    {'result': 'loss', 'native_end': True, 'technical_failure': True},
    {'result': 'win', 'native_end': True, 'fact_conflict': True},
])
def test_unknown_stopped_and_technical_runs_are_masked(facts):
    assert from_run_facts(facts) == (None, None)


def test_fixed_progress_denominator_does_not_fit_recorded_maximum():
    facts = {'result': 'loss', 'native_end': True, 'ascension': 10, 'floor_reached': 17}
    assert from_run_facts(facts) == (0, 17 / 49)
    assert from_run_facts({**facts, 'floor_reached': 33}) == (0, 33 / 49)
    assert from_run_facts({**facts, 'result': 'win'}) == (1, 1)


@pytest.mark.parametrize("model_size", ["standard", "large"])
def test_strip_value_heads_preserves_all_action_scores(tmp_path, model_size):
    torch.set_num_threads(2)
    frame = project(observation())
    vocab = fit_vocabulary([frame])
    model = MStd(vocab, value_heads=True, model_size=model_size).eval()
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.write_bytes(b'engineering fixture')
    descriptor = export_model(model, checkpoint, tmp_path / 'model', {'fixture': True})
    restored, payload = load_model(tmp_path / 'model/model.tensor-tree',
                                   descriptor['package_sha256'])
    assert not restored.value_heads
    assert all(not k.startswith(('win_head.', 'progress_head.')) for k in payload['weights'])
    assert sum(x.numel() for x in model.parameters()) - descriptor['parameters'] == 386
    with torch.inference_mode():
        assert torch.equal(model(frame), restored(frame))
        assert torch.equal(model.forward_with_values([frame])[0][0], restored(frame))


def test_value_checkpoint_resume_and_masked_heads_do_not_update():
    populations = {name: replace(population(1, name), values=((None, None),))
                   for name in ('train', 'dev', 'test')}
    vocab = fit_vocabulary(list(populations['train'].frames))
    config = MStdConfig(max_epochs=2, value_heads=True)
    trainer = MStdTrainer(populations, vocab, 'a' * 64, config)
    before = {k: v.clone() for k, v in trainer.model.state_dict().items()
              if k.startswith(('win_head.', 'progress_head.'))}
    trainer.advance()
    assert all(torch.equal(v, before[k])
               for k, v in trainer.model.state_dict().items() if k in before)
    raw = trainer.checkpoint()
    restored = MStdTrainer(populations, vocab, 'a' * 64, config)
    restored.restore(raw)
    trainer.advance()
    restored.advance()
    assert all(torch.equal(v, restored.model.state_dict()[k])
               for k, v in trainer.model.state_dict().items())
    assert trainer.identity['target_spec'] == VALUE_TARGET_SPEC
    assert trainer.identity['architecture']['id'] == 'q3-m-std-value-2'


def test_normal_preparation_worker_and_checkpoint_use_value_protocol(tmp_path):
    store = store_at(tmp_path / 'store')
    projections = [projection_fixture(store, 'source-' + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER,
                         MStdConfig(max_epochs=1, value_heads=True), 'b' * 32,
                         input_projection=V2_INPUT_SPEC)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    result = dispatch(store, ComputeRequest('training', run.artifact_id, PRODUCER,
                                           'value-one-step', stop_after=1), PRODUCER)
    checkpoint = verify_p1_checkpoint(store, result.checkpoint_id, workload)
    assert checkpoint.state['schema'] == 'stpd/p1-mstd-formal-checkpoint-2'
    assert 'win_head.weight' in checkpoint.state['model']
    assert checkpoint.state['identity']['target_spec'] == VALUE_TARGET_SPEC


def test_fixed_t2_seeds_preserve_old_prefix_and_are_reserved():
    from stpd.evaluation.suites import (
        checkpoint_reference_manifest,
        require_non_evaluation_seed,
        t2_managed_manifest,
    )

    for level, size, prefix in ((0, 40, 10), (10, 10, 2)):
        current = t2_managed_manifest("v0.111.0", level)
        assert len(set(current["seeds"])) == size
        assert current["seeds"][:prefix] == checkpoint_reference_manifest(
            "v0.111.0", "Defect", level)["seeds"]
        with pytest.raises(ValueError, match="reserved"):
            require_non_evaluation_seed(current["seeds"][-1])


def test_direct_training_refuses_reserved_t2_seed(tmp_path):
    from spireagent.json_boundary import BoundaryError
    from stpd.evaluation.suites import t2_managed_manifest

    store = store_at(tmp_path / "store")
    seed = t2_managed_manifest("v0.111.0")["seeds"][-1]
    projections = [projection_fixture(store, "source-" + str(i),
                                      seed=seed if i == 0 else None)[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    with pytest.raises(BoundaryError, match="evaluation_seed_reserved"):
        prepare_p1_run(store, snapshot.artifact_id, PRODUCER,
                       MStdConfig(max_epochs=1), "e" * 32)
