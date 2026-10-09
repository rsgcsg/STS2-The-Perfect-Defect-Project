"""Pure synthetic Source3 save -> ordered preview -> training-ready owner composition."""

from __future__ import annotations

import builtins
import hashlib
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from metadata_import_guard import install_torch_import_guard
from metadata_import_guard import no_torch_imports as no_torch_imports
from source3_product_fixture import DERIVED, ORIGINAL, ROOT, settled, setup

from spireagent.json_boundary import BoundaryError
from spireagent.workbench import local_dataset, local_recording_import
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_recording_import import LocalRecordingImporter
from stpd.fullrun.ordered_source import (
    OrderedSourceRef,
    publish_ordered_source_partition,
    verify_ordered_source_partition,
)
from stpd.ordered_source_spec import DEFAULT_RECIPE, DEFAULT_VIEW, PRETRAIN_VIEW, RAW_SCHEMA


def imported(tmp_path, monkeypatch):
    objects = setup(tmp_path, monkeypatch)
    importer, *_ = objects
    importer.start(objects[3])
    result = settled(importer)
    assert result["status"] == "completed", result
    return objects, result["artifact_id"]


def test_source3_import_and_training_ready_preserve_originals_and_never_start_training(
    tmp_path, monkeypatch,
):
    importer, datasets, catalog, candidate, store, owner, session, tool = setup(
        tmp_path, monkeypatch)
    before = {p.relative_to(session): p.read_bytes() for p in session.rglob("*") if p.is_file()}
    row = catalog.read()["candidates"][0]
    assert row["recording_type"] == "source3" and row["import_supported"] is True
    assert "closed_at" not in row and row["created_at"]
    with pytest.raises(BoundaryError, match="source3_attestation_not_supported"):
        importer.start(candidate, True)
    assert not tool.calls and not store.manifest_ids() and importer.thread is None
    importer.start(candidate, False)
    saved = settled(importer)
    assert saved["status"] == "completed", saved
    assert saved["human_origin_verified"] is False
    assert saved["next_action"] == "datasets.source3-preview"
    raw = store.get_manifest(saved["artifact_id"])
    assert raw.producer == ORIGINAL and raw.parameters.value()["schema"] == RAW_SCHEMA
    assert "human_origin_attested" not in raw.parameters.value()
    assert {p.relative_to(session): p.read_bytes()
            for p in session.rglob("*") if p.is_file()} == before
    assert importer.start(candidate)["artifact_id"] == raw.artifact_id
    reopened = LocalRecordingImporter(importer.config, catalog)
    assert reopened.status()["artifact_id"] == raw.artifact_id
    assert reopened.start(candidate)["artifact_id"] == raw.artifact_id and len(tool.calls) == 1

    datasets.start_source3_preview([raw.artifact_id], "agent_protocol", DEFAULT_VIEW)
    preview = settled(datasets)
    assert preview["status"] == "preview_ready", preview
    assert preview["can_publish"] and preview["selected"] == 1
    assert preview["N_coverage"] == {
        "cohort": "agent_protocol", "eligible": 1, "denominator": 1, "fraction": 1.0,
    }
    assert preview["counts"]["original_publications"] == 6
    assert preview["counts"]["excluded_frames"] == 1
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_claims").fetchone() == (0,)
    datasets.start_publish(preview["preview_id"])
    ready = settled(datasets)
    assert ready["status"] == "completed", ready
    assert ready["training_source_id"] == ready["result_artifact_id"]
    assert ready["recommended_recipe_id"] == DEFAULT_RECIPE
    assert ready["actual_training_use"] is False
    partition = verify_ordered_source_partition(store, ready["training_source_id"])
    assert owner.ledger.dataset(partition.manifest.artifact_id) == ("training", set(partition.runs))
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM local_source_pending").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM curation_source_uses").fetchone() == (0,)
    identities = store.manifest_ids()
    assert datasets.start_publish(preview["preview_id"])["operation"]["result_artifact_id"] == (
        partition.manifest.artifact_id)
    assert store.manifest_ids() == identities
    assert importer.start(candidate)["artifact_id"] == raw.artifact_id
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM local_source_pending").fetchone() == (0,)


