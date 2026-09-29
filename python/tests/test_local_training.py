"""Synthetic verified recording through the exact local D-Simple training owner."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import test_local_human_dataset as human_fixture
import test_local_recording_preview as recording_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
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
)
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_curation import LocalLedger
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_training import (
    DEFAULT_RECIPE,
    MEMORY_RECIPE,
    OPERATION_FILE,
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


def test_m2_preparation_limit_error_is_durable_unknown_without_retry(
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
    assert failed["status"] == "interrupted_unknown", failed
    assert failed["error_code"] == "m2_limit_exceeded_no_truncation"
    assert len(calls) == 1 and "prepare-workbench-memory" in calls[0]
    assert set(store.manifest_ids()) == before
    assert LocalTrainingService(config).status()["operation"]["status"] == "interrupted_unknown"
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        service.start(dataset_id, recipe=MEMORY_RECIPE)
    assert len(calls) == 1


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
    producer = source_identity(ROOT)

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
    assert result["status"] == "interrupted_unknown", result
    assert result["error_code"] == "training_process_failed"
    assert "run_id" in result and "result_id" not in result
    owner = configured_owner(config)
    operation = json.loads((owner.path.parent / OPERATION_FILE).read_bytes())
    assert operation["_exit_code"] == 7
    log = owner.path.parent / ("local-training-" + result["operation_id"] + ".log")
    assert log.read_bytes() == b"synthetic private failure\n"
    assert "synthetic private failure" not in json.dumps(service.status())
    with pytest.raises(BoundaryError, match="previous_training_outcome_unknown"):
        LocalTrainingService(config).start(dataset_id)


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
        assert current["status"] == "completed", json.dumps(current, sort_keys=True)
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
        assert new_current["status"] == "completed", new_current
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
