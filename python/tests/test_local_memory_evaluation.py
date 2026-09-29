"""Local-only M2 dev admission and operation tests over synthetic artifacts."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import torch
from test_local_curation import create
from test_memory_evaluation import prepared

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import Application, configuration_id, create_server
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService
from stpd.workers.memory_evaluation import evaluate_memory

PRODUCER = Producer("synthetic-local-memory-dev", "a" * 40, "b" * 64)


@pytest.fixture(autouse=True)
def cpu_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def setup_sources(tmp_path: Path, monkeypatch):
    _, directory, owner = create(tmp_path)
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    evidence = tuple(Manifest("evidence", PRODUCER) for _ in range(2))
    # Distinct source/evidence identities without opening native bytes.
    evidence = (evidence[0], Manifest("evidence", Producer("synthetic-other", "b" * 40,
                                                           "c" * 64)))
    for item in evidence:
        store.publish(item)
    sources = tuple(Manifest("dataset", PRODUCER,
                             (Parent("evidence", item.artifact_id),)) for item in evidence)
    for item in sources:
        store.publish(item)
    native = {evidence[0].artifact_id: {"session-a/run-a"},
              evidence[1].artifact_id: {"session-b/run-b"}}
    monkeypatch.setattr(LocalCurationOwner, "_human_runs",
                        staticmethod(lambda _store, source: native[source]))
    monkeypatch.setattr("stpd.fullrun.text_menu_human_import.load_human_text_source",
                        lambda _store, source: (store.get_manifest(source), ()))
    with owner.transaction() as db:
        db.executemany("INSERT INTO curation_sources VALUES(?,?,1)",
                       [(item.artifact_id, "d" * 64) for item in evidence])
        db.executemany("INSERT INTO curation_exact_source_index VALUES(?)",
                       [(item.artifact_id,) for item in evidence])
        db.executemany("INSERT INTO curation_source_runs VALUES(?,?)",
                       [(item.artifact_id, next(iter(native[item.artifact_id])))
                        for item in evidence])
    for index, source in enumerate(sources):
        owner.ledger.claim(f"claim-{index}", "training", native[evidence[index].artifact_id])
        owner.ledger.bind(f"claim-{index}", source.artifact_id)
    return owner, store, sources, evidence, native


def test_local_dev_reservation_is_model_specific_and_blocks_later_gold(tmp_path, monkeypatch):
    owner, store, sources, evidence, native = setup_sources(tmp_path, monkeypatch)
    model_operation = "a" * 32
    evaluation_operation = "b" * 32
    owner.ledger.use(native[evidence[0].artifact_id], "training", model_operation)
    owner.ledger.use_source(evidence[0].artifact_id, "training", model_operation)
    assert owner.reserve_memory_dev(
        store, sources[0].artifact_id, sources[1].artifact_id,
        model_operation, evaluation_operation) is False
    with owner.transaction() as db:
        assert db.execute("SELECT kind,reference FROM curation_uses WHERE run=?",
                          ("session-b/run-b",)).fetchall() == [
                              ("evaluation", evaluation_operation)]
    with pytest.raises(BoundaryError, match="gold_previously_used_for_evaluation"):
        owner.ledger.claim("later-gold", "gold", {"session-b/run-b"})
    # A different model may still train on the source; this is not a global ban.
    owner.ledger.use({"session-b/run-b"}, "training", "c" * 32)


def test_missing_use_and_exact_origin_fail_but_semantic_neighbor_is_diagnostic(
    tmp_path, monkeypatch,
):
    owner, store, sources, evidence, native = setup_sources(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError, match="model_training_use_unproven"):
        owner.reserve_memory_dev(store, sources[0].artifact_id, sources[1].artifact_id,
                                 "a" * 32, "b" * 32)
    owner.ledger.use(native[evidence[0].artifact_id], "training", "a" * 32)
    owner.ledger.use_source(evidence[0].artifact_id, "training", "a" * 32)
    with pytest.raises(BoundaryError, match="train_dev_source_overlap"):
        owner.reserve_memory_dev(store, sources[0].artifact_id, sources[0].artifact_id,
                                 "a" * 32, "b" * 32)
    with owner.transaction() as db:
        db.executemany("INSERT INTO curation_fingerprints VALUES(?,?)",
                       [("same-visible-page", "session-a/run-a"),
                        ("same-visible-page", "session-b/run-b")])
    assert owner.reserve_memory_dev(
        store, sources[0].artifact_id, sources[1].artifact_id,
        "a" * 32, "b" * 32) is True
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_fingerprints")
    owner.ledger.claim("test-claim", "test", {"session-b/run-b"})
    with pytest.raises(BoundaryError, match="sealed_dev_source_forbidden"):
        owner.reserve_memory_dev(store, sources[0].artifact_id, sources[1].artifact_id,
                                 "a" * 32, "b" * 32)


def test_gold_semantic_neighbor_still_blocks_dev(tmp_path, monkeypatch):
    owner, store, sources, evidence, native = setup_sources(tmp_path, monkeypatch)
    owner.ledger.use(native[evidence[0].artifact_id], "training", "a" * 32)
    owner.ledger.use_source(evidence[0].artifact_id, "training", "a" * 32)
    owner.ledger.claim("gold-neighbor", "gold", {"session-g/run-g"})
    with owner.transaction() as db:
        db.executemany("INSERT INTO curation_fingerprints VALUES(?,?)",
                       [("shared-public-input", "session-b/run-b"),
                        ("shared-public-input", "session-g/run-g")])
    with pytest.raises(BoundaryError, match="sealed_dev_source_forbidden"):
        owner.reserve_memory_dev(store, sources[0].artifact_id, sources[1].artifact_id,
                                 "a" * 32, "b" * 32)


def test_async_workbench_dev_operation_binds_existing_model_and_report(tmp_path, monkeypatch):
    (tmp_path / "workspace").mkdir()
    source_store, model_id, train, dev, _, _, _ = prepared(
        tmp_path / "synthetic-model", monkeypatch, operation_id="a" * 32)
    state, directory, owner = create(tmp_path / "workspace")
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    result = next(source_store.get_manifest(identity) for identity in source_store.manifest_ids()
                  if source_store.get_manifest(identity).kind == "run_result")
    copy_artifact(source_store, store, result.artifact_id)
    copy_artifact(source_store, store, dev.artifact_id)
    with pytest.raises(BoundaryError, match="model_training_lineage_mismatch"):
        LocalMemoryEvaluationService._training_source(store, model_id)
    completion_key = f"run-completions/{result.parent('run')}.json"
    store.blobs.put_if_absent(completion_key, source_store.blobs.get(completion_key))
    # The mutable training display has moved on; immutable model/run lineage remains valid.
    (owner.path.parent / "local-training-operation.json").write_text('{"status":"later"}')
    admitted = []
    monkeypatch.setattr(LocalCurationOwner, "reserve_memory_dev",
                        lambda self, _store, train_id, dev_id, model_op, eval_op:
                        admitted.append((train_id, dev_id, model_op, eval_op)) or False)

    def child(command, _log, _environment, *, on_started=None):
        if on_started is not None:
            on_started()
        output = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER,
                                 operation_id=command[command.index("--operation") + 1],
                                 semantic_overlap=False)
        return 0, json.dumps({"evaluation_id": output.artifact_id,
                              "evaluation_input_id": output.parent("evaluation_input")}).encode()

    monkeypatch.setattr("spireagent.workbench.local_memory_evaluation._private_child", child)
    service = LocalMemoryEvaluationService(ProjectConfig(state, "", "", None, combination()))
    with monkeypatch.context() as missing:
        missing.setattr("spireagent.workbench.local_model_dependencies.find_spec", lambda _: None)
        with pytest.raises(BoundaryError, match="local_models_extra_required"):
            service.start(model_id, dev.artifact_id)
        assert service._thread is None
        assert service.status()["operation"]["status"] == "idle"
        assert admitted == []
    started = service.start(model_id, dev.artifact_id)["operation"]
    assert started["purpose"] == "dev"
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert not service._thread.is_alive()
    completed = service.status()["operation"]
    assert completed["status"] == "completed", completed
    assert admitted == [(train.artifact_id, dev.artifact_id, "a" * 32,
                         started["operation_id"])]
    assert store.get_manifest(completed["evaluation_id"]).parameters.value()[
        "operation_id"] == started["operation_id"]


def test_http_model_detail_evaluation_action_is_explicit_and_browser_bound(tmp_path, monkeypatch):
    state, _, _ = create(tmp_path)
    config = ProjectConfig(state, "", "", None, combination())
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    (state / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id,
        "configuration_id": configuration_id(config), "port": server.server_port,
    }))
    calls = []
    monkeypatch.setattr(app, "start_local_memory_evaluation",
                        lambda model, source, **kw: calls.append((model, source, kw))
                        or {"operation": {"status": "pending", "purpose": "dev"}})
    body = json.dumps({"model_id": "a" * 64, "source_id": "b" * 64}).encode()
    headers = {"Cookie": f"{app.account.cookie_name}={app.account.cookie}",
               "Content-Type": "application/json", "Origin": root,
               "X-CSRF-Token": app.account.csrf}
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(root + "/api/local-memory-evaluations/start",
                            data=body, headers={"Content-Type": "application/json"}),
                    timeout=3)
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as malformed:
            urlopen(Request(root + "/api/local-memory-evaluations/start",
                            data=json.dumps({"model_id": "a" * 64}).encode(),
                            headers=headers), timeout=3)
        assert malformed.value.code == 400
        with urlopen(Request(root + "/api/local-memory-evaluations/start",
                             data=body, headers=headers), timeout=3) as response:
            assert json.load(response)["operation"] == {"status": "pending", "purpose": "dev"}
        assert calls == [("a" * 64, "b" * 64, {"max_settling_events": 0})]
        with urlopen(Request(root + "/api/local-memory-evaluations/status",
                             headers={"Cookie": headers["Cookie"]}), timeout=3) as response:
            assert json.load(response)["operation"]["status"] == "idle"
    finally:
        server.shutdown()
        server.server_close()
        app.close()
