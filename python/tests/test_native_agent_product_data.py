"""Synthetic sealed AgentRun import/preview/publish over existing application owners."""

from __future__ import annotations

import json
import shutil

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_native_agent_sampled_source import original as original
from test_protocol_source import setup_store

from spireagent.json_boundary import BoundaryError
from spireagent.source import source_identity
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_dataset, local_recording_import
from spireagent.workbench.developer import ROOT, ProjectConfig, combination
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.local_recording_import import LocalRecordingImporter
from spireagent.workbench.local_recordings import LocalRecordingCatalog
from stpd.fullrun.native_agent_sampled_source import verify_native_agent_sampled_partition
from stpd.native_agent_sampled_source_spec import FIXTURE_COHORT, RAW_SCHEMA, RELATION_SPEC
from stpd.native_training_source_spec import RECIPE


def setup(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store, owner = setup_store(tmp_path)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    return (LocalRecordingImporter(config, LocalRecordingCatalog(config)),
            LocalDatasetService(config), store, owner)


def settled(service):
    assert service.thread is not None
    service.thread.join(timeout=10)
    assert not service.thread.is_alive()
    result = service.status()
    return result.get("operation", result)


def imported(tmp_path, original):
    importer, datasets, store, owner = setup(tmp_path)
    importer.start_native_agent_run(str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"])
    result = settled(importer)
    assert result["status"] == "completed", result
    return importer, datasets, store, owner, result["artifact_id"]


def test_exact_original_import_preview_publish_use_separation_and_reopen(tmp_path, original):
    before = {path.name: path.read_bytes() for path in original.directory.iterdir()}
    importer, datasets, store, owner, raw_id = imported(tmp_path, original)
    raw = store.get_manifest(raw_id)
    assert raw.producer == source_identity(ROOT)
    assert raw.parameters.value()["schema"] == RAW_SCHEMA
    assert raw.parameters.value()["cohort"] == FIXTURE_COHORT
    assert {path.name: path.read_bytes() for path in original.directory.iterdir()} == before
    assert importer.status()["native_agent_support"]["product_entry_enabled"] is False
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone() == (0,)
    datasets.start_native_agent_preview([raw_id])
    preview = settled(datasets)
    assert preview["status"] == "preview_ready" and preview["can_publish"], preview
    assert preview["counts"]["known_context_samples"] == 4
    assert preview["accepted_labels"] == preview["counts"]["eligible_unique_N"] == 3
    assert preview["counts"]["readiness_exclusions"] == 3
    assert preview["counts"]["real_native_samples"] == 0
    assert preview["native_origin_status"] == "synthetic_conformance"
    # This protocol conformance fixture's three choices each have original C1.
    # Its update cannot establish a useful choice-learning result.
    assert preview["coverage"]["multi_candidate_N"] == 0
    assert preview["recommended_recipe_id"] == RECIPE
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
    datasets.start_publish(preview["preview_id"])
    result = settled(datasets)
    assert result["status"] == "completed", result
    assert result["training_source_id"] == result["result_artifact_id"]
    assert result["split_status"] == "reserved" and result["actual_training_use"] is False
    partition = verify_native_agent_sampled_partition(store, result["result_artifact_id"])
    assert owner.ledger.dataset(partition.manifest.artifact_id) == ("training", set(partition.runs))
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM curation_source_decisions").fetchone() == (7,)
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
    identities = store.manifest_ids()
    reopened = LocalDatasetService(datasets.config)
    assert reopened.status()["operation"]["split_status"] == "reserved"
    reopened.start_publish(preview["preview_id"])
    assert reopened.status()["operation"]["result_artifact_id"] == partition.manifest.artifact_id
    relocated = tmp_path / "copied-original"
    shutil.copytree(original.directory, relocated)
    fresh_importer = LocalRecordingImporter(importer.config, importer.catalog)
    fresh_importer.start_native_agent_run(str(relocated), FIXTURE_COHORT, RELATION_SPEC["id"])
    assert settled(fresh_importer)["artifact_id"] == raw_id
    assert store.manifest_ids() == identities
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


@pytest.mark.parametrize("choice", ["cohort", "relation", "relative", "link"])
def test_closed_choice_or_unsafe_directory_rejects_before_pending_write(tmp_path, original, choice):
    importer, _, store, owner = setup(tmp_path)
    directory, cohort, relation = str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"]
    if choice == "cohort":
        cohort = "declared_human"
    elif choice == "relation":
        relation = "arbitrary-module"
    elif choice == "relative":
        directory = "relative-directory"
    else:
        link = tmp_path / "symlink"
        link.symlink_to(original.directory)
        directory = str(link)
    with pytest.raises(BoundaryError):
        importer.start_native_agent_run(directory, cohort, relation)
    assert importer.thread is None and not store.manifest_ids()
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_known_raw_publication_lost_reply_is_not_called_unpublished_and_reuses_exact_bytes(
    tmp_path, original, monkeypatch
):
    importer, _, store, _, = setup(tmp_path)
    publish = ManifestArtifactStore.publish
    failed = False

    def lost(self, manifest):
        nonlocal failed
        result = publish(self, manifest)
        if manifest.parameters.value().get("schema") == RAW_SCHEMA and not failed:
            failed = True
            raise OSError("synthetic reply lost after immutable publication")
        return result

    monkeypatch.setattr(ManifestArtifactStore, "publish", lost)
    importer.start_native_agent_run(str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"])
    result = settled(importer)
    assert result["status"] == "published_index_unavailable", result
    raw_id = result["artifact_id"]
    identities = store.manifest_ids()
    monkeypatch.setattr(ManifestArtifactStore, "publish", publish)
    reopened = LocalRecordingImporter(importer.config, importer.catalog)
    reopened.start_native_agent_run(str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"])
    assert settled(reopened)["artifact_id"] == raw_id and store.manifest_ids() == identities


def test_publication_recovery_keeps_original_preview_partition_and_producer(
    tmp_path, original, monkeypatch
):
    _, datasets, store, owner, raw_id = imported(tmp_path, original)
    datasets.start_native_agent_preview([raw_id])
    preview = settled(datasets)
    sync = local_dataset.sync_registry
    monkeypatch.setattr(local_dataset, "sync_registry", lambda *_args:
                        (_ for _ in ()).throw(OSError("synthetic registry interruption")))
    datasets.start_publish(preview["preview_id"])
    failed = settled(datasets)
    assert failed["status"] == "failed" and failed["recovery_available"], failed
    identities = store.manifest_ids()
    reopened = LocalDatasetService(datasets.config)
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        reopened.start_native_agent_preview([raw_id])
    with pytest.raises(BoundaryError, match="publication_recovery_required"):
        reopened.start_source3_preview([raw_id], "agent_protocol", "decision_sample_carry")
    monkeypatch.setattr(local_dataset, "sync_registry", sync)
    monkeypatch.setattr(local_dataset, "source_identity", lambda *_args:
                        pytest.fail("recovery cannot invent a new producer"))
    reopened.start_publish(preview["preview_id"])
    result = settled(reopened)
    assert result["status"] == "completed" and store.manifest_ids() == identities, result
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)


def test_typed_raw_corruption_and_forged_operation_refs_fail_closed(tmp_path, original):
    _, datasets, store, _, raw_id = imported(tmp_path, original)
    payload = store.get_manifest(raw_id).payload("archive")
    index = json.loads(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    chunk = index["chunks"][0]["sha256"]
    store.blobs._path(f"objects/sha256/{chunk}").write_bytes(b"corrupt original archive")
    datasets.start_native_agent_preview([raw_id])
    failed = settled(datasets)
    assert failed["status"] == "failed" and "preview_id" not in failed
    value = {**datasets.operation, "status": "preview_ready", "preview_id": "a" * 32,
             "_logical_id": "b" * 64, "_admission_refs": [42]}
    datasets.path.write_text(json.dumps({"schema": local_dataset.SCHEMA, **value}))
    reopened = LocalDatasetService(datasets.config)
    assert reopened.operation_invalid


def test_import_owner_drift_retains_pending_without_other_store_write(
    tmp_path, original, monkeypatch
):
    importer, _, store, owner = setup(tmp_path)
    other = setup(tmp_path / "other-profile")[3]
    monkeypatch.setattr(local_recording_import, "_selected_curation_owner", lambda _: other)
    with pytest.raises(BoundaryError, match="workspace_owner_changed"):
        importer.start_native_agent_run(
            str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"])
    assert not store.manifest_ids()
    for selected in (owner, other):
        with selected.transaction() as db:
            assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_Gold_cannot_be_reserved_as_training_through_new_publish_owner(tmp_path, original):
    from stpd.fullrun.native_agent_sampled_source import NativeAgentSampledRef, _partition

    _, datasets, store, owner, raw_id = imported(tmp_path, original)
    datasets.start_native_agent_preview([raw_id])
    preview = settled(datasets)
    refs = tuple(NativeAgentSampledRef(**ref) for ref in datasets.operation["_admission_refs"])
    encoded, _ = _partition(store, refs, "train")
    runs = {run["run_id"] for run in json.loads(encoded)["runs"]}
    owner.ledger.claim("gold-original-machine-fixture", "gold", runs)
    datasets.start_publish(preview["preview_id"])
    result = settled(datasets)
    assert result["status"] == "failed" and result["error_code"] == "gold_reserved_data"
    assert "training_source_id" not in result
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims "
                          "WHERE purpose='training'").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)


def test_closed_import_rejects_genuine_unfinished_protocol_prefix(tmp_path, original):
    from stpd.fullrun.native_agent_sampled_source import _verified

    original.f.events = [event for event in original.f.events if event["kind"] != "stopped"]
    original.f.write()
    _verified(original.directory)  # The original remains a valid typed protocol prefix.
    importer, _, store, owner = setup(tmp_path)
    with pytest.raises(BoundaryError, match="native_agent_closed_run_required"):
        importer.start_native_agent_run(
            str(original.directory), FIXTURE_COHORT, RELATION_SPEC["id"])
    assert importer.thread is None and not store.manifest_ids()
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)


def test_import_capability_values_cannot_mutate_shared_fixed_relation():
    choices = local_recording_import.native_agent_import_choices()
    original = choices["relations"][0]["relation"]["sha256"]
    choices["relations"][0]["relation"]["sha256"] = "f" * 64
    assert local_recording_import.native_agent_import_choices()["relations"][0]["relation"][
        "sha256"] == original
