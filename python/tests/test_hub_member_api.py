"""Actual membership/campaign/export services behind the transport-neutral member router."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write
from sts2_platform_evidence import DirectoryTransferManifest, verify_human_session_bundle
from test_campaign_onboarding import campaign as campaign
from test_hub_console import service
from test_hub_console import signed as signed

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.hub import curation_access
from spireagent.hub.campaigns import create_campaign_tables
from spireagent.hub.collections import CollectionAccess
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.hub.curation import CurationLedger
from spireagent.hub.dataset_curation import DatasetCuration
from spireagent.hub.exports import REQUEST_SCHEMA
from spireagent.hub.identity import IdentityService
from spireagent.hub.member_api import MemberApi, grant_collection_sharing
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.collection_activity import CONSENT_FIELDS
from stpd.fullrun.decision_dataset import SCHEMA as DECISION_DATASET_SCHEMA
from stpd.fullrun.decision_union import UNION_SCHEMA
from stpd.fullrun.platform_bundle3 import archive_bundle


@pytest.fixture
def api(campaign, signed, tmp_path):
    access, token, _ = signed
    owner = service(tmp_path / "hub")
    with owner.operations.transaction() as db:
        create_campaign_tables(db)
    identity = IdentityService(
        owner.operations, access, b"synthetic-only-key", "https://hub.example"
    )
    admin = identity.principal(access.authenticate(token()))
    member = identity.principal(access.authenticate(token(email="collector@example.org")))
    with owner.operations.transaction() as db:
        db.execute("UPDATE devices SET owner_subject=? WHERE id='one'", (member.subject,))
        db.execute("UPDATE devices SET owner_subject=? WHERE id='two'", (admin.subject,))
    router = MemberApi(owner, identity)
    template = router.admin_create_campaign(campaign[3], admin)
    return router, owner, admin, member, template


def enroll(api):
    router, _, _, member, template = api
    return router.write(
        "campaigns/" + template["template_id"] + "/enroll",
        {"device_id": "one", "consent": {key: True for key in CONSENT_FIELDS}},
        member,
    )


def staged(api, tmp_path):
    router, owner, _, _, _ = api
    enrollment = enroll(api)
    directory = bundle3(tmp_path / "bundle")
    path = directory / "session-bundle-manifest.json"
    manifest = load(path)
    manifest.update(worker_id="one", campaign_id=enrollment["campaign_id"])
    manifest["human_origin_attestation"]["worker_id"] = "one"
    write(path, manifest)
    seal(directory)
    bundle = verify_human_session_bundle(directory).require_value()
    transfer = DirectoryTransferManifest.from_directory(
        directory, content_id=bundle.bundle_content_id, artifact_type="human-session-bundle"
    )
    archive = archive_bundle(directory)
    intent = {
        "schema": "stpd/upload-intent-v1",
        "transfer_manifest": transfer.to_dict(),
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "archive_bytes": len(archive),
    }
    upload_id = owner.intent("one", intent)["upload_id"]
    owner.staging.write(upload_id, io.BytesIO(archive), len(archive))
    owner.operations.request_verification(upload_id)
    return upload_id, bundle, enrollment


def indexed_overlap_pair(
    api, *, shared_source: bool = False, candidate_schema: str | None = None
):
    """Synthetic curation identities and currently shared source receipts."""
    _, owner, _, _, _ = api
    def verified_source(content_id: str) -> Manifest:
        archive_bytes = b"synthetic-owner-source-archive-" + content_id.encode()
        archive = owner.store.put_payload("archive", io.BytesIO(archive_bytes))
        source = Manifest(
            "evidence", owner.producer, payloads=(archive,),
            parameters=FrozenObject.of({
                "schema": "stpd/received-bundle-v1",
                "content_id": content_id,
                "disposition": "verified",
            }),
        )
        owner.store.publish(source)
        upload = owner.operations.create_upload(
            "one", content_id, source.artifact_id,
            {"archive_sha256": archive.sha256, "archive_bytes": archive.size},
        )
        with owner.operations.transaction() as db:
            db.execute(
                "UPDATE uploads SET status='verified',receipt=? WHERE id=?",
                (json.dumps({
                    "evidence_id": source.artifact_id,
                    "content_id": content_id,
                    "status": "verified",
                }), upload["id"]),
            )
        return source

    source_a = verified_source("1" * 64)
    source_b = source_a if shared_source else verified_source("2" * 64)

    def legacy_dataset(source: Manifest, schema: str) -> Manifest:
        records = owner.store.put_payload("records", io.BytesIO(b"synthetic-records"))
        selection = owner.store.put_payload("selection", io.BytesIO(b"synthetic-selection"))
        return Manifest(
            "dataset", owner.producer,
            parents=(Parent("source_" + source.artifact_id, source.artifact_id),),
            payloads=(records, selection),
            parameters=FrozenObject.of({
                "schema": schema,
                "logical_id": hashlib.sha256((schema + source.artifact_id).encode()).hexdigest(),
                "rules": {
                    "schema": "stpd/decision-selection-v1",
                    "complete_only": False,
                    "wins_only": False,
                    "no_failures_only": False,
                    "filters": {},
                    "seed": 0,
                },
                "records": 1,
                "scope": "platform_verified",
                "split_status": "assigned",
            }),
        )

    if candidate_schema is None:
        candidate = Manifest(
            "dataset", owner.producer,
            parents=(Parent("source_" + source_a.artifact_id, source_a.artifact_id),),
            parameters=FrozenObject.of({
                "schema": "stpd/curated-decision-dataset-v1",
                "purpose": "training", "sealed_test": False,
            }),
        )
    else:
        candidate = legacy_dataset(source_a, DECISION_DATASET_SCHEMA)
        if candidate_schema == "legacy_union":
            owner.store.publish(candidate)
            union_records = owner.store.put_payload(
                "records", io.BytesIO(b"synthetic-union-records")
            )
            union_selection = owner.store.put_payload(
                "selection", io.BytesIO(b"synthetic-union-selection")
            )
            candidate = Manifest(
                "dataset", owner.producer,
                parents=(Parent("dataset_" + candidate.artifact_id, candidate.artifact_id),),
                payloads=(union_records, union_selection),
                parameters=FrozenObject.of({
                    "schema": UNION_SCHEMA,
                    "logical_id": hashlib.sha256(b"synthetic-union").hexdigest(),
                    "rules": {
                        "schema": "stpd/decision-selection-v1",
                        "complete_only": False,
                        "wins_only": False,
                        "no_failures_only": False,
                        "filters": {},
                        "seed": 0,
                    },
                    "records": 1,
                    "scope": "platform_verified",
                    "split_status": "assigned",
                }),
            )
        elif candidate_schema != "legacy":
            raise ValueError("unknown synthetic candidate schema")
    payload = owner.store.put_payload(
        "selection", io.BytesIO(b"synthetic-only-gold-selection-payload")
    )
    gold = Manifest(
        "dataset", owner.producer,
        parents=(Parent("source_" + source_b.artifact_id, source_b.artifact_id),),
        payloads=(payload,),
        parameters=FrozenObject.of({
            "schema": "stpd/curated-decision-dataset-v1",
            "purpose": "gold", "sealed_test": True,
        }),
    )
    for manifest in (candidate, gold):
        owner.store.publish(manifest)
        owner.console_index.artifact(manifest)

    candidate_run, gold_run = "synthetic-training-run", "synthetic-gold-run"
    ledger = CurationLedger(owner.operations)
    for source_id, run_id in (
        (source_a.artifact_id, candidate_run), (source_b.artifact_id, gold_run)
    ):
        with owner.operations.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO curation_sources VALUES(?,?,1)",
                (source_id, hashlib.sha256(source_id.encode()).hexdigest()),
            )
            db.execute(
                "INSERT OR IGNORE INTO curation_source_runs VALUES(?,?)", (source_id, run_id)
            )
            db.execute(
                "INSERT OR IGNORE INTO curation_exact_source_index VALUES(?)", (source_id,)
            )
            db.execute(
                "INSERT OR IGNORE INTO curation_fingerprints VALUES(?,?)",
                ("fingerprint-" + run_id, run_id),
            )
    if candidate_schema is None:
        ledger.claim("synthetic-training-claim", "training", (candidate_run,))
        ledger.bind("synthetic-training-claim", candidate.artifact_id)
    ledger.claim("synthetic-gold-claim", "gold", (gold_run,), require_inventory=True)
    ledger.bind("synthetic-gold-claim", gold.artifact_id)
    return candidate, gold


def test_member_routes_owned_enrollment_and_exact_detail(api):
    router, _, admin, member, template = api
    listing = router.read("campaigns", "limit=1", member)
    assert listing["total"] == 1 and listing["templates"] == [template]
    enrollment = enroll(api)
    assert enroll(api) == enrollment
    own = router.read("campaigns/enrollments", "", member)
    assert own["items"] == [enrollment]
    exact = router.read("campaigns/enrollments/" + enrollment["enrollment_id"], "", member)
    assert exact == enrollment and exact["human_origin_verified"] is False
    assert router.read("campaigns/enrollments", "", admin)["items"] == []
    with pytest.raises(BoundaryError, match="enrollment_not_found"):
        router.read("campaigns/enrollments/" + enrollment["enrollment_id"], "", admin)
    with pytest.raises(BoundaryError, match="owned_active_device_required"):
        router.write(
            "campaigns/" + template["template_id"] + "/enroll",
            {"device_id": "two", "consent": {key: True for key in CONSENT_FIELDS}},
            member,
        )
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        router.write(
            "campaigns/" + template["template_id"] + "/enroll",
            {"device_id": "one", "subject": admin.subject, "consent": {}},
            member,
        )


def test_admin_creation_separate_and_personal_session_cannot_admin(api):
    router, _, admin, member, template = api
    value = {**template["template"], "version": 2}
    with pytest.raises(BoundaryError, match="admin_browser_required"):
        router.admin_create_campaign(value, member)
    with pytest.raises(BoundaryError, match="admin_browser_required"):
        router.admin_create_campaign(value, replace(admin, session_binding=""))
    assert router.admin_create_campaign(value, admin)["template"]["version"] == 2
    with pytest.raises(BoundaryError, match="resource_not_found"):
        router.write("campaigns", value, admin)


def test_daily_settings_require_current_admin_and_do_not_create_consent(api):
    router, owner, admin, member, _ = api
    assert router.read("collection-settings", "", member)["default"] is None
    body = {"name": "Daily", "description": "New recordings", "consent_text": "Explicit consent"}
    for principal in (member, replace(admin, session_binding="")):
        with pytest.raises(BoundaryError, match="admin_browser_required"):
            router.admin_collection_settings(body, principal)
    first = router.admin_collection_settings(body, admin)
    assert first["default"]["template"]["schema"] == "stpd/collection-activity-v2"
    assert router.admin_collection_settings(body, admin)["default"] == first["default"]
    assert router.read("campaigns/enrollments", "", member)["items"] == []
    selected = router.write(
        "campaigns/" + first["default"]["template_id"] + "/enroll",
        {
            "device_id": "one",
            "consent": {key: True for key in CONSENT_FIELDS},
        },
        member,
    )
    second = router.admin_collection_settings({**body, "description": "New wording"}, admin)
    assert second["default"]["template"]["version"] == 2
    assert router.read("campaigns/enrollments/" + selected["enrollment_id"], "", member) == selected
    router.admin_collection_settings({"template_id": first["default"]["template_id"]}, admin)
    assert router.read("collection-settings", "", member)["default"] == first["default"]
    with pytest.raises(BoundaryError):
        router.read("collection-settings", "extra=1", member)
    with pytest.raises(BoundaryError, match="resource_not_found"):
        router.write("collection-settings", body, admin)
    router.identity.membership.update(admin, member.member_id, {"status": "disabled"})
    with pytest.raises(BoundaryError, match="membership_not_authorized"):
        router.read("collection-settings", "", member)


def test_default_classification_is_immutable_when_recommendation_changes(api, tmp_path):
    router, owner, admin, member, _ = api
    daily = router.admin_collection_settings(
        {"name": "Daily", "description": "Recordings", "consent_text": "Explicit consent"}, admin
    )
    daily_api = (router, owner, admin, member, daily["default"])
    upload, _, _ = staged(daily_api, tmp_path)
    assert owner.verify_pending() == 1
    observed = owner.console_index.collections(member, limit=25, offset=0)["items"][0]
    assert observed["id"] == upload and observed["collection_context"]["kind"] == "default"
    router.admin_collection_settings(
        {"name": "Changed", "description": "Recordings", "consent_text": "Explicit consent"}, admin
    )
    later = owner.console_index.collections(member, limit=25, offset=0)["items"][0]
    assert later["collection_context"] == observed["collection_context"]
    assert later["receipt"] == observed["receipt"]


def test_payload_export_and_stale_membership_rechecked(api):
    router, owner, admin, member, _ = api
    payload = owner.store.put_payload("weights", io.BytesIO(b"immutable weights"))
    manifest = Manifest("model", owner.producer, payloads=(payload,))
    owner.store.publish(manifest)
    owner.console_index.artifact(manifest)
    metadata = owner.console_index.artifacts(member, "models", limit=1, offset=0)["items"][0]
    assert metadata["payloads"] == [payload.to_dict()]
    selected = router.write(
        "exports",
        {
            "schema": REQUEST_SCHEMA,
            "collections": [],
            "artifacts": [{"artifact_id": manifest.artifact_id, "roles": ["weights"]}],
        },
        member,
    )
    personal = replace(member, session_binding="")
    assert router.read("exports/" + selected["export_id"], "", personal) == selected
    file = next(item for item in selected["files"] if item["role"] == "weights")
    info, stream = router.payload(selected["export_id"], file["file_id"], personal)
    assert hashlib.sha256(b"".join(stream)).hexdigest() == info["sha256"]
    router.identity.membership.update(admin, member.member_id, {"status": "disabled"})
    for call in (
        lambda: router.read("campaigns", "", member),
        lambda: router.read("exports/" + selected["export_id"], "", member),
        lambda: router.payload(selected["export_id"], file["file_id"], personal),
    ):
        with pytest.raises(BoundaryError, match="membership_not_authorized"):
            call()
    with pytest.raises(BoundaryError):
        router.read("campaigns", "", ConsolePrincipal("collector", ("one",)))


def test_curation_overlap_reports_source_overlap_without_recording_use(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, shared_source=True)
    monkeypatch.setattr(
        owner.store,
        "read_payload",
        lambda *_args, **_kwargs: pytest.fail("overlap metadata must not read payloads"),
    )
    with owner.operations.transaction() as db:
        uses_before = db.execute("SELECT count(*) FROM curation_uses").fetchone()[0]
        source_uses_before = db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0]

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["schema"] == "stpd/curation-overlap-v1"
    assert result["status"] == "overlap"
    assert result["findings"]["source"] == {"status": "overlap", "count": 1}
    assert result["findings"]["run"] == {"status": "none", "count": 0}
    assert result["coverage"]["physical_game"] == "unknown"
    assert result["commitments"]["comparison_sha256"]
    serialized = json.dumps(result)
    assert candidate.artifact_id not in serialized and gold.artifact_id not in serialized
    assert b"synthetic-only-gold-selection-payload" not in serialized.encode()
    with owner.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_uses").fetchone()[0] == uses_before
        assert (
            db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0]
            == source_uses_before
        )


def test_curation_overlap_no_indexed_overlap_and_incomplete_inventory_is_unknown(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    query = f"candidate={candidate.artifact_id}&gold={gold.artifact_id}"
    result = router.read("curation-overlap", query, member)
    assert result["status"] == "no_indexed_overlap"
    assert result["findings"]["source"]["count"] == 0
    assert result["findings"]["run"]["status"] == "none"
    assert result["findings"]["run"]["count"] == 0

    pending = owner.operations.create_upload("one", "c" * 64, "d" * 64, {})
    with owner.operations.transaction() as db:
        db.execute(
            "UPDATE uploads SET status='verified',receipt=? WHERE id=?",
            (json.dumps({"evidence_id": "e" * 64}), pending["id"]),
        )
    incomplete = router.read("curation-overlap", query, member)
    assert incomplete["status"] == "unknown"
    assert incomplete["coverage"]["verified_source_inventory"] == "incomplete"
    assert incomplete["findings"]["source"]["status"] == "unknown"
    assert incomplete["findings"]["run"] == {"status": "unknown", "count": None}
    assert incomplete["findings"]["run_group"]["status"] == "unknown"
    assert incomplete["coverage"]["run_group"] == "incomplete"
    assert "verified_source_inventory_pending" in incomplete["reasons"]


def test_curation_overlap_missing_exact_source_index_keeps_run_unknown(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    candidate_source = candidate.parents[0].artifact_id
    with owner.operations.transaction() as db:
        db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (candidate_source,))

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["coverage"]["candidate_source"] == "incomplete"
    assert result["coverage"]["run_group"] == "incomplete"
    assert result["findings"]["source"] == {"status": "unknown", "count": None}
    assert result["findings"]["run"] == {"status": "unknown", "count": None}


def test_curation_overlap_run_group_coverage_tracks_missing_fingerprints(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    with owner.operations.transaction() as db:
        db.execute(
            "DELETE FROM curation_fingerprints WHERE run='synthetic-training-run'"
        )

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["coverage"]["run"] == "complete"
    assert result["coverage"]["run_group"] == "incomplete"
    assert result["findings"]["run"] == {"status": "none", "count": 0}
    assert result["findings"]["run_group"] == {
        "status": "unknown", "related_runs": None
    }


@pytest.mark.parametrize("candidate_schema", ["legacy", "legacy_union"])
def test_curation_overlap_legacy_source_superset_needs_no_claim_or_payload_reads(
    api, candidate_schema, monkeypatch
):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, candidate_schema=candidate_schema)
    with owner.operations.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_claims WHERE artifact=?", (candidate.artifact_id,)
        ).fetchone()[0] == 0
    monkeypatch.setattr(
        owner.store,
        "read_payload",
        lambda *_args, **_kwargs: pytest.fail("legacy overlap metadata must not read payloads"),
    )

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "no_indexed_overlap"
    assert result["candidate_scope"] == "source_superset"
    assert result["findings"]["run"] == {"status": "none", "count": 0}
    assert result["coverage"]["run_group"] == "complete"
    assert result["coverage"]["physical_game"] == "unknown"
    serialized = json.dumps(result)
    assert candidate.artifact_id not in serialized and gold.artifact_id not in serialized
    assert b"synthetic-only-gold-selection-payload" not in serialized.encode()


def test_curation_overlap_legacy_training_claim_is_source_vouched(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, candidate_schema="legacy")
    ledger = CurationLedger(owner.operations)
    ledger.claim("synthetic-legacy-training-claim", "training", ("synthetic-training-run",))
    ledger.bind("synthetic-legacy-training-claim", candidate.artifact_id)
    monkeypatch.setattr(
        owner.store,
        "read_payload",
        lambda *_args, **_kwargs: pytest.fail("overlap metadata must not read payloads"),
    )

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "no_indexed_overlap"
    assert result["candidate_scope"] == "source_superset"
    assert result["coverage"]["candidate_source"] == "complete"
    assert result["coverage"]["run"] == "complete"
    assert result["coverage"]["run_group"] == "complete"
    assert result["findings"]["run"] == {"status": "none", "count": 0}


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("multiple", "legacy_candidate_claim_conflict"),
        ("nontraining", "legacy_candidate_claim_conflict"),
        ("misbound", "legacy_candidate_claim_conflict"),
        ("unbacked_run", "candidate_claim_source_mismatch"),
        ("empty_runs", "candidate_owner_membership_missing"),
    ],
)
def test_curation_overlap_legacy_claim_conflicts_remain_unknown(api, case, reason):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, candidate_schema="legacy")
    ledger = CurationLedger(owner.operations)
    if case == "multiple":
        for claim_id in ("legacy-training-one", "legacy-training-two"):
            ledger.claim(claim_id, "training", ("synthetic-training-run",))
            ledger.bind(claim_id, candidate.artifact_id)
    elif case == "nontraining":
        ledger.claim("legacy-test-claim", "test", ("synthetic-training-run",))
        ledger.bind("legacy-test-claim", candidate.artifact_id)
    elif case == "misbound":
        ledger.claim(candidate.artifact_id, "training", ("synthetic-training-run",))
        ledger.bind(candidate.artifact_id, "f" * 64)
    elif case == "unbacked_run":
        ledger.claim("legacy-unbacked-claim", "training", ("unindexed-run",))
        ledger.bind("legacy-unbacked-claim", candidate.artifact_id)
    else:
        with owner.operations.transaction() as db:
            db.execute(
                "INSERT INTO curation_claims VALUES(?,?,?,?)",
                ("legacy-empty-claim", "training", candidate.artifact_id, 0.0),
            )

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert reason in result["reasons"]


def test_curation_overlap_legacy_historical_default_registers_training_claim(
    api, monkeypatch
):
    _, owner, _, _, _ = api
    source = Manifest(
        "evidence", owner.producer,
        parameters=FrozenObject.of({
            "schema": "stpd/received-bundle-v1", "disposition": "verified",
        }),
    )
    owner.store.publish(source)
    candidate = Manifest(
        "dataset", owner.producer,
        parents=(Parent("source_" + source.artifact_id, source.artifact_id),),
        parameters=FrozenObject.of({"schema": DECISION_DATASET_SCHEMA}),
    )
    owner.store.publish(candidate)
    curation = DatasetCuration(owner, object())
    monkeypatch.setattr(
        curation,
        "load",
        lambda _manifest: SimpleNamespace(run_ids={"historical-training-run"}),
    )

    curation._prepare_gold(lambda *_args: None)

    with owner.operations.transaction() as db:
        claim = db.execute(
            "SELECT id,purpose,artifact FROM curation_claims WHERE artifact=?",
            (candidate.artifact_id,),
        ).fetchone()
        claim_runs = {
            row[0]
            for row in db.execute(
                "SELECT run FROM curation_claim_runs WHERE claim=?", (candidate.artifact_id,)
            )
        }
    assert claim["id"] == candidate.artifact_id
    assert claim["purpose"] == "training"
    assert claim["artifact"] == candidate.artifact_id
    assert claim_runs == {"historical-training-run"}


def test_curation_overlap_legacy_source_superset_reports_possible_overlap(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, shared_source=True, candidate_schema="legacy")

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "possible_overlap"
    assert result["candidate_scope"] == "source_superset"
    assert result["findings"]["source"] == {"status": "overlap", "count": 1}
    assert result["findings"]["run"]["status"] == "possible_overlap"
    assert result["findings"]["run"]["count"] is None
    assert "candidate_source_superset_requires_refinement" in result["reasons"]


def test_curation_overlap_missing_legacy_source_index_stays_unknown(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, candidate_schema="legacy")
    candidate_source = candidate.parents[0].artifact_id
    ledger = CurationLedger(owner.operations)
    ledger.claim("synthetic-legacy-training-claim", "training", ("synthetic-training-run",))
    ledger.bind("synthetic-legacy-training-claim", candidate.artifact_id)
    with owner.operations.transaction() as db:
        db.execute("DELETE FROM curation_exact_source_index WHERE source=?", (candidate_source,))

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["candidate_scope"] == "source_superset"
    assert result["findings"]["run"] == {"status": "unknown", "count": None}
    assert result["coverage"]["run_group"] == "incomplete"
    assert "source_index_incomplete" in result["reasons"]


def test_curation_overlap_hides_missing_or_unauthorized_catalog_candidates(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    with owner.operations.transaction() as db:
        db.execute("DELETE FROM console_artifacts WHERE artifact_id=?", (candidate.artifact_id,))

    get_manifest = owner.store.get_manifest
    manifest_reads = []

    def tracked_get_manifest(identity):
        manifest_reads.append(identity)
        return get_manifest(identity)

    monkeypatch.setattr(owner.store, "get_manifest", tracked_get_manifest)
    unauthorized = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )
    absent = router.read(
        "curation-overlap",
        f"candidate={'a' * 64}&gold={gold.artifact_id}",
        member,
    )
    def without_observed_at(result):
        return {key: value for key, value in result.items() if key != "observed_at"}

    assert manifest_reads == []
    assert without_observed_at(unauthorized) == without_observed_at(absent)
    assert unauthorized["reasons"] == ["candidate_unavailable"]


@pytest.mark.parametrize("sharing_state", ["missing_upload", "withdrawn"])
def test_curation_overlap_requires_current_source_access_and_hides_denial(
    api, sharing_state
):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    candidate_source = candidate.parents[0].artifact_id
    with owner.operations.transaction() as db:
        row = db.execute(
            "SELECT id FROM uploads WHERE json_extract(receipt,'$.evidence_id')=?",
            (candidate_source,),
        ).fetchone()
        assert row is not None
        if sharing_state == "missing_upload":
            db.execute("DELETE FROM uploads WHERE id=?", (row[0],))
        else:
            db.execute(
                "INSERT INTO collection_sharing VALUES(?,?,?,?)",
                (row[0], 0, "b" * 64, 1.0),
            )

    unavailable = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )
    absent = router.read(
        "curation-overlap",
        f"candidate={'a' * 64}&gold={gold.artifact_id}",
        member,
    )
    def normalize(result):
        return {key: value for key, value in result.items() if key != "observed_at"}

    assert unavailable["status"] == "unknown"
    assert unavailable["reasons"] == ["candidate_unavailable"]
    assert normalize(unavailable) == normalize(absent)


def test_curation_overlap_finds_cross_source_duplicate_run_group(api):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    with owner.operations.transaction() as db:
        db.executemany(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            (
                ("late-shared-fingerprint", "synthetic-training-run"),
                ("late-shared-fingerprint", "synthetic-gold-run"),
            ),
        )

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )
    assert result["status"] == "overlap"
    assert result["findings"]["source"] == {"status": "none", "count": 0}
    assert result["findings"]["run"] == {"status": "none", "count": 0}
    assert result["findings"]["run_group"] == {"status": "overlap", "related_runs": 2}


@pytest.mark.parametrize("overflow_scope", ["legacy_source", "candidate_claim", "gold_claim"])
def test_curation_overlap_run_materialization_cap_is_unknown(api, monkeypatch, overflow_scope):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(
        api, candidate_schema="legacy" if overflow_scope == "legacy_source" else None
    )
    extra_run = "synthetic-over-cap-" + overflow_scope
    source_id = (
        candidate.parents[0].artifact_id
        if overflow_scope != "gold_claim" else gold.parents[0].artifact_id
    )
    with owner.operations.transaction() as db:
        db.execute(
            "INSERT INTO curation_source_runs VALUES(?,?)", (source_id, extra_run)
        )
        db.execute(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            ("fingerprint-" + extra_run, extra_run),
        )
        if overflow_scope == "candidate_claim":
            claim_id = db.execute(
                "SELECT id FROM curation_claims WHERE artifact=?", (candidate.artifact_id,)
            ).fetchone()[0]
            db.execute("INSERT INTO curation_claim_runs VALUES(?,?)", (claim_id, extra_run))
        elif overflow_scope == "gold_claim":
            claim_id = db.execute(
                "SELECT id FROM curation_claims WHERE artifact=?", (gold.artifact_id,)
            ).fetchone()[0]
            db.execute("INSERT INTO curation_claim_runs VALUES(?,?)", (claim_id, extra_run))

    monkeypatch.setattr(curation_access, "MAX_OVERLAP_RUNS", 1)
    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["coverage"]["run"] == "incomplete"
    assert result["coverage"]["run_group"] == "incomplete"
    assert result["findings"]["run"]["status"] == "unknown"
    assert "overlap_run_limit" in result["reasons"]


def test_curation_overlap_retains_exact_hit_when_run_cap_is_exceeded(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    with owner.operations.transaction() as db:
        claim_id = db.execute(
            "SELECT id FROM curation_claims WHERE artifact=?", (candidate.artifact_id,)
        ).fetchone()[0]
        source_id = candidate.parents[0].artifact_id
        db.execute(
            "INSERT INTO curation_source_runs VALUES(?,?)",
            (source_id, "synthetic-gold-run"),
        )
        db.execute(
            "INSERT INTO curation_claim_runs VALUES(?,?)",
            (claim_id, "synthetic-gold-run"),
        )
    monkeypatch.setattr(curation_access, "MAX_OVERLAP_RUNS", 1)

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "overlap"
    assert result["findings"]["run"] == {"status": "overlap", "count": None}
    assert result["coverage"]["run"] == "incomplete"
    assert result["coverage"]["run_group"] == "incomplete"


def test_curation_overlap_run_group_expansion_cap_is_unknown(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    runs = [
        "synthetic-training-run", "synthetic-chain-1", "synthetic-chain-2",
        "synthetic-chain-3", "synthetic-gold-run",
    ]
    with owner.operations.transaction() as db:
        for index, (left, right) in enumerate(zip(runs[:-1], runs[1:], strict=True)):
            fingerprint = f"synthetic-chain-edge-{index}"
            db.executemany(
                "INSERT INTO curation_fingerprints VALUES(?,?)",
                ((fingerprint, left), (fingerprint, right)),
            )
    monkeypatch.setattr(curation_access, "MAX_RUN_GROUP_ROWS", 2)

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["coverage"]["run"] == "complete"
    assert result["coverage"]["run_group"] == "incomplete"
    assert result["findings"]["run_group"] == {
        "status": "unknown", "related_runs": None
    }
    assert "run_group_limit" in result["reasons"]


def test_curation_overlap_run_group_sqlite_budget_is_unknown(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    with owner.operations.transaction() as db:
        db.executemany(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            (("synthetic-wide-fanout", "synthetic-training-run"),)
            + tuple(
                ("synthetic-wide-fanout", f"synthetic-wide-run-{index}")
                for index in range(2_000)
            ),
        )
    monkeypatch.setattr(curation_access, "MAX_RUN_GROUP_SQLITE_VM_STEPS", 1_000)

    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["coverage"]["run_group"] == "incomplete"
    assert result["findings"]["run_group"] == {
        "status": "unknown", "related_runs": None
    }
    assert "run_group_query_limit" in result["reasons"]


def test_curation_overlap_source_authorization_scan_uses_request_budget(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    receipt = json.dumps({
        "evidence_id": "f" * 64,
        "content_id": "e" * 64,
        "status": "verified",
    })
    with owner.operations.transaction() as db:
        db.executemany(
            "INSERT INTO uploads(id,device,content_id,manifest_sha,intent,status,receipt) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                (
                    f"synthetic-source-auth-{index:06d}",
                    "synthetic-source-auth-device",
                    f"{index:064x}",
                    "a" * 64,
                    "{}",
                    "verified",
                    receipt,
                )
                for index in range(40_000)
            ),
        )

    monkeypatch.setattr(curation_access, "MAX_OVERLAP_REQUEST_SQLITE_VM_STEPS", 100_000)
    original = CollectionAccess.manifests_for_evidence.__func__
    observed = {"called": False, "interrupted": False}

    def observe_source_read(cls, db, store, evidence_id, *, max_rows=None):
        observed["called"] = True
        try:
            return original(cls, db, store, evidence_id, max_rows=max_rows)
        except sqlite3.OperationalError as error:
            observed["interrupted"] = (
                getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
            )
            raise

    monkeypatch.setattr(
        CollectionAccess, "manifests_for_evidence", classmethod(observe_source_read)
    )
    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert observed == {"called": True, "interrupted": True}
    assert result["status"] == "unknown"
    assert result["reasons"] == ["owner_index_query_limit"]
    assert result["findings"]["source"] == {"status": "unknown", "count": None}


def test_curation_overlap_source_authorization_row_cap_is_unknown(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api)
    source_id = candidate.parents[0].artifact_id
    with owner.operations.transaction() as db:
        row = db.execute(
            "SELECT device,content_id,manifest_sha,intent,status,receipt FROM uploads "
            "WHERE json_extract(receipt,'$.evidence_id')=?",
            (source_id,),
        ).fetchone()
        assert row is not None
        db.execute(
            "INSERT INTO uploads(id,device,content_id,manifest_sha,intent,status,receipt) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "synthetic-source-auth-duplicate", "synthetic-second-device", row["content_id"],
                row["manifest_sha"], row["intent"], row["status"], row["receipt"],
            ),
        )

    monkeypatch.setattr(curation_access, "MAX_OVERLAP_SOURCE_UPLOAD_ROWS", 1)
    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert result["status"] == "unknown"
    assert result["reasons"] == ["owner_index_query_limit"]
    assert result["findings"]["source"] == {"status": "unknown", "count": None}


def test_curation_overlap_inventory_scan_uses_request_budget(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, shared_source=True)
    with owner.operations.transaction() as db:
        candidate_claim = db.execute(
            "SELECT id FROM curation_claims WHERE artifact=?", (candidate.artifact_id,)
        ).fetchone()[0]
        gold_claim = db.execute(
            "SELECT id FROM curation_claims WHERE artifact=?", (gold.artifact_id,)
        ).fetchone()[0]
        shared_run = "synthetic-inventory-shared-run"
        db.executemany(
            "INSERT INTO curation_claim_runs VALUES(?,?)",
            ((candidate_claim, shared_run), (gold_claim, shared_run)),
        )
        db.execute(
            "INSERT INTO curation_source_runs VALUES(?,?)",
            (candidate.parents[0].artifact_id, shared_run),
        )
        # Synthetic-only expression index isolates the full inventory scan from the
        # separately tested receipt-authorization scan; production adds no index.
        db.execute(
            "CREATE INDEX synthetic_receipt_evidence ON uploads "
            "(json_extract(receipt,'$.evidence_id'))"
        )
        sources = [
            (
                hashlib.sha256(f"synthetic-inventory-source-{index}".encode()).hexdigest(),
                hashlib.sha256(f"synthetic-inventory-digest-{index}".encode()).hexdigest(),
                1,
            )
            for index in range(20_000)
        ]
        db.executemany("INSERT INTO curation_sources VALUES(?,?,?)", sources)
        db.executemany(
            "INSERT INTO uploads(id,device,content_id,manifest_sha,intent,status,receipt) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                (
                    f"synthetic-inventory-upload-{index:06d}",
                    "synthetic-inventory-device",
                    f"{index:064x}",
                    "b" * 64,
                    "{}",
                    "verified",
                    json.dumps({
                        "evidence_id": sources[index][0],
                        "content_id": f"{index:064x}",
                        "status": "verified",
                    }),
                )
                for index in range(len(sources))
            ),
        )

    monkeypatch.setattr(curation_access, "MAX_OVERLAP_REQUEST_SQLITE_VM_STEPS", 100_000)
    original = curation_access._hub_inventory_pending
    observed = {"called": False, "interrupted": False}

    def observe_inventory_read(db):
        observed["called"] = True
        try:
            return original(db)
        except sqlite3.OperationalError as error:
            observed["interrupted"] = (
                getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
            )
            raise

    monkeypatch.setattr(curation_access, "_hub_inventory_pending", observe_inventory_read)
    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert observed == {"called": True, "interrupted": True}
    assert result["status"] == "overlap"
    assert result["reasons"] == ["owner_index_query_limit"]
    assert result["candidate_scope"] == "owner_claim_runs"
    assert result["findings"]["source"] == {"status": "overlap", "count": None}
    assert result["findings"]["run"] == {"status": "overlap", "count": None}
    assert result["findings"]["run_group"] == {
        "status": "unknown", "related_runs": None
    }
    assert result["coverage"]["verified_source_inventory"] == "unknown"
    assert result["coverage"]["run_group"] == "unknown"


def test_curation_overlap_run_group_request_budget_retains_prior_hits(api, monkeypatch):
    router, owner, _, member, _ = api
    candidate, gold = indexed_overlap_pair(api, shared_source=True)
    with owner.operations.transaction() as db:
        candidate_claim = db.execute(
            "SELECT id FROM curation_claims WHERE artifact=?", (candidate.artifact_id,)
        ).fetchone()[0]
        gold_claim = db.execute(
            "SELECT id FROM curation_claims WHERE artifact=?", (gold.artifact_id,)
        ).fetchone()[0]
        shared_run = "synthetic-group-shared-run"
        db.executemany(
            "INSERT INTO curation_claim_runs VALUES(?,?)",
            ((candidate_claim, shared_run), (gold_claim, shared_run)),
        )
        db.execute(
            "INSERT INTO curation_source_runs VALUES(?,?)",
            (candidate.parents[0].artifact_id, shared_run),
        )
        db.executemany(
            "INSERT INTO curation_fingerprints VALUES(?,?)",
            (("synthetic-group-wide-fanout", shared_run),)
            + tuple(
                ("synthetic-group-wide-fanout", f"synthetic-group-run-{index}")
                for index in range(2_000)
            ),
        )

    original = curation_access._bounded_rows
    observed = {"group_query": False, "request_budget_exhausted": False}

    def exhaust_request_budget_in_group_query(
        db, query, parameters=(), *, budget, vm_steps=curation_access.MAX_OVERLAP_SQLITE_VM_STEPS
    ):
        if "WITH RECURSIVE seeds(side,run)" in query:
            observed["group_query"] = True
            # Keep all authorization, source and run reads intact; exhaust only the
            # shared route budget during the later recursive group expansion.
            budget.max_vm_steps = budget.steps + 10_000
            rows = original(db, query, parameters, budget=budget, vm_steps=vm_steps)
            observed["request_budget_exhausted"] = budget.exhausted
            return rows
        return original(db, query, parameters, budget=budget, vm_steps=vm_steps)

    monkeypatch.setattr(curation_access, "_bounded_rows", exhaust_request_budget_in_group_query)
    result = router.read(
        "curation-overlap",
        f"candidate={candidate.artifact_id}&gold={gold.artifact_id}",
        member,
    )

    assert observed == {"group_query": True, "request_budget_exhausted": True}
    assert result["status"] == "overlap"
    assert result["reasons"] == ["owner_index_query_limit"]
    assert result["findings"]["source"] == {"status": "overlap", "count": None}
    assert result["findings"]["run"] == {"status": "overlap", "count": None}
    assert result["findings"]["run_group"] == {
        "status": "unknown", "related_runs": None
    }
    assert result["coverage"]["run"] == "unknown"
    assert result["coverage"]["run_group"] == "unknown"


def test_curation_overlap_route_is_member_only_and_rejects_ambiguous_queries(api):
    router, _, _, member, _ = api
    with pytest.raises(BoundaryError, match="invalid_curation_overlap_query"):
        router.read("curation-overlap", "candidate=" + "a" * 64, member)
    with pytest.raises(BoundaryError, match="invalid_curation_overlap_query"):
        router.read(
            "curation-overlap",
            f"candidate={'a' * 64}&candidate={'b' * 64}&gold={'c' * 64}",
            member,
        )
    with pytest.raises(BoundaryError, match="membership_not_authorized"):
        router.read(
            "curation-overlap",
            f"candidate={'a' * 64}&gold={'b' * 64}",
            ConsolePrincipal("collector", ("one",)),
        )


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "status=verified",
        "url=https://evil.invalid",
        "limit=1&limit=2",
    ],
)
def test_member_pagination_is_bounded(api, query):
    router, _, _, member, _ = api
    with pytest.raises(BoundaryError):
        router.read("campaigns", query, member)


def test_receiver_automatically_grants_only_exact_verified_enrollment(api, tmp_path):
    router, owner, _, member, _ = api
    upload, bundle, enrollment = staged(api, tmp_path)
    assert owner.verify_pending() == 1
    row = owner.operations.upload(upload)
    receipt_before = row["receipt"]
    assert row["status"] == "verified"
    assert (
        router.exports.collections.collection_access([upload])[upload]["availability"]
        == "available"
    )
    result = router.write(
        "exports", {"schema": REQUEST_SCHEMA, "collections": [upload], "artifacts": []}, member
    )
    assert result["files_count"] == 1 and result["files"][0]["role"] == "archive"
    with owner.operations.transaction() as db:
        events = db.execute(
            "SELECT detail FROM events WHERE operation='collection_sharing_associated' "
            "AND subject=?",
            (upload,),
        ).fetchall()
        assert len(events) == 1
        detail = json.loads(events[0][0])
        assert detail["human_origin_verified"] is False
        assert (
            detail["binding"]["enrollment_sha256"]
            == hashlib.sha256(json_bytes(enrollment)).hexdigest()
        )
        assert detail["evidence_ref"] == hashlib.sha256(json_bytes(detail["binding"])).hexdigest()
    # The same exact verified value is idempotent, including concurrent retries.
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: owner.associate_verified_bundle(upload, bundle), range(4)))
    assert owner.operations.upload(upload)["receipt"] == receipt_before
    grant_collection_sharing(
        owner, upload_id=upload, approved=False, evidence_ref="e" * 64, actor="owner"
    )
    assert owner.associate_verified_bundle(upload, bundle)["availability"] == "not_granted"
    assert (
        router.exports.collections.collection_access([upload])[upload]["availability"]
        == "not_granted"
    )
    assert owner.operations.upload(upload)["receipt"] == receipt_before


def test_receiver_wrong_device_and_missing_enrollment_do_not_grant(api, tmp_path):
    router, owner, _, _, _ = api
    upload, bundle, _ = staged(api, tmp_path)
    # A valid archive claiming another enrolled device never borrows that consent.
    # Project access to accepted bytes does not assert an enrollment association.
    with owner.operations.transaction() as db:
        db.execute("UPDATE uploads SET device='two' WHERE id=?", (upload,))
    owner.verify_pending()
    assert owner.operations.upload(upload)["status"] == "verified"
    with owner.operations.transaction() as db:
        detail = db.execute(
            "SELECT detail FROM events WHERE operation='collection_sharing_unavailable' "
            "AND subject=?",
            (upload,),
        ).fetchone()[0]
        assert json.loads(detail)["reason"] == "verified_bundle_identity_mismatch"
    assert (
        router.exports.collections.collection_access([upload])[upload]["availability"]
        == "available"
    )
    with pytest.raises(BoundaryError, match="verified_bundle_identity_mismatch"):
        owner.associate_verified_bundle(upload, bundle)
    with owner.operations.transaction() as db:
        db.execute("UPDATE uploads SET device='one' WHERE id=?", (upload,))
        db.execute("DELETE FROM collection_enrollments")
    assert owner.associate_verified_bundle(upload, bundle)["reason"] == "enrollment_not_found"
    assert (
        router.exports.collections.collection_access([upload])[upload]["availability"]
        == "available"
    )
