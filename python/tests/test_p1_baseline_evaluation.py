"""Actual baseline producers reach the ordinary sealed-TEST summary consumer."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, build_opener

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.artifact_contracts import Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server
from spireagent.workbench.local_evaluation import summary
from stpd.p1.baseline_spec import BaselineConfig
from stpd.p1.baseline_worker import execute_baseline_run
from stpd.p1.baseline_workload import prepare_baseline_run


@pytest.fixture(scope="module", params=["random", "frequency", "linear"])
def recorded(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("baseline-evaluation-" + request.param)
    store = store_at(root / "store")
    projections = [projection_fixture(store, "summary-" + str(i))[0] for i in range(3)]
    # A source publisher need not be the later model trainer.
    original = explicit_snapshot(store, projections)
    snapshot = replace(original, producer=replace(PRODUCER, source_revision="d" * 40))
    store.publish(snapshot)
    run = prepare_baseline_run(store, snapshot.artifact_id, PRODUCER,
                               BaselineConfig(algorithm=request.param, max_epochs=1), "a" * 32)
    result = execute_baseline_run(store, ObjectStoreRunReporter(store, store.blobs),
                                  run.artifact_id, PRODUCER, attempt_id="summary")
    report = store.get_manifest(store.get_manifest(result.result_id).parent("evaluation"))
    return root, store, report


def test_real_producer_summary_reads_only_metadata_and_http_detail(recorded, monkeypatch):
    root, store, report = recorded
    model = store.get_manifest(report.parent("model"))
    invalid_model = replace(model, parameters=FrozenObject.of({
        **model.parameters.value(), "algorithm": []}))
    store.publish(invalid_model)
    invalid_report = replace(report, parents=(Parent("model", invalid_model.artifact_id),))
    store.publish(invalid_report)

    def no_payload(*_):
        pytest.fail("recorded DEV summary must not decode payloads or sealed TEST")

    monkeypatch.setattr(ManifestArtifactStore, "read_payload", no_payload)
    value = summary(store, report.artifact_id)
    assert report.parameters.value()["test"]["N"] > 0
    assert value["partition"] == "dev"
    assert value["dev"] == report.parameters.value()["dev"]
    assert value["decision_count"] == value["dev"]["N"]
    assert value["test_metrics"] == "sealed_not_exposed"
    assert "test" not in value and "rows" not in value
    registry = root / "registry.sqlite"
    SQLiteRegistry(registry)
    config = ProjectConfig(root / "state", "", "", None, combination(),
                           LocalResearchWorkspaceConfig(store.blobs.root, registry))
    app = Application(config)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    url = f"http://127.0.0.1:{server.server_port}"
    # The new invalid immutable records are deliberate negative inputs.
    before = (store.manifest_ids(), registry.read_bytes())
    try:
        client.open(url + "/").close()
        route = url + "/api/local-workspace/evaluations/"
        with client.open(route + report.artifact_id) as response:
            actual = json.load(response)
        assert actual == value
        with pytest.raises(HTTPError) as rejected:
            client.open(url + "/api/local-workspace/evaluations/" + invalid_report.artifact_id)
        assert rejected.value.code == 409
        assert json.load(rejected.value)["error"] == "invalid_p1_baseline_model_parent"
        assert (store.manifest_ids(), registry.read_bytes()) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


@pytest.mark.parametrize("name,value", [
    ("N", True), ("N", 0), ("N", "1"),
    ("mean_score_softmax_N_loss", None), ("mean_score_softmax_N_loss", True),
    ("mean_score_softmax_N_loss", -1), ("mean_score_softmax_N_loss", 10**400),
    ("expected_greedy_correct", True), ("expected_greedy_correct", -1),
    ("expected_greedy_correct", 10**400), ("tie_semantics", "first maximum"),
])
def test_invalid_dev_counts_types_ranges_and_ties_reject(recorded, name, value):
    _, store, report = recorded
    info = report.parameters.value()
    changed = replace(report, parameters=FrozenObject.of({
        **info, "dev": {**info["dev"], name: value}}))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_dev"):
        summary(store, changed.artifact_id)


def test_wrong_model_parent_and_training_identity_reject(recorded):
    _, store, report = recorded
    model = store.get_manifest(report.parent("model"))
    training = store.get_manifest(model.parent("training_input"))
    wrong = replace(report, parents=(Parent("model", training.artifact_id),))
    store.publish(wrong)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_model_parent"):
        summary(store, wrong.artifact_id)
    changed_model = replace(model, producer=replace(model.producer, source_revision="f" * 40))
    store.publish(changed_model)
    changed = replace(report, parents=(Parent("model", changed_model.artifact_id),))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_model_parent"):
        summary(store, changed.artifact_id)


@pytest.mark.parametrize("algorithm", [[], {}, True, None])
def test_nonstring_algorithm_is_a_typed_rejection(recorded, algorithm):
    _, store, report = recorded
    model = store.get_manifest(report.parent("model"))
    changed_model = replace(model, parameters=FrozenObject.of({
        **model.parameters.value(), "algorithm": algorithm}))
    store.publish(changed_model)
    changed = replace(report, parents=(Parent("model", changed_model.artifact_id),))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_model_parent"):
        summary(store, changed.artifact_id)


@pytest.mark.parametrize("field,value", [("partition_N", {"dev": True}),
                                           ("partition_N", {"dev": 999}),
                                           ("snapshot_selection_sha256", "f" * 64)])
def test_forged_training_source_binding_rejects(recorded, field, value):
    _, store, report = recorded
    model = store.get_manifest(report.parent("model"))
    training = store.get_manifest(model.parent("training_input"))
    run = store.get_manifest(model.parent("run"))
    changed_training = replace(training, parameters=FrozenObject.of({
        **training.parameters.value(), field: value}))
    store.publish(changed_training)
    changed_run = replace(run, parents=tuple(
        Parent(p.role, changed_training.artifact_id) if p.role == "training_input" else p
        for p in run.parents))
    store.publish(changed_run)
    changed_model = replace(model, parents=tuple(
        Parent(p.role, changed_training.artifact_id) if p.role == "training_input" else
        Parent(p.role, changed_run.artifact_id) if p.role == "run" else p for p in model.parents))
    store.publish(changed_model)
    changed = replace(report, parents=(Parent("model", changed_model.artifact_id),))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_(input|source)_parent"):
        summary(store, changed.artifact_id)


def test_test_metadata_stays_sealed_and_model_selection_is_typed(recorded):
    _, store, report = recorded
    info = report.parameters.value()
    changed = replace(report, parameters=FrozenObject.of({
        **info, "test": {"sealed_marker": "private"}}))
    store.publish(changed)
    value = summary(store, changed.artifact_id)
    assert "private" not in json.dumps(value) and "test" not in value
    changed = replace(report, parameters=FrozenObject.of({**info, "model_selection": "test_best"}))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="invalid_p1_baseline_model_parent"):
        summary(store, changed.artifact_id)
