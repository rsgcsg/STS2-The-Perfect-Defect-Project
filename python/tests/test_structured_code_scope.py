"""Versioned structured identity checks using synthetic CPU data and copied source."""

from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from test_structured_resume import Authority, PauseControl, dataset, equal_tree, finish
from test_structured_s0 import manifest_for, sample

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd import structured_policy_installation as installation
from stpd.canonical import semantic_hash
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.policy import structured_export
from stpd.policy.structured_port import StructuredPolicyAdapter
from stpd.structured_code_scope import (
    INFERENCE_PATHS,
    INFERENCE_SCOPE,
    LEGACY_SCOPE,
    ROOT,
    SCOPED_CHECKPOINT_SCHEMA,
    SCOPED_CONFIG_SCHEMA,
    SCOPED_MODEL_SCHEMA,
    SCOPED_PACKAGE_SCHEMA,
    SCOPED_RUN_SCHEMA,
    TRAINING_PATHS,
    TRAINING_SCOPE,
    code_identity,
    exporter_runtime,
)
from stpd.structured_workload_contracts import StructuredTrainingConfig, StructuredWorkloadRequest
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.fixture(autouse=True)
def cpu_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "source"
    for folder in ("stpd", "spireagent"):
        shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "uv.lock", root / "uv.lock")
    return root


def producer(revision="a", root=ROOT):
    return Producer("https://example.test/source", revision * 40,
                    hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest())


def provenance(root=ROOT):
    origin = producer(root=root)
    return {"training_producer": origin.to_dict(), "export_producer": origin.to_dict(),
            "run_id": "1" * 64, "training_input_id": "2" * 64, "checkpoint_id": "3" * 64}


def export(path, *, scoped=True, root=ROOT):
    return structured_export.export_structured_package(
        structured_export.StructuredM2(seed=0), path, source_revision="a" * 40,
        data_sha256="d" * 64, source_kind="synthetic", teacher_sha256="e" * 64,
        training={"config": {"seed": 0}, "metrics": {}},
        code_scope=INFERENCE_SCOPE if scoped else LEGACY_SCOPE,
        provenance=provenance(root) if scoped else None,
    )


def test_workbench_is_excluded_but_legacy_digest_stays_broad(source_tree):
    before = {scope: code_identity(scope, source_tree)
              for scope in (INFERENCE_SCOPE, TRAINING_SCOPE)}
    legacy = structured_export.code_digest(source_tree)
    path = source_tree / "spireagent/workbench/local_models.py"
    path.write_bytes(path.read_bytes() + b"\n# unrelated application presentation\n")
    assert before == {scope: code_identity(scope, source_tree)
                      for scope in (INFERENCE_SCOPE, TRAINING_SCOPE)}
    assert structured_export.code_digest(source_tree) != legacy


@pytest.mark.parametrize("path,affected", [
    ("stpd/models/structured_m2.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/fullrun/structured_inputs.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/fullrun/text_menu_inputs.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/fullrun/semantic_projection.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/workers/checkpoint_codec.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/models/__init__.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/__init__.py", {INFERENCE_SCOPE, TRAINING_SCOPE}),
    ("stpd/policy/structured_port.py", {INFERENCE_SCOPE}),
    ("stpd/policy/structured_export.py", {INFERENCE_SCOPE}),
    ("stpd/models/structured_engine.py", {TRAINING_SCOPE}),
    ("stpd/models/structured_training.py", {TRAINING_SCOPE}),
    ("stpd/structured_workload_contracts.py", {TRAINING_SCOPE}),
    ("stpd/workers/structured_execution.py", {TRAINING_SCOPE}),
    ("stpd/workers/structured_run.py", {TRAINING_SCOPE}),
    ("spireagent/storage/store.py", {TRAINING_SCOPE}),
])
def test_scopes_bind_all_owning_source_paths(source_tree, path, affected):
    before = {scope: code_identity(scope, source_tree)
              for scope in (INFERENCE_SCOPE, TRAINING_SCOPE)}
    target = source_tree / path
    target.write_bytes(target.read_bytes() + b"\n# changed trusted implementation\n")
    for scope, identity in before.items():
        assert (code_identity(scope, source_tree) != identity) == (scope in affected)


