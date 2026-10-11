"""Metadata projection is read-only and cannot load a numerical backend."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, combination
from spireagent.workbench.local_model_export import SUPPORT_SCHEMA, LocalModelExport
from stpd.fullrun.text_menu_inputs import IDENTITY

PRODUCER = Producer("test/export-support", "a" * 40, "b" * 64)


def source(tmp_path: Path, parameters: dict, *, kind: str = "model"):
    archive = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    # Intentionally unavailable payload bytes: support must not verify or load them.
    model = Manifest(kind, PRODUCER, payloads=(Payload("weights", "c" * 64, 10),),
                     parameters=FrozenObject.of(parameters))
    archive.blobs.put_if_absent(f"manifests/{model.artifact_id}.json", model.to_bytes())
    registry_path = tmp_path / "registry.sqlite"
    sync_registry(archive, SQLiteRegistry(registry_path))
    state = tmp_path / "state"
    state.mkdir()
    config = ProjectConfig(state, "", "", None, combination(),
                           LocalResearchWorkspaceConfig(tmp_path / "store", registry_path))
    return LocalModelExport(config), model


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("schema,model_type,profile", [
    ("stpd/native-structured-m2-model-v1", "native", "native-logical-v1"),
    ("stpd/native-structured-m2-model-v2", "native", "native-logical-v1"),
    ("stpd/source3-ordered-native-m2-model-v1", "native", "native-logical-v1"),
    ("stpd/structured-m2-model-v1", "structured", "text-menu-m2-v2"),
    ("stpd/structured-m2-model-v2", "structured", "text-menu-m2-v2"),
    ("stpd/structured-m2-model-v3", "structured", "text-menu-m2-v2"),
])
def test_closed_family_support_is_not_verified_readiness(tmp_path, schema, model_type, profile):
    service, model = source(tmp_path, {"schema": schema})
    before = files(tmp_path)
    assert service.support(model.artifact_id) == {
        "schema": SUPPORT_SCHEMA, "model_id": model.artifact_id, "status": "supported",
        "model_type": model_type, "runtime_profile": profile, "verification_state": "not_checked",
    }
    assert service.thread is None
    assert files(tmp_path) == before
    with pytest.raises(BoundaryError, match="verified_export_required"):
        service.verified_for_registration(model.artifact_id)


@pytest.mark.parametrize("alter,kind", [
    ({"schema": "future/native-model-v99"}, "model"),
    ({"schema": "stpd/native-structured-m2-model-v1"}, "dataset"),
    ({"schema": ["stpd/native-structured-m2-model-v1"]}, "model"),
    ({"schema": "stpd/experimental-m2-model-v1", "config": {}}, "model"),
    ({"schema": "stpd/stage1a-model-v1", "config": {}}, "model"),
])
def test_unknown_invalid_metadata_cannot_inherit_support(tmp_path, alter, kind):
    service, model = source(tmp_path, alter, kind=kind)
    before = files(tmp_path)
    assert service.support(model.artifact_id)["status"] == "unsupported"
    assert files(tmp_path) == before
    with pytest.raises(BoundaryError):
        service.support("not-an-artifact")
    with pytest.raises(BoundaryError):
        service.support("d" * 64)


@pytest.mark.parametrize("recipe", ["stage1a.b.s.v2", "stage1a.dsimple.s.v1"])
def test_legacy_scratch_metadata_support_and_exact_serializer(tmp_path, recipe):
    parameters = {"schema": "stpd/stage1a-model-v1", "qualification": "engineering_only",
                  "config": {"recipe": recipe, "device": "cpu"},
                  "backbone": {"kind": "scratch"}, "serializer": IDENTITY}
    service, model = source(tmp_path, parameters)
    assert service.support(model.artifact_id)["runtime_profile"] == "text-menu-v1"
    assert denied_numerical_support(service, model)["runtime_profile"] == "text-menu-v1"
    parameters["serializer"] = {**IDENTITY, "future": True}
    altered = Manifest("model", PRODUCER, parameters=FrozenObject.of(parameters))
    archive = ManifestArtifactStore(LocalBlobStore(service.config.research_workspace.store_dir))
    archive.publish(altered)
    assert service.support(altered.artifact_id)["status"] == "unsupported"


def denied_numerical_support(service, model):
    script = '''
import importlib.abc, json, sys
class DenyNumerical(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'numpy', 'safetensors'}:
            raise RuntimeError('numerical import denied: ' + fullname)
sys.meta_path.insert(0, DenyNumerical())
from pathlib import Path
from spireagent.workbench.developer import ProjectConfig, LocalResearchWorkspaceConfig
from spireagent.workbench.local_model_export import LocalModelExport
raw = json.loads(sys.argv[1])
workspace = raw.get('research_workspace')
registered = (LocalResearchWorkspaceConfig(Path(workspace['store_dir']),
    Path(workspace['registry_path'])) if workspace else None)
config = ProjectConfig(Path(raw['state_dir']), '', '', None, raw['combination'], registered)
value = LocalModelExport(config).support(sys.argv[2])
assert value['status'] == 'supported'
assert not {'torch', 'numpy', 'safetensors'}.intersection(sys.modules)
print(json.dumps(value))
'''
    return json.loads(subprocess.check_output(
        [sys.executable, "-c", script, json.dumps(service.config.to_dict()), model.artifact_id],
        text=True,
    ))


def test_support_import_and_execution_never_import_numerical_modules(tmp_path):
    service, model = source(tmp_path, {"schema": "stpd/source3-ordered-native-m2-model-v1"})
    before = files(tmp_path)
    assert denied_numerical_support(service, model)["verification_state"] == "not_checked"
    assert files(tmp_path) == before



@pytest.mark.parametrize("profile", ["text-menu-v1", "text-menu-v2",
                                     "text-menu-v1-confirmed-interaction",
                                     "text-menu-v2-confirmed-interaction"])
def test_memory_support_uses_recorded_recipe_profile_without_payload_reads(tmp_path, profile):
    from dataclasses import asdict

    from spireagent.artifact_contracts import Parent
    from spireagent.workbench.memory_recipe import _FIXED_CONFIG
    from stpd.fullrun.memory_projection_config import (
        history_episode_projection_config,
        v2_episode_projection_config,
    )

    parameters = {"schema": "stpd/experimental-m2-model-v1", "partition": "train",
                  "qualification": "engineering_only", "episodes": 1,
                  "config": {**_FIXED_CONFIG, "vocab_size": 128, "episode_count": 1,
                             "slots": 1, "reset_each_step": False}}
    service, _unused = source(tmp_path, parameters)
    archive = ManifestArtifactStore(LocalBlobStore(service.config.research_workspace.store_dir))
    original = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
        "schema": ("stpd/managed-text-menu-observed-source-v1" if profile.startswith("text-menu-v2")
                   else "stpd/human-text-input-source-v1"),
    }))
    archive.publish(original)
    projection = (asdict(history_episode_projection_config(profile)) if "confirmed" in profile
                  else asdict(v2_episode_projection_config()) if profile == "text-menu-v2"
                  else None)
    training_input = Manifest("training_input", PRODUCER,
                              parents=(Parent("source", original.artifact_id),),
                              parameters=FrozenObject.of({
                                  "schema": "stpd/experimental-m2-training-input-v2",
                                  "projection_config": projection,
                              }))
    archive.publish(training_input)
    run = Manifest("run", PRODUCER, parents=(Parent("training_input", training_input.artifact_id),),
                   parameters=FrozenObject.of({"config": parameters["config"]}))
    archive.publish(run)
    model = Manifest(
        "model", PRODUCER,
        parents=(Parent("run", run.artifact_id),
                 Parent("training_input", training_input.artifact_id)),
        parameters=FrozenObject.of(parameters),
    )
    archive.publish(model)
    before = files(tmp_path)
    value = service.support(model.artifact_id)
    assert value["status"] == "supported"
    assert value["model_type"] == "memory"
    assert value["runtime_profile"] == ("text-menu-m2-v2" if profile.startswith("text-menu-v2")
                                        else "text-menu-m2-v1")
    assert value["memory_recipe"].endswith(".v2" if profile.startswith("text-menu-v2") else ".v1")
    assert ("confirmed-interaction" in value["memory_recipe"]) == ("confirmed" in profile)
    assert value["verification_state"] == "not_checked"
    assert denied_numerical_support(service, model) == value
    assert files(tmp_path) == before


def test_support_get_authentication_and_strict_query_without_a_socket(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from spireagent.workbench import developer_server

    service, model = source(tmp_path, {"schema": "stpd/source3-ordered-native-m2-model-v1"})
    app = SimpleNamespace(
        local_model_export=service,
        account=SimpleNamespace(cookie_name="workbench", cookie="approved-cookie"),
    )
    captured = {}

    def no_socket(address, handler):
        captured["handler"] = handler
        return SimpleNamespace(server_port=12345)

    monkeypatch.setattr(developer_server, "ThreadingHTTPServer", no_socket)
    server = developer_server.create_server(app)
    before = files(tmp_path)

    def get(query, *, cookie="workbench=approved-cookie", host="127.0.0.1:12345"):
        handler = captured["handler"].__new__(captured["handler"])
        handler.server = server
        handler.headers = {"Host": host, "Cookie": cookie}
        handler.path = "/api/local-model-exports/support" + query
        reply = []
        handler.respond = lambda code, body: reply.append((code, json.loads(body)))
        handler.do_GET()
        assert len(reply) == 1
        return reply[0]

    code, value = get("?model_id=" + model.artifact_id)
    assert code == 200 and value == service.support(model.artifact_id)
    assert "csrf_token" not in value
    assert get("?model_id=" + model.artifact_id, cookie="")[0] == 401
    assert get("?model_id=" + model.artifact_id, host="untrusted.test")[0] == 403
    for query in ("", "?model_id=", "?model_id=" + model.artifact_id + "&extra=1",
                  "?model_id=" + model.artifact_id + "&model_id=" + model.artifact_id):
        assert get(query)[0] in {400, 409}
    assert get("?model_id=" + "d" * 64)[0] == 409
    assert files(tmp_path) == before
    assert service.thread is None



def test_managed_workspace_metadata_read_never_initializes_or_imports_backend(tmp_path):
    from dataclasses import replace

    service, model = source(tmp_path, {"schema": "stpd/source3-ordered-native-m2-model-v1"})
    state = service.config.state_dir
    identity, created = "e" * 32, "2026-01-01T00:00:00Z"
    directory = state / "managed-research-workspaces" / identity
    directory.mkdir(parents=True)
    (tmp_path / "store").rename(directory / "store")
    (tmp_path / "registry.sqlite").rename(directory / "registry.sqlite")
    (state / "managed-research-workspace.json").write_text(json.dumps({
        "schema": "stpd/managed-local-workspace-registration-v1",
        "workspace_id": identity, "created_at": created,
    }))
    (directory / "workspace.json").write_text(json.dumps({
        "schema": "stpd/managed-local-workspace-v1", "workspace_id": identity,
        "created_at": created, "store": "store", "registry": "registry.sqlite",
    }))
    service = LocalModelExport(replace(service.config, research_workspace=None))
    before = files(tmp_path)
    assert denied_numerical_support(service, model)["status"] == "supported"
    assert files(tmp_path) == before
    # Legacy read-only metadata remains readable; it does not repair curation.
    assert not (directory / "curation.sqlite").exists()
    (directory / "workspace.json").write_text("{}")
    with pytest.raises(BoundaryError, match="workspace_marker_invalid"):
        service.support(model.artifact_id)
