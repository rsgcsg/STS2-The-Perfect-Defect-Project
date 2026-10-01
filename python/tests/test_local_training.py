"""Synthetic verified recording through the exact local D-Simple training owner."""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import test_local_human_dataset as human_fixture
import test_local_recording_preview as recording_fixture

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage import replaceable_file
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_dataset as dataset_module
from spireagent.workbench import local_training as training_module
from spireagent.workbench.developer import (
    ROOT,
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    combination,
)
from spireagent.workbench.developer_server import (
    Application,
    configuration_id,
    create_server,
    instance_lock,
)
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_curation import LocalLedger
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_training import (
    DEFAULT_RECIPE,
    LOCK_FILE,
    MEMORY_RECIPE,
    OPERATION_FILE,
    PUBLIC_M0_RECIPE,
    SCHEMA_V3,
    LocalTrainingService,
)
from spireagent.workbench.memory_recipe import RESET_K1_RECIPE


def _ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, runs: int = 3,
           public_bindings: bool = True):
    original_bundle = recording_fixture.bundle3
    monkeypatch.setattr(recording_fixture, "bundle3", lambda path, **_kw:
                        original_bundle(path, runs=runs, public_bindings=public_bindings))
    _, source, store = recording_fixture._fixture(tmp_path / "library", canonical=True)
    state = tmp_path / "profile"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(tmp_path / "library/store",
                                                        tmp_path / "library/registry.sqlite"))
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=30)
    assert preparation.status()["status"] == "ready", preparation.status()
    producer = store.get_manifest(source).producer
    monkeypatch.setattr(dataset_module, "source_identity", lambda _: producer)
    datasets = LocalDatasetService(config)
    datasets.start_preview(source, "training", None)
    assert datasets.thread is not None
    datasets.thread.join(timeout=30)
    ready = datasets.status()["operation"]
    assert ready["status"] == "preview_ready", ready
    datasets.start_publish(ready["preview_id"])
    assert datasets.thread is not None
    datasets.thread.join(timeout=30)
    published = datasets.status()["operation"]
    assert published["status"] == "completed", published
    return config, published["result_artifact_id"], source, store


def _settle(service: LocalTrainingService, timeout: float = 180) -> dict:
    assert service._thread is not None
    service._thread.join(timeout=timeout)
    assert not service._thread.is_alive()
    return service.status()["operation"]


def test_missing_model_backend_preserves_valid_training_input(tmp_path, monkeypatch):
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    service = LocalTrainingService(config)
    before = store.manifest_ids()
    with monkeypatch.context() as missing:
        missing.setattr("spireagent.workbench.local_model_dependencies.find_spec", lambda _: None)
        with pytest.raises(BoundaryError, match="local_models_extra_required"):
            service.start(dataset_id)
    assert service.status()["operation"]["status"] == "idle"
    assert service.status()["availability"] == "ready"
    assert service._thread is None
    assert store.manifest_ids() == before
    owner = configured_owner(config)
    path = owner.path.parent / OPERATION_FILE
    lock_path = path.parent / LOCK_FILE
    assert lock_path.is_file() and not path.exists()
    lock_bytes = lock_path.read_bytes()

    def parked(held, _path, _identity, _owner, _store):
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", parked)
    started = service.start(dataset_id)["operation"]
    assert started["status"] == "pending"
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert not service._thread.is_alive()
    assert lock_path.read_bytes() == lock_bytes


def test_public_m0_profile_is_closed_and_persisted_in_the_existing_operation(tmp_path, monkeypatch):
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    service = LocalTrainingService(config)
    with pytest.raises(BoundaryError, match="unsupported_input_profile"):
        service.start(dataset_id, input_profile="canonical")
    with pytest.raises(BoundaryError, match="unsupported_input_profile"):
        service.start(dataset_id, input_profile={"profile": "public_lite"})

    def parked(held, _path, _identity, _owner, _store):
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", parked)
    started = service.start(dataset_id, input_profile="public_lite")["operation"]
    assert started["schema"] == SCHEMA_V3
    assert started["input_profile"] == "public_lite"
    assert started["recipe"] == PUBLIC_M0_RECIPE
    assert started["result_type"] == "evaluated"
    assert started["evaluation_status"] == "pending"
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert not service._thread.is_alive()
    owner = configured_owner(config)
    operation = LocalTrainingService._read(owner.path.parent / OPERATION_FILE, owner.identity)
    assert operation["schema"] == SCHEMA_V3
    assert operation["input_profile"] == "public_lite"


def test_public_m0_workbench_http_train_export_and_registration_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    config_path = tmp_path / "project.json"
    config_path.write_text(canonical_json(config.to_dict()), encoding="utf-8")
    app = Application(config, config_path=config_path)
    from spireagent.workbench.developer_server import atomic_json

    atomic_json(config.state_dir / "runtime.json", {
        "instance_id": app.instance_id,
        "configuration_id": configuration_id(config),
    })
    wrapper = tmp_path / "python-child"
    child_python = sys.executable
    package_path = str(ROOT)
    tests_path = str(ROOT / "tests")
    wrapper.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={shlex.quote(package_path)}:{shlex.quote(tests_path)} exec "
        f"{shlex.quote(child_python)} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.setattr(sys, "executable", str(wrapper))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    headers = {"Cookie": cookie, "Content-Type": "application/json", "Origin": url,
               "X-CSRF-Token": app.account.csrf}

    def request_json(path: str, *, body: dict | None = None) -> dict:
        request = Request(url + path,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method="POST" if body is not None else "GET")
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    try:
        started = request_json("/api/local-training/start",
                               body={"dataset_id": dataset_id,
                                     "input_profile": "public_lite"})
        assert started["schema"] == SCHEMA_V3
        assert started["operation"]["input_profile"] == "public_lite"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = request_json("/api/local-training/status")
            if status["operation"].get("status") in {"completed", "failed", "interrupted_unknown"}:
                break
            time.sleep(0.2)
        assert status["operation"]["status"] == "completed", status
        trained = status["operation"]
        assert trained["schema"] == SCHEMA_V3
        assert trained["input_profile"] == "public_lite"
        model = store.get_manifest(trained["model_id"])
        assert model.parameters.value()["schema"] == \
            "stpd/stage1a-light-action-m0-public-model-v1"
        assert model.parameters.value()["config"]["steps"] == 3

        worker_errors = []
        old_thread_trace = threading.gettrace()

        def trace_export(frame, event, arg):
            if frame.f_code.co_name == "_run" and event == "exception":
                worker_errors.append(arg[0])

        threading.settrace(trace_export)
        try:
            exporting = request_json("/api/local-model-exports/start",
                                     body={"model_id": trained["model_id"]})
            assert exporting["schema"] == "stpd/local-model-export-operation-v3"
            assert exporting["operation"]["model_type"] == "public_m0"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                exported = request_json("/api/local-model-exports/status")
                if exported["operation"].get("status") in {"completed", "failed", "interrupted"}:
                    break
                time.sleep(0.2)
        finally:
            threading.settrace(old_thread_trace)
        assert exported["operation"]["status"] == "completed", exported
        assert UnboundLocalError not in worker_errors
        destination = app.local_model_export.verified_public_m0_for_registration(
            trained["model_id"], deadline=time.monotonic() + 10)
        assert destination.is_dir()
        package = destination / "model.json"
        assert package.is_file()
        original_package = package.read_bytes()
        package.write_bytes(b"{}\n")
        with pytest.raises(BoundaryError, match="public_m0_export_verification_failed"):
            app.local_model_export.start(trained["model_id"])
        package.write_bytes(original_package)
        assert app.local_model_export.start(trained["model_id"])["operation"]["status"] == \
            "completed"

        owner = configured_owner(config)
        with owner.transaction() as db:
            db.execute("DELETE FROM curation_source_uses WHERE reference=?",
                       (trained["operation_id"],))
            db.execute("DELETE FROM curation_uses WHERE reference=?",
                       (trained["operation_id"],))
        with pytest.raises(BoundaryError, match="public_m0_lineage_invalid"):
            app.local_model_export.start(trained["model_id"])
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def test_private_child_drains_large_stderr_and_keeps_stdout_machine_record(tmp_path: Path):
    script = ("import sys; sys.stderr.write('x' * 262144); "
              "sys.stderr.flush(); sys.stdout.write('{\"run_id\":\"ok\"}\\n')")
    log = tmp_path / "child.log"
    exit_code, output = training_module._private_child(
        [sys.executable, "-c", script], log, dict(os.environ))
    assert exit_code == 0
    assert json.loads(output) == {"run_id": "ok"}
    assert log.stat().st_size == 128 * 1024