def test_dependency_lock_is_separate_and_exact(source_tree):
    before = {scope: code_identity(scope, source_tree)
              for scope in (INFERENCE_SCOPE, TRAINING_SCOPE)}
    lock = source_tree / "uv.lock"
    lock.write_bytes(lock.read_bytes() + b"\n# dependency drift\n")
    for scope, identity in before.items():
        after = code_identity(scope, source_tree)
        assert after["source_sha256"] == identity["source_sha256"]
        assert after["dependency_lock_sha256"] != identity["dependency_lock_sha256"]


def test_missing_symlink_and_unknown_scope_fail_closed(source_tree, tmp_path):
    path = source_tree / "stpd/workers/checkpoint_codec.py"
    content = path.read_bytes()
    path.unlink()
    for scope in (INFERENCE_SCOPE, TRAINING_SCOPE):
        with pytest.raises(BoundaryError, match="missing_or_unsafe"):
            code_identity(scope, source_tree)
    external = tmp_path / "codec.py"
    external.write_bytes(content)
    path.symlink_to(external)
    with pytest.raises(BoundaryError, match="missing_or_unsafe"):
        code_identity(INFERENCE_SCOPE, source_tree)
    with pytest.raises(BoundaryError, match="unsupported_scope"):
        code_identity("artifact-chosen-files", source_tree)


def test_scoped_package_and_legacy_dispatch_survive_only_compatible_source(source_tree,
                                                                          tmp_path, monkeypatch):
    monkeypatch.setattr(structured_export, "ROOT", source_tree)
    current, old = tmp_path / "v2", tmp_path / "v1"
    scoped, legacy = export(current, root=source_tree), export(old, scoped=False, root=source_tree)
    assert scoped["schema"] == SCOPED_PACKAGE_SCHEMA
    assert legacy["schema"] == structured_export.PACKAGE_SCHEMA
    assert scoped["weights"]["sha256"] == legacy["weights"]["sha256"]
    assert scoped["model_id"] != legacy["model_id"]
    before = (current / "model.json").read_bytes()
    path = source_tree / "spireagent/workbench/local_models.py"
    path.write_bytes(path.read_bytes() + b"\n# presentation change\n")
    assert structured_export.load_structured_package(current)[0] == scoped
    assert (current / "model.json").read_bytes() == before
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        structured_export.load_structured_package(old)
    path = source_tree / "stpd/models/structured_m2.py"
    path.write_bytes(path.read_bytes() + b"\n# graph implementation drift\n")
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        structured_export.load_structured_package(current)


def test_v2_installation_and_actual_port_roundtrip(tmp_path):
    package = tmp_path / "package"
    metadata = export(package)
    facts = manifest_for(package, metadata)
    config_path, manifest_path = tmp_path / "config.json", tmp_path / "policy.json"
    config, manifest = installation.bind_structured_export(
        ROOT, package, config_path, manifest_path, manifest_id="synthetic-v2",
        policy=facts["policy"], requirements=facts["requirements"], support=facts["support"],
        binding_root=tmp_path,
    )
    assert config["schema"] == SCOPED_CONFIG_SCHEMA
    assert manifest["adapter_config"]["stage1a"]["code_digest_scope"] == INFERENCE_SCOPE
    assert installation.validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (
        config, manifest)
    assert installation.arguments({"config": str(config_path), "manifest": str(manifest_path)})[
        :2] == ["-m", "stpd.policy.structured_port"]
    adapter = StructuredPolicyAdapter(package, manifest_path)
    snapshot = sample()
    keys = [item["action_id"] for item in snapshot["menu_actions"]["actions"]]
    request = {"run_id": "synthetic-run", "continuity_token": "segment", "manifest": manifest,
               "bundle": {"observation": snapshot, "reads": []}, "candidate_count": len(keys),
               "candidate_digest": semantic_hash(keys)}
    result, completion = adapter.decide(request)
    assert len(result["scores"]) == len(keys)
    assert result["selected_index"] in range(len(keys))
    assert completion["snapshot_id"] == snapshot["snapshot_id"]
    adapter.close()


@pytest.mark.parametrize("mutate", [
    lambda value: value["code_identity"].update(scope=TRAINING_SCOPE),
    lambda value: value["code_identity"].update(dependency_lock_sha256="0" * 64),
    lambda value: value["runtime"].update(dtype="float64"),
    lambda value: value["provenance"]["export_producer"].update(uv_lock_sha256="0" * 64),
])
def test_v2_package_rejects_scope_dependency_runtime_and_producer_tampering(tmp_path, mutate):
    package = tmp_path / "package"
    metadata = export(package)
    mutate(metadata)
    metadata["model_id"] = semantic_hash({k: v for k, v in metadata.items() if k != "model_id"})
    (package / "model.json").write_bytes(json_bytes(metadata))
    with pytest.raises(BoundaryError):
        structured_export.load_structured_package(package)