def test_zero_labels_do_not_switch_view_and_explicit_pretraining_stays_distinct(
    tmp_path, monkeypatch,
):
    objects, raw_id = imported(tmp_path, monkeypatch)
    datasets, store = objects[1], objects[4]
    datasets.start_source3_preview([raw_id], "declared_human", DEFAULT_VIEW)
    empty = settled(datasets)
    assert empty["status"] == "preview_ready" and empty["selected"] == 0
    assert empty["source_view"] == DEFAULT_VIEW and not empty["can_publish"]
    assert empty["human_origin_verified"] is False
    with pytest.raises(BoundaryError, match="preview_cannot_publish"):
        datasets.start_publish(empty["preview_id"])
    datasets.start_source3_preview([raw_id], "agent_protocol", PRETRAIN_VIEW)
    pretrain = settled(datasets)
    assert pretrain["qualification"] == "ordered_recorded_capture_N_pretraining_only"
    assert "-N-pretrain-v1" in pretrain["recommended_recipe_id"]
    datasets.start_publish(pretrain["preview_id"])
    ready = settled(datasets)
    assert ready["status"] == "completed", ready
    assert ready["source_view"] == PRETRAIN_VIEW
    assert store.get_manifest(ready["training_source_id"]).parameters.value()["qualification"] == (
        pretrain["qualification"])


@pytest.mark.parametrize("change", ["close", "tool", "support", "accounting"])
def test_exact_candidate_changes_or_unsupported_tool_never_pack(tmp_path, monkeypatch, change):
    importer, _, catalog, candidate, store, _, session, tool = setup(tmp_path, monkeypatch)
    if change in {"close", "accounting"}:
        path = session / "source-close-receipt.json"
        value = json.loads(path.read_bytes())
        value["accounting_complete"] = change != "accounting"
        if change == "close":
            value["session_id"] = "wrong-original-session"
        path.write_text(json.dumps(value))
    elif change == "tool":
        tool.release_id = "9" * 64
    else:
        tool.supported = False
    with pytest.raises(BoundaryError):
        importer.start(candidate)
    assert not tool.calls and not store.manifest_ids() and importer.thread is None
    if change == "support":
        row = catalog.read()["candidates"][0]
        assert row["import_supported"] is False
        assert row["import_reason"] == "source3_tool_support_required"


def test_pack_failure_preserves_raw_and_no_publication(tmp_path, monkeypatch):
    importer, _, _, candidate, store, _, session, tool = setup(tmp_path, monkeypatch)
    before = (session / "recording-manifest.json").read_bytes()
    tool.fail = True
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "failed", failed
    assert not store.manifest_ids()
    assert (session / "recording-manifest.json").read_bytes() == before


def test_raw_publication_before_binding_failure_is_reconciled_without_second_pack(
    tmp_path, monkeypatch,
):
    from spireagent.workbench.local_curation import LocalCurationOwner

    importer, datasets, _, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    original = LocalCurationOwner.published_source
    monkeypatch.setattr(LocalCurationOwner, "published_source", lambda *_args:
                        (_ for _ in ()).throw(OSError("synthetic index failure")))
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "published_index_unavailable", failed
    assert failed["artifact_id"] in store.manifest_ids()
    monkeypatch.setattr(LocalCurationOwner, "published_source", original)
    recovered = importer.start(candidate)
    assert recovered["status"] == "completed" and len(tool.calls) == 1
    datasets.start_source3_preview([recovered["artifact_id"]], "agent_protocol", DEFAULT_VIEW)
    preview = settled(datasets)
    datasets.start_publish(preview["preview_id"])
    assert settled(datasets)["status"] == "completed"


