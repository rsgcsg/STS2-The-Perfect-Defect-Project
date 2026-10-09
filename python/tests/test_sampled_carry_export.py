"""CPU2 fixture trainer/export conformance; no live or real-data training."""

from __future__ import annotations

import copy

import pytest
import torch
from test_native_structured_training import origin
from test_ordered_source import contract_bundle, original_basis
from test_ordered_source_training import source
from test_protocol_source import setup_store
from test_sampled_carry_source import projected, rows, set_basis
from test_structured_resume import Authority, equal_tree, finish

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_m2 import StructuredM2
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_code_scope import PATHS, TRAINING_PATHS_NATIVE
from stpd.native_graph_spec import GraphSpec, NativeGraphControl, ResetSpec
from stpd.native_sampled_carry_spec import (
    FEATURE_PROJECTION,
    INPUT_SPEC,
    RECIPE,
    STATE_FORMAT,
    VIEW,
    sampled_agent_spec,
)
from stpd.ordered_source_spec import SCOPE, recipe_control
from stpd.policy.native_structured_export import (
    GRAPH_WEIGHT_SCHEMA,
    _native_manifest,
    export_native_model,
    load_native_package,
    ordered_native_agent_spec,
)
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.fixture(autouse=True)
def cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.fixture
def completed(tmp_path):
    store, _ = setup_store(tmp_path)
    _, _, partition = source(store, view=VIEW)
    config = StructuredTrainingConfig(epochs=1, max_updates=1)
    control = recipe_control(RECIPE)
    producer = origin()
    run = prepare_structured_workload(
        store,
        partition.dataset,
        producer,
        config,
        source_id=partition.manifest.artifact_id,
        operation_id="1" * 32,
        code_scope=SCOPE,
        model_control=control,
    )
    result = execute_structured_workload(
        store,
        ObjectStoreRunReporter(store, store.blobs),
        StructuredWorkloadRequest(
            run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
        ),
        producer,
        authority=Authority(),
        attempt_producer=producer,
    )
    assert result.state == "completed" and result.optimizer_updates == 1
    folder = tmp_path / "sampled-package"
    export_native_model(store, result.model_id, folder)
    package, model = load_native_package(folder)
    return store, partition, result, folder, package, model


def test_existing_fixture_engine_and_export_select_exact_shared_contract(completed):
    _, partition, result, folder, package, model = completed
    assert package["input_spec"] == INPUT_SPEC
    assert package["projection"] == FEATURE_PROJECTION
    assert package["agent_spec"] == sampled_agent_spec(recipe_control(RECIPE))
    assert package["state_format_version"] == STATE_FORMAT
    assert package["source"]["source_artifact_id"] == partition.manifest.artifact_id
    assert package["provenance"]["checkpoint_id"] == result.checkpoint_id
    assert package["qualification"] == "source3_decision_sample_carry_N_reexpression_only"
    assert "stpd/native_sampled_carry_spec.py" in PATHS
    assert "stpd/native_sampled_carry_spec.py" in TRAINING_PATHS_NATIVE
    weights = decode_checkpoint((folder / "weights.tensor-tree").read_bytes())
    assert weights["schema"] == GRAPH_WEIGHT_SCHEMA
    assert weights["projection"] == FEATURE_PROJECTION
    assert model.model_control == recipe_control(RECIPE)


@pytest.mark.parametrize("field", ["input_spec", "agent_spec", "state_format_version"])
def test_rehashed_old_package_contract_cannot_impersonate_sampled(completed, field):
    _, _, _, _, package, _ = completed
    from stpd.canonical import semantic_hash
    from stpd.fullrun.native_structured_inputs import INPUT_SPEC as OLD_INPUT
    from stpd.policy.native_structured_export import GRAPH_STATE_FORMAT

    forged = copy.deepcopy(package)
    forged[field] = {
        "input_spec": OLD_INPUT,
        "agent_spec": ordered_native_agent_spec(recipe_control(RECIPE)),
        "state_format_version": GRAPH_STATE_FORMAT,
    }[field]
    forged["model_id"] = semantic_hash({k: v for k, v in forged.items() if k != "model_id"})
    with pytest.raises(BoundaryError, match="package_identity_mismatch"):
        _native_manifest(json_bytes(forged))


@pytest.mark.parametrize(
    "control",
    [
        NativeGraphControl(GraphSpec(8)),
        NativeGraphControl(reset=ResetSpec("reset_before_each_actual_advance")),
    ],
)
def test_sampled_package_builder_rejects_other_existing_graph_controls(control):
    with pytest.raises(BoundaryError, match="k1d96_carry_required"):
        ordered_native_agent_spec(control, view=VIEW)


def test_unlabelled_sample_changes_carry_and_explicit_cut_restarts_W0(tmp_path, monkeypatch):
    bundle = contract_bundle(tmp_path)
    template = rows(bundle)[0]
    inputs = []
    for ordinal in range(1, 6):
        row = copy.deepcopy(template)
        row.update(sequence=ordinal, input_id=f"input-{ordinal}", input_prefix_ordinal=str(ordinal))
        observation, catalog, _, _ = original_basis(tmp_path, ordinal + 1)
        set_basis(tmp_path, row, observation, catalog)
        if ordinal < 5:
            row["outcome"].update(mapping_status="unmapped", selected_action=None, match_count=0)
        inputs.append(row)
    bundle.inputs = tuple(inputs)
    bundle.final_input_prefix_ordinal = "5"
    bundle.boundaries[0]["after_input_ordinal"] = "5"
    _, _, dataset = projected(bundle)
    model = StructuredM2(seed=0, model_control=recipe_control(RECIPE))
    with torch.no_grad():
        frames = [step.frame for step in dataset.runs[0].steps]
        w0 = model.initial_memory()
        context_memory = w0
        for frame in frames[:4]:
            context_memory = model.advance(model.encode(frame), context_memory)
        carried = model.advance(model.encode(frames[4]), context_memory)
        isolated = model.advance(model.encode(frames[4]), w0)
        assert not torch.equal(carried, isolated)
    config = StructuredTrainingConfig(epochs=1, max_updates=1)
    engine = StructuredTrainingEngine(
        dataset, config, code_scope=SCOPE, model_control=recipe_control(RECIPE)
    )
    first_chunk = engine.advance_chunk()
    assert first_chunk.optimizer_updates == 0
    assert engine.memory is not None
    equal_tree(context_memory, engine.memory)
    inputs[3]["outcome"]["delivery"] = "unknown"
    _, _, cut_dataset = projected(bundle)
    assert cut_dataset.runs[0].steps[4].reset_before
    cut_engine = StructuredTrainingEngine(
        cut_dataset, config, code_scope=SCOPE, model_control=recipe_control(RECIPE)
    )
    original_score = cut_engine.model.score
    scored_memory = []

    def score(frame, entities, memory):
        scored_memory.append(memory.detach().clone())
        return original_score(frame, entities, memory)

    monkeypatch.setattr(cut_engine.model, "score", score)
    finish(cut_engine)
    # Explicit cuts are handled by the existing engine, never a second trainer.
    assert cut_engine.updates == 1
    equal_tree(isolated, scored_memory[0])
