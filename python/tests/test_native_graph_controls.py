"""Four tiny native controls through the existing numerical and Agent owners."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import pytest
import torch
from test_native_structured_model import ack, offer, snapshot, state_metadata
from test_native_structured_model import agent_files as agent_files
from test_native_structured_model import (
    test_actual_stdio_agent_session_consume_ack_act_and_empty_c_await as stdio_loop,
)
from test_native_structured_training import data, origin
from test_structured_resume import Authority, PauseControl, equal_tree, finish

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.models.native_structured_scorer import NativeStructuredScorer
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_m2 import StructuredM2
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_graph_spec import (
    PRESETS,
    GraphSpec,
    NativeGraphControl,
    ResetSpec,
    checked_control,
)
from stpd.policy.native_agent import NativeStructuredAgent, bind_native_agent
from stpd.policy.native_structured_export import (
    GRAPH_STATE_FORMAT,
    GRAPH_TRAINED_PACKAGE_SCHEMA,
    encode_native_weights,
    export_native_model,
    load_native_package,
    require_native_model_package,
)
from stpd.policy.structured_export import export_structured_package
from stpd.structured_profiles import NATIVE_GRAPH_SCOPE, NATIVE_SCOPE
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.fixture(autouse=True)
def two_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def test_default_weight_bytes_and_common_parameter_initialization_remain_exact():
    model = StructuredM2(seed=0)
    assert len(model.state_dict()) == 43
    assert hashlib.sha256(encode_native_weights(model)).hexdigest() == (
        "18b863a7ec88ff5de0a35bd657ee9936d5a809a4bc29945d1cfc2abf9e7fe9b2"
    )
    rng = torch.get_rng_state().clone()
    for preset in PRESETS:
        controlled = StructuredM2(seed=0, model_control=preset)
        assert torch.equal(torch.get_rng_state(), rng)
        for key, tensor in model.state_dict().items():
            candidate = controlled.state_dict()[key]
            assert torch.equal(tensor, candidate[:1] if key == "write_query" else candidate)
    # The explicitly identified K1 carry control has exactly the default math.
    frame = data().runs[0].steps[0].frame
    controlled = StructuredM2(seed=0, model_control=PRESETS[0])
    entities = model.encode(frame)
    assert torch.equal(entities, controlled.encode(frame))
    old = model.initial_memory()
    actual = controlled.initial_memory()
    for _ in range(3):
        old = model.advance(entities, old)
        actual = controlled.advance(entities, actual)
        assert torch.equal(old, actual)
        assert torch.equal(model.score(frame, entities, old),
                           controlled.score(frame, entities, actual))


@pytest.mark.parametrize("control", PRESETS, ids=lambda value: value.id)
def test_each_control_resumes_exact_numerical_state_and_rejects_cross_control(control):
    config = StructuredTrainingConfig(epochs=2, max_updates=3)
    original = StructuredTrainingEngine(data(), config, code_scope=NATIVE_GRAPH_SCOPE,
                                        model_control=control)
    original.advance_chunk()
    raw = original.checkpoint()
    resumed = StructuredTrainingEngine(data(), config, code_scope=NATIVE_GRAPH_SCOPE,
                                       model_control=control)
    resumed.restore(raw)
    equal_tree(original.memory, resumed.memory)
    equal_tree(original.optimizer.state_dict(), resumed.optimizer.state_dict())
    assert original.memory.shape == (control.graph.slots, 96)
    assert finish(original) == finish(resumed)
    equal_tree(original.model.state_dict(), resumed.model.state_dict())
    equal_tree(original.optimizer.state_dict(), resumed.optimizer.state_dict())
    for other in PRESETS:
        if other == control:
            continue
        target = StructuredTrainingEngine(data(), config, code_scope=NATIVE_GRAPH_SCOPE,
                                          model_control=other)
        before = target.checkpoint()
        with pytest.raises(BoundaryError, match="exact_resume_identity_mismatch"):
            target.restore(raw)
        assert target.checkpoint() == before
    # Shape tampering is rejected before weights, optimizer, memory or RNG mutate.
    target = StructuredTrainingEngine(data(), config, code_scope=NATIVE_GRAPH_SCOPE,
                                      model_control=control)
    changed = decode_checkpoint(raw)
    changed["memory"] = torch.zeros((8 if control.graph.slots == 1 else 1, 96))
    before = target.checkpoint()
    with pytest.raises(BoundaryError, match="memory_tensor_mismatch"):
        target.restore(encode_checkpoint(changed))
    assert target.checkpoint() == before


@pytest.mark.parametrize("control", PRESETS, ids=lambda value: value.id)
def test_each_control_consumes_once_and_reset_only_precedes_actual_advance(control):
    model = StructuredM2(seed=0, model_control=control)
    scorer = NativeStructuredScorer(model, "1" * 64, "2" * 64)
    one, c1 = snapshot()
    two, c2 = snapshot(2)
    for index, (observation, catalog) in enumerate(((one, c1), (one, c1), (two, c2))):
        before = scorer.memory.clone()
        report = scorer.propose_consume(offer(observation, catalog, f"a-{index}",
                                             scorer.consumption_id))
        assert report["advanced"] == (index != 1)
        assert torch.equal(scorer.memory, before)  # Proposal never commits.
        scorer.acknowledge(ack(report, str(index + 1)))
        if index == 1:
            assert torch.equal(scorer.memory, before)
            scorer.scores()
            assert torch.equal(scorer.memory, before)
        if index == 2:
            fresh = NativeStructuredScorer(model, "1" * 64, "2" * 64)
            fresh_report = fresh.propose_consume(offer(two, c2))
            fresh.acknowledge(ack(fresh_report))
            if control.reset.mode == "reset_before_each_actual_advance":
                assert torch.equal(scorer.memory, fresh.memory)
            else:
                assert not torch.equal(scorer.memory, fresh.memory)
    assert scorer.state_version == 2
    state = scorer.state()
    fresh = NativeStructuredScorer(model, "1" * 64, "2" * 64)
    fresh.restore(state)
    assert torch.equal(fresh.memory, scorer.memory)
    assert fresh.scores() == scorer.scores()
    altered = copy.deepcopy(state)
    altered["model_control"] = PRESETS[(PRESETS.index(control) + 1) % 4].to_dict()
    fresh = NativeStructuredScorer(model, "1" * 64, "2" * 64)
    with pytest.raises(BoundaryError, match="state_control_binding"):
        fresh.restore(altered)
    assert fresh.unit is None


@pytest.mark.parametrize("control", PRESETS, ids=lambda value: value.id)
def test_each_control_store_resume_export_reconcile_stdio_and_opaque_binding(
    control, tmp_path, agent_files
):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    producer = origin()
    run = prepare_structured_workload(store, data(), producer,
        StructuredTrainingConfig(epochs=2, max_updates=3), operation_id="1" * 32,
        code_scope=NATIVE_GRAPH_SCOPE, model_control=control)
    request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                        "1" * 32, "2" * 32)
    paused = execute_structured_workload(store, reporter, request, producer,
        authority=Authority(), control=PauseControl(), attempt_producer=producer)
    assert paused.state == "paused" and paused.optimizer_updates == 1
    request = replace(request, attempt_id="3" * 32, mode="resume",
                      resume_checkpoint_id=paused.checkpoint_id)
    completed = execute_structured_workload(store, reporter, request, producer,
        authority=Authority(), attempt_producer=producer)
    assert completed.state == "completed" and completed.optimizer_updates == 3
    folder = tmp_path / "controlled-export"
    export_native_model(store, completed.model_id, folder)
    metadata, model = load_native_package(folder)
    require_native_model_package(store.get_manifest(completed.model_id), metadata)
    assert metadata["schema"] == GRAPH_TRAINED_PACKAGE_SCHEMA
    assert metadata["graph"]["id"] == control.id
    assert model.model_control == control
    assert model.initial_memory().shape == (control.graph.slots, 96)
    reconciled = execute_structured_workload(store, reporter,
        replace(request, mode="reconcile", resume_checkpoint_id=None), producer,
        authority=Authority(), attempt_producer=producer)
    assert reconciled.model_id == completed.model_id
    template = agent_files[3]
    path = tmp_path / "controlled-agent.json"
    manifest = bind_native_agent(folder, path, manifest_id="native-controlled-fixture",
        requirements=template["requirements"], support=template["support"],
        required_seams=template["input"]["attachment"]["required_seams"])
    stdio_loop((folder, path, metadata, manifest))
    agent = NativeStructuredAgent(folder, path)
    observation, catalog = snapshot()
    report = agent.consume(offer(observation, catalog))
    agent.scorer.acknowledge(ack(report))
    state_info = state_metadata(agent)
    state_info["state_format_version"] = GRAPH_STATE_FORMAT
    state = agent.export_state(state_info)
    resumed = NativeStructuredAgent(folder, path)
    resumed.restore_state(state_info, state)
    assert resumed.scorer.scores() == agent.scorer.scores()
    changed = copy.deepcopy(state_info)
    changed["model_bindings"][0]["model_id"] = "0" * 64
    fresh = NativeStructuredAgent(folder, path)
    with pytest.raises(BoundaryError, match="state_package_input_spec_binding"):
        fresh.restore_state(changed, state)
    assert fresh.scorer.unit is None


def test_untrusted_control_domain_and_legacy_export_fail_before_publication(tmp_path):
    for slots in (True, 2, 8.0):
        with pytest.raises(BoundaryError, match="unsupported_graph"):
            StructuredM2(model_control=NativeGraphControl(GraphSpec(slots)))
    with pytest.raises(BoundaryError, match="unsupported_reset"):
        StructuredM2(model_control=NativeGraphControl(reset=ResetSpec("every_read")))
    changed = PRESETS[0].to_dict()
    changed["graph"]["slots"] = True
    with pytest.raises(BoundaryError, match="unsupported_control"):
        checked_control(changed)
    with pytest.raises(BoundaryError, match="control_scope_mismatch"):
        StructuredTrainingEngine(data(), StructuredTrainingConfig(), code_scope=NATIVE_SCOPE,
                                  model_control=PRESETS[0])
    model = StructuredM2(model_control=PRESETS[0])
    with pytest.raises(BoundaryError, match="native_control_not_legacy"):
        export_structured_package(model, tmp_path / "legacy", source_revision="a" * 40,
            data_sha256="b" * 64, source_kind="synthetic", teacher_sha256="c" * 64, training={})
    assert not (tmp_path / "legacy").exists()
    # Native scope/control rejection precedes immutable Source/Run publication.
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    with pytest.raises(BoundaryError, match="control_scope_mismatch"):
        prepare_structured_workload(store, data(), origin(), StructuredTrainingConfig(),
            operation_id="1" * 32, code_scope=NATIVE_GRAPH_SCOPE)
    assert store.manifest_ids() == ()