def test_schema_renaming_cannot_migrate_a_legacy_package(tmp_path):
    package = tmp_path / "old-package"
    metadata = export(package, scoped=False)
    metadata["schema"] = SCOPED_PACKAGE_SCHEMA
    (package / "model.json").write_bytes(json_bytes(metadata))
    with pytest.raises(BoundaryError):
        structured_export.load_structured_package(package)


def test_scoped_checkpoint_exact_numerics_and_no_cross_scope_restore():
    data, config = dataset(), StructuredTrainingConfig(epochs=2)
    original = StructuredTrainingEngine(data, config, code_scope=TRAINING_SCOPE)
    original.advance_chunk()
    raw = original.checkpoint()
    assert decode_checkpoint(raw)["schema"] == SCOPED_CHECKPOINT_SCHEMA
    resumed = StructuredTrainingEngine(data, config, code_scope=TRAINING_SCOPE)
    resumed.restore(raw)
    assert finish(original) == finish(resumed)
    equal_tree(original.model.state_dict(), resumed.model.state_dict())
    equal_tree(original.optimizer.state_dict(), resumed.optimizer.state_dict())
    legacy = StructuredTrainingEngine(data, config)
    for target, state in ((legacy, raw),
                          (StructuredTrainingEngine(data, config, code_scope=TRAINING_SCOPE),
                           legacy.checkpoint())):
        before = {name: tensor.clone() for name, tensor in target.model.state_dict().items()}
        with pytest.raises(BoundaryError, match="exact_resume_identity_mismatch"):
            target.restore(state)
        equal_tree(before, target.model.state_dict())
    renamed = decode_checkpoint(legacy.checkpoint())
    renamed["schema"] = SCOPED_CHECKPOINT_SCHEMA
    with pytest.raises(BoundaryError, match="exact_resume_identity_mismatch"):
        resumed.restore(encode_checkpoint(renamed))


def test_scoped_run_preserves_original_and_actual_attempt_producers(tmp_path):
    archive = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    original, current = producer(), producer("b")
    run = prepare_structured_workload(archive, dataset(), original, StructuredTrainingConfig(),
                                      operation_id="1" * 32, code_scope=TRAINING_SCOPE)
    assert run.parameters.value()["schema"] == SCOPED_RUN_SCHEMA
    request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                        "1" * 32, "2" * 32)
    paused = execute_structured_workload(
        archive, reporter, request, original, authority=Authority(),
        control=PauseControl(), attempt_producer=original)
    assert paused.state == "paused"
    resumed = replace(request, mode="resume", attempt_id="3" * 32,
                      resume_checkpoint_id=paused.checkpoint_id)
    completed = execute_structured_workload(archive, reporter, resumed, original,
                                             authority=Authority(), attempt_producer=current)
    assert completed.state == "completed"
    saved = archive.get_manifest(completed.checkpoint_id)
    model = archive.get_manifest(completed.model_id)
    assert saved.producer == model.producer == current
    assert model.parameters.value()["schema"] == SCOPED_MODEL_SCHEMA
    package = tmp_path / "completed-package"
    package.mkdir()
    for role, name in (("package_manifest", "model.json"), ("weights", "weights.tensor-tree")):
        (package / name).write_bytes(b"".join(archive.read_payload(model.payload(role))))
    metadata, _ = structured_export.load_structured_package(package)
    assert metadata["provenance"] == {
        "training_producer": original.to_dict(), "export_producer": current.to_dict(),
        "run_id": run.artifact_id, "training_input_id": run.parent("training_input"),
        "checkpoint_id": saved.artifact_id, "export_runtime": exporter_runtime(),
    }
    assert metadata["source"]["source_revision"] == original.source_revision
    reconciled = execute_structured_workload(archive, reporter,
        replace(request, mode="reconcile", attempt_id="4" * 32), original,
        authority=Authority(), attempt_producer=producer("c"))
    assert reconciled.result_id == completed.result_id and reconciled.model_id == model.artifact_id
    assert archive.get_manifest(run.artifact_id).producer == original


