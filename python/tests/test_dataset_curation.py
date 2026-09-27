from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_decision_store import setup
from test_hub_member_data import MEMBER

from spireagent.hub.curation import CurationLedger
from spireagent.hub.curation_access import record_use
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError
from stpd.fullrun.decision_dataset import SelectionRules, _identity
from stpd.fullrun.decision_store import load, preview


def build(jobs, upload, purpose="training", paired=None):
    request = {
        "uploads": [upload],
        "name": purpose,
        "rules": SelectionRules().to_dict(),
        "preview_id": None,
        "curation": {"purpose": purpose, "paired_training": paired},
    }
    first = jobs.create(MEMBER, request)
    jobs.run(first["id"])
    result = jobs.read(MEMBER, first["id"])
    if result["state"] != "completed":
        return result
    second = jobs.create(MEMBER, {**request, "preview_id": first["id"]})
    jobs.run(second["id"])
    return jobs.read(MEMBER, second["id"])


def test_manifest_first_keeps_exact_rows_and_historical_snapshot(tmp_path: Path) -> None:
    owner, upload, source, jobs = setup(tmp_path)
    result = build(jobs, upload)
    assert result["state"] == "completed"
    manifest, selected = load(owner.store, result["result"]["artifact_id"])
    assert {p.role for p in manifest.payloads} == {"selection"}
    assert len(selected.records) == 6
    assert set(selected.report.value()["splits"].values()) == {"train", "dev"}
    record = selected.records[0]
    ledger = CurationLedger(owner.operations)
    ledger.annotate(_identity(record), MEMBER.subject, "exclude", "误操作")
    # The existing immutable dataset does not silently change underneath a model.
    assert load(owner.store, manifest.artifact_id)[1] == selected
    revised = build(jobs, upload)
    _, newer = load(owner.store, revised["result"]["artifact_id"])
    assert len(newer.records) < len(selected.records)
    assert record.transition_id not in {r.transition_id for r in newer.records}
    ledger.annotate(_identity(record), MEMBER.subject, "restore", "复核后恢复")
    restored = build(jobs, upload)
    assert (
        load(owner.store, restored["result"]["artifact_id"])[1].records
        == preview(owner.store, (source,), SelectionRules()).records
    )
    assert [item["action"] for item in ledger.history(_identity(record))] == ["exclude", "restore"]


def test_separate_test_dataset_blocks_whole_run_overlap(tmp_path: Path) -> None:
    owner, upload, _, jobs = setup(tmp_path)
    training = build(jobs, upload)["result"]["artifact_id"]
    request = {
        "uploads": [upload],
        "name": "test",
        "rules": SelectionRules().to_dict(),
        "preview_id": None,
        "curation": {"purpose": "test", "paired_training": training},
    }
    first = jobs.create(MEMBER, request)
    jobs.run(first["id"])
    assert jobs.read(MEMBER, first["id"])["error"] == "training_test_overlap"
    independent = build(jobs, upload, "test")
    manifest, selected = load(owner.store, independent["result"]["artifact_id"])
    assert set(selected.report.value()["splits"].values()) == {"test"}
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        record_use(owner.operations, owner.store, manifest, "training")


def test_gold_reserves_raw_and_derivatives_and_cannot_be_hidden_away(tmp_path: Path) -> None:
    owner, upload, source, jobs = setup(tmp_path)
    result = build(jobs, upload, "gold")
    assert result["state"] == "completed", result
    artifact = result["result"]["artifact_id"]
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        record_use(owner.operations, owner.store, source, "download")
    with pytest.raises(BoundaryError, match="unauthorized"):
        jobs.collections.artifact(artifact)
    jobs.set_archived(MEMBER, {"ids": [result["id"]], "archived": True})
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        record_use(owner.operations, owner.store, source, "training")
    blocked = build(jobs, upload)
    assert blocked["error"] == "gold_reserved_data"
    second_gold = build(jobs, upload, "gold")
    assert second_gold["error"] == "gold_requires_gold_merge"


def test_gold_cannot_relabel_existing_dataset(tmp_path: Path) -> None:
    _, upload, _, jobs = setup(tmp_path)
    build(jobs, upload)
    assert build(jobs, upload, "gold")["error"] == "gold_already_in_other_dataset"


def test_gold_and_training_claim_race_has_exactly_one_winner(tmp_path: Path) -> None:
    ledger = CurationLedger(Operations(tmp_path / "operations.sqlite"))

    def claim(purpose):
        try:
            ledger.claim(purpose, purpose, {"run"})
            return purpose
        except BoundaryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(claim, ["gold", "training"]))
    assert len([item for item in result if item]) == 1


