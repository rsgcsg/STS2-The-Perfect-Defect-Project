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


def test_trusted_targets_update_both_heads_and_report_action_nll_separately():
    populations = {name: replace(population(1, name), values=((1.0, 0.5),))
                   for name in ('train', 'dev', 'test')}
    vocab = fit_vocabulary(list(populations['train'].frames))
    trainer = MStdTrainer(populations, vocab, 'a' * 64,
                          MStdConfig(max_epochs=1, value_heads=True))
    before = {name: value.clone() for name, value in trainer.model.state_dict().items()}
    progress = trainer.advance()
    assert progress['mean_N_loss'] >= 0 and 0 < progress['auxiliary_loss'] < .1
    for name in ('win_head.weight', 'progress_head.weight'):
        assert not torch.equal(before[name], trainer.model.state_dict()[name])
    assert trainer.best_loss == progress['dev']['mean_N_loss']


def test_new_optional_config_keeps_legacy_wire_shape_and_rejects_invalid_flags():
    from spireagent.json_boundary import BoundaryError

    legacy = MStdConfig().to_dict()
    assert 'value_heads' not in legacy and 'model_size' not in legacy
    assert MStdConfig.decode(legacy) == MStdConfig()
    with pytest.raises(BoundaryError, match='boolean_value_heads_required'):
        MStdConfig(value_heads=1)
    with pytest.raises(BoundaryError, match='large_model_requires_value_protocol'):
        MStdConfig(model_size='large')


@pytest.mark.parametrize("abandoned", [False, True])
def test_native_abandonment_is_masked_through_original_journal_and_population(tmp_path, abandoned):
    from spireagent.json_boundary import FrozenObject
    from stpd.p1.dataset import SnapshotReader
    from stpd.p1.workload import populations_from_reader

    store = store_at(tmp_path / "store")
    projections = []
    for i in range(3):
        name = "source-" + str(i)
        _, original, value = projection_fixture(store, name)
        journal = [{"run_id": "run-0001", "kind": "run_ended_native",
                    "detail": "RunManager.OnEnded(isVictory=false)"}]
        if abandoned:
            journal.append({"run_id": "run-0001", "kind": "run_abandoned_native",
                            "detail": "RunManager.OnEnded observed IsAbandoned=true."})
        original = replace(original, parameters=FrozenObject.of({
            **original.parameters.value(), "run_journal": journal}))
        store.publish(original)
        projections.append(projection_fixture(store, name, value=value, original=original)[0])
    snapshot = explicit_snapshot(store, projections)
    populations = populations_from_reader(SnapshotReader(store, snapshot.artifact_id),
                                           input_projection=V2_INPUT_SPEC, value_heads=True)
    for pop in populations.values():
        assert pop.values[0][0] == (None if abandoned else 0.0)
        if abandoned:
            assert pop.values[0] == (None, None)


def test_register_rejects_descriptor_architecture_tampering(tmp_path):
    import json

    from stpd.p1.export import register

    vocab = fit_vocabulary([project(observation())])
    model = MStd(vocab, value_heads=True)
    checkpoint = tmp_path / "checkpoint"
    checkpoint.write_bytes(b"synthetic fixture")
    package = tmp_path / "package"
    export_model(model, checkpoint, package, {})
    path = package / "model.json"
    descriptor = json.loads(path.read_bytes())
    descriptor["architecture"]["id"] = "false-large-identity"
    path.write_text(json.dumps(descriptor))
    with pytest.raises(ValueError, match="descriptor differs"):
        register(package, tmp_path / "private", {})


@pytest.mark.parametrize("model_size", ["standard", "large"])
def test_completed_value_run_roundtrips_final_result_and_action_model(tmp_path, model_size):
    from stpd.workers.p1_run import materialize_p1_model, verify_p1_result

    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "source-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_p1_run(store, snapshot.artifact_id, PRODUCER,
                         MStdConfig(max_epochs=1, value_heads=True, model_size=model_size),
                         "c" * 32, input_projection=V2_INPUT_SPEC)
    workload = verify_p1_run(store, run.artifact_id, PRODUCER)
    receipt = dispatch(store, ComputeRequest("training", run.artifact_id, PRODUCER,
                                             "natural-complete"), PRODUCER)
    result, model, checkpoint = verify_p1_result(store, receipt.output_id, workload)
    assert checkpoint.state["phase"] == "completed"
    descriptor = materialize_p1_model(store, model.artifact_id, tmp_path / "export")
    loaded, payload = load_model(tmp_path / "export/model.tensor-tree",
                                 descriptor["package_sha256"])
    assert loaded.model_size == model_size and not loaded.value_heads
    original = checkpoint.state["best_model"]
    assert set(original) - set(payload["weights"]) == {
        "win_head.weight", "win_head.bias", "progress_head.weight", "progress_head.bias"}
    assert all(torch.equal(value, original[key]) for key, value in payload["weights"].items())


def test_local_application_rejects_remote_only_value_protocol():
    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.trusted_recipes import validate_recipe_config

    with pytest.raises(BoundaryError, match="value_heads_require_remote_training"):
        validate_recipe_config("p1-mstd-N-v1", MStdConfig(value_heads=True).to_dict())


@pytest.mark.parametrize("model_size", [None, [], {}])
def test_model_size_type_boundary_is_explicit(model_size):
    from spireagent.json_boundary import BoundaryError

    with pytest.raises(BoundaryError, match="unsupported_model_size"):
        MStdConfig(model_size=model_size)


@pytest.mark.parametrize("ascension", [0, 10])
def test_fixed_t2_manifest_uses_normal_suite_loader_and_rejects_changed_seeds(tmp_path, ascension):
    import hashlib
    import json

    from spireagent.json_boundary import BoundaryError
    from spireagent.workbench.reference_suite import load_manifest
    from stpd.evaluation.suites import t2_managed_manifest

    manifest = t2_managed_manifest("v0.111.0", ascension)
    path = tmp_path / "t2-manifest.json"
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    assert load_manifest(path, hashlib.sha256(raw).hexdigest())[1] == manifest
    manifest["seeds"][-1] = manifest["seeds"][0]
    changed = json.dumps(manifest).encode()
    path.write_bytes(changed)
    with pytest.raises(BoundaryError, match="manifest_configuration_changed"):
        load_manifest(path, hashlib.sha256(changed).hexdigest())
