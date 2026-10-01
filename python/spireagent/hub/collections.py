"""Project members use accepted project data; explicit withdrawals remain effective."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING, Any

from spireagent.artifact_contracts import Manifest
from spireagent.hub.access import project_member, require_artifact_access
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from stpd.collection_activity import ENROLLMENT_SCHEMA, validate_enrollment

if TYPE_CHECKING:
    from spireagent.hub.uploads import UploadService

MAX_SELECTIONS = 100


class CollectionAccess:
    def __init__(self, service: UploadService) -> None:
        self.service = service
        with service.operations.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS collection_sharing("
                "upload_id TEXT PRIMARY KEY,approved INTEGER NOT NULL,"
                "evidence_ref TEXT NOT NULL,changed_at REAL NOT NULL)"
            )

    @staticmethod
    def require_member(principal: ConsolePrincipal) -> None:
        if not project_member(principal):
            raise BoundaryError("hub", "unauthorized")

    @staticmethod
    def upload_id(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise BoundaryError("sharing", "invalid_upload_id")
        return value

    def set_collection_access(
        self,
        upload_id: str,
        *,
        approved: bool,
        evidence_ref: str,
        actor: str,
    ) -> None:
        """Owner operation after explicit scoped approval, not a member HTTP mutation.

        The reference identifies the immutable approval/enrollment evidence. Revocation
        affects future requests without rewriting an export, receipt or archive.
        """
        identity = self.upload_id(upload_id)
        digest(evidence_ref, "sharing.evidence_ref")
        if type(approved) is not bool or not actor or len(actor) > 256:
            raise BoundaryError("sharing", "invalid_approval")
        with self.service.operations.transaction() as db:
            if db.execute("SELECT 1 FROM uploads WHERE id=?", (identity,)).fetchone() is None:
                raise BoundaryError("sharing", "collection_not_found")
            previous = db.execute(
                "SELECT approved,evidence_ref FROM collection_sharing WHERE upload_id=?",
                (identity,),
            ).fetchone()
            if previous and tuple(previous) == (int(approved), evidence_ref):
                return
            db.execute(
                "INSERT INTO collection_sharing VALUES(?,?,?,?) "
                "ON CONFLICT(upload_id) DO UPDATE SET approved=excluded.approved,"
                "evidence_ref=excluded.evidence_ref,changed_at=excluded.changed_at",
                (identity, int(approved), evidence_ref, time.time()),
            )
            self.service.operations._event(
                db,
                actor,
                "collection_sharing_changed",
                identity,
                {"approved": approved, "evidence_ref": evidence_ref},
            )

    def associate_verified_collection(self, upload_id: str, bundle: object) -> dict[str, Any]:
        """Receiver-only seam using the fresh successful verifier value, never a UI summary.

        Existing explicit denials/revocations win over automatic enrollment association.
        A declaration is authorization, not proof that actions were Human or scientific.
        """
        from sts2_platform_evidence import HumanSessionBundleV3

        self.upload_id(upload_id)
        if not isinstance(bundle, HumanSessionBundleV3):
            return {"availability": "not_granted", "reason": "current_verified_bundle_required"}
        campaign = re.fullmatch(r"campaign-([a-f0-9]{32})", bundle.campaign_id)
        if campaign is None:
            return {"availability": "not_granted", "reason": "no_enrollment_identity"}
        with self.service.operations.transaction() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=?", (upload_id,)).fetchone()
            if row is None or row["status"] != "verified" or not row["receipt"]:
                raise BoundaryError("sharing", "collection_not_verified")
            receipt = json.loads(row["receipt"])
            if (
                row["content_id"] != bundle.bundle_content_id
                or receipt.get("content_id") != bundle.bundle_content_id
                or receipt.get("status") != "verified"
                or row["device"] != bundle.worker_id
            ):
                raise BoundaryError("sharing", "verified_bundle_identity_mismatch")
            old = db.execute(
                "SELECT approved FROM collection_sharing WHERE upload_id=?", (upload_id,)
            ).fetchone()
            if old is not None:
                return {
                    "availability": "available" if old[0] == 1 else "not_granted",
                    "reason": "existing_owner_decision_preserved",
                }
            enrollment = db.execute(
                "SELECT e.*,a.template,d.owner_subject,d.active,m.status AS membership_status "
                "FROM collection_enrollments e JOIN collection_activities a ON a.id=e.template_id "
                "JOIN devices d ON d.id=e.device_id "
                "LEFT JOIN identity_members m ON m.subject=e.subject "
                "WHERE e.id=?",
                (campaign[1],),
            ).fetchone()
            if enrollment is None:
                return {"availability": "not_granted", "reason": "enrollment_not_found"}
            if (
                enrollment["device_id"] != row["device"]
                or enrollment["owner_subject"] != enrollment["subject"]
                or enrollment["active"] != 1
                or enrollment["membership_status"] != "active"
            ):
                raise BoundaryError("sharing", "active_enrolled_device_owner_required")
            declaration = validate_enrollment(
                {
                    "schema": ENROLLMENT_SCHEMA,
                    "enrollment_id": enrollment["id"],
                    "template_id": enrollment["template_id"],
                    "template": json.loads(enrollment["template"]),
                    "device_id": enrollment["device_id"],
                    "campaign_id": bundle.campaign_id,
                    "consent": json.loads(enrollment["consent"]),
                    "declared_at": enrollment["created_at"],
                    "human_origin_verified": False,
                }
            )
            binding = {
                "schema": "stpd/enrolled-bundle-sharing-v1",
                "enrollment_sha256": hashlib.sha256(json_bytes(declaration)).hexdigest(),
                "enrollment_id": enrollment["id"],
                "template_id": enrollment["template_id"],
                "upload_id": upload_id,
                "device_id": row["device"],
                "bundle_content_id": bundle.bundle_content_id,
                "bundle_checksums_sha256": bundle.bundle_sha256,
                "received_artifact_id": digest(receipt.get("evidence_id"), "sharing.received_id"),
            }
            evidence_ref = hashlib.sha256(json_bytes(binding)).hexdigest()
            db.execute(
                "INSERT INTO collection_sharing VALUES(?,?,?,?)",
                (upload_id, 1, evidence_ref, time.time()),
            )
            self.service.operations._event(
                db,
                enrollment["subject"],
                "collection_sharing_associated",
                upload_id,
                {"evidence_ref": evidence_ref, "binding": binding, "human_origin_verified": False},
            )
        return {"availability": "available", "scope": "project_members"}

    def collection_access(self, upload_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Safe page-sized availability only. Download rechecks actual immutable bytes."""
        if len(upload_ids) > MAX_SELECTIONS:
            raise BoundaryError("sharing", "selection_limit")
        result = {}
        with closing(self.service.console_index.read()) as db:
            for identity in upload_ids:
                self.upload_id(identity)
                row = db.execute(
                    "SELECT u.status,s.approved FROM uploads u LEFT JOIN collection_sharing s "
                    "ON s.upload_id=u.id WHERE u.id=?",
                    (identity,),
                ).fetchone()
                reason = "not_granted"
                if row and (row[1] == 1 or (row[1] is None and row[0] == "verified")):
                    reason = (
                        "available" if row[0] in {"verified", "quarantined"} else "not_received"
                    )
                result[identity] = {"availability": reason, "scope": "project_members"}
        return result

    def collection(self, upload_id: str) -> Manifest:
        identity = self.upload_id(upload_id)
        with closing(self.service.console_index.read()) as db:
            row = db.execute(
                "SELECT u.*,s.approved FROM uploads u LEFT JOIN collection_sharing s "
                "ON s.upload_id=u.id WHERE u.id=?",
                (identity,),
            ).fetchone()
            return self._manifest_for_shared_row(row, self.service.store)

    @staticmethod
    def _manifest_for_shared_row(row: Any, store: Any) -> Manifest:
        """Apply the same current receipt/share checks to an already-read upload row."""
        if row is None or row["approved"] == 0 or (
            row["approved"] is None and row["status"] != "verified"
        ):
            raise BoundaryError("sharing", "collection_not_shared")
        if row["status"] not in {"verified", "quarantined"} or not row["receipt"]:
            raise BoundaryError("sharing", "collection_not_received")
        receipt = json.loads(row["receipt"])
        manifest = store.get_manifest(receipt["evidence_id"])
        info = manifest.parameters.value()
        intent = json.loads(row["intent"])
        archive = manifest.payload("archive")
        if (
            manifest.kind != "evidence"
            or info.get("schema") != "stpd/received-bundle-v1"
            or info.get("content_id") != row["content_id"]
            or receipt.get("content_id") != row["content_id"]
            or info.get("disposition") != row["status"]
            or receipt.get("status") != row["status"]
            or archive.sha256 != intent.get("archive_sha256")
            or archive.size != intent.get("archive_bytes")
        ):
            raise BoundaryError("sharing", "collection_identity_mismatch")
        return manifest

    @classmethod
    def manifests_for_evidence(
        cls,
        db: sqlite3.Connection,
        store: Any,
        evidence_id: str,
        *,
        max_rows: int | None = None,
    ) -> tuple[Manifest, ...]:
        """Require current project-shareable uploads for one source in a caller snapshot."""
        digest(evidence_id, "sharing.evidence_id")
        query = (
            "SELECT u.*,s.approved FROM uploads u LEFT JOIN collection_sharing s "
            "ON s.upload_id=u.id WHERE json_extract(u.receipt,'$.evidence_id')=? ORDER BY u.id"
        )
        parameters: tuple[Any, ...] = (evidence_id,)
        if max_rows is not None:
            if max_rows < 0:
                raise ValueError("max_rows must not be negative")
            query += " LIMIT ?"
            parameters += (max_rows + 1,)
        rows = db.execute(query, parameters).fetchall()
        if max_rows is not None and len(rows) > max_rows:
            raise BoundaryError("sharing", "source_upload_limit")
        if not rows:
            raise BoundaryError("sharing", "collection_not_shared")
        manifests = tuple(cls._manifest_for_shared_row(row, store) for row in rows)
        if any(manifest.artifact_id != evidence_id for manifest in manifests):
            raise BoundaryError("sharing", "collection_identity_mismatch")
        return manifests

    def artifact(self, artifact_id: str, *, use: str | None = None) -> Manifest:
        manifest = self.service.store.get_manifest(digest(artifact_id, "export.artifact_id"))
        checked = require_artifact_access(
            manifest, project_member=True, store=self.service.store,
        )
        from spireagent.hub.curation_access import guarded_runs, record_use

        if use is None:
            guarded_runs(self.service.operations, self.service.store, manifest, nodes=checked)
        else:
            record_use(self.service.operations, self.service.store, manifest, use, nodes=checked)
        # Catalogued project datasets need no second publication grant. A deliberate
        # withdrawal of a received source still applies to its derived data.
        if manifest.kind == "dataset":
            for ancestor in checked:
                info = ancestor.parameters.value()
                if info.get("schema") != "stpd/received-bundle-v1":
                    continue
                with closing(self.service.console_index.read()) as db:
                    rows = db.execute(
                        "SELECT u.id,s.approved FROM uploads u LEFT JOIN collection_sharing s "
                        "ON s.upload_id=u.id WHERE json_extract(u.receipt,'$.evidence_id')=?",
                        (ancestor.artifact_id,),
                    ).fetchall()
                if any(row["approved"] == 0 for row in rows):
                    raise BoundaryError("sharing", "source_sharing_not_established")
        return manifest