def test_private_child_drains_after_log_write_failure(tmp_path: Path, monkeypatch) -> None:
    script = ("import sys; sys.stderr.write('x' * 262144); "
              "sys.stderr.flush(); sys.stdout.write('{\"run_id\":\"ok\"}\\n')")
    real_fdopen = os.fdopen

    class BrokenLog:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.stream.close()

        def write(self, _chunk):
            raise OSError("synthetic_log_failure")

        def flush(self):
            self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    monkeypatch.setattr(training_module.os, "fdopen",
                        lambda fd, mode: BrokenLog(real_fdopen(fd, mode)))
    result = []

    def invoke():
        try:
            training_module._private_child(
                [sys.executable, "-c", script], tmp_path / "broken.log", dict(os.environ))
        except OSError as error:
            result.append(str(error))

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "child pipes must drain after the log write fails"
    assert result == ["synthetic_log_failure"]


def test_exact_synthetic_public_bc_dsimple_subprocess(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, source, store = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    service = LocalTrainingService(config)
    before = tuple(store.manifest_ids())
    assert service.status()["operation"] == {"status": "idle"}
    assert not (owner.path.parent / OPERATION_FILE).exists()
    assert tuple(store.manifest_ids()) == before
    started = service.start(dataset_id)["operation"]
    assert started["status"] == "pending"
    completed = _settle(service)
    assert completed["status"] == "completed", completed
    assert set(("allocation_id", "view_id", "input_id", "run_id", "checkpoint_id",
                "result_id", "model_id", "evaluation_id")) <= set(completed)
    result = store.get_manifest(completed["result_id"])
    model = store.get_manifest(completed["model_id"])
    report = store.get_manifest(completed["evaluation_id"])
    assert result.parent("run") == completed["run_id"]
    assert result.parent("model") == model.artifact_id
    assert result.parent("offline_evaluation") == report.artifact_id
    assert report.parameters.value()["partition"] == "dev"
    assert model.parameters.value()["serializer"] == {
        "profile": "public_compact", "status": "provisional",
        "version": "stpd-public-snapshot-compact-v2",
    }
    assert model.parameters.value()["config"]["recipe"] == "stage1a.dsimple.s.v1"
    assert model.parameters.value()["graph"] == "dsimple.vector.v1"
    assert model.parameters.value()["config"]["steps"] == 3
    assert model.parameters.value()["config"]["dropout"] == 0.0
    assert model.parameters.value()["config"]["max_tokens"] == 16384
    assert {key: model.parameters.value()["config"][key] for key in
            ("width", "layers", "heads", "feedforward", "device")} == {
                "width": 48, "layers": 1, "heads": 2, "feedforward": 96, "device": "cpu",
            }
    assert store.get_manifest(completed["run_id"]).parameters.value()["cpu_threads"] == 2
    durable = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert durable["_exit_code"] == 0
    registry = SQLiteRegistry(config.research_workspace.registry_path, readonly=True)
    assert registry.get(model.artifact_id).to_bytes() == model.to_bytes()
    assert registry.get(report.artifact_id).to_bytes() == report.to_bytes()
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses WHERE reference=?",
                          (completed["operation_id"],)).fetchone()[0] >= 3
        assert db.execute("SELECT source FROM curation_source_uses WHERE reference=?",
                          (completed["operation_id"],)).fetchone() == (source,)
    assert service.start(dataset_id)["operation"]["operation_id"] == completed["operation_id"]
    assert LocalTrainingService(config).status()["operation"] == completed
    launched = service.start(
        dataset_id, after_completed_operation_id=completed["operation_id"]
    )["operation"]
    assert launched["status"] == "pending"
    assert launched["operation_id"] != completed["operation_id"]
    assert launched["previous_completed"] == {
        key: completed[key] for key in ("operation_id", "dataset_id", "result_id",
                                        "model_id", "evaluation_id")
    }
    with pytest.raises(BoundaryError):
        service.start(dataset_id, after_completed_operation_id=completed["operation_id"])
    second = _settle(service)
    assert second["status"] == "completed", second
    assert second["operation_id"] == launched["operation_id"]
    assert second["result_id"] != completed["result_id"]
    assert second["previous_completed"] == launched["previous_completed"]
    assert store.get_manifest(completed["result_id"]) == result
    assert store.get_manifest(completed["model_id"]) == model
    assert store.get_manifest(completed["evaluation_id"]) == report
    with pytest.raises(BoundaryError):
        service.start(dataset_id, after_completed_operation_id=completed["operation_id"])
    with pytest.raises(BoundaryError):
        service.start("f" * 64, after_completed_operation_id=second["operation_id"])
    assert service.start(dataset_id)["operation"]["operation_id"] == second["operation_id"]


