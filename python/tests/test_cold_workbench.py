"""Collection and recorded-report reads do not require the optional ML backend."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

import pytest
from test_artifact_store_v1 import PRODUCER
from test_local_evaluation import _token

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import FrozenObject, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_evaluation import summary
from spireagent.workbench.memory_recipe import MEMORY_RECIPES, memory_settings_for_recipe
from stpd.fullrun.confirmed_interaction import HISTORY_PROFILES
from stpd.fullrun.evaluation import candidate_metrics, summarize_rows
from stpd.fullrun.memory_projection_config import (
    MemoryEpisodeProjectionConfig,
    history_episode_projection_config,
    v2_episode_projection_config,
)
from stpd.workers.memory_ranking import MemoryConfig
from stpd.workers.report_schemas import MEMORY_EVALUATION_PROTOCOL, MEMORY_EVALUATION_SCHEMA


def _memory_metadata(store: ManifestArtifactStore) -> dict[str, str]:
    """Publish metadata-only fixtures; detail reads must never load model weights."""
    models = {}
    for recipe in sorted(MEMORY_RECIPES):
        settings = memory_settings_for_recipe(recipe)
        v2 = settings.input_profile.startswith("text-menu-v2")
        config = asdict(MemoryConfig(
            vocab_size=32, episode_count=2, slots=settings.slots,
            reset_each_step=settings.reset_each_step, width=48, layers=1, heads=2,
            feedforward=96, max_tokens=16384, max_total_input_tokens=4_194_304,
            max_episode_observations=768, max_episode_input_tokens=4_194_304,
            max_chunk_steps=2, max_chunk_input_tokens=24_576, max_actions_per_step=256))
        source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
            "schema": "stpd/managed-text-menu-observed-source-v1" if v2
            else "stpd/human-text-input-source-v1"}))
        store.publish(source)
        if settings.input_profile in HISTORY_PROFILES:
            projection = history_episode_projection_config(settings.input_profile)
        elif v2:
            projection = v2_episode_projection_config()
        else:
            projection = MemoryEpisodeProjectionConfig(
                "stpd/memory-episode-projection-config-v1", 64)
        training_input = Manifest("training_input", PRODUCER,
            parents=(Parent("source", source.artifact_id),), parameters=FrozenObject.of({
                "schema": "stpd/experimental-m2-training-input-v2",
                "projection_config": asdict(projection)}))
        store.publish(training_input)
        run = Manifest("run", PRODUCER,
            parents=(Parent("training_input", training_input.artifact_id),),
            parameters=FrozenObject.of({"config": config}))
        store.publish(run)
        model = Manifest("model", PRODUCER,
            parents=(Parent("run", run.artifact_id),
                     Parent("training_input", training_input.artifact_id)),
            parameters=FrozenObject.of({"schema": "stpd/experimental-m2-model-v1",
                "config": config, "episodes": 2, "partition": "train",
                "qualification": "engineering_only"}))
        store.publish(model)
        models[model.artifact_id] = recipe
    return models


def _memory_report(store: ManifestArtifactStore, model_id: str) -> Manifest:
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({"partition": "dev"}))
    store.publish(source)
    parameters = {"partition": "dev", "protocol": MEMORY_EVALUATION_PROTOCOL,
                  "semantic_overlap": False, "operation_id": "a" * 32,
                  "qualification": "engineering_only"}
    evaluation_input = Manifest("analysis", PRODUCER,
        parents=(Parent("model", model_id), Parent("source", source.artifact_id)),
        payloads=(store.put_bytes("projection", b"PRIVATE_PROJECTION_SENTINEL"),),
        parameters=FrozenObject.of({**parameters,
            "schema": "stpd/experimental-m2-evaluation-input-v1"}))
    store.publish(evaluation_input)
    rows = [{"transition_id": "PRIVATE_ROW_SENTINEL", "run_id": "recorded-group",
             "surface": "text_menu", "family": "observed_input", "split": "dev",
             "candidate_count": 2, **candidate_metrics([1.0, 0.0], (0,))}]
    report = Manifest("offline_evaluation", PRODUCER,
        parents=(Parent("model", model_id), Parent("source", source.artifact_id),
                 Parent("evaluation_input", evaluation_input.artifact_id)),
        payloads=(store.put_bytes("metrics", json_bytes({"rows": rows,
            "summary": summarize_rows(rows, seed=1701, native_run_independence=False)})),),
        parameters=FrozenObject.of({**parameters, "schema": MEMORY_EVALUATION_SCHEMA,
            "scientific_verdict": "not_claimed", "native_run_independence": False,
            "strict_deduplicated_benchmark": False, "rows": 1,
            "model_selection_exposure": "unknown", "train_dev_rendered_overlap_count": 0}))
    store.publish(report)
    return report


def test_cold_collection_and_existing_report_without_ml_imports(tmp_path: Path) -> None:
    token_store, evaluation_id = _token(tmp_path / "sample")
    store = cast(ManifestArtifactStore, token_store)
    assert isinstance(store.blobs, LocalBlobStore)
    memory_models = _memory_metadata(store)
    assert len(memory_models) == len(MEMORY_RECIPES)
    assert set(memory_models.values()) == MEMORY_RECIPES
    memory_reports = {_memory_report(store, model_id).artifact_id: recipe
                      for model_id, recipe in memory_models.items()}
    root = Path(__file__).resolve().parents[1]
    script = r'''
import builtins
import json
import sys
import threading
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, build_opener

real_import = builtins.__import__
def no_ml(name, *args, **kwargs):
    if name.split(".", 1)[0] in {"torch", "tokenizers", "safetensors", "transformers"}:
        raise AssertionError("ML import during read-only Workbench request: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = no_ml

from spireagent.storage.registry import SQLiteRegistry
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.developer_server import Application, create_server

(store_root, registry_path, state_root, evaluation_id,
 memory_models_json, memory_reports_json) = sys.argv[1:]
SQLiteRegistry(Path(registry_path))
config = ProjectConfig(Path(state_root), "", "", None, combination(),
    LocalResearchWorkspaceConfig(Path(store_root), Path(registry_path)))
app = Application(config)
server = create_server(app)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
client = build_opener(HTTPCookieProcessor(CookieJar()))
base = "http://127.0.0.1:" + str(server.server_port)
try:
    for route in ("/", "/api/status", "/api/local-models/status",
                  "/api/local-training/status", "/api/local-memory-evaluations/status",
                  "/api/member/collection-flow"):
        with client.open(base + route) as response:
            assert response.status == 200, route
    with client.open(base + "/api/local-workspace?kind=offline_evaluation") as response:
        assert json.load(response)["total"] == 1 + len(json.loads(memory_reports_json))
    with client.open(base + "/api/local-workspace/evaluations/" + evaluation_id) as response:
        report = json.load(response)
    assert report["evaluation_id"] == evaluation_id
    assert report["validation_scope"] == "recorded_report_and_parent_identities"
    for model_id, recipe in json.loads(memory_models_json).items():
        with client.open(base + "/api/local-workspace/artifacts/" + model_id) as response:
            detail = json.load(response)
        assert detail["artifact_id"] == model_id
        assert detail["workbench_memory_recipe"] == recipe
    for report_id, recipe in json.loads(memory_reports_json).items():
        with client.open(base + "/api/local-workspace/evaluations/" + report_id) as response:
            detail = json.load(response)
        assert detail["model_recipe"] == recipe
        assert detail["evaluation_input_schema"] == "stpd/experimental-m2-evaluation-input-v1"
        assert "model_view_id" not in detail and "view_schema" not in detail
    import spireagent.workbench.local_model_dependencies as dependencies
    dependencies.find_spec = lambda _name: None
    for operation in (lambda: app.local_model_registration.register("0" * 64),):
        try:
            operation()
        except BoundaryError as error:
            assert error.code == "local_models_extra_required"
        else:
            raise AssertionError("missing ML dependencies did not block mutation")
    assert not {"torch", "tokenizers", "safetensors", "transformers"} & sys.modules.keys()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    app.close()
'''
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    interpreter = os.environ.get("STPD_TEST_COLD_INTERPRETER", sys.executable)
    child = subprocess.run(
        [interpreter, "-B", "-c", script, str(store.blobs.root),
         str(tmp_path / "registry.sqlite"), str(tmp_path / "state"), evaluation_id,
         json.dumps(memory_models), json.dumps(memory_reports)],
        env=env, cwd=root, text=True, capture_output=True, timeout=30,
    )
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("change", [None, "width", "missing_config_key", "missing_parent"])
def test_memory_summary_reads_only_report_and_never_guesses_recipe(tmp_path, monkeypatch, change):
    from test_artifact_store_v1 import store as make_store

    store = make_store(tmp_path / "objects")
    models = _memory_metadata(store)
    model_id = next(identity for identity, recipe in models.items()
                    if recipe == "stage1a.dsimple.m2.k8.experimental.v2")
    if change:
        model = store.get_manifest(model_id)
        parameters = model.parameters.value()
        if change == "width":
            parameters["config"]["width"] = 96
        elif change == "missing_config_key":
            del parameters["config"]["reset_each_step"]
        model = replace(model, parameters=FrozenObject.of(parameters),
                        parents=() if change == "missing_parent" else model.parents)
        store.publish(model)
        model_id = model.artifact_id
    report = _memory_report(store, model_id)
    reads = []
    original = store.read_payload

    def report_only(payload):
        reads.append(payload.role)
        assert payload.role == "metrics"
        return original(payload)

    monkeypatch.setattr(store, "read_payload", report_only)
    value = summary(store, report.artifact_id)
    assert value["model_recipe"] == (None if change else models[model_id])
    assert value["evaluation_input_id"] == report.parent("evaluation_input")
    assert value["evaluation_input_schema"] == "stpd/experimental-m2-evaluation-input-v1"
    assert value["validation_scope"] == "recorded_report_and_parent_identities"
    assert value["decision_count"] == 1
    assert reads == ["metrics"]
    assert "PRIVATE_" not in json.dumps(value)
    assert "model_view_id" not in value and "view_schema" not in value


@pytest.mark.parametrize(("profile", "input_profile"), [
    ("text-menu-v1", None),
    ("text-menu-m2-v1", "text-menu-v1"),
    ("text-menu-m2-v2", "text-menu-v2"),
    ("text-menu-m2-v1", "text-menu-v1-confirmed-interaction"),
    ("text-menu-m2-v2", "text-menu-v2-confirmed-interaction"),
])
def test_completed_export_status_preserves_profile_without_importing_ml(
    tmp_path, profile, input_profile,
):
    # Only the completed export's metadata is needed for this read. Weight and
    # binding verification must remain unavailable until the backend is present.
    script = r'''
import builtins
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

real_import = builtins.__import__
def no_ml(name, *args, **kwargs):
    if name.split(".", 1)[0] in {"torch", "tokenizers", "safetensors", "transformers"}:
        raise AssertionError("ML import while reading export status: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = no_ml
from spireagent.workbench import local_model_dependencies as dependencies
from spireagent.workbench.local_model_registration import LocalModelRegistration
from stpd.fullrun.memory_projection_config import (
    MemoryEpisodeProjectionConfig, history_episode_projection_config,
    v2_episode_projection_config,
)
dependencies.find_spec = lambda _: None
state, profile, input_profile = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
model_id = "a" * 64
operation = {"status": "completed", "model_id": model_id}
if input_profile != "None":
    operation["model_type"] = "memory"
    export = state / "model-exports" / model_id
    export.mkdir(parents=True)
    if input_profile.endswith("confirmed-interaction"):
        projection = history_episode_projection_config(input_profile)
    elif input_profile == "text-menu-v2":
        projection = v2_episode_projection_config()
    else:
        projection = MemoryEpisodeProjectionConfig(
            "stpd/memory-episode-projection-config-v1", 0)
    (export / "model.json").write_text(json.dumps({"projection_config": asdict(projection)}))
before = {str(path): path.read_bytes() for path in state.rglob("*") if path.is_file()}
service = LocalModelRegistration(SimpleNamespace(state_dir=state),
    SimpleNamespace(status=lambda: {"availability": "ready", "operation": operation}),
    SimpleNamespace())
result = service.status(model_id)
assert result["status"] == "unavailable", result
assert result["reason_code"] == "local_models_extra_required", result
assert result["runtime_profile"] == profile, result
assert operation["status"] == "completed"
assert {str(path): path.read_bytes() for path in state.rglob("*") if path.is_file()} == before
assert not {"torch", "tokenizers", "safetensors", "transformers"} & sys.modules.keys()
'''
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    child = subprocess.run(
        [os.environ.get("STPD_TEST_COLD_INTERPRETER", sys.executable), "-B", "-c", script,
         str(tmp_path), profile, str(input_profile)],
        env=env, cwd=root, text=True, capture_output=True, timeout=20,
    )
    assert child.returncode == 0, child.stderr
