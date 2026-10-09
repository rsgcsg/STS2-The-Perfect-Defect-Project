"""Bounded synthetic CPU2 common-source engine/export/native constructor checks."""

from __future__ import annotations

import copy
import json

import pytest
import torch
from test_native_agent_sampled_source import ROOT, SHARED, publish
from test_native_agent_sampled_source import original as original
from test_native_structured_training import data as synthetic_data
from test_native_structured_training import origin
from test_native_training_source_v2 import source3
from test_protocol_source import setup_store
from test_structured_resume import Authority

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.canonical import semantic_hash
from stpd.models.structured_engine import execution_identity
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_graph_spec import TRAINING_SCOPE, NativeGraphControl
from stpd.native_sampled_carry_spec import INPUT_SPEC, sampled_agent_spec
from stpd.native_training_source_spec import (
    DIRECT_PROFILE,
    MODEL_SCHEMA,
    PACKAGE_SCHEMA,
    SOURCE3_PROFILE,
)
from stpd.policy.native_agent import NativeStructuredAgent, bind_native_agent
from stpd.policy.native_structured_export import (
    _native_manifest,
    export_native_model,
    load_native_package,
    require_native_model_package,
)
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


@pytest.fixture(params=["direct", "source3"])
def completed(tmp_path, original, request):
    store, _ = setup_store(tmp_path)
    partition = publish(store, original)[2] if request.param == "direct" else source3(store)
    config = StructuredTrainingConfig(epochs=1, max_updates=1)
    producer = origin()
    run = prepare_structured_workload(
        store,
        partition.dataset,
        producer,
        config,
        source_id=partition.manifest.artifact_id,
        operation_id="1" * 32,
        code_scope=TRAINING_SCOPE,
        model_control=NativeGraphControl(),
    )
    identity = run.parameters.value()["execution_identity"]
    assert identity["source_schema"] == json.loads(partition.dataset.source_bytes)["schema"]
    assert identity["input_spec"] == INPUT_SPEC
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
    path = tmp_path / "common-native"
    export_native_model(store, result.model_id, path)
    package, model = load_native_package(path)
    assert package["schema"] == PACKAGE_SCHEMA
    assert package["agent_spec"] == sampled_agent_spec(NativeGraphControl())
    assert package["source"]["source_profile"] == (
        DIRECT_PROFILE if request.param == "direct" else SOURCE3_PROFILE
    )
    artifact = store.get_manifest(result.model_id)
    assert artifact.parameters.value()["schema"] == MODEL_SCHEMA
    require_native_model_package(artifact, package)
    manifest = tmp_path / "agent.json"
    seams = json.loads(
        (
            ROOT / "components/connector/contracts/native-logical-publication-profile-v1.json"
        ).read_bytes()
    )["required_seams"]
    bind_native_agent(
        path,
        manifest,
        manifest_id="synthetic-common-native-source-v2",
        requirements=copy.deepcopy(SHARED["manifest"]["requirements"]),
        support=copy.deepcopy(SHARED["manifest"]["support"]),
        required_seams=seams,
    )
    agent = NativeStructuredAgent(path, manifest)
    assert (
        agent.metadata["input_spec"] == INPUT_SPEC
        and agent.metadata["agent_spec"]["version"] == "1.1.0"
    )
    assert model.model_control == NativeGraphControl()
    return package, store, partition, result


def test_common_engine_export_and_fresh_constructor(completed):
    package, _, partition, result = completed
    assert package["source"]["source_artifact_id"] == partition.manifest.artifact_id
    assert package["provenance"]["checkpoint_id"] == result.checkpoint_id


def test_wrong_rehashed_source_discriminant_cannot_load(completed):
    package, _, _, _ = completed
    wrong = copy.deepcopy(package)
    wrong["source"]["source_profile"] = (
        SOURCE3_PROFILE if package["source"]["source_profile"] == DIRECT_PROFILE else DIRECT_PROFILE
    )
    wrong["model_id"] = semantic_hash(
        {key: value for key, value in wrong.items() if key != "model_id"}
    )
    with pytest.raises(BoundaryError):
        _native_manifest(json_bytes(wrong))


def test_synthetic_graph_execution_schema_default_remains_accurate():
    from stpd.structured_profiles import NATIVE_SOURCE_SCHEMA

    config = StructuredTrainingConfig(epochs=1, max_updates=1)
    identity = execution_identity(
        synthetic_data(), config, code_scope=TRAINING_SCOPE, model_control=NativeGraphControl()
    )
    assert identity["source_schema"] == NATIVE_SOURCE_SCHEMA