def _human_ready(tmp_path: Path, monkeypatch):
    datasets, _ = human_fixture._prepared(tmp_path)
    config = datasets.config
    owner = configured_owner(config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    sources = [human_fixture._fresh_source(tmp_path, monkeypatch, store, owner, name)
               for name in ("b", "c")]
    monkeypatch.setattr(dataset_module, "source_identity",
                        lambda _: store.get_manifest(sources[0]).producer)
    datasets.start_human_preview(sources)
    preview = human_fixture._settle(datasets)
    assert preview["can_publish"]
    datasets.start_publish(preview["preview_id"])
    published = human_fixture._settle(datasets)
    assert published["status"] == "completed", published
    return config, published["result_artifact_id"], sources, store


def test_claimed_human_m2_train_only_subprocess(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, sources, store = _human_ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    service = LocalTrainingService(config)
    started = service.start(dataset_id, recipe=MEMORY_RECIPE)["operation"]
    assert started["recipe"] == MEMORY_RECIPE
    with pytest.raises(BoundaryError, match="operation_in_progress"):
        service.start(dataset_id)
    completed = _settle(service)
    assert completed["status"] == "completed", completed
    assert completed["result_type"] == "train_only"
    assert completed["evaluation_status"] == "not_run"
    assert "evaluation_id" not in completed
    run = store.get_manifest(completed["run_id"])
    recipe_config = run.parameters.value()["config"]
    assert {key: recipe_config[key] for key in (
        "max_tokens", "max_total_input_tokens", "max_episode_input_tokens",
        "max_episode_observations", "max_chunk_steps", "max_chunk_input_tokens",
        "max_actions_per_step", "cpu_threads",
    )} == {
        "max_tokens": 16384, "max_total_input_tokens": 4194304,
        "max_episode_input_tokens": 4194304, "max_episode_observations": 768,
        "max_chunk_steps": 2, "max_chunk_input_tokens": 24576,
        "max_actions_per_step": 256, "cpu_threads": 2,
    }
    training_input = store.get_manifest(completed["input_id"])
    result = store.get_manifest(completed["result_id"])
    model = store.get_manifest(completed["model_id"])
    assert training_input.parent("source") == dataset_id
    assert training_input.parameters.value()["schema"] == "stpd/experimental-m2-training-input-v2"
    assert run.parent("training_input") == completed["input_id"]
    assert result.parent("checkpoint") == completed["checkpoint_id"]
    assert result.parent("model") == completed["model_id"]
    assert model.parameters.value()["partition"] == "train"
    assert SQLiteRegistry(config.research_workspace.registry_path, readonly=True).get(
        model.artifact_id).to_bytes() == model.to_bytes()
    with owner.transaction() as db:
        assert {row[0] for row in db.execute(
            "SELECT source FROM curation_source_uses WHERE reference=?",
            (completed["operation_id"],))} == set(sources)
    assert service.start(dataset_id, recipe=MEMORY_RECIPE)["operation"] == completed
    with pytest.raises(BoundaryError, match="new_experiment_precondition_failed"):
        service.start(dataset_id)
    assert LocalTrainingService(config).status()["operation"] == completed
    run_id = completed["run_id"]
    assert (store.get_manifest(run_id).parameters.value()["operation_id"]
            == completed["operation_id"])
    next_operation = service.start(dataset_id, recipe=MEMORY_RECIPE,
                                   after_completed_operation_id=completed["operation_id"])["operation"]
    assert next_operation["status"] == "pending"
    second = _settle(service)
    assert second["status"] == "completed", second
    assert second["run_id"] != run_id
    assert second["previous_completed"]["result_id"] == completed["result_id"]
    assert (store.get_manifest(second["run_id"]).parameters.value()["operation_id"]
            == second["operation_id"])

    reset_started = service.start(
        dataset_id, recipe=RESET_K1_RECIPE,
        after_completed_operation_id=second["operation_id"],
    )["operation"]
    assert reset_started["recipe"] == RESET_K1_RECIPE
    assert reset_started["result_type"] == "train_only"
    reset = _settle(service)
    assert reset["status"] == "completed", reset
    assert reset["recipe"] == RESET_K1_RECIPE
    assert reset["run_id"] != second["run_id"]
    assert reset["model_id"] != second["model_id"]
    assert reset["result_id"] != second["result_id"]
    assert reset["input_id"] == second["input_id"]
    assert reset["previous_completed"]["result_id"] == second["result_id"]
    assert reset["previous_completed"]["model_id"] == second["model_id"]
    persistent_config = store.get_manifest(second["run_id"]).parameters.value()["config"]
    reset_config = store.get_manifest(reset["run_id"]).parameters.value()["config"]
    assert reset_config == {**persistent_config, "reset_each_step": True}

    # A later default experiment stays available with an explicit predecessor.
    def parked(held, _path, _identity, _owner, _store):
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", parked)
    default = service.start(dataset_id,
                            after_completed_operation_id=reset["operation_id"])["operation"]
    assert default["schema"] == "stpd/local-training-operation-v2"
    assert default["recipe"] == "stage1a.dsimple.s.v1"
    assert default["result_type"] == "evaluated"
    assert default["evaluation_status"] == "pending"
    assert default["previous_completed"]["result_type"] == "train_only"
    assert service._thread is not None
    service._thread.join(timeout=10)


@pytest.mark.parametrize("broken", ["claim", "index"])
def test_m2_rejects_unclaimed_or_unindexed_source_before_derivatives(
    tmp_path: Path, monkeypatch, broken: str,
) -> None:
    config, dataset_id, _, store = _human_ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    before = set(store.manifest_ids())
    if broken == "claim":
        monkeypatch.setattr(LocalLedger, "dataset", lambda _self, _id: None)
    else:
        monkeypatch.setattr(LocalLedger, "source_runs", lambda _self, _id: None)
    service = LocalTrainingService(config)
    service.start(dataset_id, recipe=MEMORY_RECIPE)
    failed = _settle(service)
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == ("training_claim_mismatch" if broken == "claim"
                                    else "source_index_incomplete")
    assert "input_id" not in failed and "run_id" not in failed
    assert set(store.manifest_ids()) == before
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses WHERE reference=?",
                          (failed["operation_id"],)).fetchone() == (0,)


def test_m2_preparation_limit_error_is_durable_failure_without_automatic_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, store = _human_ready(tmp_path, monkeypatch)
    before = set(store.manifest_ids())
    calls = []

    def reject(command, _log_path, _environment, *, on_started):
        calls.append(command)
        on_started()
        return 2, b'{"error_code":"m2_limit_exceeded_no_truncation"}\n'

    monkeypatch.setattr(training_module, "_private_child", reject)
    service = LocalTrainingService(config)
    service.start(dataset_id, recipe=MEMORY_RECIPE)
    failed = _settle(service)
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == "m2_limit_exceeded_no_truncation"
    assert len(calls) == 1 and "prepare-workbench-memory" in calls[0]
    assert set(store.manifest_ids()) == before
    assert LocalTrainingService(config).status()["operation"]["status"] == "failed"
    service.start(dataset_id, recipe=MEMORY_RECIPE)
    retried = _settle(service)
    assert retried["status"] == "failed", retried
    assert len(calls) == 2, "a second child runs only after a separate explicit start"


def test_m2_pre_spawn_failure_is_retryable_failed_without_run(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, store = _human_ready(tmp_path, monkeypatch)
    before = set(store.manifest_ids())

    def cannot_spawn(_command, _log_path, _environment, *, on_started):
        del on_started
        raise OSError("synthetic_no_child")

    monkeypatch.setattr(training_module, "_private_child", cannot_spawn)
    service = LocalTrainingService(config)
    service.start(dataset_id, recipe=MEMORY_RECIPE)
    failed = _settle(service)
    assert failed["status"] == "failed", failed
    assert failed["error_code"] == "training_storage_or_process_error"
    assert "run_id" not in failed
    assert set(store.manifest_ids()) == before


def test_insufficient_independent_components_never_reserves_use(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch, runs=1)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "insufficient_independent_components"
    assert "run_id" not in result
    owner = configured_owner(config)
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses").fetchone() == (0,)


def test_missing_public_h_binding_never_falls_back_to_native_input(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, source, _ = _ready(tmp_path, monkeypatch, public_bindings=False)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed", result
    assert result["error_code"] == "nonempty_train_dev_required"
    assert "run_id" not in result
    owner = configured_owner(config)
    with owner.transaction() as db:
        assert db.execute("SELECT source FROM curation_source_uses WHERE reference=?",
                          (result["operation_id"],)).fetchone() == (source,)


def test_one_slot_across_profiles_and_unknown_restart(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    second_state = tmp_path / "other-profile"
    second_state.mkdir()
    other = ProjectConfig(second_state, "", "", None, combination(), config.research_workspace)
    service = LocalTrainingService(config)
    entered, release = threading.Event(), threading.Event()

    def held(_lock, _path, _identity, _owner, _store):
        entered.set()
        assert release.wait(10)
        _lock.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", held)
    service.start(dataset_id)
    assert entered.wait(5)
    contender = LocalTrainingService(other)
    assert contender.status()["operation"]["status"] == "pending"
    same = contender.start(dataset_id)["operation"]
    assert same["operation_id"] == service.status()["operation"]["operation_id"]
    with pytest.raises(BoundaryError, match="operation_in_progress"):
        contender.start("a" * 64)
    release.set()
    assert service._thread is not None
    service._thread.join(timeout=10)
    assert contender.status()["operation"]["status"] == "interrupted_unknown"
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        contender.start("a" * 64)


def test_new_experiment_requires_exact_completed_predecessor_under_one_lock(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    old = {
        "schema": "stpd/local-training-operation-v1", "status": "completed",
        "stage": "completed", "operation_id": "1" * 32, "dataset_id": dataset_id,
        "run_id": "2" * 64, "result_id": "3" * 64,
        "model_id": "4" * 64, "evaluation_id": "5" * 64,
        "_owner": list(owner.identity),
    }
    (owner.path.parent / OPERATION_FILE).write_text(json.dumps(old))
    service = LocalTrainingService(config)
    entered, release = threading.Event(), threading.Event()

    def held(lock, _path, _identity, _owner, _store):
        try:
            entered.set()
            assert release.wait(10)
        finally:
            lock.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", held)
    with pytest.raises(BoundaryError):
        service.start(dataset_id, after_completed_operation_id="6" * 32)
    with pytest.raises(BoundaryError):
        service.start("f" * 64, after_completed_operation_id=old["operation_id"])
    assert service.start(dataset_id)["operation"]["operation_id"] == old["operation_id"]
    assert not entered.is_set()
    try:
        new = service.start(dataset_id, after_completed_operation_id=old["operation_id"])
        assert entered.wait(5)
        assert new["operation"]["status"] == "pending"
        assert new["operation"]["operation_id"] != old["operation_id"]
        assert new["operation"]["previous_completed"]["result_id"] == old["result_id"]
        with pytest.raises(BoundaryError):
            service.start(dataset_id, after_completed_operation_id=old["operation_id"])
    finally:
        release.set()
        if service._thread is not None:
            service._thread.join(timeout=10)
            assert not service._thread.is_alive()


def test_v1_completed_recipe_change_requires_explicit_new_experiment(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    old = {
        "schema": "stpd/local-training-operation-v1", "status": "completed",
        "stage": "completed", "operation_id": "1" * 32, "dataset_id": dataset_id,
        "run_id": "2" * 64, "result_id": "3" * 64,
        "model_id": "4" * 64, "evaluation_id": "5" * 64,
        "_owner": list(owner.identity),
    }
    path = owner.path.parent / OPERATION_FILE
    path.write_text(json.dumps(old))
    service = LocalTrainingService(config)
    assert service.status()["operation"]["evaluation_id"] == old["evaluation_id"]
    with pytest.raises(BoundaryError, match="new_experiment_precondition_failed"):
        service.start(dataset_id, recipe=MEMORY_RECIPE)
    assert json.loads(path.read_text()) == old
    broken = dict(old)
    del broken["evaluation_id"]
    path.write_text(json.dumps(broken))
    assert service.status()["availability"] == "recovery_required"
    path.write_text(json.dumps(old))

    def parked(held, _path, _identity, _owner, _store):
        held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", parked)
    started = service.start(dataset_id, recipe=MEMORY_RECIPE,
                            after_completed_operation_id=old["operation_id"])["operation"]
    assert started["schema"] == "stpd/local-training-operation-v2"
    assert started["recipe"] == MEMORY_RECIPE
    assert started["previous_completed"] == {
        key: old[key] for key in ("operation_id", "dataset_id", "result_id",
                                 "model_id", "evaluation_id")
    }
    assert service._thread is not None
    service._thread.join(timeout=10)


@pytest.mark.parametrize("terminal", ["completed", "failed", "pending", "corrupt", "missing"])
def test_status_rechecks_journal_after_parent_releases_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str,
) -> None:
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    service = LocalTrainingService(config)
    entered, finish = threading.Event(), threading.Event()
    launches = []

    def finish_operation(held, path, identity, _owner, _store):
        launches.append(identity)
        try:
            entered.set()
            assert finish.wait(10)
            if terminal == "completed":
                service._advance(path, identity, status="completed", stage="completed",
                                 run_id="a" * 64, result_id="b" * 64,
                                 model_id="c" * 64, evaluation_id="d" * 64)
            elif terminal == "failed":
                service._advance(path, identity, status="failed",
                                 error_code="synthetic_preparation_failed")
            elif terminal == "corrupt":
                path.write_bytes(b"{incomplete")
            elif terminal == "missing":
                path.unlink()
        finally:
            held.__exit__(None, None, None)

    monkeypatch.setattr(service, "_run", finish_operation)
    started = service.start(dataset_id)["operation"]
    try:
        assert entered.wait(5)
        original_read = service._read
        first_read = True

        def read_before_parent_finishes(path, identity):
            nonlocal first_read
            value = original_read(path, identity)
            if first_read:
                first_read = False
                assert value["status"] == "pending"
                # The observer has read pending. The real supervising lock is
                # released only after the terminal journal write, before its probe.
                finish.set()
                assert service._thread is not None
                service._thread.join(timeout=10)
                assert not service._thread.is_alive()
            return value

        monkeypatch.setattr(service, "_read", read_before_parent_finishes)
        manifests_before = tuple(store.manifest_ids())
        observed = service.status()
        journal = owner.path.parent / OPERATION_FILE
        durable = journal.read_bytes() if journal.exists() else None
        if terminal in {"corrupt", "missing"}:
            assert observed["availability"] == "recovery_required", observed
            assert observed["reason"] == "operation_recovery_required"
        else:
            operation = observed["operation"]
            assert operation["operation_id"] == started["operation_id"]
            if terminal == "pending":
                assert operation["status"] == "interrupted_unknown"
                assert operation["error_code"] == "previous_training_outcome_unknown"
                assert json.loads(durable)["status"] == "pending"
            else:
                assert operation["status"] == terminal, observed
                if terminal == "completed":
                    assert operation["result_id"] == "b" * 64
                else:
                    assert operation["error_code"] == "synthetic_preparation_failed"
        if terminal != "missing":
            assert service.status() == observed
        assert (journal.read_bytes() if journal.exists() else None) == durable
        assert tuple(store.manifest_ids()) == manifests_before
        assert launches == [started["operation_id"]]
    finally:
        finish.set()
        if service._thread is not None:
            service._thread.join(timeout=10)
            assert not service._thread.is_alive()


def test_nonzero_child_keeps_run_and_private_exit_for_diagnosis(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    producer = source_identity(ROOT, require_clean=False)

    launches = []

    def fail_child(command, log_path, _environment, *, on_started):
        launches.append(command)
        on_started()
        log_path.write_bytes(b"synthetic private failure\n")
        return 7, b""

    monkeypatch.setattr(training_module, "_private_child", fail_child)
    monkeypatch.setattr(training_module, "source_identity", lambda _root: producer)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert len(launches) == 1
    assert result["status"] == "failed", result
    assert result["error_code"] == "training_process_failed"
    assert "run_id" in result and "result_id" not in result
    owner = configured_owner(config)
    operation = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert operation["_exit_code"] == 7
    log = owner.path.parent / ("local-training-" + result["operation_id"] + ".log")
    assert log.read_bytes() == b"synthetic private failure\n"
    assert "synthetic private failure" not in json.dumps(service.status())
    with pytest.raises(BoundaryError, match="previous_training_failed"):
        LocalTrainingService(config).start(dataset_id)


@pytest.mark.parametrize("log_write_fails", [False, True])
def test_parent_exception_diagnostic_preserves_unknown_after_child_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log_write_fails: bool,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    producer = source_identity(ROOT, require_clean=False)
    secret = "private-training-dataset-path"

    def fail_child(_command, _log_path, _environment, *, on_started):
        on_started()
        error = OSError(13, secret)
        error.winerror = 32
        raise error

    monkeypatch.setattr(training_module, "_private_child", fail_child)
    monkeypatch.setattr(training_module, "source_identity", lambda _root: producer)
    if log_write_fails:
        def fail_log(*_args):
            raise OSError(13, "private-log-path")
        monkeypatch.setattr(training_module, "_write_parent_failure", fail_log)
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "interrupted_unknown", result
    assert result["error_code"] == "training_storage_or_process_error"
    assert "run_id" in result and "result_id" not in result
    diagnostic = service._failure_diagnostic
    assert diagnostic is not None
    assert diagnostic["stage"] == "training"
    assert diagnostic["exception_type"] == "builtins.PermissionError"
    assert diagnostic["errno"] == 13 and diagnostic["winerror"] == 32
    assert diagnostic["owner_call"]["file"] == "local_training.py"
    assert diagnostic["owner_call"]["function"] == "_run"
    assert diagnostic["origin"]["file"] == "test_local_training.py"
    assert diagnostic["origin"]["function"] == "fail_child"
    assert secret not in json.dumps(diagnostic)
    assert secret not in json.dumps(service.status())
    owner = configured_owner(config)
    log = owner.path.parent / ("local-training-" + result["operation_id"] +
                               "-parent-error.log")
    if log_write_fails:
        assert not log.exists()
    else:
        assert secret in log.read_text()
        assert log.stat().st_size <= training_module.PARENT_FAILURE_LOG_BYTES
        if os.name != "nt":
            assert log.stat().st_mode & 0o777 == 0o600


def test_invalid_producer_operation_or_owner_never_starts_child(
    tmp_path: Path, monkeypatch,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    launched = []
    monkeypatch.setattr(training_module.subprocess, "Popen",
                        lambda *_args, **_kwargs: launched.append(1))
    monkeypatch.setattr(training_module, "source_identity",
                        lambda _root: (_ for _ in ()).throw(
                            BoundaryError("source", "clean_checkout_required")))
    service = LocalTrainingService(config)
    service.start(dataset_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "clean_checkout_required"
    assert launched == []
    owner = configured_owner(config)
    operation_path = owner.path.parent / OPERATION_FILE
    operation_path.write_bytes(b"{broken")
    assert service.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        service.start(dataset_id)
    assert launched == []
    operation_path.unlink()
    owner.path.unlink()
    assert service.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="ledger_recovery_required"):
        service.start(dataset_id)
    assert launched == []


def test_gold_or_test_claim_never_admitted(tmp_path: Path, monkeypatch) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    original = store.get_manifest(dataset_id)
    # A forged manifest ID cannot borrow the real training claim.
    from spireagent.artifact_contracts import Manifest
    from spireagent.json_boundary import FrozenObject

    forged = Manifest("dataset", original.producer, original.parents, original.payloads,
                      FrozenObject.of({**original.parameters.value(), "purpose": "gold"}))
    store.publish(forged)
    service = LocalTrainingService(config)
    service.start(forged.artifact_id)
    result = _settle(service)
    assert result["status"] == "failed"
    assert result["error_code"] == "curated_training_dataset_required"
    assert "run_id" not in result


def test_http_readonly_status_and_exact_browser_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    app = Application(ProjectConfig(state, "", "", None, combination()))
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    try:
        with pytest.raises(HTTPError) as absent:
            urlopen(url + "/api/local-training/status", timeout=3)
        assert absent.value.code == 401
        before = tuple(state.iterdir())
        with urlopen(Request(url + "/api/local-training/status",
                             headers={"Cookie": cookie}), timeout=3) as response:
            status = json.load(response)
        assert status["schema"] == "stpd/local-training-operation-v1"
        assert status["availability"] == "workspace_required"
        assert status["csrf_token"] == app.account.csrf
        assert tuple(state.iterdir()) == before
        body = json.dumps({"dataset_id": "a" * 64}).encode()
        headers = {"Cookie": cookie, "Content-Type": "application/json"}
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url + "/api/local-training/start", data=body,
                            headers=headers), timeout=3)
        assert denied.value.code == 403
        headers.update({"Origin": url, "X-CSRF-Token": app.account.csrf})
        with pytest.raises(HTTPError) as unavailable:
            urlopen(Request(url + "/api/local-training/start", data=body,
                            headers=headers), timeout=3)
        assert unavailable.value.code == 409
        assert json.load(unavailable.value)["error"] == "running_instance_unavailable"
        with pytest.raises(HTTPError) as new_unavailable:
            urlopen(Request(url + "/api/local-training/start",
                            data=json.dumps({"dataset_id": "a" * 64,
                                             "after_completed_operation_id": "b" * 32}).encode(),
                            headers=headers), timeout=3)
        assert new_unavailable.value.code == 409
        assert json.load(new_unavailable.value)["error"] == "running_instance_unavailable"
        with pytest.raises(HTTPError) as malformed_precondition:
            urlopen(Request(url + "/api/local-training/start",
                            data=json.dumps({"dataset_id": "a" * 64,
                                             "after_completed_operation_id": None}).encode(),
                            headers=headers), timeout=3)
        assert malformed_precondition.value.code == 400
        with pytest.raises(HTTPError) as unknown:
            urlopen(Request(url + "/api/local-training/start",
                            data=json.dumps({"dataset_id": "a" * 64, "extra": 1}).encode(),
                            headers=headers), timeout=3)
        assert unknown.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        app.close()


@pytest.mark.parametrize("recipe", [DEFAULT_RECIPE, MEMORY_RECIPE, RESET_K1_RECIPE])
def test_http_explicit_new_recipe_accepts_bounded_body_and_rejects_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recipe: str,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    previous_id = "1" * 32
    (owner.path.parent / OPERATION_FILE).write_text(json.dumps({
        "schema": "stpd/local-training-operation-v1", "status": "completed",
        "stage": "completed", "operation_id": previous_id,
        "dataset_id": dataset_id, "run_id": "2" * 64,
        "result_id": "3" * 64, "model_id": "4" * 64,
        "evaluation_id": "5" * 64, "_owner": list(owner.identity),
    }))
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    (config.state_dir / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port,
    }))
    headers = {
        "Cookie": f"{app.account.cookie_name}={app.account.cookie}",
        "Content-Type": "application/json", "Origin": url,
        "X-CSRF-Token": app.account.csrf,
    }
    body = json.dumps({"dataset_id": dataset_id,
                       "after_completed_operation_id": previous_id,
                       "recipe": recipe}).encode()
    assert len(body) <= 256

    def parked(held, _path, _identity, _owner, _store):
        held.__exit__(None, None, None)

    monkeypatch.setattr(app.local_training, "_run", parked)
    try:
        def post(payload: bytes, request_headers: dict[str, str] = headers):
            return urlopen(Request(url + "/api/local-training/start",
                                   data=payload, headers=request_headers), timeout=5)

        with pytest.raises(HTTPError) as forbidden:
            post(body, {**headers, "Origin": "http://invalid.local"})
        assert forbidden.value.code == 403
        with pytest.raises(HTTPError) as wrong_recipe:
            post(json.dumps({"dataset_id": dataset_id,
                             "after_completed_operation_id": previous_id,
                             "recipe": "unrecognized"}).encode())
        assert wrong_recipe.value.code == 409
        assert json.load(wrong_recipe.value)["error"] == "unsupported_training_recipe"
        with pytest.raises(HTTPError) as oversized:
            post(body + b" " * (257 - len(body)))
        assert oversized.value.code == 400
        with post(body) as response:
            started = json.load(response)["operation"]
        assert started["status"] == "pending"
        assert started.get("recipe", DEFAULT_RECIPE) == recipe
        assert started["previous_completed"]["operation_id"] == previous_id
        assert app.local_training._thread is not None
        app.local_training._thread.join(timeout=10)
    finally:
        server.shutdown()
        server.server_close()
        app.close()


def test_http_explicit_start_tracks_exact_completed_run_without_get_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    (config.state_dir / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port,
    }))

    def status() -> dict:
        with urlopen(Request(url + "/api/local-training/status",
                             headers={"Cookie": cookie}), timeout=5) as response:
            return json.load(response)

    def completion_failure(operation: dict) -> str:
        return json.dumps({
            "status": operation.get("status"), "stage": operation.get("stage"),
            "error_code": operation.get("error_code"),
            "safe_failure": app.local_training._failure_diagnostic,
        }, sort_keys=True)

    try:
        owner = configured_owner(config)
        operation_path = owner.path.parent / OPERATION_FILE
        before = tuple(store.manifest_ids())
        initial = status()
        assert initial["schema"] == "stpd/local-training-operation-v1"
        assert initial["operation"]["status"] == "idle"
        assert not operation_path.exists()
        assert tuple(store.manifest_ids()) == before
        body = json.dumps({"dataset_id": dataset_id}).encode()
        headers = {"Cookie": cookie, "Content-Type": "application/json",
                   "Origin": url, "X-CSRF-Token": initial["csrf_token"]}
        with urlopen(Request(url + "/api/local-training/start", data=body,
                             headers=headers), timeout=5) as response:
            started = json.load(response)
        assert started["availability"] == "ready"
        assert started["operation"]["dataset_id"] == dataset_id
        operation_id = started["operation"]["operation_id"]
        deadline = time.monotonic() + 45
        observed = []
        while time.monotonic() < deadline:
            current = status()["operation"]
            observed.append(current["status"])
            if current["status"] != "pending":
                break
            time.sleep(0.05)
        if current["status"] != "completed":
            pytest.fail(completion_failure(current))
        assert current["operation_id"] == operation_id
        result = store.get_manifest(current["result_id"])
        assert result.parent("run") == current["run_id"]
        assert result.parent("model") == current["model_id"]
        assert result.parent("offline_evaluation") == current["evaluation_id"]
        assert "pending" in observed or observed == ["completed"]
        manifest_ids = tuple(store.manifest_ids())
        second = status()["operation"]
        assert second == current
        assert tuple(store.manifest_ids()) == manifest_ids
        new_body = json.dumps({
            "dataset_id": dataset_id, "after_completed_operation_id": operation_id,
        }).encode()
        with urlopen(Request(url + "/api/local-training/start", data=new_body,
                             headers=headers), timeout=5) as response:
            new_started = json.load(response)["operation"]
        assert new_started["status"] == "pending"
        assert new_started["operation_id"] != operation_id
        assert new_started["previous_completed"]["result_id"] == current["result_id"]
        with pytest.raises(HTTPError) as repeated:
            urlopen(Request(url + "/api/local-training/start", data=new_body,
                            headers=headers), timeout=5)
        assert repeated.value.code == 409
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            new_current = status()["operation"]
            if new_current["status"] != "pending":
                break
            time.sleep(0.05)
        if new_current["status"] != "completed":
            pytest.fail(completion_failure(new_current))
        assert new_current["operation_id"] == new_started["operation_id"]
        with pytest.raises(HTTPError) as stale:
            urlopen(Request(url + "/api/local-training/start", data=new_body,
                            headers=headers), timeout=5)
        assert stale.value.code == 409
        with urlopen(Request(url + "/api/local-workspace/artifacts/" + current["result_id"],
                             headers={"Cookie": cookie}), timeout=5) as response:
            assert json.load(response)["artifact_id"] == current["result_id"]
        public = json.dumps({"start": started, "completed": second})
        assert app.control_token not in public and app.account.cookie not in public
        assert str(owner.store_dir) not in public
        assert "_owner" not in public and "_exit_code" not in public
    finally:
        server.shutdown()
        server.server_close()
        app.close()


@pytest.mark.parametrize("probe", ["exists", "is_file"])
def test_http_status_reads_journal_after_transient_missing_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: str,
) -> None:
    """A completed replacement must supersede a stale pathname probe."""
    config, dataset_id, _, store = _ready(tmp_path, monkeypatch)
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()))
    app = Application(config, config_path=config_path)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    cookie = f"{app.account.cookie_name}={app.account.cookie}"
    (config.state_dir / "runtime.json").write_text(json.dumps({
        "instance_id": app.instance_id, "configuration_id": configuration_id(config),
        "port": server.server_port,
    }))
    finish = threading.Event()
    launches = []

    def parked(held, _path, identity, _owner, _store):
        launches.append(identity)
        try:
            assert finish.wait(10)
        finally:
            held.__exit__(None, None, None)

    monkeypatch.setattr(app.local_training, "_run", parked)
    owner = configured_owner(config)
    path = owner.path.parent / OPERATION_FILE
    try:
        headers = {"Cookie": cookie, "Content-Type": "application/json",
                   "Origin": url, "X-CSRF-Token": app.account.csrf}
        body = json.dumps({"dataset_id": dataset_id}).encode()
        with urlopen(Request(url + "/api/local-training/start", data=body,
                             headers=headers), timeout=5) as response:
            started = json.load(response)["operation"]
        assert started["status"] == "pending"
        journal = path.read_bytes()
        manifests = tuple(store.manifest_ids())
        original_probe = getattr(Path, probe)

        def missing_during_replacement(self):
            if self == path:
                # Observe the rename gap, then complete publication and cleanup
                # before the reader checks for unresolved replacement files.
                backup = path.with_name("." + path.name + ".previous-race")
                os.replace(path, backup)
                missing = original_probe(path)
                os.replace(backup, path)
                assert missing is False
                return missing
            return original_probe(self)

        with monkeypatch.context() as racing:
            racing.setattr(Path, probe, missing_during_replacement)
            with urlopen(Request(url + "/api/local-training/status",
                                 headers={"Cookie": cookie}), timeout=5) as response:
                observed = json.load(response)
        assert observed["availability"] == "ready", observed
        assert observed["operation"] == started
        assert path.read_bytes() == journal
        assert tuple(store.manifest_ids()) == manifests
        assert launches == [started["operation_id"]]
    finally:
        finish.set()
        if app.local_training._thread is not None:
            app.local_training._thread.join(timeout=10)
            assert not app.local_training._thread.is_alive()
        server.shutdown()
        server.server_close()
        app.close()