def test_publication_index_failure_reopens_exact_partition_without_new_use_or_producer(
    tmp_path, monkeypatch,
):
    objects, raw_id = imported(tmp_path, monkeypatch)
    datasets, store, owner = objects[1], objects[4], objects[5]
    datasets.start_source3_preview([raw_id], "agent_protocol", DEFAULT_VIEW)
    preview = settled(datasets)
    original = local_dataset.sync_registry
    monkeypatch.setattr(local_dataset, "sync_registry", lambda *_args:
                        (_ for _ in ()).throw(OSError("synthetic registry interruption")))
    datasets.start_publish(preview["preview_id"])
    failed = settled(datasets)
    assert failed["status"] == "failed" and failed["recovery_available"] is True
    identities = store.manifest_ids()
    reopened = LocalDatasetService(datasets.config)
    assert not reopened.operation_invalid
    assert reopened.status()["operation"]["recovery_available"] is True
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        reopened.start_source3_preview([raw_id], "agent_protocol", PRETRAIN_VIEW)
    monkeypatch.setattr(local_dataset, "sync_registry", original)
    monkeypatch.setattr(local_dataset, "source_identity", lambda *_args:
                        pytest.fail("recovery must retain its captured derived producer"))
    reopened.start_publish(preview["preview_id"])
    ready = settled(reopened)
    assert ready["status"] == "completed", ready
    assert store.manifest_ids() == identities
    assert store.get_manifest(ready["training_source_id"]).producer == DERIVED
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)


def test_related_heldout_partition_cannot_be_prepared_for_training(tmp_path, monkeypatch):
    objects, raw_id = imported(tmp_path, monkeypatch)
    datasets, store, owner = objects[1], objects[4], objects[5]
    datasets.start_source3_preview([raw_id], "agent_protocol", DEFAULT_VIEW)
    preview = settled(datasets)
    refs = tuple(OrderedSourceRef(**ref) for ref in datasets.operation["_admission_refs"])
    heldout = publish_ordered_source_partition(store, refs, "test", DERIVED)
    owner.reserve_verified_ordered_source(store, heldout.manifest.artifact_id)
    datasets.start_publish(preview["preview_id"])
    rejected = settled(datasets)
    assert rejected["status"] == "failed", rejected
    assert "training_source_id" not in rejected
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)


@pytest.mark.parametrize("cohort,view", [("unknown", DEFAULT_VIEW),
                                         ("agent_protocol", "arbitrary-clone-view")])
def test_closed_choices_reject_before_thread_or_admission(tmp_path, monkeypatch, cohort, view):
    objects, raw_id = imported(tmp_path, monkeypatch)
    datasets, store = objects[1], objects[4]
    before = store.manifest_ids()
    with pytest.raises(BoundaryError):
        datasets.start_source3_preview([raw_id], cohort, view)
    assert datasets.thread is None and store.manifest_ids() == before


def test_original_physical_golden_exclusions_keep_human_denominator_and_do_not_fallback(
    tmp_path, monkeypatch,
):
    importer, datasets, _, candidate, _, _, _, _ = setup(
        tmp_path, monkeypatch,
        golden=ROOT / "components/evidence/tests/fixtures/source_session_v3_ordered/bundle",
    )
    importer.start(candidate)
    saved = settled(importer)
    assert saved["status"] == "completed", saved
    for view in (DEFAULT_VIEW, PRETRAIN_VIEW):
        datasets.start_source3_preview([saved["artifact_id"]], "declared_human", view)
        report = settled(datasets)
        assert report["status"] == "preview_ready", report
        assert report["source_view"] == view and not report["can_publish"]
        assert report["N_coverage"]["denominator"] == 1
        assert report["N_coverage"]["eligible"] == 0
        assert report["excluded_labels"] == 1 and report["exclusions"]
        assert report["counts"]["original_inputs"] == 2
        assert report["human_origin_verified"] is False


def test_missing_source3_evidence_api_never_uses_human_verifier_or_packer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    importer, _, _, candidate, store, _, _, tool = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(local_recording_import, "evidence_owner", SimpleNamespace())
    with pytest.raises(BoundaryError, match="source3_evidence_api_required"):
        importer.start(candidate)
    assert not store.manifest_ids() and not tool.calls and importer.thread is None


def test_workspace_owner_drift_after_pack_cannot_publish_or_index_another_store(
    tmp_path, monkeypatch,
):
    from spireagent.workbench.managed_local_workspace import create_managed_workspace

    importer, _, _, candidate, store, owner, _, tool = setup(tmp_path, monkeypatch)
    other_state = tmp_path / "other-profile"
    other_state.mkdir()
    other = create_managed_workspace(other_state)["curation_owner"]
    pack = tool.pack_source_v3
    def change_owner(self, *args):
        pack(self, *args)
        monkeypatch.setattr(local_recording_import, "_selected_curation_owner", lambda _: other)
    monkeypatch.setattr(tool, "pack_source_v3", change_owner)
    importer.start(candidate)
    failed = settled(importer)
    assert failed["status"] == "publication_unknown", failed
    assert not store.manifest_ids()
    with owner.transaction() as db:
        assert db.execute("SELECT artifact FROM local_source_pending").fetchone() == (None,)
    with other.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM local_source_pending").fetchone() == (0,)