def test_scoped_attempt_requires_current_provenance_and_exact_lock_before_writes(tmp_path):
    archive = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    origin = producer()
    run = prepare_structured_workload(archive, dataset(), origin, StructuredTrainingConfig(),
                                      operation_id="1" * 32, code_scope=TRAINING_SCOPE)
    request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                        "1" * 32, "2" * 32)
    for actual in (None, Producer(origin.repository, "b" * 40, "0" * 64)):
        with pytest.raises(BoundaryError, match="current_attempt_producer_required"):
            execute_structured_workload(archive, reporter, request, origin,
                                          authority=Authority(), attempt_producer=actual)
    assert not reporter.events(run.artifact_id)

# Reviewed boundaries whose implementation verifies/uses the second scope.
# All other possible imports, including nested imports, must fit their own scope.
CROSS_SCOPE_FUNCTIONS = {
    ("stpd/models/structured_engine.py", "code_digest"),  # Legacy broad identity only.
    ("stpd/workers/structured_run.py", "run_structured_job"),  # Legacy final-only API.
    ("stpd/workers/structured_execution.py", "_completed"),  # Verifies inference package.
    ("stpd/workers/structured_execution.py", "execute_structured_workload"),  # Final export only.
}


def _local_module_path(module: str) -> str | None:
    if module.split(".")[0] not in {"stpd", "spireagent"}:
        return None
    stem = module.replace(".", "/")
    if (ROOT / (stem + ".py")).is_file():
        return stem + ".py"
    if (ROOT / stem / "__init__.py").is_file():
        return stem + "/__init__.py"
    raise AssertionError("unknown local import: " + module)


@pytest.mark.parametrize("scope,paths", [(INFERENCE_SCOPE, INFERENCE_PATHS),
                                          (TRAINING_SCOPE, TRAINING_PATHS)])
def test_reviewed_static_potential_imports_and_initializers_are_covered(scope, paths):
    assert paths == tuple(sorted(set(paths)))
    for relative in paths:
        tree = ast.parse((ROOT / relative).read_text())
        package = relative.removesuffix(".py").split("/")[:-1]

        def visit(node, function=None, *, package=package, relative=relative):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = node.name
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and (
                node.test.id == "TYPE_CHECKING"):
                for child in node.orelse:
                    visit(child, function)
                return
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    parts = package[:len(package) - node.level + 1] if node.level else []
                    module = ".".join([*parts, *(node.module or "").split(".")]).rstrip(".")
                    modules = [module]
                    initializer = ROOT / module.replace(".", "/") / "__init__.py"
                    if initializer.is_file():
                        lazy = {}
                        for declaration in ast.parse(initializer.read_text()).body:
                            if (isinstance(declaration, ast.Assign) and any(
                                    isinstance(target, ast.Name) and target.id == "_EXPORT_MODULES"
                                    for target in declaration.targets)):
                                lazy = ast.literal_eval(declaration.value)
                        for alias in node.names:
                            stem = (module + "." + alias.name).replace(".", "/")
                            if (ROOT / (stem + ".py")).is_file() or (
                                    ROOT / stem / "__init__.py").is_file():
                                modules.append(module + "." + alias.name)
                            elif alias.name in lazy:
                                modules.append(module + "." + lazy[alias.name])
                for module in modules:
                    path = _local_module_path(module)
                    if path is None:
                        continue
                    allowed = set(paths)
                    if (scope == TRAINING_SCOPE and path.startswith("stpd/policy/")
                            and (relative, function) in CROSS_SCOPE_FUNCTIONS):
                        allowed |= set(INFERENCE_PATHS)
                    assert path in allowed, (scope, relative, function, path)
                    parent = Path(path).parent
                    while str(parent) != ".":
                        initializer = (parent / "__init__.py").as_posix()
                        assert initializer in allowed, (scope, relative, initializer)
                        parent = parent.parent
            if isinstance(node, ast.Call):
                name = (node.func.id if isinstance(node.func, ast.Name) else
                        node.func.attr if isinstance(node.func, ast.Attribute) else "")
                if name in {"import_module", "__import__"}:
                    # This fixed lazy export map cannot be selected by artifacts.
                    assert relative in {"stpd/models/__init__.py", "stpd/policy/__init__.py"}
                    assert name == "import_module"
                    assert ast.unparse(node) == "import_module('.' + module, __name__)"
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree)


