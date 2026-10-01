"""Synthetic durable declaration tests; never use the real recording store."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest
from test_local_recording_preview import _fixture

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench import managed_local_workspace as managed
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_curation import (
    LocalCurationOwner,
)
from stpd.fullrun.curated_dataset import curate, publish_selection
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_store import preview, publish

PRODUCER = Producer("local/synthetic-user-declaration", "a" * 40, "b" * 64)
WRITTEN_AT = "2026-10-01T01:02:03.004Z"


def _prepared(tmp_path: Path, monkeypatch, *, restrictive: bool = False):
    _, source_id, source_store = _fixture(tmp_path / "source", canonical=True)
    state = tmp_path / "state"
    state.mkdir()
    selected = managed.create_managed_workspace(state)
    directory = state / managed.ROOT_NAME / selected["workspace_id"]
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    copy_artifact(source_store, store, source_id)
    owner: LocalCurationOwner = selected["curation_owner"]
    source = store.get_manifest(source_id)
    projections = []
    rules = SelectionRules(no_failures_only=restrictive)
    base = preview(store, (source,), rules, on_projection=projections.append)
    assert len(projections) == 1
    owner.ledger.index_source(source_id, projections[0])
    owner.index_human_runs(store, source_id)
    canonical = publish(store, (source,), rules, PRODUCER, base.logical_id)
    monkeypatch.setattr("spireagent.workbench.local_dataset.source_identity", lambda _: PRODUCER)
    service = __import__(
        "spireagent.workbench.local_dataset", fromlist=["LocalDatasetService"],
    ).LocalDatasetService(ProjectConfig(state, "", "", None, combination()))
    return state, directory, owner, store, source_id, canonical, service, rules


def _statement(text: str = "Synthetic statement") -> dict:
    return {
        "kind": "direct_user_instruction", "reference": "synthetic-message-1",
        "written_at": WRITTEN_AT, "text_basis": "normalized",
    }


def _register(owner: LocalCurationOwner, store: ManifestArtifactStore, dataset_id: str,
              *, request_id: str | None = None, statement: str = "Synthetic statement") -> dict:
    return owner.register_user_declaration(
        store, (dataset_id,), PRODUCER,
        request_id=request_id or uuid.uuid4().hex, statement=statement,
        statement_source=_statement(statement), self_recorded=True,
        project_training_authorized_now=True,
    )


def _make_curated(owner: LocalCurationOwner, store: ManifestArtifactStore,
                  canonical: Manifest, rules: SelectionRules) -> tuple[str, str]:
    source = store.get_manifest(canonical.parent("source_" + canonical.parents[0].artifact_id))
    base = preview(store, (source,), rules)
    annotations = owner.ledger.annotations(
        base.records, runs=base.run_ids,
    )
    selected = curate(base, "training", annotations)
    operation = uuid.uuid4().hex
    owner.ledger.claim(operation, "training", selected.run_ids)
    manifest = publish_selection(
        store, (source,), rules, PRODUCER, selected,
        merging=False, expected=selected.logical_id, paired_training=None,
    )
    owner.ledger.bind(operation, manifest.artifact_id)
    owner.reserve_training_datasets(store, (manifest.artifact_id,), operation)
    return manifest.artifact_id, operation


def test_register_is_immutable_idempotent_and_keeps_times_separate(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    request = uuid.uuid4().hex
    first = _register(owner, store, canonical.artifact_id, request_id=request)
    second = _register(owner, store, canonical.artifact_id, request_id=request)
    manifest = store.get_manifest(first["declaration_id"])
    info = manifest.parameters.value()
    assert second == first
    assert first["recorded_at"] != WRITTEN_AT
    assert info["statement"]["text"] == "Synthetic statement"
    assert info["statement"]["source"]["written_at"] == WRITTEN_AT
    assert info["historical_authorization"] == "unknown"
    assert info["historical_manual_exposure"] == "unknown"
    assert not manifest.payloads
    assert owner.read_user_declaration(store, canonical.artifact_id)["status"] == "registered"
    with pytest.raises(BoundaryError, match="declaration_request_conflict"):
        _register(owner, store, canonical.artifact_id, request_id=request,
                  statement="Changed statement")
    source = store.get_manifest(canonical.parents[0].artifact_id)
    alternate_rules = SelectionRules(seed=1)
    alternate_base = preview(store, (source,), alternate_rules)
    alternate = publish(store, (source,), alternate_rules, PRODUCER,
                        alternate_base.logical_id)
    with pytest.raises(BoundaryError, match="declaration_request_conflict"):
        owner.register_user_declaration(
            store, (canonical.artifact_id, alternate.artifact_id), PRODUCER,
            request_id=request, statement="Synthetic statement",
            statement_source=_statement(), self_recorded=True,
            project_training_authorized_now=True,
        )


def test_read_missing_optional_table_is_logically_read_only_and_unbound_copy_is_not_registered(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    before_ledger = owner.path.read_bytes()
    before_manifests = store.manifest_ids()
    reads = 0
    read_payload = store.read_payload

    def count_payload(payload):
        nonlocal reads
        reads += 1
        yield from read_payload(payload)

    store.read_payload = count_payload  # type: ignore[method-assign]
    missing = owner.read_user_declaration(store, canonical.artifact_id)
    assert missing == {"status": "not_durably_registered",
                       "dataset_id": canonical.artifact_id}
    assert owner.path.read_bytes() == before_ledger
    assert store.manifest_ids() == before_manifests
    assert reads == 0
    connection = sqlite3.connect(
        owner.path.resolve().as_uri() + "?mode=ro", uri=True,
    )
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='local_user_declarations'",
        ).fetchone() is None
    finally:
        connection.close()
    unbound = Manifest("analysis", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/local-user-declaration-v1",
    }))
    store.publish(unbound)
    assert owner.read_user_declaration(store, canonical.artifact_id)["status"] == \
        "not_durably_registered"


def test_read_closes_sqlite_connections_on_success_and_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    _register(owner, store, canonical.artifact_id)
    import spireagent.workbench.local_curation as curation

    connect = sqlite3.connect
    opened = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(curation.sqlite3, "connect", tracked_connect)
    result = owner.read_user_declaration(store, canonical.artifact_id)
    assert result["status"] == "registered"
    assert len(opened) == 2  # registration lookup and current source-scope recheck
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")

    opened.clear()

    def fail_validation(_db):
        raise BoundaryError("local_curation", "synthetic_read_failure")

    monkeypatch.setattr(owner, "_validate_existing", fail_validation)
    failure = owner.read_user_declaration(store, canonical.artifact_id)
    assert failure == {"status": "unavailable", "dataset_id": canonical.artifact_id,
                       "reason": "synthetic_read_failure"}
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_read_sees_latest_committed_declaration_while_wal_is_nonempty(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    keeper = sqlite3.connect(owner.path)
    try:
        assert keeper.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        result = _register(owner, store, canonical.artifact_id)
        wal_path = owner.path.with_name(owner.path.name + "-wal")
        assert wal_path.is_file() and wal_path.stat().st_size > 0
        current = owner.read_user_declaration(store, canonical.artifact_id)
        assert current["status"] == "registered"
        assert current["declaration_id"] == result["declaration_id"]
        assert wal_path.stat().st_size > 0
    finally:
        keeper.close()


def test_unregistered_declaration_copy_cannot_satisfy_require_without_payload_reads(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    registered = _register(owner, store, canonical.artifact_id)
    original = store.get_manifest(registered["declaration_id"])
    copied = Manifest(
        "analysis", Producer("local/unregistered-copy", "c" * 40, "d" * 64),
        original.parents, (), original.parameters,
    )
    store.publish(copied)
    reads: list[str] = []

    def fail_payload(_payload):
        reads.append("read")
        raise AssertionError("unregistered declaration must fail before payload access")
        yield b""

    store.read_payload = fail_payload  # type: ignore[method-assign]
    with pytest.raises(BoundaryError, match="declaration_not_latest_registration"):
        owner.require_user_training_declaration(
            store, (canonical.artifact_id,), copied.artifact_id,
            training_operation_id=uuid.uuid4().hex,
        )
    assert reads == []


def test_service_resolves_the_configured_owner_and_current_producer(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, service, _ = _prepared(tmp_path, monkeypatch)
    request = uuid.uuid4().hex
    result = service.register_user_declaration(
        [canonical.artifact_id], request_id=request, statement="Synthetic statement",
        statement_source=_statement(), self_recorded=True,
        project_training_authorized_now=True,
    )
    assert result["status"] == "registered"
    assert owner.read_user_declaration(store, canonical.artifact_id)["declaration_id"] == \
        result["declaration_id"]


@pytest.mark.parametrize("damage", ["missing_index", "changed_archive", "held_out_group"])
def test_registration_rejects_missing_or_heldout_source_authority_before_publish(
    tmp_path: Path, monkeypatch, damage: str,
) -> None:
    _, _, owner, store, source_id, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    runs: list[str] = []
    with owner.transaction() as db:
        if damage == "missing_index":
            db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (source_id,))
        elif damage == "changed_archive":
            db.execute("UPDATE curation_sources SET archive=? WHERE id=?",
                       ("0" * 64, source_id))
        else:
            runs = [row[0] for row in db.execute(
                "SELECT run FROM curation_source_runs WHERE source=?", (source_id,),
            )]
    if damage == "held_out_group":
        owner.ledger.claim(uuid.uuid4().hex, "gold", set(runs), require_inventory=True)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError):
        _register(owner, store, canonical.artifact_id)
    assert store.manifest_ids() == before
    assert owner.read_user_declaration(store, canonical.artifact_id)["status"] == \
        "not_durably_registered"


def test_wrong_store_and_unsupported_dataset_family_reject_without_registration(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, source_id, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    other_store = ManifestArtifactStore(LocalBlobStore(tmp_path / "other-store"))
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        _register(owner, other_store, canonical.artifact_id)
    unsupported = store.publish(Manifest(
        "dataset", PRODUCER, (Parent("source_" + source_id, source_id),), (),
        FrozenObject.of({"schema": "stpd/decision-union-v1"}),
    ))
    with pytest.raises(BoundaryError, match="declaration_dataset_unsupported"):
        _register(owner, store, unsupported)


def test_require_accepts_exact_typed_curated_descendant_without_new_use_rows(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, rules = _prepared(tmp_path, monkeypatch)
    declaration = _register(owner, store, canonical.artifact_id)
    curated_id, operation = _make_curated(owner, store, canonical, rules)
    with owner.transaction() as db:
        before = (db.execute("SELECT count(*) FROM curation_uses").fetchone()[0],
                  db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0])
    result = owner.require_user_training_declaration(
        store, (curated_id,), declaration["declaration_id"],
        training_operation_id=operation,
    )
    with owner.transaction() as db:
        after = (db.execute("SELECT count(*) FROM curation_uses").fetchone()[0],
                 db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0])
    assert result["declaration_id"] == declaration["declaration_id"]
    assert result["datasets"][0]["artifact_id"] == curated_id
    assert before == after


def _fake_curated(store: ManifestArtifactStore, source_ids: tuple[str, ...], *,
                  merging: bool = False, rules: SelectionRules | None = None) -> str:
    prefix = "dataset_" if merging else "source_"
    parents = tuple(Parent(prefix + identity, identity) for identity in sorted(source_ids))
    info = {
        "schema": "stpd/curated-decision-dataset-v1", "logical_id": "c" * 64,
        "purpose": "training", "rules": (rules or SelectionRules()).to_dict(),
        "merging": merging, "paired_training": None, "records": 1, "runs": 1,
        "scope": "platform_verified", "split_status": "assigned",
        "materialization": "on_demand", "sealed_test": False, "reservation": None,
        "isolation": "ordinary", "historical_external_exposure": "unknown",
    }
    payload = store.put_bytes("selection", b"synthetic invalid until read", "application/json")
    return store.publish(Manifest("dataset", PRODUCER, parents, (payload,), FrozenObject.of(info)))


@pytest.mark.parametrize("foreign_count", [1, 2])
def test_foreign_or_coinput_source_is_rejected_before_any_payload_read(
    tmp_path: Path, monkeypatch, foreign_count: int,
) -> None:
    _, _, owner, store, source_id, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    declaration = _register(owner, store, canonical.artifact_id)
    source = store.get_manifest(source_id)
    foreign = Manifest("evidence", Producer("local/synthetic-alias", "c" * 40, "d" * 64),
                       source.parents, source.payloads, source.parameters)
    store.publish(foreign)
    ids = ((foreign.artifact_id,) if foreign_count == 1 else
           (source_id, foreign.artifact_id))
    candidate = _fake_curated(store, ids)
    payload_reads: list[str] = []

    def fail_payload(_payload):
        payload_reads.append("read")
        raise AssertionError("coverage preproof must precede payload reads")
        yield b""

    store.read_payload = fail_payload  # type: ignore[method-assign]
    with pytest.raises(BoundaryError, match="declaration_coverage_unproven"):
        owner.require_user_training_declaration(
            store, (candidate,), declaration["declaration_id"],
            training_operation_id=uuid.uuid4().hex,
        )
    assert payload_reads == []


def test_restrictive_declared_recipe_rejects_reduced_source_inventory_before_payload(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, source_id, canonical, _, _ = _prepared(
        tmp_path, monkeypatch, restrictive=True,
    )
    second_source_store = _fixture(tmp_path / "second-source", canonical=True)[2]
    # Select the source manifest by kind; the fixture store also contains local bundles.
    second_source_original = next(
        identity for identity in second_source_store.manifest_ids()
        if second_source_store.get_manifest(identity).kind == "evidence"
    )
    copy_artifact(second_source_store, store, second_source_original)
    source_manifest = store.get_manifest(second_source_original)
    second_source = Manifest(
        "evidence", Producer("local/synthetic-second-source", "e" * 40, "f" * 64),
        source_manifest.parents, source_manifest.payloads, source_manifest.parameters,
    )
    store.publish(second_source)
    projections = []
    rules = SelectionRules(no_failures_only=True)
    preview(store, (second_source,), rules, on_projection=projections.append)
    owner.ledger.index_source(second_source.artifact_id, projections[0])
    owner.index_human_runs(store, second_source.artifact_id)
    all_sources = (store.get_manifest(source_id), second_source)
    combined = preview(store, all_sources, rules)
    declared = publish(store, all_sources, rules, PRODUCER, combined.logical_id)
    declaration = _register(owner, store, declared.artifact_id)
    candidate = _fake_curated(store, (source_id,), rules=rules)
    payload_reads = []

    def fail_payload(_payload):
        payload_reads.append("read")
        raise AssertionError("restrictive reduced-inventory proof must be metadata-only")
        yield b""

    store.read_payload = fail_payload  # type: ignore[method-assign]
    with pytest.raises(BoundaryError, match="declaration_coverage_unproven"):
        owner.require_user_training_declaration(
            store, (candidate,), declaration["declaration_id"],
            training_operation_id=uuid.uuid4().hex,
        )
    assert payload_reads == []


def test_require_rejects_heldout_ancestor_from_manifests_before_payload(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    declaration = _register(owner, store, canonical.artifact_id)
    held_out = store.publish(Manifest(
        "dataset", PRODUCER, (), (),
        FrozenObject.of({"schema": "synthetic/held-out-v1", "purpose": "gold"}),
    ))
    payload_reads = []

    def fail_payload(_payload):
        payload_reads.append("read")
        raise AssertionError("held-out ancestry rejection must be manifest-only")
        yield b""

    store.read_payload = fail_payload  # type: ignore[method-assign]
    with pytest.raises(BoundaryError, match="held_out_data_cannot_train"):
        owner.require_user_training_declaration(
            store, (held_out,), declaration["declaration_id"],
            training_operation_id=uuid.uuid4().hex,
        )
    assert payload_reads == []


def test_append_failure_orphan_stays_unregistered_and_retry_commits_new_artifact(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    with sqlite3.connect(owner.path) as db:
        db.execute(
            "CREATE TABLE local_user_declarations (sequence INTEGER PRIMARY KEY AUTOINCREMENT,"
            "request_id TEXT NOT NULL,dataset TEXT NOT NULL,artifact TEXT NOT NULL,"
            "recorded_at TEXT NOT NULL,UNIQUE(request_id,dataset))"
        )
        db.execute(
            "CREATE TRIGGER fail_declaration_append BEFORE INSERT ON local_user_declarations "
            "BEGIN SELECT RAISE(ABORT,'synthetic append failure'); END"
        )
    request = uuid.uuid4().hex
    published = []
    publish = store.publish
    read_payload = store.read_payload

    def capture(manifest):
        identity = publish(manifest)
        published.append(identity)
        return identity

    store.publish = capture  # type: ignore[method-assign]
    with pytest.raises(BoundaryError):
        _register(owner, store, canonical.artifact_id, request_id=request)
    assert len(published) == 1
    orphan = published[0]
    assert owner.read_user_declaration(store, canonical.artifact_id)["status"] == \
        "not_durably_registered"
    payload_reads = []

    def fail_payload(_payload):
        payload_reads.append("read")
        raise AssertionError("an unpublished request cannot authorize payload access")
        yield b""

    store.read_payload = fail_payload  # type: ignore[method-assign]
    with pytest.raises(BoundaryError, match="declaration_not_latest_registration"):
        owner.require_user_training_declaration(
            store, (canonical.artifact_id,), orphan,
            training_operation_id=uuid.uuid4().hex,
        )
    assert payload_reads == []
    with sqlite3.connect(owner.path) as db:
        db.execute("DROP TRIGGER fail_declaration_append")
    store.read_payload = read_payload  # type: ignore[method-assign]
    committed = _register(owner, store, canonical.artifact_id, request_id=request)
    assert committed["declaration_id"] != orphan
    assert owner.read_user_declaration(store, canonical.artifact_id)["declaration_id"] == \
        committed["declaration_id"]
    retried = _register(owner, store, canonical.artifact_id, request_id=request)
    assert retried == committed


def test_corrupt_latest_registration_does_not_fall_back_to_older_declaration(
    tmp_path: Path, monkeypatch,
) -> None:
    _, _, owner, store, _, canonical, _, _ = _prepared(tmp_path, monkeypatch)
    _register(owner, store, canonical.artifact_id)
    latest = _register(owner, store, canonical.artifact_id)
    with sqlite3.connect(owner.path) as db:
        db.execute(
            "UPDATE local_user_declarations SET artifact=? WHERE dataset=? "
            "AND sequence=(SELECT max(sequence) FROM local_user_declarations WHERE dataset=?)",
            ("0" * 64, canonical.artifact_id, canonical.artifact_id),
        )
    result = owner.read_user_declaration(store, canonical.artifact_id)
    assert result["status"] == "unavailable"
    assert result.get("declaration_id") != latest["declaration_id"]
