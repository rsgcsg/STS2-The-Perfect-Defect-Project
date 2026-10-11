"""Synthetic S0-format byte/ArtifactStore/ledger tests, never native/Human evidence."""

from __future__ import annotations

import io
import runpy
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.managed_local_workspace import ROOT_NAME, create_managed_workspace
from stpd.fullrun import protocol_source as sources

FIXTURE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "tools/test/test_baseline_s0_dataset.py")
)
ORIGINAL = Producer("fixture://synthetic-s0", "e" * 40, "a" * 64)
PROJECTION = Producer("fixture://synthetic-projection", "b" * 40, "b" * 64)
OPERATION = "a" * 32


def setup_store(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    selected = create_managed_workspace(state)
    owner = selected["curation_owner"]
    store = ManifestArtifactStore(
        LocalBlobStore(state / ROOT_NAME / selected["workspace_id"] / "store", create=False)
    )
    return store, owner


def publish_run(store, root, seed="1", suffix="one"):
    directory, rows = FIXTURE["create_run"](root, seed=seed, suffix=suffix)
    ref = sources.publish_protocol_run(
        store, directory, ORIGINAL, PROJECTION, origin="synthetic_fixture"
    )
    return ref, directory, rows


def closure(store, artifact_id):
    seen, pending = set(), [artifact_id]
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(p.artifact_id for p in store.get_manifest(identity).parents)
    return seen


def replace_raw(store, ref, mutate):
    manifest = store.get_manifest(ref.raw_id)
    raw = sources._bytes(store, manifest, "archive", sources.MAX_ARCHIVE_BYTES)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    mutate(files)
    payload = store.put_bytes("archive", sources._archive(files), "application/zip")
    info = manifest.parameters.value() | {"inventory": sources._inventory(files)}
    forged = Manifest(
        "evidence", manifest.producer, payloads=(payload,), parameters=FrozenObject.of(info)
    )
    store.publish(forged)
    return sources.ProtocolRunRef(forged.artifact_id, ref.report_id)


def test_archived_original_bytes_replay_without_live_template_or_run(tmp_path):
    store, _owner = setup_store(tmp_path)
    ref, directory, _ = publish_run(store, tmp_path)
    shutil.rmtree(directory)
    shutil.rmtree(tmp_path / "host")
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    assert source.dataset.source_kind == "synthetic"
    assert source.source_ids == (ref.raw_id,)
    assert source.runs == {"s0-collect-one"}
    assert len(source.index) == 2
    assert all(item["capsule_path"].startswith("captures/") for item in source.index)
    report = decode_json(store.bytes(store.get_manifest(ref.report_id).payload("report")))
    assert report["origin"] == "synthetic_fixture"
    assert report["original_producer"] == ORIGINAL.to_dict()
    assert store.get_manifest(ref.report_id).producer == PROJECTION
    assert report["coverage"]["native_exit"] is None  # Legacy absence is retained.
    assert report["counts"]["excluded_unoffered_captures"] == 1
    assert report["coverage"]["history"] == "sampled_acquired_history_only"
    assert all(not item["path"].startswith("profiles/") for item in report["inventory"])
    assert sources.verify_protocol_source_partition(store, source.manifest.artifact_id) == source


def test_multisplit_ancestry_and_original_source_use_are_isolated(tmp_path):
    store, owner = setup_store(tmp_path)
    selected = {}
    refs = {}
    for index, split in enumerate(("train", "dev", "test"), 1):
        refs[split], _, _ = publish_run(store, tmp_path, str(index), split)
        selected[split] = sources.publish_protocol_source_partition(
            store, (refs[split],), split, PROJECTION
        )
        owner.reserve_verified_protocol_source(store, selected[split].manifest.artifact_id)
    train = selected["train"]
    assert closure(store, train.manifest.artifact_id) == {
        train.manifest.artifact_id,
        refs["train"].raw_id,
        refs["train"].report_id,
    }
    owner.record_verified_protocol_training_use(store, train.manifest.artifact_id, OPERATION)
    summary = owner.require_verified_protocol_training_use(
        store, train.manifest.artifact_id, OPERATION
    )
    assert summary["source_ids"] == [refs["train"].raw_id]
    with owner.transaction() as db:
        assert db.execute("SELECT source,kind FROM curation_source_uses").fetchall() == [
            (refs["train"].raw_id, "training")
        ]
        assert db.execute("SELECT run,kind FROM curation_uses").fetchall() == [
            ("s0-collect-train", "training")
        ]
        # The exact index is actual raw offer membership, not a fake Human dataset.
        assert db.execute("SELECT source FROM curation_exact_source_index").fetchall() == [
            (ref.raw_id,) for ref in refs.values()
        ]
        assert db.execute("SELECT count(*) FROM curation_occurrences").fetchone() == (0,)
    model = Manifest("model", PROJECTION, (Parent("source", train.manifest.artifact_id),))
    store.publish(model)
    for split in ("dev", "test"):
        identity = selected[split].manifest.artifact_id
        assert owner.ledger.dataset(identity) == ("test", set(selected[split].runs))
        for method in (
            owner.record_verified_protocol_training_use,
            owner.require_verified_protocol_training_use,
        ):
            with pytest.raises(BoundaryError, match="train_only_source_required"):
                method(store, identity, OPERATION)
        receipt = owner.record_verified_protocol_evaluation_use(
            store, identity, model.artifact_id, ("b" if split == "dev" else "c") * 32
        )
        assert receipt["use"] == "evaluation"
        assert not receipt["clean_held_out_claim"]
    with owner.transaction() as db:
        assert {tuple(row) for row in db.execute("SELECT run,kind FROM curation_uses")} == {
            ("s0-collect-train", "training"),
            ("s0-collect-dev", "evaluation"),
            ("s0-collect-test", "evaluation"),
        }


@pytest.mark.parametrize("left,right", [("train", "dev"), ("train", "test"), ("dev", "test")])
def test_same_seed_group_cannot_cross_partition_purpose(tmp_path, left, right):
    store, owner = setup_store(tmp_path)
    first, _, _ = publish_run(store, tmp_path, "1", "one")
    second, _, _ = publish_run(store, tmp_path, "1", "two")
    a = sources.publish_protocol_source_partition(store, (first,), left, PROJECTION)
    b = sources.publish_protocol_source_partition(store, (second,), right, PROJECTION)
    assert a.source_groups == b.source_groups
    owner.reserve_verified_protocol_source(store, a.manifest.artifact_id)
    with pytest.raises(BoundaryError, match="protocol_split_purpose_overlap"):
        owner.reserve_verified_protocol_source(store, b.manifest.artifact_id)
    assert owner.ledger.dataset(b.manifest.artifact_id) is None


def test_existing_gold_original_run_cannot_be_reserved_or_used(tmp_path):
    store, owner = setup_store(tmp_path)
    # Reserve before the new fixture source appears: existing exact run protection.
    owner.ledger.claim("gold-fixture", "gold", {"s0-collect-one"})
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)