@pytest.mark.parametrize("body,paths", [
    ("import stpd.policy.structured_export\nimport stpd.policy.structured_port", INFERENCE_PATHS),
    ("import stpd.workers.structured_execution\nimport stpd.models.structured_engine",
     TRAINING_PATHS),
])
def test_actual_clean_process_all_local_modules_fit_the_reviewed_scope(body, paths):
    script = f'''
import sys
from pathlib import Path
import torch
torch.set_num_threads(2)
{body}
root = Path({str(ROOT)!r})
actual = {{Path(m.__file__).resolve().relative_to(root).as_posix()
           for n, m in sys.modules.items() if n.split(".")[0] in {{"stpd", "spireagent"}}
           and getattr(m, "__file__", None)}}
assert actual <= set({paths!r}), actual - set({paths!r})
assert "stpd/structured_code_scope.py" in actual
assert not any(n.startswith(("spireagent.workbench", "spireagent.hub", "stpd.training",
                             "stpd.qwen", "stpd.policy.s1")) for n in sys.modules)
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_copied_source_pause_resume_after_workbench_edit(source_tree, tmp_path):
    # The child imports its copied source, so this proves real current-code
    # identity checks rather than a mocked digest or an ancestor receipt.
    input_path = tmp_path / "input.json"
    input_path.write_bytes(dataset().source_bytes)
    script = f'''
import importlib.abc
import sys
from pathlib import Path
sys.path.insert(0, {str(source_tree)!r})
import torch
torch.set_num_threads(2)
class BlockApplication(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("spireagent.workbench", "spireagent.hub", "stpd.training",
                                "stpd.qwen", "stpd.policy.adapter", "stpd.policy.s1")):
            raise AssertionError("application/legacy import: " + fullname)
sys.meta_path.insert(0, BlockApplication())
from spireagent.artifact_contracts import Producer
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.structured_sequences import parse_structured_dataset
from stpd.structured_code_scope import (
    TRAINING_SCOPE, ROOT, code_identity, TRAINING_PATHS, INFERENCE_PATHS)
from stpd.structured_workload_contracts import StructuredTrainingConfig, StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    prepare_structured_workload, execute_structured_workload)
root = Path({str(source_tree)!r})
assert ROOT == root
origin = Producer("https://example.test/source", "a" * 40,
                  code_identity(TRAINING_SCOPE)["dependency_lock_sha256"])
current = Producer(origin.repository, "b" * 40, origin.uv_lock_sha256)
data = parse_structured_dataset(Path({str(input_path)!r}).read_bytes())
store = ManifestArtifactStore(LocalBlobStore(Path({str(tmp_path / 'child-store')!r})))
reporter = ObjectStoreRunReporter(store, store.blobs)
class Fence:
    def assert_current(self, *args): pass
    def authorize_resume(self, *args): pass
class Pause:
    def requested_action(self): return "pause"
run = prepare_structured_workload(store, data, origin, StructuredTrainingConfig(),
                                 operation_id="1" * 32, code_scope=TRAINING_SCOPE)
request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                    "1" * 32, "2" * 32)
paused = execute_structured_workload(store, reporter, request, origin, authority=Fence(),
                                    control=Pause(), attempt_producer=origin)
assert paused.state == "paused"
assert "stpd.policy.structured_export" not in sys.modules
before = code_identity(TRAINING_SCOPE)
path = root / "spireagent/workbench/local_models.py"
path.write_bytes(path.read_bytes() + b"\\n# unrelated Workbench change\\n")
assert code_identity(TRAINING_SCOPE) == before
request = StructuredWorkloadRequest(run.artifact_id, run.parent("training_input"),
                                    "1" * 32, "3" * 32, mode="resume",
                                    resume_checkpoint_id=paused.checkpoint_id)
completed = execute_structured_workload(store, reporter, request, origin,
                                        authority=Fence(), attempt_producer=current)
assert completed.state == "completed"
assert store.get_manifest(completed.checkpoint_id).producer == current
assert store.get_manifest(completed.model_id).producer == current
actual = {{Path(m.__file__).resolve().relative_to(root).as_posix()
           for n, m in sys.modules.items() if n.split(".")[0] in {{"stpd", "spireagent"}}
           and getattr(m, "__file__", None)}}