@pytest.mark.parametrize("winerror", [2, 5, 32])
def test_replaceable_journal_read_reconciles_windows_observation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int,
) -> None:
    path = tmp_path / "operation.json"
    path.write_bytes(b"durable operation")
    original_open = replaceable_file.open_replaceable_read
    attempts = []

    @contextmanager
    def interrupted_open(file):
        attempts.append(file)
        if len(attempts) == 1:
            error = (FileNotFoundError(2, "synthetic rename gap") if winerror == 2 else
                     PermissionError(13, "synthetic Windows sharing denial"))
            error.winerror = winerror
            raise error
        with original_open(file) as stream:
            yield stream

    monkeypatch.setattr(replaceable_file, "open_replaceable_read", interrupted_open)
    assert replaceable_file.read_replaceable_bytes(path) == b"durable operation"
    assert attempts == [path, path]
    assert path.read_bytes() == b"durable operation"


@pytest.mark.parametrize("winerror,expected_attempts", [(2, 10), (5, 10), (32, 10), (87, 1)])
def test_replaceable_journal_observation_failure_is_bounded_and_readonly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int, expected_attempts: int,
) -> None:
    path = tmp_path / "operation.json"
    path.write_bytes(b"durable operation")
    attempts = []

    @contextmanager
    def unavailable(file):
        attempts.append(file)
        error = PermissionError(13, "synthetic persistent observation failure")
        error.winerror = winerror
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr(replaceable_file, "open_replaceable_read", unavailable)
    with pytest.raises(PermissionError):
        replaceable_file.read_replaceable_bytes(path)
    assert attempts == [path] * expected_attempts
    assert path.read_bytes() == b"durable operation"
    assert tuple(tmp_path.iterdir()) == (path,)