def test_training_use_requires_actual_complete_offer_index(tmp_path):
    store, owner = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    with pytest.raises(BoundaryError, match="protocol_source_claim_mismatch"):
        owner.record_verified_protocol_training_use(store, source.manifest.artifact_id, OPERATION)
    owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
    with owner.transaction() as db:
        db.execute("DELETE FROM curation_source_decisions WHERE source=?", (ref.raw_id,))
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        owner.record_verified_protocol_training_use(store, source.manifest.artifact_id, OPERATION)


@pytest.mark.parametrize("which", ["raw", "report", "source"])
def test_actual_store_payload_corruption_rejected(tmp_path, which):
    store, _ = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    manifest = store.get_manifest(
        {"raw": ref.raw_id, "report": ref.report_id, "source": source.manifest.artifact_id}[which]
    )
    index = decode_json(store.blobs.get(f"payload-indexes/v1/{manifest.payloads[0].sha256}.json"))
    (store.blobs.root / "objects/sha256" / index["chunks"][0]["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(BoundaryError):
        sources.verify_protocol_source_partition(store, source.manifest.artifact_id)


def test_rehashed_report_cannot_launder_self_declared_qualification(tmp_path):
    store, _ = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    original = store.get_manifest(ref.report_id)
    report = decode_json(store.bytes(original.payload("report")))
    report["coverage"]["history"] = "full_native_history"
    payload = store.put_bytes("report", json_bytes(report))
    false = Manifest(
        "analysis", original.producer, original.parents, (payload,), original.parameters
    )
    store.publish(false)
    with pytest.raises(BoundaryError, match="verification_report_binding"):
        sources.publish_protocol_source_partition(
            store, (sources.ProtocolRunRef(ref.raw_id, false.artifact_id),), "train", PROJECTION
        )


def test_foreign_report_lineage_and_extra_collection_parent_reject(tmp_path):
    store, _ = setup_store(tmp_path)
    a, _, _ = publish_run(store, tmp_path)
    b, _, _ = publish_run(store, tmp_path, "2", "two")
    with pytest.raises(BoundaryError, match="typed_verification_report_required"):
        sources.publish_protocol_source_partition(
            store, (sources.ProtocolRunRef(a.raw_id, b.report_id),), "train", PROJECTION
        )
    source = sources.publish_protocol_source_partition(store, (a,), "train", PROJECTION)
    manifest = source.manifest
    mixed = Manifest(
        manifest.kind,
        manifest.producer,
        (*manifest.parents, Parent("collection", b.report_id)),
        manifest.payloads,
        manifest.parameters,
    )
    store.publish(mixed)
    with pytest.raises(BoundaryError, match="partition_parent_closure_mismatch"):
        sources.verify_protocol_source_partition(store, mixed.artifact_id)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("gap", "ledger_order_or_type"),
        ("capsule", "capsule_bytes_or_identity_mismatch"),
        ("template", "template_file_checksum_drift"),
        ("extra", "unrecorded_archive_files"),
    ],
)
def test_resigned_archive_still_requires_actual_raw_joins(tmp_path, mutation, code):
    store, _ = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)

    def mutate(files):
        if mutation == "gap":
            rows = [decode_json(line) for line in files["run/records.jsonl"].splitlines()]
            rows[1]["record_index"] = 99
            files["run/records.jsonl"] = b"".join(
                json_bytes(row).rstrip(b"\n") + b"\n" for row in rows
            )
        elif mutation == "capsule":
            key = next(key for key in files if key.startswith("run/captures/"))
            files[key] += b" "
        elif mutation == "template":
            key = next(key for key in files if key.startswith("template/user-data/"))
            files[key] = b"x" * len(files[key])
        else:
            files["run/unrecorded.log"] = b"fixture extra"

    forged = replace_raw(store, ref, mutate)
    with pytest.raises(BoundaryError, match=code):
        sources.publish_protocol_source_partition(store, (forged,), "train", PROJECTION)


