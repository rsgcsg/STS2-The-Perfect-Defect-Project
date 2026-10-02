"""A single offline report is verified without exposing decision-level rows."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, build_opener

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_decision_policy import identity as decision_identity
from test_decision_training import prepared as decision_prepared
from test_fullrun_worker import training
from test_stage1a_training import token_inputs

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server
from spireagent.workbench.local_evaluation import SCHEMA, summary
from stpd.fullrun.decision_training import (
    AllocationSpec,
    publish_allocation,
    publish_decision_view,
)
from stpd.fullrun.features import compile_features
from stpd.fullrun.representation import FullRunSerializer
from stpd.qwen.fake_backend import DeterministicFakeQwenBackend
from stpd.workers.contracts import TrainingConfig, prepare_run, prepare_training_input
from stpd.workers.token_ranking import TokenConfig
from stpd.workers.token_worker import execute_tokens, prepare_token_run
from stpd.workers.worker import execute


def _token(tmp_path: Path) -> tuple[object, str]:
    torch.set_num_threads(1)
    owner, inputs = token_inputs(tmp_path)
    config = TokenConfig.text_menu_small_b(
        steps=2, width=16, layers=2, heads=2, feedforward=32, device="cpu"
    )
    runtime = replace(owner.producer, source_revision="c" * 40)
    assert runtime != owner.producer
    run = prepare_token_run(owner.store, inputs, config, runtime)
    result = execute_tokens(owner.store, ObjectStoreRunReporter(owner.store, owner.store.blobs),
                            run.artifact_id, runtime)
    evaluation = owner.store.get_manifest(result.result_id).parent("offline_evaluation")
    return owner.store, evaluation


def test_human_report_keeps_session_groups_unknown_and_rejects_claimed_independence(
    tmp_path: Path,
) -> None:
    from test_text_menu_human_import import _declared_bundle

    from spireagent.json_boundary import json_bytes
    from stpd.fullrun.text_menu_human_import import (
        publish_human_text_bc_view,
        publish_human_text_source,
        publish_verified_human_text_bundle,
    )
    from stpd.fullrun.token_inputs import load_token_inputs, publish_token_inputs

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    identities = []
    for name, page in (("session-a", "same-page"), ("session-b", "same-page"),
                       ("session-z", "distinct-page")):
        bundle, _, _ = _declared_bundle(
            tmp_path / name, "begin_card_play_exact_factory_return", "begin_card_play",
            session_id=name, page_name=page)
        identities.append(publish_verified_human_text_bundle(
            store, bundle, PRODUCER).artifact_id)
    source = publish_human_text_source(store, tuple(identities), PRODUCER)
    view = publish_human_text_bc_view(store, source.artifact_id, PRODUCER)
    tokens = publish_token_inputs(store, view.artifact_id, "s", PRODUCER, max_tokens=4096)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        config = TokenConfig.text_menu_small_b(
            steps=1, width=16, layers=1, heads=2, feedforward=32, max_tokens=4096)
        run = prepare_token_run(store, load_token_inputs(store, tokens.artifact_id),
                                config, PRODUCER)
        completed = execute_tokens(store, ObjectStoreRunReporter(store, store.blobs),
                                   run.artifact_id, PRODUCER)
    finally:
        torch.set_num_threads(previous_threads)
    report = store.get_manifest(store.get_manifest(completed.result_id).parent(
        "offline_evaluation"))
    value = summary(store, report.artifact_id)
    assert value["reported_run_groups"] == 2
    assert value["grouping"] == "session_scoped_run_group"
    assert value["native_run_independence"] == "unknown_across_sessions"
    assert value["decision_count"] == 2
    assert set(value["baselines"]) == {"uniform_legal", "action_only"}
    assert "percentile_95" not in json.dumps(value) and "rows" not in value
    # A valid second view must not relabel the model/report while the real
    # training input still names the first view. All other identities agree.
    other_view = publish_human_text_bc_view(
        store, source.artifact_id, replace(PRODUCER, source_revision="d" * 40))
    assert other_view.artifact_id != view.artifact_id
    model = store.get_manifest(report.parent("model"))
    rebound_model = replace(model, parents=tuple(
        replace(parent, artifact_id=other_view.artifact_id)
        if parent.role == "model_view" else parent for parent in model.parents
    ))
    store.publish(rebound_model)
    rebound_report = replace(report, parents=tuple(
        replace(parent, artifact_id=(other_view.artifact_id if parent.role == "model_view"
                                     else rebound_model.artifact_id))
        for parent in report.parents
    ))
    store.publish(rebound_report)
    with pytest.raises(BoundaryError, match="invalid_report_parentage"):
        summary(store, rebound_report.artifact_id)
    original = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
    for location in ("model", "uniform_legal", "action_only"):
        metrics = json.loads(json.dumps(original))
        selected = metrics["summary"] if location == "model" else metrics["baselines"][location]
        selected["bootstrap"] = {"status": "computed", "unit": "whole_run", "runs": 2}
        invalid = replace(report, payloads=(store.put_bytes(
            "metrics", json_bytes(metrics), "application/json"),))
        store.publish(invalid)
        with pytest.raises(BoundaryError, match="invalid_report_summary"):
            summary(store, invalid.artifact_id)


def test_token_single_summary_keeps_exact_parent_ids_and_never_returns_rows(
    tmp_path: Path, monkeypatch,
) -> None:
    store, identity = _token(tmp_path)
    roles = []
    original_read = store.read_payload

    def observed(payload):
        roles.append(payload.role)
        yield from original_read(payload)

    monkeypatch.setattr(store, "read_payload", observed)
    result = summary(store, identity)
    assert roles == ["metrics"]
    assert result["schema"] == SCHEMA
    assert result["validation_scope"] == "recorded_report_and_parent_identities"
    assert result["evaluation_schema"] == "stpd/stage1a-ranking-evaluation-v1"
    assert result["partition"] == "dev"
    assert result["model_recipe"] == "stage1a.b.s.v2"
    assert result["qualification"] == "engineering_only"
    assert result["decision_count"] >= 1
    assert set(result["baselines"]) == {"uniform_legal", "action_only"}
    encoded = json.dumps(result)
    private_row_keys = {
        "rows", "transition_id", "run_id", "state_text", "action_texts",
        "action_keys", "chosen_index", "target_index", "label", "labels",
    }

    def summary_keys(value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from summary_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from summary_keys(child)

    assert private_row_keys.isdisjoint(summary_keys(result))
    report = store.get_manifest(identity)
    metrics = json.loads(b"".join(original_read(report.payload("metrics"))))
    from stpd.fullrun.features import load_model_view

    model = store.get_manifest(report.parent("model"))
    _, samples = load_model_view(store, model.parent("model_view"))
    dev_samples = [sample for sample in samples if sample.split == "dev"]
    assert len(dev_samples) == len(metrics["rows"]) == result["decision_count"]
    private_values = {
        row[field] for row in metrics["rows"] for field in ("transition_id", "run_id")
    }
    for sample in dev_samples:
        private_values.update((sample.state_text, *sample.action_texts, *sample.action_keys,
                               sample.action_keys[sample.chosen_index]))
    assert all(value not in encoded for value in private_values if value)
    assert result["dev_qualification"]["reported_restrictions"]["qualified_run_ids"] == {
        "status": "unreported", "claims": [],
    }
    copied = ManifestArtifactStore(LocalBlobStore(tmp_path / "copy"))
    assert copy_artifact(store, copied, identity) == identity
    assert not (copied.blobs.root / "run-completions").exists()
    assert summary(copied, identity) == result

    original = store.get_manifest(identity)
    fake = replace(original, parameters=FrozenObject.of({**original.parameters.value(),
                                                          "partition": "test"}))
    store.publish(fake)
    with pytest.raises(BoundaryError, match="sealed_test_evaluation"):
        summary(store, fake.artifact_id)
    swapped = replace(original, parents=tuple(
        replace(parent, artifact_id=original.parent("model"))
        if parent.role == "model_view" else parent
        for parent in original.parents
    ))
    store.publish(swapped)
    with pytest.raises(BoundaryError):
        summary(store, swapped.artifact_id)
    metrics = original.payload("metrics")

    def overflow(payload):
        raw = b"".join(original_read(payload))
        if payload == metrics:
            value = json.loads(raw)
            value["rows"][0]["nll"] = float("inf")
            raw = json.dumps(value, separators=(",", ":")).replace("Infinity", "1e999").encode()
        yield raw

    monkeypatch.setattr(store, "read_payload", overflow)
    with pytest.raises(BoundaryError, match="nonfinite_report_metric"):
        summary(store, identity)

    def malformed(payload):
        raw = b"".join(original_read(payload))
        if payload == metrics:
            value = json.loads(raw)
            value["rows"] = ["not-a-row"] * len(value["rows"])
            raw = json.dumps(value).encode()
        yield raw

    monkeypatch.setattr(store, "read_payload", malformed)
    with pytest.raises(BoundaryError, match="invalid_report_structure"):
        summary(store, identity)

    def enormous(payload):
        raw = b"".join(original_read(payload))
        if payload == metrics:
            value = json.loads(raw)
            value["summary"]["overall"]["nll"] = 10**400
            raw = json.dumps(value).encode()
        yield raw

    monkeypatch.setattr(store, "read_payload", enormous)
    with pytest.raises(BoundaryError, match="invalid_report_summary"):
        summary(store, identity)
    monkeypatch.setattr(store, "read_payload", observed)
    index = store.blobs.root / "payload-indexes" / "v1" / f"{metrics.sha256}.json"
    index.unlink()
    with pytest.raises(BoundaryError):
        summary(store, identity)


def test_legacy_dev_reads_only_recorded_summary_and_test_is_sealed(
    tmp_path: Path, monkeypatch,
) -> None:
    store, reporter, envelope, _ = training(tmp_path)
    _, run = prepare_run(store, envelope.artifact_id, PRODUCER)
    result = execute(store, reporter, run.artifact_id, PRODUCER)
    identity = store.get_manifest(result.result_id).parent("offline_evaluation")
    roles = []
    original_read = store.read_payload

    def observed(payload):
        roles.append(payload.role)
        yield from original_read(payload)

    monkeypatch.setattr(store, "read_payload", observed)
    value = summary(store, identity)
    assert roles == ["summary"]
    assert value["validation_scope"] == "recorded_report_and_parent_identities"
    assert value["evaluation_schema"] == "stpd/offline-ranking-evaluation-v1"
    assert value["partition"] == "dev" and value["decision_count"] > 0
    assert value["scientific_verdict"] == "not_claimed"
    assert "rows" not in value
    original = store.get_manifest(identity)
    sealed = replace(original, parameters=FrozenObject.of({**original.parameters.value(),
                                                            "partition": "test"}))
    store.publish(sealed)
    with pytest.raises(BoundaryError, match="sealed_test_evaluation"):
        summary(store, sealed.artifact_id)
    unknown = replace(original, parameters=FrozenObject.of({**original.parameters.value(),
                                                             "schema": "unknown"}))
    store.publish(unknown)
    with pytest.raises(BoundaryError, match="unsupported_evaluation"):
        summary(store, unknown.artifact_id)


def test_decision_view_worker_report_is_supported_without_loading_lineage(
    tmp_path: Path, monkeypatch,
) -> None:
    owner, dataset = decision_prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(), owner.producer)
    view = publish_decision_view(
        owner.store, allocation.artifact_id, FullRunSerializer(), owner.producer
    )
    backend = DeterministicFakeQwenBackend(1024)
    backend.identity = decision_identity()
    features = compile_features(owner.store, view.artifact_id, backend, owner.producer)
    input_manifest = prepare_training_input(
        owner.store, features.artifact_id, owner.producer,
        TrainingConfig(max_steps=2, seed=1701),
    )
    _, run = prepare_run(owner.store, input_manifest.artifact_id, owner.producer)
    reporter = ObjectStoreRunReporter(owner.store, owner.store.blobs)
    result = execute(owner.store, reporter, run.artifact_id, owner.producer)
    evaluation_id = owner.store.get_manifest(result.result_id).parent("offline_evaluation")
    roles = []
    original_read = owner.store.read_payload

    def observed(payload):
        roles.append(payload.role)
        yield from original_read(payload)

    monkeypatch.setattr(owner.store, "read_payload", observed)
    value = summary(owner.store, evaluation_id)
    assert roles == ["summary"]
    assert value["evaluation_schema"] == "stpd/offline-ranking-evaluation-v1"
    assert value["view_schema"] == "stpd/decision-model-view-v1"
    assert value["validation_scope"] == "recorded_report_and_parent_identities"
    assert value["decision_count"] > 0
    # The shared view loader accepts public BC, but this FullRun worker does not.
    # Keep every other parent relationship valid to isolate the view admission.
    evaluation = owner.store.get_manifest(evaluation_id)
    model = owner.store.get_manifest(evaluation.parent("model"))
    unsupported = replace(view, parameters=FrozenObject.of({
        **view.parameters.value(), "schema": "stpd/public-observation-bc-view-v2",
    }))
    owner.store.publish(unsupported)
    rebound_model = replace(model, parents=tuple(
        replace(parent, artifact_id=unsupported.artifact_id)
        if parent.role == "model_view" else parent for parent in model.parents
    ))
    owner.store.publish(rebound_model)
    rebound_report = replace(evaluation, parents=tuple(
        replace(parent, artifact_id=(unsupported.artifact_id if parent.role == "model_view"
                                     else rebound_model.artifact_id))
        for parent in evaluation.parents
    ))
    owner.store.publish(rebound_report)
    roles.clear()
    with pytest.raises(BoundaryError, match="invalid_report_parentage"):
        summary(owner.store, rebound_report.artifact_id)
    assert roles == []


def test_http_requires_local_cookie_and_exact_id_without_cloud_login(tmp_path: Path,
                                                                      monkeypatch) -> None:
    store, identity = _token(tmp_path / "sample")
    registry_path = tmp_path / "registry.sqlite"
    SQLiteRegistry(registry_path)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination(),
                           LocalResearchWorkspaceConfig(store.blobs.root, registry_path))
    app = Application(config)
    monkeypatch.setattr(app.account, "status", lambda: pytest.fail("cloud login not required"))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    route = f"/api/local-workspace/evaluations/{identity}"
    roles = []
    original_read = ManifestArtifactStore.read_payload

    def observed(store, payload):
        roles.append(payload.role)
        yield from original_read(store, payload)

    monkeypatch.setattr(ManifestArtifactStore, "read_payload", observed)
    try:
        with pytest.raises(HTTPError) as denied:
            client.open(root + route)
        assert denied.value.code == 401
        client.open(root + "/").close()
        before = (store.manifest_ids(), registry_path.read_bytes())
        with client.open(root + "/api/local-workspace?kind=offline_evaluation") as response:
            assert json.load(response)["total"] == 1
        assert roles == []
        with client.open(root + route) as response:
            body = json.load(response)
        assert body["evaluation_id"] == identity
        assert roles == ["metrics"]
        assert "rows" not in json.dumps(body)
        assert (store.manifest_ids(), registry_path.read_bytes()) == before
        for bad in (route + "?all=true", route + "/raw"):
            with pytest.raises(HTTPError) as rejected:
                client.open(root + bad)
            assert rejected.value.code in {400, 404}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()
