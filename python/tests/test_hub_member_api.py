"""Actual membership/campaign/export services behind the transport-neutral member router."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
from dataclasses import replace

import pytest
from platform_bundle3_fixture import bundle3, load, seal, write
from sts2_platform_evidence import DirectoryTransferManifest, verify_human_session_bundle
from test_campaign_onboarding import campaign as campaign
from test_hub_console import service
from test_hub_console import signed as signed

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.hub.campaigns import create_campaign_tables
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.hub.curation import CurationLedger
from spireagent.hub.exports import REQUEST_SCHEMA
from spireagent.hub.identity import IdentityService
from spireagent.hub.member_api import MemberApi, grant_collection_sharing
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.collection_activity import CONSENT_FIELDS
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


def indexed_overlap_pair(api, *, shared_source: bool = False):
    """Synthetic curation identities; this fixture never reads a selection payload."""
    _, owner, _, _, _ = api
    source_a = Manifest(
        "evidence", owner.producer,
        parameters=FrozenObject.of({
            "schema": "stpd/received-bundle-v1", "content_id": "1" * 64,
        }),
    )
    source_b = source_a if shared_source else Manifest(
        "evidence", owner.producer,
        parameters=FrozenObject.of({
            "schema": "stpd/received-bundle-v1", "content_id": "2" * 64,
        }),
    )
    owner.store.publish(source_a)
    if source_b.artifact_id != source_a.artifact_id:
        owner.store.publish(source_b)
    candidate = Manifest(
        "dataset", owner.producer,
        parents=(Parent("source_" + source_a.artifact_id, source_a.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/curated-decision-dataset-v1",
            "purpose": "training", "sealed_test": False,
        }),
    )
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
    assert result["findings"]["source"] == {"status": "unknown", "count": None}
    assert result["findings"]["run"] == {"status": "unknown", "count": None}


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