@pytest.mark.parametrize(
    "name,mode",
    [("../escape", stat.S_IFREG), ("run/link", stat.S_IFLNK), ("run/records.jsonl", stat.S_IFDIR)],
)
def test_unsafe_zip_entries_reject_before_extract(tmp_path, name, mode):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        entry = zipfile.ZipInfo(name)
        entry.external_attr = (mode | 0o600) << 16
        archive.writestr(entry, b"fixture")
    with pytest.raises(BoundaryError, match="unsafe_archive_entry"):
        sources._extract(output.getvalue(), tmp_path)
    assert not (tmp_path.parent / "escape").exists()


def test_original_producer_revision_and_dirty_hash_shape_are_bound(tmp_path):
    store, _ = setup_store(tmp_path)
    directory, rows = FIXTURE["create_run"](tmp_path)
    with pytest.raises(BoundaryError, match="original_producer_revision_mismatch"):
        sources.publish_protocol_run(
            store, directory, PROJECTION, PROJECTION, origin="synthetic_fixture"
        )
    rows[0]["payload"]["source_diff_sha256"] = "unknown"
    FIXTURE["write_rows"](directory, rows)
    with pytest.raises(BoundaryError):
        sources.publish_protocol_run(
            store, directory, ORIGINAL, PROJECTION, origin="synthetic_fixture"
        )
    assert store.manifest_ids() == ()