def test_hub_gold_inventory_check_uses_verified_upload_and_commits_atomically(
    tmp_path: Path,
) -> None:
    owner, _, source, _ = setup(tmp_path)
    ledger = CurationLedger(owner.operations)
    with pytest.raises(BoundaryError, match="gold_source_inventory_pending"):
        ledger.claim("reserved", "gold", {"run"}, require_inventory=True)
    with owner.operations.transaction() as db:
        assert db.execute("SELECT 1 FROM curation_claims WHERE id='reserved'").fetchone() is None
        db.execute(
            "INSERT INTO curation_sources VALUES(?,?,1)",
            (source.artifact_id, source.payload("archive").sha256),
        )
    ledger.claim("reserved", "gold", {"run"}, require_inventory=True)
    with owner.operations.transaction() as db:
        assert db.execute(
            "SELECT purpose FROM curation_claims WHERE id='reserved'"
        ).fetchone()[0] == "gold"


def test_transitive_duplicate_groups_and_unindexed_use_are_not_lost(tmp_path: Path) -> None:
    operations = Operations(tmp_path / "operations.sqlite")
    ledger = CurationLedger(operations)
    with operations.transaction() as db:
        db.executemany(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            [("x", "a"), ("x", "b"), ("y", "b"), ("y", "c")],
        )
    assert ledger.overlap({"a"}, {"c"})["overlap"]
    ledger.use_source("source", "training", "old-model")
    with operations.transaction() as db:
        db.execute("INSERT INTO curation_source_runs VALUES('source','c')")
    with pytest.raises(BoundaryError, match="gold_previously_used_for_training"):
        ledger.claim("gold", "gold", {"a"})
    # Hiding or moving an artifact cannot remove the durable use ledger.
    assert ledger.source_runs("source") is None


def test_download_materialization_is_durable_and_keeps_library_version(tmp_path: Path) -> None:
    import io

    import pyarrow.parquet as pq

    owner, upload, _, jobs = setup(tmp_path)
    dataset_id = build(jobs, upload)["result"]["artifact_id"]
    task = jobs.materialize(MEMBER, {"dataset_id": dataset_id})
    assert jobs.materialize(MEMBER, {"dataset_id": dataset_id}) == task
    jobs.run(task["id"])
    result = jobs.read(MEMBER, task["id"])
    assert result["state"] == "completed"
    materialized = owner.store.get_manifest(result["result"]["artifact_id"])
    assert materialized.kind == "analysis"
    assert materialized.parent("dataset") == dataset_id
    assert {p.role for p in owner.store.get_manifest(dataset_id).payloads} == {"selection"}
    raw = b"".join(owner.store.read_payload(materialized.payload("records")))
    assert pq.read_table(io.BytesIO(raw)).num_rows == 6
    assert jobs.materialize(MEMBER, {"dataset_id": dataset_id})["id"] == task["id"]


def test_quality_is_paged_and_parent_exclusion_keeps_original_sequences(tmp_path: Path) -> None:
    from spireagent.hub.quality import QualityAnnotations

    owner, upload, source, jobs = setup(tmp_path)
    quality = QualityAnnotations(owner)
    assert quality.read(MEMBER, upload, 1, 0)["availability"] == "index_pending"
    build(jobs, upload)
    first = quality.read(MEMBER, upload, 1, 0)
    second = quality.read(MEMBER, upload, 1, 1)
    assert first["total"] == 6 and first["next_offset"] == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    key = first["items"][0]["id"]
    quality.write(
        MEMBER, {"upload_id": upload, "occurrence": key, "action": "exclude", "reason": "点错了"}
    )
    result = build(jobs, upload)
    dataset = load(owner.store, result["result"]["artifact_id"])[1]
    assert dataset.report.value()["exclusion_counts"]["human_quality_exclusion"] == 1
    assert dataset.report.value()["exclusion_counts"]["excluded_parent_decision"] == 1
    assert len(preview(owner.store, (source,), SelectionRules()).records) == 6
    assert quality.read(MEMBER, upload, 1, 0)["items"][0]["annotations"][0]["reason"] == "点错了"


