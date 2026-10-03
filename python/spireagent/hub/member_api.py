"""Authenticated member route composition, independent of HTTP cookies and transport.

The caller verifies browser JWT/personal token on every request and applies exact Origin
and CSRF checks to browser writes. This helper rechecks current durable membership, never
accepts a device token or uses query/body claims as an identity. Admin creation is separate.

Owner CLI hooks (not member routes):
    refresh_project_statistics(service, upload_ids=(ID,), dataset_ids=())
    grant_collection_sharing(service, upload_id=ID, approved=True,
                             evidence_ref=SHA256, actor=OWNER)
Neither hook admits training, rewrites receipts or derives consent from a timestamp.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

from spireagent.hub.campaigns import Campaigns
from spireagent.hub.console_auth import ConsolePrincipal
from spireagent.hub.console_index import pagination, timestamp
from spireagent.hub.decision_jobs import DecisionJobs
from spireagent.hub.exports import ExportService
from spireagent.hub.identity import IdentityService
from spireagent.hub.live_evaluations import LiveEvaluations
from spireagent.hub.quality import QualityAnnotations
from spireagent.hub.statistics import refresh_decision_statistics
from spireagent.hub.uploads import LocalStaging, S3Staging, UploadService
from spireagent.json_boundary import BoundaryError, decode_json, digest, object_fields
from stpd.collection_activity import ENROLLMENT_SCHEMA, validate_enrollment


class MemberApi:
    def __init__(self, service: UploadService, identity: IdentityService) -> None:
        if service.operations.path.resolve() != identity.ops.path.resolve():
            raise BoundaryError("member_api", "identity_store_mismatch")
        self.service, self.identity = service, identity
        self.campaigns = Campaigns(service.operations, identity.membership)
        self.exports = ExportService(service)
        self.decisions = DecisionJobs(service)
        self.quality = QualityAnnotations(service)
        self.live_evaluations = LiveEvaluations(service, identity.membership)

    def _principal(self, principal: ConsolePrincipal) -> ConsolePrincipal:
        with self.service.operations.transaction() as db:
            # Activation belongs to the signed identity boundary, not a data API request.
            return self.identity.membership.authorize(db, principal)

    def read(self, route: str, query: str, principal: ConsolePrincipal) -> dict[str, Any]:
        current = self._principal(principal)
        if route == "curation-overlap":
            try:
                pairs = parse_qsl(
                    query, strict_parsing=True, keep_blank_values=True, max_num_fields=2
                )
                values = dict(pairs)
                if len(pairs) != 2 or len(values) != 2 or set(values) != {"candidate", "gold"}:
                    raise ValueError
            except ValueError:
                raise BoundaryError("member_api", "invalid_curation_overlap_query") from None
            self.exports.collections.require_member(current)
            from spireagent.hub.curation_access import overlap_metadata

            return overlap_metadata(self.service, current, values["candidate"], values["gold"])
        quality = re.fullmatch(r"collections/([a-f0-9]{32})/decisions", route)
        if quality:
            limit, offset, status = pagination(query)
            if status is not None:
                raise BoundaryError("quality", "unexpected_status_filter")
            return self.quality.read(current, quality[1], limit, offset)
        if route == "games" and not query:
            return self.decisions.games(current)
        if route in {"datasets", "datasets/archived"}:
            limit, offset, status = pagination(query)
            if status is not None:
                raise BoundaryError("member_api", "unexpected_status_filter")
            return self.decisions.list(current, archived=route.endswith("/archived"),
                                       limit=limit, offset=offset)
        job = re.fullmatch(r"datasets/([a-f0-9]{32})", route)
        if job and not query:
            return self.decisions.read(current, job[1])
        if route == "collection-settings" and not query:
            return self.collection_settings(current)
        if route in {"campaigns", "campaigns/enrollments"}:
            limit, offset, status = pagination(query)
            if status is not None:
                raise BoundaryError("member_api", "unexpected_status_filter")
            if route == "campaigns":
                return {
                    **self.campaigns.list(current, limit=limit, offset=offset),
                    "limit": limit,
                    "offset": offset,
                    "observed_at": timestamp(),
                }
            return self._enrollments(current, limit=limit, offset=offset)
        if query:
            raise BoundaryError("member_api", "unexpected_query")
        enrollment = re.fullmatch(r"campaigns/enrollments/([a-f0-9]{32})", route)
        if enrollment:
            return cast(
                dict[str, Any], self._enrollments(current, enrollment_id=enrollment[1])["item"]
            )
        export = re.fullmatch(r"exports/([a-f0-9]{64})", route)
        if export:
            return self.exports.read(current, export[1])
        raise BoundaryError("member_api", "resource_not_found")

    def write(self, route: str, body: object, principal: ConsolePrincipal) -> dict[str, Any]:
        current = self._principal(principal)
        if route == "artifacts/visibility":
            from spireagent.hub.artifact_visibility import ArtifactVisibility
            return ArtifactVisibility(self.service).write(current, body)
        if route == "quality-annotations":
            return self.quality.write(current, body)
        enrollment = re.fullmatch(r"campaigns/([a-f0-9]{64})/enroll", route)
        if enrollment:
            value = object_fields(body, {"device_id", "consent"}, "campaign.enroll")
            return self.campaigns.enroll(
                current, enrollment[1], value["device_id"], value["consent"]
            )
        if route == "live-evaluations":
            return self.live_evaluations.publish(current, body)
        if route == "exports":
            return self.exports.create(current, body)
        if route == "datasets":
            return self.decisions.create(current, body)
        if route == "datasets/materialize":
            return self.decisions.materialize(current, body)
        if route == "datasets/visibility":
            return self.decisions.set_archived(current, body)
        retry = re.fullmatch(r"datasets/([a-f0-9]{32})/retry", route)
        if retry:
            return self.decisions.retry(current, retry[1], body)
        cancel = re.fullmatch(r"datasets/([a-f0-9]{32})/cancel", route)
        if cancel:
            return self.decisions.cancel(current, cancel[1], body)
        # No admin, compute, grants or implicit enrollment mutations on this surface.
        raise BoundaryError("member_api", "resource_not_found")

    def admin_create_campaign(self, body: object, principal: ConsolePrincipal) -> dict[str, Any]:
        """Called only after fresh browser identity + Origin + CSRF; owner repeats admin check."""
        return self.campaigns.create(principal, body)

    def _upload_hosts(self) -> list[str]:
        staging = self.service.staging
        if isinstance(staging, LocalStaging):
            address = staging.public_url
        elif isinstance(staging, S3Staging):
            # The owning S3 client is configured for path addressing; never expose a
            # signed URL or storage credential merely to render collection settings.
            address = staging.client.meta.endpoint_url
        else:
            raise BoundaryError("collection", "upload_destination_unavailable")
        host = urlsplit(address).hostname
        if not host:
            raise BoundaryError("collection", "upload_destination_unavailable")
        return [host]

    def collection_settings(self, principal: ConsolePrincipal) -> dict[str, Any]:
        return {
            "schema": "stpd/collection-settings-v1",
            "default": self.campaigns.default(principal),
            "upload_hosts": self._upload_hosts(),
            "observed_at": timestamp(),
        }

    def admin_collection_settings(
        self, body: object, principal: ConsolePrincipal
    ) -> dict[str, Any]:
        if isinstance(body, dict) and set(body) == {"template_id"}:
            self.campaigns.set_default(principal, body["template_id"])
        else:
            self.campaigns.publish_default(principal, body, self._upload_hosts())
        return self.collection_settings(principal)

    def payload(
        self,
        export_id: str,
        file_id: str,
        principal: ConsolePrincipal,
    ) -> tuple[dict[str, Any], Iterator[bytes]]:
        current = self._principal(principal)
        return self.exports.payload(current, export_id, file_id)

    def _enrollments(
        self,
        principal: ConsolePrincipal,
        *,
        limit: int = 25,
        offset: int = 0,
        enrollment_id: str | None = None,
    ) -> dict[str, Any]:
        # Enrollment consent is member-private. Even an administrator does not borrow
        # another member's declaration to prepare this computer's collection directory.
        where = "e.subject=? AND d.owner_subject=e.subject AND d.active=1"
        values: tuple[Any, ...] = (principal.subject,)
        if enrollment_id is not None:
            digest(enrollment_id, "campaign.enrollment_id", length=32)
            where += " AND e.id=?"
            values += (enrollment_id,)
        joined = (
            " FROM collection_enrollments e JOIN collection_activities a ON a.id=e.template_id "
            "JOIN devices d ON d.id=e.device_id WHERE " + where
        )
        with self.service.operations.transaction() as db:
            self.identity.membership.authorize(db, principal)
            total = db.execute("SELECT COUNT(*)" + joined, values).fetchone()[0]
            rows = db.execute(
                "SELECT e.*,a.template"
                + joined
                + " ORDER BY e.created_at DESC,e.id LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
        items = [
            validate_enrollment(
                {
                    "schema": ENROLLMENT_SCHEMA,
                    "enrollment_id": row["id"],
                    "template_id": row["template_id"],
                    "template": decode_json(row["template"]),
                    "device_id": row["device_id"],
                    "campaign_id": "campaign-" + row["id"],
                    "consent": decode_json(row["consent"]),
                    "declared_at": row["created_at"],
                    "human_origin_verified": False,
                }
            )
            for row in rows
        ]
        if enrollment_id is not None and not items:
            raise BoundaryError("campaign", "enrollment_not_found")
        return {
            "schema": "stpd/member-enrollments-v1",
            "items": items,
            **({"item": items[0]} if enrollment_id is not None else {}),
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "observed_at": timestamp(),
        }


def refresh_project_statistics(
    service: UploadService,
    *,
    upload_ids: tuple[str, ...] = (),
    dataset_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Explicit owner CLI operation; heavy bounded profiling never runs on HTTP GET."""
    return refresh_decision_statistics(service, upload_ids=upload_ids, dataset_ids=dataset_ids)


def grant_collection_sharing(
    service: UploadService,
    *,
    upload_id: str,
    approved: bool,
    evidence_ref: str,
    actor: str,
) -> dict[str, Any]:
    """Explicit owner CLI approval/revocation tied to an immutable approval evidence digest.

    This is not callable through MemberApi.write. The owner must establish the actual
    authorization before invoking it; neither membership nor upload implies consent.
    """
    exports = ExportService(service)
    exports.collections.set_collection_access(
        upload_id, approved=approved, evidence_ref=evidence_ref, actor=actor
    )
    return {"upload_id": upload_id, **exports.collections.collection_access([upload_id])[upload_id]}
