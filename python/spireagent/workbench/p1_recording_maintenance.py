"""Serial closed-recording maintenance using the application's existing import operation."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sts2_platform_evidence.collection_tool import P1_TARGET_PROJECTION

from spireagent.json_boundary import BoundaryError, digest

from .native_tasks import P1_ORIGINS
from .p1_recording_storage import open_peer

if TYPE_CHECKING:
    from .local_recording_import import LocalRecordingImporter


def _scope(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) == {"candidate_ids"}:
        ids = value["candidate_ids"]
        if not isinstance(ids, list) or not ids:
            raise BoundaryError("p1_maintenance", "invalid_scope")
        return {"candidate_ids": [digest(v, "p1_maintenance.candidate") for v in ids]}
    if set(value) == {"from_now", "declared_origins"} and value["from_now"] is True:
        value = {
            "closed_after": datetime.now(UTC).isoformat(),
            "declared_origins": value["declared_origins"],
        }
    if set(value) != {"closed_after", "declared_origins"}:
        raise BoundaryError("p1_maintenance", "invalid_scope")
    origins = value["declared_origins"]
    if (
        not isinstance(origins, list)
        or not origins
        or any(origin not in P1_ORIGINS for origin in origins)
        or datetime.fromisoformat(value["closed_after"]).tzinfo is None
    ):
        raise BoundaryError("p1_maintenance", "invalid_scope")
    return value


def _in_scope(candidate: dict[str, Any], scope: dict[str, Any] | None) -> bool:
    if scope is None:
        return True
    if "candidate_ids" in scope:
        return candidate["candidate_id"] in scope["candidate_ids"]
    if candidate.get("declared_origin") not in scope["declared_origins"]:
        return False
    try:
        created = datetime.fromisoformat(candidate["closed_at"])
        return created >= datetime.fromisoformat(scope["closed_after"])
    except (KeyError, ValueError, TypeError):
        return False  # A date-scoped policy excludes an undated source; the original stays intact.


class P1RecordingMaintenance:
    def __init__(self, importer: LocalRecordingImporter) -> None:
        self.importer = importer
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def configure(
        self,
        enabled: bool,
        peer: dict[str, Any] | None,
        projection_id: str | None = P1_TARGET_PROJECTION,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if type(enabled) is not bool or projection_id not in {None, P1_TARGET_PROJECTION}:
            raise BoundaryError("p1_maintenance", "invalid_policy")
        with self.importer.lock:
            state = self.importer._maintenance
            previous = state["policy"]
            selected_scope = _scope(scope) if scope is not None else previous.get("scope")
            if enabled and peer is not None and peer.get("kind") == "s3" and selected_scope is None:
                raise BoundaryError("p1_maintenance", "explicit_cloud_scope_required")
            if peer is not None:
                open_peer(peer)
            if (
                previous["peer"] != peer
                or previous["projection_id"] != projection_id
                or previous.get("scope") != selected_scope
            ):
                state["completed_candidates"] = []
            state["policy"] = {
                "enabled": enabled,
                "peer": peer,
                "projection_id": projection_id,
                "scope": selected_scope,
            }
            self.importer._save()
            return dict(state)

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="p1-recording-maintenance"
        )
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop.wait(10):
            try:
                self.tick()
            except Exception as error:
                # Preserve failure and disable dispatch while the application stays available.
                with self.importer.lock:
                    self.importer._maintenance.update(
                        status="failed", error_code=getattr(error, "code", "maintenance_failed")
                    )
                    self.importer._maintenance["policy"]["enabled"] = False
                    self.importer._save()

    def tick(self) -> dict[str, Any]:
        from .collection_tool_registration import current_collection_tool
        from .local_recording_import import _selected_store
        from .p1_recording_store import _matching, projection_format

        importer = self.importer
        with importer.lock:
            state = importer._maintenance
            policy = state["policy"]
            if not policy["enabled"]:
                return {**state, "status": "disabled"}
            operation = importer.operation
            if importer.thread is not None and importer.thread.is_alive():
                return {**state, "status": "operation_in_progress"}
            if operation.get("status") in {
                "pending",
                "publication_unknown",
                "interrupted_unknown",
                "published_index_unavailable",
            }:
                state.update(
                    status="blocked", error_code="original_operation_reconciliation_required"
                )
                importer._save()
                return dict(state)
            active = state.get("active")
            if active is not None and operation.get("status") == "failed":
                state.update(
                    status="failed", error_code=operation.get("error_code", "operation_failed")
                )
                policy["enabled"] = False
                importer._save()
                return dict(state)
            if active is not None and policy.get("scope") is not None:
                if "scope_facts" not in active:
                    catalog = importer.catalog.read()
                    candidate = importer.catalog.candidate(active["candidate_id"])
                    if catalog.get("status") != "ready" or candidate is None:
                        state.update(
                            status="blocked", error_code="original_candidate_scope_unavailable"
                        )
                        importer._save()
                        return dict(state)
                    active["scope_facts"] = {
                        "candidate_id": active["candidate_id"],
                        "declared_origin": candidate.get("declared_origin"),
                        "closed_at": candidate.get("closed_at"),
                    }
                if not _in_scope(active["scope_facts"], policy["scope"]):
                    state.update(active=None, status="excluded_by_scope", error_code=None)
                    importer._save()
                    return dict(state)
            store, _ = _selected_store(importer.config, require_curation=False)
            if active is None:
                catalog = importer.catalog.read()
                if catalog.get("status") != "ready":
                    state.update(
                        status="unavailable",
                        error_code=catalog.get("error_code", "catalog_unavailable"),
                    )
                    importer._save()
                    return dict(state)
                completed = state.setdefault("completed_candidates", [])
                candidates = [
                    c
                    for c in catalog["candidates"]
                    if c.get("recording_type") == "p1_native"
                    and c["candidate_id"] not in completed
                    and _in_scope(c, policy.get("scope"))
                ]
                if not candidates:
                    state.update(status="up_to_date", error_code=None)
                    importer._save()
                    return dict(state)
                candidate = importer.catalog.candidate(candidates[0]["candidate_id"])
                if candidate is None:
                    raise BoundaryError("p1_maintenance", "candidate_changed_or_unavailable")
                identity = candidates[0]["candidate_id"]
                source = importer._existing_candidate(
                    store, identity, candidate, verify_payloads=False
                )
                active = {
                    "candidate_id": identity,
                    "source_artifact_id": source,
                    "scope_facts": {
                        "candidate_id": identity,
                        "declared_origin": candidate.get("declared_origin"),
                        "closed_at": candidate.get("closed_at"),
                    },
                }
                state.update(active=active, status="running", error_code=None)
                importer._save()
                if source is None:
                    importer.start(identity, human_origin_attested=False)
                    return dict(state)
            source = active["source_artifact_id"]
            if source is None:
                if (
                    operation.get("candidate_id") != active["candidate_id"]
                    or operation.get("status") != "completed"
                ):
                    raise BoundaryError("p1_maintenance", "original_import_result_required")
                source = operation["artifact_id"]
                active["source_artifact_id"] = source
                importer._save()
            if policy["peer"] is not None:
                verified = any(
                    row["artifact_id"] == source
                    and row["peer"] == policy["peer"]
                    and row["storage_action"] == "preserve"
                    and row["storage_receipt"].get("full_readback") is True
                    for row in importer._storage_receipts.values()
                )
                if not verified:
                    importer.start_p1_storage("preserve", source, policy["peer"])
                    return dict(state)
            projection = policy["projection_id"]
            if projection is not None:
                _, release = current_collection_tool(importer.config)
                existing = _matching(
                    store,
                    projection_format(projection)[0],
                    source_artifact_id=source,
                    tool_release_id=release,
                    projection_id=projection,
                    run_id="run-0001",
                )
                if existing is None:
                    importer.start_p1_derivation(source, projection)
                    return dict(state)
            state.setdefault("completed_candidates", []).append(active["candidate_id"])
            state.update(active=None, status="completed", error_code=None)
            importer._save()
            return dict(state)