def test_status_missing_journal_while_owner_holds_lock_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _, _, store = _ready(tmp_path, monkeypatch)
    service = LocalTrainingService(config)
    owner = configured_owner(config)
    path = owner.path.parent / OPERATION_FILE
    lock_path = path.parent / LOCK_FILE
    manifests = tuple(store.manifest_ids())
    assert service.status()["operation"] == {"status": "idle"}
    assert not lock_path.exists()  # A GET must not initialize the owner path.
    with instance_lock(lock_path):
        assert not path.exists()
        before = tuple(path.parent.iterdir())
        observed = service.status()
        assert observed["availability"] == "recovery_required"
        assert observed["reason"] == "operation_recovery_required"
        assert tuple(path.parent.iterdir()) == before
        assert not path.exists()
    assert service._thread is None
    assert tuple(store.manifest_ids()) == manifests


def test_existing_instance_lock_never_creates_missing_owner_path(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "owner.lock"
    with pytest.raises(FileNotFoundError), instance_lock(path, create=False):
        pytest.fail("missing lock must not be acquired")
    assert not path.parent.exists()
    path.parent.mkdir()
    path.write_bytes(b"existing lock")
    with (instance_lock(path, create=False),
          pytest.raises(BoundaryError, match="already_running"),
          instance_lock(path, create=False)):
        pytest.fail("held lock must not be acquired")
    assert path.read_bytes() == b"existing lock"


@pytest.mark.parametrize("kind", ["directory", "corrupt", "wrong_owner"])
def test_journal_descriptor_read_retains_recovery_boundaries(
    tmp_path: Path, kind: str,
) -> None:
    path = tmp_path / "operation.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "corrupt":
        path.write_bytes(b"{broken")
    else:
        path.write_text(json.dumps({
            "schema": training_module.SCHEMA, "status": "pending", "stage": "reserving",
            "operation_id": "a" * 32, "dataset_id": "b" * 64, "_owner": ["other"],
        }))
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        LocalTrainingService._read(path, ("owner",))


def test_status_reader_does_not_block_training_journal_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a status read to remain open during the real owner stage update."""
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    path = owner.path.parent / OPERATION_FILE
    operation_id = "a" * 32
    training_module.write_replaceable_json(path, {
        "schema": "stpd/local-training-operation-v1", "status": "pending",
        "stage": "reserving", "operation_id": operation_id,
        "dataset_id": dataset_id, "_owner": list(owner.identity),
    })
    service = LocalTrainingService(config)
    entered = threading.Event()
    release = threading.Event()
    reader = {"open": False, "share_delete": False}
    observed: list[dict] = []
    errors: list[BaseException] = []
    status_thread_name = "held-training-status-reader"
    original_path_open = Path.open
    original_replace = os.replace

    def hold_reader(*, share_delete: bool) -> None:
        reader.update(open=True, share_delete=share_delete)
        entered.set()
        assert release.wait(10)

    def path_open(self, *args, **kwargs):
        stream = original_path_open(self, *args, **kwargs)
        if self == path and threading.current_thread().name == status_thread_name:
            hold_reader(share_delete=False)
        return stream

    def windows_replace(source, destination):
        if Path(destination) == path and reader["open"] and not reader["share_delete"]:
            error = PermissionError(13, "synthetic Windows sharing denial")
            error.winerror = 5
            raise error
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(os, "replace", windows_replace)
    # New code uses a shared-delete reader on both OSes. The old source has no
    # module, so this same test reaches the ordinary Path.open reader above.
    replaceable = sys.modules.get("spireagent.storage.replaceable_file")
    if replaceable is not None:
        original_shared_open = replaceable.open_replaceable_read

        @contextmanager
        def shared_open(file):
            with original_shared_open(file) as stream:
                if file == path and threading.current_thread().name == status_thread_name:
                    hold_reader(share_delete=True)
                yield stream

        monkeypatch.setattr(replaceable, "open_replaceable_read", shared_open)

    def read_status() -> None:
        try:
            observed.append(service.status())
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=read_status, name=status_thread_name)
    with instance_lock(path.parent / LOCK_FILE):
        thread.start()
        try:
            assert entered.wait(5), "status must hold its journal reader open"
            service._advance(path, operation_id, stage="public_view")
        finally:
            release.set()
            thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors, errors
    assert observed[0]["operation"]["operation_id"] == operation_id
    assert observed[0]["operation"]["status"] == "pending"
    assert json.loads(path.read_bytes())["stage"] == "public_view"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows file sharing")
def test_windows_replaceable_reader_allows_atomic_journal_replacement(tmp_path: Path) -> None:
    from spireagent.storage.replaceable_file import open_replaceable_read

    path = tmp_path / "operation.json"
    path.write_bytes(b"old")
    with path.open("rb") as ordinary:
        assert ordinary.read() == b"old"
        with pytest.raises(OSError) as denied:
            training_module.write_replaceable_json(path, {"status": "new"})
        assert getattr(denied.value, "winerror", None) is not None
    assert path.read_bytes() == b"old"
    with open_replaceable_read(path) as shared:
        assert shared.read() == b"old"
        training_module.write_replaceable_json(path, {"status": "new"})
        assert shared.seek(0) == 0
        assert shared.read() == b"old"
    assert json.loads(path.read_bytes()) == {"status": "new"}


@pytest.mark.parametrize("winerror", [1175, 1176, 1177])
def test_failed_windows_journal_replacement_preserves_recovery_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int,
) -> None:
    config, dataset_id, _, _ = _ready(tmp_path, monkeypatch)
    owner = configured_owner(config)
    path = owner.path.parent / OPERATION_FILE
    operation_id = "a" * 32
    training_module.write_replaceable_json(path, {
        "schema": "stpd/local-training-operation-v1", "status": "pending",
        "stage": "reserving", "operation_id": operation_id,
        "dataset_id": dataset_id, "_owner": list(owner.identity),
    })
    old = path.read_bytes()

    def fail_replacement(temporary: Path, target: Path, backup: Path) -> None:
        if winerror == 1177:
            os.replace(target, backup)  # Microsoft's 1177 missing-canonical layout.
        error = PermissionError(13, "synthetic ReplaceFileW failure")
        error.winerror = winerror
        raise error

    with pytest.raises(PermissionError):
        replaceable_file._write_json(
            path, {"schema": "stpd/local-training-operation-v1", "status": "pending",
                   "stage": "public_view", "operation_id": operation_id,
                   "dataset_id": dataset_id, "_owner": list(owner.identity)},
            fail_replacement)

    candidates = list(path.parent.glob("." + path.name + ".pending-*"))
    assert len(candidates) == 1
    assert json.loads(candidates[0].read_bytes())["stage"] == "public_view"
    backups = list(path.parent.glob("." + path.name + ".previous-*"))
    if winerror == 1177:
        assert not path.exists()
        assert len(backups) == 1 and backups[0].read_bytes() == old
        observed = LocalTrainingService(config).status()
        assert observed["availability"] == "recovery_required"
        assert observed["reason"] == "operation_recovery_required"
        with pytest.raises(BoundaryError) as rejected:
            LocalTrainingService(config).start(dataset_id)
        assert rejected.value.code == "operation_recovery_required"
    else:
        assert path.read_bytes() == old
        assert not backups