assert actual <= set(TRAINING_PATHS) | set(INFERENCE_PATHS), actual
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=source_tree,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_portable_abi_accepts_other_exporter_platform_but_not_torch_suffix(tmp_path):
    package = tmp_path / "cross-platform"
    metadata = export(package)
    metadata["provenance"]["export_runtime"] = {
        "python": "3.11.99", "system": "Linux", "machine": "x86_64"}
    metadata["model_id"] = semantic_hash({k: v for k, v in metadata.items() if k != "model_id"})
    (package / "model.json").write_bytes(json_bytes(metadata))
    actual, model = structured_export.load_structured_package(package)
    assert actual["provenance"]["export_runtime"]["system"] == "Linux"
    assert model.initial_memory().shape == (1, 96)
    metadata["runtime"]["torch_version"] += "+different-wheel-build"
    metadata["model_id"] = semantic_hash({k: v for k, v in metadata.items() if k != "model_id"})
    (package / "model.json").write_bytes(json_bytes(metadata))
    with pytest.raises(BoundaryError, match="runtime_identity_mismatch"):
        structured_export.load_structured_package(package)


def test_numerical_resume_platform_identity_remains_exact():
    engine = StructuredTrainingEngine(
        dataset(), StructuredTrainingConfig(), code_scope=TRAINING_SCOPE)
    value = decode_checkpoint(engine.checkpoint())
    value["identity"]["runtime"]["system"] = "other-platform"
    with pytest.raises(BoundaryError, match="exact_resume_identity_mismatch"):
        engine.restore(encode_checkpoint(value))


def test_v3_cannot_attach_v2_checkpoint_before_engine_or_publication(tmp_path, monkeypatch):
    from stpd.workers import structured_execution

    archive = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(archive, archive.blobs)
    origin = producer()
    old = prepare_structured_workload(archive, dataset(), origin, StructuredTrainingConfig(),
                                      operation_id="1" * 32)
    old_request = StructuredWorkloadRequest(old.artifact_id, old.parent("training_input"),
                                            "1" * 32, "2" * 32)
    paused = execute_structured_workload(archive, reporter, old_request, origin,
                                         authority=Authority(), control=PauseControl())
    current = prepare_structured_workload(archive, dataset(), origin, StructuredTrainingConfig(),
                                          operation_id="3" * 32, code_scope=TRAINING_SCOPE)
    request = StructuredWorkloadRequest(current.artifact_id, current.parent("training_input"),
                                        "3" * 32, "4" * 32, mode="resume",
                                        resume_checkpoint_id=paused.checkpoint_id)

    def forbidden_engine(*args, **kwargs):
        raise AssertionError("invalid attachment reached numerical engine")

    monkeypatch.setattr(structured_execution, "StructuredTrainingEngine", forbidden_engine)
    with pytest.raises(BoundaryError):
        execute_structured_workload(archive, reporter, request, origin, authority=Authority(),
                                      attempt_producer=producer("b"))
    assert not reporter.events(current.artifact_id)


def test_binder_is_a_separate_trusted_edge_with_an_inventory_of_actual_dependencies():
    additional = {"stpd/structured_policy_installation.py", "stpd/token_policy_installation.py",
                  "spireagent/package_identity.py", "spireagent/policy_files.py"}
    # Installation lazy paths are invoked by the roundtrip test. The shared
    # token module contributes only this pure helper and its module imports.
    from stpd.token_policy_installation import _manifest_artifact_path

    tree = ast.parse((ROOT / "stpd/token_policy_installation.py").read_text())
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_manifest_artifact_path")
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(helper))
    assert _manifest_artifact_path(Path("model/model.json"), Path("registry"))
    script = f'''
import sys
from pathlib import Path
import stpd.structured_policy_installation
import stpd.policy.structured_export
import stpd.policy.structured_port
root = Path({str(ROOT)!r})
actual = {{Path(m.__file__).resolve().relative_to(root).as_posix()
           for n, m in sys.modules.items() if n.split(".")[0] in {{"stpd", "spireagent"}}
           and getattr(m, "__file__", None)}}
assert actual <= set({INFERENCE_PATHS!r}) | {additional!r}, actual
assert not any(n.startswith(("spireagent.workbench", "spireagent.hub", "stpd.training",
                             "stpd.qwen", "stpd.policy.s1")) for n in sys.modules)
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
