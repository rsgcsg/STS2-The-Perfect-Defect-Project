"""Real Store/ledger fixtures for reusable preparation; no Human qualification."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.managed_local_workspace import ROOT_NAME, create_managed_workspace
from spireagent.workbench.p1_data_preparation import (
    P1PreparationCache,
    freeze_p1_data,
    preparation_identity,
    prepare_p1_data,
)
from spireagent.workbench.p1_training_data import record_p1_training_use, reserve_p1_snapshot
from stpd.p1.dataset import inspect_projections, iter_snapshot_points, merge_projection_inspections


def workspace(tmp_path, names=("a", "b", "c")):
    state = tmp_path / "state"
    state.mkdir()
    selected = create_managed_workspace(state)
    owner = selected["curation_owner"]
    store = ManifestArtifactStore(
        LocalBlobStore(state / ROOT_NAME / selected["workspace_id"] / "store", create=False)
    )
    projections = [projection_fixture(store, name)[0] for name in names]
    return owner, store, projections


def test_incremental_decode_counts_and_fixed_snapshot_consumer(tmp_path, capsys):
    owner, store, projections = workspace(tmp_path)
    first = prepare_p1_data(owner, store)
    second = prepare_p1_data(owner, store)
    assert first["cache"]["decoded_projections"] == 3
    assert second["cache"]["decoded_projections"] == 0
    assert second["cache"]["hits"] == 3
    assert first["selection"] == second["selection"]
    assert first["ready_to_freeze"]
    frozen = freeze_p1_data(owner, store, PRODUCER, **second["selection"], reserve=True)
    record_p1_training_use(owner, store, frozen["snapshot_id"], "a" * 32)
    projection_fixture(store, "d")
    third = prepare_p1_data(owner, store)
    assert third["cache"]["decoded_projections"] == 1
    assert third["cache"]["hits"] == 3
    assert third["ready_to_freeze"]
    roles = {
        p["source_decision_id"]: third["allocation"]["split_by_group"][p["group_id"]]
        for p in third["index"]["points"]
    }
    assert roles["decision-d"] == "train"
    for point in first["index"]["points"]:
        assert (
            roles[point["source_decision_id"]]
            == first["selection"]["split_by_group"][point["group_id"]]
        )
    next_frozen = freeze_p1_data(owner, store, PRODUCER, **third["selection"], reserve=True)
    assert len(list(iter_snapshot_points(store, frozen["snapshot_id"]))) == 3
    assert len(list(iter_snapshot_points(store, next_frozen["snapshot_id"]))) == 4
    # Disposable cache must not break the existing authority backup contract.
    owner.backup(tmp_path / "backup.sqlite")
    print(
        json.dumps(
            {
                "first": first["cache"],
                "second": second["cache"],
                "added_source": third["cache"],
                "seconds": [x["elapsed_seconds"] for x in (first, second, third)],
            }
        )
    )


def test_merge_matches_normal_reader_alias_and_selection(tmp_path):
    owner, store, projections = workspace(tmp_path)
    first = projections[0]
    alias = Manifest(
        first.kind,
        first.producer,
        first.parents,
        first.payloads,
        FrozenObject.of({**first.parameters.value(), "tool_release_id": "second-tool"}),
    )
    store.publish(alias)
    ids = [p.artifact_id for p in (*projections, alias)]
    indexes = [inspect_projections(store, [identity]) for identity in ids]
    assert merge_projection_inspections(indexes) == inspect_projections(store, ids)
    selected = {alias.artifact_id: [], first.artifact_id: ["decision-a"]}
    assert merge_projection_inspections(
        indexes, selected_decisions=selected
    ) == inspect_projections(store, ids, selected_decisions=selected)
    prepared = prepare_p1_data(owner, store)
    assert prepared["index"]["unique_N"] == 3
    assert prepared["index"]["exact_duplicate_N"] == 1


def test_unknown_groups_require_explicit_engineering_policy_and_are_not_qualified(tmp_path):
    owner, store, _ = workspace(tmp_path, names=())
    for name in ("x", "y", "z"):
        _, source, value = projection_fixture(store, name)
        unknown = replace(
            source, parameters=FrozenObject.of({**source.parameters.value(), "lineage": {}})
        )
        store.publish(unknown)
        # Explicitly select only the unknown source variants below.
        projection_fixture(store, name, value=value, original=unknown)
    ids = [
        row
        for row in store.manifest_ids()
        if store.get_manifest(row).kind == "analysis"
        and store.get_manifest(store.get_manifest(row).parent("source")).parameters.value()[
            "lineage"
        ]
        == {}
    ]
    conservative = prepare_p1_data(owner, store, projection_ids=ids)
    assert not conservative["ready_to_freeze"]
    assert set(conservative["allocation"]["split_by_group"].values()) == {"train"}
    engineering = prepare_p1_data(
        owner, store, projection_ids=ids, allocation_policy="observed_groups_engineering"
    )
    again = prepare_p1_data(
        owner, store, projection_ids=ids, allocation_policy="observed_groups_engineering"
    )
    assert engineering["ready_to_freeze"]
    assert engineering["selection"] == again["selection"]
    assert engineering["index"]["unknown_independence_groups"] == 3
    assert engineering["research_admission"] == "not_evaluated"
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone()[0] == 0
    frozen = freeze_p1_data(owner, store, PRODUCER, **engineering["selection"], reserve=True)
    assert frozen["unknown_independence_groups"] == 3


def test_cache_dependency_change_corruption_and_payload_mutation_reverify(tmp_path, monkeypatch):
    import spireagent.workbench.p1_data_preparation as module

    owner, store, projections = workspace(tmp_path, names=("a",))
    prepare_p1_data(owner, store)
    monkeypatch.setattr(module, "preparation_identity", lambda: "f" * 64)
    changed = prepare_p1_data(owner, store)
    assert changed["cache"]["decoded_projections"] == 1
    cache = P1PreparationCache(owner.path)
    import sqlite3

    with sqlite3.connect(cache.path) as db:
        db.execute("UPDATE projection_cache SET body=?", (b"corrupt",))
    corrupt = prepare_p1_data(owner, store)
    assert corrupt["cache"]["corrupt"] == 1
    assert corrupt["cache"]["decoded_projections"] == 1
    payload = projections[0].payload("segments")
    index = json.loads(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    target = store.blobs._path("objects/sha256/" + index["chunks"][0]["sha256"])
    target.write_bytes(b"corrupt")
    broken = prepare_p1_data(owner, store)
    assert not broken["ready_to_freeze"]
    assert broken["selection"] is None
    assert broken["conflicts"]
    assert broken["cache"]["hits"] == 0


def test_actual_dependency_digest_changes(tmp_path, monkeypatch):
    import spireagent.workbench.p1_data_preparation as module

    root = tmp_path / "root"
    fake = root / "package" / "workbench" / "module.py"
    fake.parent.mkdir(parents=True)
    source = root / "reader.py"
    source.write_text("first")
    monkeypatch.setattr(module, "__file__", str(fake))
    monkeypatch.setattr(module, "DEPENDENCIES", ("reader.py",))
    before = preparation_identity()
    source.write_text("fixed verifier")
    assert preparation_identity() != before


def test_conflict_is_explicit_and_exact_projection_selection_resolves(tmp_path):
    owner, store, projections = workspace(tmp_path, names=("a",))
    original = projections[0]
    _, source, value = projection_fixture(store, "a")
    value = copy.deepcopy(value)
    value["segments"][0]["points"][0]["chosen"] = "c7"
    projection_fixture(store, "a", value=value, original=source)
    report = prepare_p1_data(owner, store)
    assert report["selection"] is None
    assert report["conflicts"][0]["code"] == "overlapping_projection_N_conflict_select_one"
    resolved = prepare_p1_data(owner, store, projection_ids=[original.artifact_id])
    assert resolved["index"]["unique_N"] == 1
    assert not resolved["conflicts"]


def test_quality_exclusions_are_filtered_without_discarding_other_sources(tmp_path):
    from spireagent.p1_curation import _occurrence, index_p1_projection

    owner, store, projections = workspace(tmp_path)
    index_p1_projection(owner.ledger, store, projections[0].artifact_id)
    owner.ledger.annotate(_occurrence("decision-a"), "fixture", "exclude", "invalid supervision")
    prepared = prepare_p1_data(owner, store)
    assert prepared["index"]["unique_N"] == 2
    assert prepared["allocation"]["quality_filtered_N"] == [
        {"source_decision_id": "decision-a", "reason": "invalid supervision"}
    ]
    assert (
        prepared["classification"][projections[0].artifact_id]["masks"][
            "next_missing_or_not_applicable"
        ]
        == 1
    )
    assert {p["source_decision_id"] for p in prepared["index"]["points"]} == {
        "decision-b",
        "decision-c",
    }


def test_gold_and_prior_conflicting_exposure_are_reported(tmp_path):
    owner, store, projections = workspace(tmp_path)
    snapshot = explicit_snapshot(store, projections)
    reserve_p1_snapshot(owner, store, snapshot.artifact_id)
    _, value = __import__("stpd.p1.dataset", fromlist=["load_snapshot"]).load_snapshot(
        store, snapshot.artifact_id
    )
    heldout = next(p for p in value["points"] if p["split"] == "dev")
    # Exposure history is permanent and may predate the current P1 reservation.
    owner.ledger.use({heldout["root_run_id"]}, "training", "old-external-attempt")
    result = prepare_p1_data(owner, store)
    assert not result["ready_to_freeze"]
    assert "curation_allocation_conflicts" in result["allocation"]["blockers"]
    assert any(
        "related_run_split_purpose_overlap" in x["codes"] for x in result["allocation"]["conflicts"]
    )


def test_snapshot_reader_reuses_validation_but_rechecks_points(tmp_path, monkeypatch):
    import stpd.p1.dataset as module

    _, store, projections = workspace(tmp_path)
    snapshot = explicit_snapshot(store, projections)
    original = module.load_snapshot
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "load_snapshot", counted)
    reader = module.SnapshotReader(store, snapshot.artifact_id)
    selection = reader.selection
    selection["points"].clear()
    first_read = list(reader.iter_points())
    assert len(first_read) == 3
    first_read[0][0]["split"] = "external_mutation"
    assert len(list(reader.iter_points())) == 3
    assert reader.selection["unique_N"] == 3
    assert len(calls) == 1


def test_cold_peer_recovery_has_same_reader_and_cache_semantics(tmp_path):
    from spireagent.storage.cache import configure_cache
    from spireagent.storage.store import copy_artifact

    owner, store, projections = workspace(tmp_path)
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    for projection in projections:
        copy_artifact(store, peer, projection.artifact_id)
    configure_cache(store, str(peer.blobs.root), max_bytes=1024)
    first = prepare_p1_data(owner, store)
    assert first["ready_to_freeze"]
    for prefix in ("objects/", "payload-indexes/"):
        for key in store.blobs.keys(prefix):
            (store.blobs.root / key).unlink()
    cached = prepare_p1_data(owner, store)
    uncached = prepare_p1_data(owner, store, cache=False)
    assert not cached["conflicts"]
    assert cached["cache"]["hits"] == 3
    assert cached["cache"]["decoded_projections"] == 0
    assert cached["cache"]["verified_payload_bytes"] > 0
    assert cached["index"] == uncached["index"] == first["index"]
    assert cached["classification"] == uncached["classification"]
    readonly = ManifestArtifactStore(LocalBlobStore(store.blobs.root, create=False, readonly=True))
    projection = projections[0]
    lease = store.blobs.root / ".cache-leases" / (projection.payload("segments").sha256 + ".lock")
    lease.unlink()
    for cache in (True, False):
        failed = prepare_p1_data(owner, readonly, cache=cache)
        assert failed["selection"] is None
        assert not failed["ready_to_freeze"]
        assert "cache_read_lease_missing" in {x["code"] for x in failed["conflicts"]}


def test_missing_peer_payload_remains_an_error_even_with_cached_index(tmp_path):
    from spireagent.storage.cache import configure_cache
    from spireagent.storage.store import copy_artifact

    owner, store, projections = workspace(tmp_path, names=("a",))
    peer = ManifestArtifactStore(LocalBlobStore(tmp_path / "peer"))
    copy_artifact(store, peer, projections[0].artifact_id)
    configure_cache(store, str(peer.blobs.root), max_bytes=1024)
    prepare_p1_data(owner, store)
    for candidate in (store, peer):
        for key in candidate.blobs.keys("objects/"):
            (candidate.blobs.root / key).unlink()
    for cache in (True, False):
        failed = prepare_p1_data(owner, store, cache=cache)
        assert failed["selection"] is None
        assert "object_not_found" in {x["code"] for x in failed["conflicts"]}


def test_gold_reservation_cannot_become_an_engineering_draft(tmp_path):
    owner, store, _ = workspace(tmp_path, names=())
    owner.ledger.claim("reserved-gold", "gold", {"a"})
    projection_fixture(store, "a")
    prepared = prepare_p1_data(owner, store, allocation_policy="observed_groups_engineering")
    assert not prepared["ready_to_freeze"]
    assert prepared["allocation"]["conflicts"][0]["codes"] == ["gold_reserved_data"]


def test_missing_original_manifest_never_reuses_success(tmp_path):
    import pytest

    from spireagent.json_boundary import BoundaryError

    owner, store, projections = workspace(tmp_path, names=("a",))
    prepare_p1_data(owner, store)
    source = projections[0].parent("source")
    (store.blobs.root / "manifests" / (source + ".json")).unlink()
    for cache in (True, False):
        with pytest.raises(BoundaryError, match="object_not_found"):
            prepare_p1_data(owner, store, cache=cache)


def test_wrong_json_parameter_types_fail_before_publication_or_reservation(tmp_path):
    import pytest

    from spireagent.json_boundary import BoundaryError

    owner, store, projections = workspace(tmp_path)
    original_ids = store.manifest_ids()
    valid = {"projection_ids": [p.artifact_id for p in projections], "split_salt": "fixture"}
    for reserve in ("false", 0, 1, None, []):
        with pytest.raises(BoundaryError, match="invalid_reserve_boolean"):
            freeze_p1_data(owner, store, PRODUCER, **valid, reserve=reserve)
    for field, value in (
        ("cache", "false"),
        ("cache", 0),
        ("cache", None),
        ("declared_origins", "declared_human"),
        ("declared_origins", [None]),
        ("projection_ids", {"id": projections[0].artifact_id}),
        ("projection_names", [True]),
        ("tool_release_ids", "tool"),
        ("selected_decisions", {projections[0].artifact_id: "decision-a"}),
        ("revision", []),
        ("allocation_policy", []),
        ("split_salt", None),
    ):
        with pytest.raises(BoundaryError):
            prepare_p1_data(owner, store, **{field: value})
    assert store.manifest_ids() == original_ids
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM curation_uses").fetchone()[0] == 0
    assert not owner.path.with_name(owner.path.stem + ".p1-preparation-cache.sqlite").exists()