def test_missing_raw_inventory_files_fail_with_typed_boundary(tmp_path):
    store, _ = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    forged = replace_raw(store, ref, lambda files: files.pop("run/records.jsonl"))
    with pytest.raises(BoundaryError, match="raw_archive_incomplete_or_invalid"):
        sources.publish_protocol_source_partition(store, (forged,), "train", PROJECTION)


def test_derived_row_tampering_cannot_be_rehashed_into_admission(tmp_path):
    store, _ = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    value = decode_json(source.dataset.source_bytes)
    value["runs"][0]["steps"][0]["chosen_action_id"] = None
    payload = store.put_bytes("source", json_bytes(value))
    manifest = source.manifest
    false = Manifest(
        "dataset",
        PROJECTION,
        manifest.parents,
        (payload,),
        FrozenObject.of(manifest.parameters.value() | {"source_sha256": payload.sha256}),
    )
    store.publish(false)
    with pytest.raises(BoundaryError, match="derived_source_raw_join_mismatch"):
        sources.verify_protocol_source_partition(store, false.artifact_id)


def test_foreign_model_same_group_cannot_evaluate_different_raw_id(tmp_path):
    store, owner = setup_store(tmp_path)
    a, _, _ = publish_run(store, tmp_path, "1", "one")
    b, _, _ = publish_run(store, tmp_path, "1", "two")
    imported_train = sources.publish_protocol_source_partition(store, (a,), "train", PROJECTION)
    heldout = sources.publish_protocol_source_partition(store, (b,), "test", PROJECTION)
    owner.reserve_verified_protocol_source(store, heldout.manifest.artifact_id)
    # Imported model has no local training reservation; immutable ancestry still binds its group.
    model = Manifest("model", PROJECTION, (Parent("source", imported_train.manifest.artifact_id),))
    store.publish(model)
    with pytest.raises(BoundaryError, match="model_held_out_source_group_overlap"):
        owner.record_verified_protocol_evaluation_use(
            store, heldout.manifest.artifact_id, model.artifact_id, OPERATION
        )


def test_native_exit_report_retains_new_actual_receipt_fields(tmp_path):
    store, _ = setup_store(tmp_path)
    directory, rows = FIXTURE["create_run"](tmp_path)
    FIXTURE["add_native_exit"](directory, rows, forced=True)
    ref = sources.publish_protocol_run(
        store, directory, ORIGINAL, PROJECTION, origin="synthetic_fixture"
    )
    report = decode_json(store.bytes(store.get_manifest(ref.report_id).payload("report")))
    assert report["coverage"]["native_exit"]["receipt"]["forced"] is True
    assert sources.publish_protocol_verification(store, ref.raw_id, PROJECTION) == ref


def test_derived_source_use_cannot_replace_original_raw_source_use(tmp_path):
    store, owner = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
    owner.ledger.use(source.runs, "training", OPERATION)
    owner.ledger.use_source(source.manifest.artifact_id, "training", OPERATION)
    with pytest.raises(BoundaryError, match="protocol_training_use_missing"):
        owner.require_verified_protocol_training_use(store, source.manifest.artifact_id, OPERATION)


def test_wrong_raw_run_index_does_not_admit_use(tmp_path):
    store, owner = setup_store(tmp_path)
    ref, _, _ = publish_run(store, tmp_path)
    source = sources.publish_protocol_source_partition(store, (ref,), "train", PROJECTION)
    owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
    with owner.transaction() as db:
        db.execute(
            "UPDATE curation_source_runs SET run='different-fixture-run' WHERE source=?",
            (ref.raw_id,),
        )
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        owner.record_verified_protocol_training_use(store, source.manifest.artifact_id, OPERATION)