def test_original_commit_lock_is_associated_exactly_without_current_lock_substitution(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "git-history"
    root = repo / "python"
    root.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL)
    git("init")
    git("config", "user.name", "Synthetic")
    git("config", "user.email", "fixture@example.invalid")
    (root / "uv.lock").write_bytes(b"original exact associated lock\n")
    git("add", "python/uv.lock")
    git("commit", "-m", "synthetic original")
    original = git("rev-parse", "HEAD").decode().strip()
    (root / "uv.lock").write_bytes(b"different current lock never substituted\n")
    monkeypatch.setattr(local_recording_import, "ROOT", root)
    producer = local_recording_import._original_recorder_producer(original)
    assert producer.source_revision == original
    assert producer.uv_lock_sha256 == hashlib.sha256(
        b"original exact associated lock\n").hexdigest()
    with pytest.raises(BoundaryError, match="provenance_unavailable"):
        local_recording_import._original_recorder_producer("e" * 40)
    with pytest.raises(BoundaryError, match="provenance_required"):
        local_recording_import._original_recorder_producer("HEAD")


def test_metadata_guard_blocks_import_requests_and_allows_backend_discovery(monkeypatch):
    before = sys.modules.get("torch")
    with monkeypatch.context() as scoped:
        guard = install_torch_import_guard(scoped)
        # Discovery is metadata; it does not import a tensor backend.
        importlib.util.find_spec("torch")
        for name in ("torch", "torch.nn"):
            with pytest.raises(AssertionError, match="attempted a Torch import"):
                builtins.__import__(name)
            with pytest.raises(AssertionError, match="attempted a Torch import"):
                importlib.import_module(name)
        with pytest.raises(AssertionError, match="attempted a Torch import"):
            importlib.import_module(".nn", package="torch")
        with pytest.raises(AssertionError, match="attempted a Torch import"):
            builtins.__import__("nn", {"__package__": "torch"}, level=1)
        assert guard.attempts == ["torch", "torch", "torch.nn", "torch.nn", "torch.nn", "torch.nn"]
    assert sys.modules.get("torch") is before


def test_metadata_guard_accepts_preloaded_module_without_unloading_or_importing_it():
    script = """
import builtins
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
import pytest
from metadata_import_guard import install_torch_import_guard
assert 'torch' not in sys.modules
existing = ModuleType('torch')
existing.__spec__ = importlib.machinery.ModuleSpec('torch', loader=None)
sys.modules['torch'] = existing
with pytest.MonkeyPatch.context() as patch:
    guard = install_torch_import_guard(patch)
    assert sys.modules['torch'] is existing
    assert importlib.util.find_spec('torch') is existing.__spec__
    for operation in (lambda: builtins.__import__('torch'),
                      lambda: importlib.import_module('torch.nn')):
        try:
            operation()
        except AssertionError:
            pass
        else:
            raise AssertionError('preloaded Torch import request bypassed guard')
    assert guard.attempts == ['torch', 'torch.nn']
    assert sys.modules['torch'] is existing
assert sys.modules['torch'] is existing
assert not any(name.startswith('torch.') for name in sys.modules)
print('preloaded metadata guard selftest passed; no backend import or unload')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parent, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "preloaded metadata guard selftest passed" in result.stdout


def test_metadata_guard_blocks_builtin_package_fallbacks_and_preserves_other_packages():
    script = """
import builtins
import importlib.machinery
import sys
from types import ModuleType
import pytest
from metadata_import_guard import install_torch_import_guard
assert 'torch' not in sys.modules
roots = {}
for name in ('torch', 'fixture_meta'):
    root = ModuleType(name)
    root.__path__ = []
    root.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    child = ModuleType(name + '.nn')
    child.__spec__ = importlib.machinery.ModuleSpec(name + '.nn', loader=None)
    root.nn = child
    sys.modules[name] = root
    sys.modules[name + '.nn'] = child
    roots[name] = (root, child)