def test_gold_merge_and_catalog_preserve_sealing(tmp_path: Path) -> None:
    owner, upload, source, jobs = setup(tmp_path)
    first = build(jobs, upload, "gold")["result"]["artifact_id"]
    body = {
        "datasets": [first],
        "name": "Gold union",
        "preview_id": None,
        "rules": SelectionRules().to_dict(),
        "curation": {"purpose": "gold", "paired_training": None},
    }
    for _ in range(2):
        task = jobs.create(MEMBER, body)
        jobs.run(task["id"])
        assert jobs.read(MEMBER, task["id"])["state"] == "completed"
        task = jobs.create(MEMBER, {**body, "preview_id": task["id"]})
        jobs.run(task["id"])
        result = jobs.read(MEMBER, task["id"])
        assert result["state"] == "completed", result
        merged = result["result"]["artifact_id"]
        assert owner.store.get_manifest(merged).parameters.value()["purpose"] == "gold"
        body["datasets"] = [merged]
    catalog = owner.console_index.artifacts(MEMBER, "datasets", limit=25, offset=0)
    assert len(catalog["items"]) == 3
    assert all(item["payloads"] == [] and item["parents"] == [] for item in catalog["items"])
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        record_use(owner.operations, owner.store, source, "download")


def test_offline_training_cannot_wrap_held_out_data(tmp_path: Path) -> None:
    from spireagent.artifact_contracts import Manifest, Parent
    from stpd.fullrun.dataset_policy import training_sources
    from stpd.workers.contracts import TrainingConfig, prepare_training_input

    owner, upload, _, jobs = setup(tmp_path)
    test_id = build(jobs, upload, "test")["result"]["artifact_id"]
    wrapper = Manifest("feature_set", owner.producer, parents=(Parent("data", test_id),))
    owner.store.publish(wrapper)
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        training_sources(owner.store, wrapper.artifact_id)
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        prepare_training_input(owner.store, wrapper.artifact_id, owner.producer, TrainingConfig())


def test_selection_receipt_never_exports_source_context_or_quality_comments(tmp_path: Path) -> None:
    import json

    owner, upload, _, jobs = setup(tmp_path)
    original = build(jobs, upload)["result"]["artifact_id"]
    record = load(owner.store, original)[1].records[0]
    ledger = CurationLedger(owner.operations)
    ledger.annotate(_identity(record), MEMBER.subject, "flag", "private reason not for export")
    result = build(jobs, upload)
    manifest, selected = load(owner.store, result["result"]["artifact_id"])
    body = b"".join(owner.store.read_payload(manifest.payload("selection")))
    receipt = json.loads(body)
    assert set(receipt) == {
        "schema",
        "logical_id",
        "report_sha256",
        "annotations",
        "paired_training",
    }
    assert b"private reason" not in body
    assert "context" not in receipt and "report" not in receipt
    assert len(selected.records) == 6
    assert ledger.history(_identity(record))[0]["reason"] == "private reason not for export"


def test_quality_revision_and_gold_reservation_are_one_transaction(tmp_path: Path) -> None:
    owner, upload, _, jobs = setup(tmp_path)
    original = build(jobs, upload)["result"]["artifact_id"]
    selected = load(owner.store, original)[1]
    ledger = CurationLedger(owner.operations)
    before = ledger.annotations(selected.records)
    ledger.annotate(_identity(selected.records[0]), MEMBER.subject, "exclude", "changed")
    with pytest.raises(BoundaryError, match="quality_annotations_changed"):
        ledger.claim(
            "stale",
            "training",
            (r.run_id for r in selected.records),
            annotation_revision=before["revision"],
        )
    with owner.operations.transaction() as db:
        assert db.execute("SELECT 1 FROM curation_claims WHERE id='stale'").fetchone() is None


def test_receiver_can_index_sources_after_gold_without_public_access_bypass(tmp_path: Path) -> None:
    import json

    owner, upload, source, jobs = setup(tmp_path)
    build(jobs, upload, "gold")
    with owner.operations.transaction() as db:
        # Simulate a newly arriving/recovered source whose projection is not yet indexed.
        db.execute("UPDATE curation_sources SET complete=0 WHERE id=?", (source.artifact_id,))
    pending = jobs.pending()
    assert pending
    jobs.run(pending)
    with owner.operations.transaction() as db:
        row = db.execute(
            "SELECT owner,state,result,error FROM decision_jobs WHERE id=?", (pending,)
        ).fetchone()
        assert row["owner"] == "receiver" and row["state"] == "completed", dict(row)
        assert json.loads(row["result"])["selected"] == 6
    assert CurationLedger(owner.operations).source_runs(source.artifact_id)
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        record_use(owner.operations, owner.store, source, "download")