original = builtins.__import__
with pytest.MonkeyPatch.context() as patch:
    guard = install_torch_import_guard(patch)
    cases = [
        {'__package__':None, '__spec__':roots['torch'][0].__spec__, '__name__':'torch'},
        {'__spec__':None, '__name__':'torch', '__path__':[]},
        {'__name__':'torch.child'},
    ]
    for globals_value in cases:
        try:
            builtins.__import__('nn', globals_value, fromlist=('x',), level=1)
        except AssertionError:
            pass
        else:
            raise AssertionError('relative Torch package fallback bypassed guard')
    assert guard.attempts == ['torch.nn'] * len(cases)
    result = builtins.__import__('nn', {'__name__':'fixture_meta', '__path__':[]},
                                 fromlist=('x',), level=1)
    assert result is roots['fixture_meta'][1]
    assert guard.attempts == ['torch.nn'] * len(cases)
assert builtins.__import__ is original
for name, (root, child) in roots.items():
    assert sys.modules[name] is root and sys.modules[name + '.nn'] is child
print('builtin package fallbacks blocked; unrelated cached package unchanged')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parent, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "builtin package fallbacks blocked" in result.stdout


def test_metadata_guard_invalid_package_and_level_errors_remain_builtin_owned():
    script = """
import builtins
import importlib.machinery
import sys
import pytest
from metadata_import_guard import install_torch_import_guard
assert 'torch' not in sys.modules
original = builtins.__import__
spec = importlib.machinery.ModuleSpec('torch', loader=None, is_package=True)
cases = [
    ({'__package__':42, '__spec__':spec}, 1),
    ({'__package__':'', '__name__':'torch'}, 1),
    ({'__spec__':None}, 1),
    ({'__package__':'torch'}, -1),
    ({'__package__':'torch'}, 'one'),
    ({'__package__':'torch'}, None),
    ({'__package__':'torch'}, 1.5),
    ({'__package__':'torch'}, 2**100),
    ({'__package__':'torch'}, 2),
]
def failure(operation):
    try:
        operation()
    except Exception as error:
        return type(error), str(error)
    raise AssertionError('invalid package/level unexpectedly succeeded')
expected = [failure(lambda g=g, n=n: original('nn', g, fromlist=('x',), level=n))
            for g, n in cases]
with pytest.MonkeyPatch.context() as patch:
    guard = install_torch_import_guard(patch)
    actual = [failure(lambda g=g, n=n: builtins.__import__('nn', g, fromlist=('x',), level=n))
              for g, n in cases]
    assert actual == expected, (actual, expected)
    assert guard.attempts == []
assert builtins.__import__ is original and 'torch' not in sys.modules
print('invalid package and level failures delegated unchanged')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parent, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "failures delegated unchanged" in result.stdout


def test_metadata_guard_swallowed_import_attempt_still_fails_autouse_teardown(tmp_path):
    isolated = tmp_path / "test_swallowed_backend_import.py"
    isolated.write_text("""
import importlib
from metadata_import_guard import no_torch_imports as no_torch_imports
def test_swallowed():
    try:
        importlib.import_module('torch')
    except Exception:
        pass
""")
    script = """
import sys
import pytest
assert 'torch' not in sys.modules
status = pytest.main(['-q', '-p', 'no:cacheprovider', sys.argv[1]])
assert status == 1, status
assert 'torch' not in sys.modules
print('swallowed Torch import rejected by autouse fixture; backend remained absent')
"""
    result = subprocess.run([sys.executable, "-c", script, str(isolated)],
                            capture_output=True, text=True, cwd=Path(__file__).resolve().parent,
                            timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unexpected Torch import attempts" in result.stdout
    assert "swallowed Torch import rejected by autouse fixture" in result.stdout


def test_metadata_guard_nested_scope_restores_original_import_functions(monkeypatch):
    original_builtin, original_module = builtins.__import__, importlib.import_module
    existing = sys.modules.get("torch")
    with monkeypatch.context() as scoped:
        guard = install_torch_import_guard(scoped)
        assert builtins.__import__ == guard.builtin_import
        assert importlib.import_module == guard.module_import
        importlib.util.find_spec("torch")
        assert not guard.attempts
    assert builtins.__import__ is original_builtin
    assert importlib.import_module is original_module
    assert sys.modules.get("torch") is existing