def test_legacy_report_cannot_export_unselected_gold_context(tmp_path: Path) -> None:
    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.json_boundary import FrozenObject

    owner, _, source, _ = setup(tmp_path)
    ledger = CurationLedger(owner.operations)
    # A legacy manifest's selected set can be disjoint while its report retains
    # unselected source context. Exercise the download guard with that registry state.
    with owner.operations.transaction() as db:
        db.execute(
            "INSERT INTO curation_sources VALUES(?,?,1)",
            (source.artifact_id, source.payload("archive").sha256),
        )
        db.executemany(
            "INSERT INTO curation_source_runs VALUES(?,?)",
            [(source.artifact_id, "training-run"), (source.artifact_id, "gold-run")],
        )
    legacy = Manifest(
        "dataset",
        owner.producer,
        (Parent("source", source.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/decision-dataset-v1"}),
    )
    owner.store.publish(legacy)
    ledger.claim("old-selection", "training", {"training-run"})
    ledger.bind("old-selection", legacy.artifact_id)
    ledger.claim("gold-selection", "gold", {"gold-run"})
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        record_use(owner.operations, owner.store, legacy, "download")


def test_test_merge_cannot_be_relabelled_training(tmp_path: Path) -> None:
    _, upload, _, jobs = setup(tmp_path)
    test = build(jobs, upload, "test")["result"]["artifact_id"]
    first = jobs.create(
        MEMBER,
        {
            "name": "invalid training merge",
            "datasets": [test],
            "rules": SelectionRules().to_dict(),
            "preview_id": None,
            "curation": {"purpose": "training", "paired_training": None},
        },
    )
    jobs.run(first["id"])
    assert jobs.read(MEMBER, first["id"])["error"] == "test_merge_requires_only_test"


def test_quality_source_membership_is_not_inferred_from_shared_run(tmp_path: Path) -> None:
    from spireagent.hub.quality import QualityAnnotations

    owner, upload, source, jobs = setup(tmp_path)
    build(jobs, upload)
    quality = QualityAnnotations(owner)
    initial = quality.read(MEMBER, upload, 25, 0)
    with owner.operations.transaction() as db:
        first = db.execute("SELECT * FROM curation_occurrences LIMIT 1").fetchone()
        # Another package can contain a later decision of exactly the same native run.
        db.execute(
            "INSERT INTO curation_occurrences VALUES(?,?,?)",
            ("f" * 64, first["run"], "other-package-decision"),
        )
        db.execute(
            "INSERT INTO curation_occurrence_details VALUES(?,999,'combat','combat','{}')",
            ("f" * 64,),
        )
        db.execute(
            "INSERT INTO curation_source_decisions VALUES(?,?,?)", ("e" * 64, "f" * 64, "d" * 64)
        )
    assert quality.read(MEMBER, upload, 25, 0) == initial
    with pytest.raises(BoundaryError, match="occurrence_not_found"):
        quality.write(
            MEMBER,
            {
                "upload_id": upload,
                "occurrence": "f" * 64,
                "action": "exclude",
                "reason": "wrong source",
            },
        )
    with owner.operations.transaction() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM curation_source_decisions WHERE source=?",
                (source.artifact_id,),
            ).fetchone()[0]
            == initial["total"]
        )


def test_legacy_source_index_is_rebuilt_by_background_profile(tmp_path: Path) -> None:
    from spireagent.hub.quality import QualityAnnotations

    owner, upload, source, jobs = setup(tmp_path)
    jobs.run(jobs.pending())
    quality = QualityAnnotations(owner)
    with owner.operations.transaction() as db:
        db.execute("DELETE FROM curation_exact_source_index")
        db.execute("DELETE FROM curation_source_decisions")
        db.execute(
            "UPDATE decision_jobs SET request=json_remove(request,'$.decision_index_version')"
        )
    assert quality.read(MEMBER, upload, 25, 0)["availability"] == "index_pending"
    with pytest.raises(BoundaryError, match="source_index_pending"):
        quality.write(
            MEMBER,
            {
                "upload_id": upload,
                "occurrence": "f" * 64,
                "action": "flag",
                "reason": "wait for accurate index",
            },
        )
    task = jobs.pending()
    assert task is not None
    jobs.run(task)
    assert CurationLedger(owner.operations).exact_source_ready(source.artifact_id)
    assert quality.read(MEMBER, upload, 25, 0)["total"] == 6
    assert jobs.pending() is None
