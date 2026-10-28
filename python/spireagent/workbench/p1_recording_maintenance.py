"""Serial closed-recording maintenance using the application's existing import operation."""

from __future__ import annotations

import errno
import logging
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
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


def _ledger_stamp(path: Path) -> list[tuple[int, int]]:
    """Ignore SQLite's empty, connection-created WAL; retain actual ledger writes."""
    result = []
    for current in (path, path.with_name(path.name + "-wal")):
        try:
            info = current.stat()
        except FileNotFoundError:
            continue
        if current == path or info.st_size:
            result.append((info.st_mtime_ns, info.st_size))
    return result


class P1RecordingMaintenance:
    def __init__(self, importer: LocalRecordingImporter) -> None:
        self.importer = importer
        self.mixed_runs: Any | None = None
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def configure(
        self,
        enabled: bool,
        peer: dict[str, Any] | None,
        projection_id: str | None = P1_TARGET_PROJECTION,
        scope: dict[str, Any] | None = None,
        cool_recent: bool | None = None,
        cohort_policy: str | None = None,
    ) -> dict[str, Any]:
        if type(enabled) is not bool or projection_id not in {None, P1_TARGET_PROJECTION}:
            raise BoundaryError("p1_maintenance", "invalid_policy")
        with self.importer.lock:
            state = self.importer._maintenance
            previous = state["policy"]
            cooling = previous.get("cool_recent", False) if cool_recent is None else cool_recent
            if type(cooling) is not bool:
                raise BoundaryError("p1_maintenance", "invalid_policy")
            selected_scope = _scope(scope) if scope is not None else previous.get("scope")
            selected_cohort = cohort_policy or previous.get("cohort_policy")
            if selected_cohort is not None:
                from .p1_cohort_intake import load_policy

                load_policy(Path(selected_cohort))
            if cooling and (peer is None or selected_scope is None):
                raise BoundaryError("p1_maintenance", "explicit_cooling_peer_and_scope_required")
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
                "cool_recent": cooling,
                **({"cohort_policy": selected_cohort} if selected_cohort else {}),
            }
            state.update(status="running" if enabled else "disabled", error_code=None,
                         status_since=datetime.now(UTC).isoformat())
            state.pop("persistence_error", None)
            try:
                self.importer._save()
            except OSError:
                # A policy that could not be saved must not start background work.
                state["policy"]["enabled"] = False
                raise
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
                self._failed(error)

    def _failed(self, error: Exception) -> None:
        # ENOSPC used to kill this thread a second time while saving its failure.
        # Keep the visible in-memory failure and worker alive, but never redispatch.
        logging.getLogger(__name__).error("Recording maintenance stopped", exc_info=error)
        with self.importer.lock:
            state = self.importer._maintenance
            state.update(status="failed", status_since=datetime.now(UTC).isoformat(), error_code=(
                "disk_full" if isinstance(error, OSError) and error.errno == errno.ENOSPC
                else getattr(error, "code", "maintenance_failed")))
            state["policy"]["enabled"] = False
            try:
                self.importer._save()
            except OSError as save_error:
                state["persistence_error"] = errno.errorcode.get(save_error.errno or 0, "OSError")
                logging.getLogger(__name__).error(
                    "Maintenance failure remains in memory; state could not be saved",
                    exc_info=save_error,
                )
        self._observe(("failed", state.get("error_code")))

    def tick(self) -> dict[str, Any]:
        before = self.importer._maintenance
        previous = (before.get("status"), before.get("error_code"))
        try:
            self._tick_and_refresh()
        finally:
            # Status publication is independent of successful intake/recount.
            # The existing operation remains the only persistent state owner.
            self._observe(previous)
        return dict(self.importer._maintenance)

    def _observe(self, previous: tuple[Any, Any]) -> None:
        with self.importer.lock:
            state = self.importer._maintenance
            now = datetime.now(UTC).isoformat()
            if previous != (state.get("status"), state.get("error_code")):
                state["status_since"] = now
            state.setdefault("status_since", now)
            state["checked_at"] = now
            try:
                self.importer._save()
            except OSError as error:
                state["persistence_error"] = errno.errorcode.get(error.errno or 0, "OSError")
                logging.getLogger(__name__).exception("Maintenance observation could not be saved")
            snapshot = self.importer.maintenance_status()
        if state["policy"].get("cohort_policy"):
            from .p1_cohort_intake import write_page

            try:
                write_page(self.importer.config, snapshot)
            except (OSError, ValueError, KeyError, TypeError):
                # A replaceable HTML view must not stop source preservation.
                logging.getLogger(__name__).exception("Recording summary page could not be written")

    def _tick_and_refresh(self) -> dict[str, Any]:
        state = self._tick()
        policy = state["policy"]
        if state.get("persistence_error") and not policy["enabled"]:
            with self.importer.lock:
                persistence_error = self.importer._maintenance.pop("persistence_error")
                try:
                    self.importer._save()  # Persist the failure only, never repeat the work.
                except OSError:
                    self.importer._maintenance["persistence_error"] = persistence_error
                    return dict(self.importer._maintenance)
        if policy.get("cohort_policy") and state.get("status") in {
            "completed", "up_to_date", "cache_over_budget",
        }:
            if not self._admit_step():
                return dict(self.importer._maintenance)
            from stpd.p1.data import digest as content_digest
            from stpd.p1.data import json_bytes

            from .local_recording_import import _selected_curation_owner, _selected_store
            from .p1_cohort_intake import load_policy, refresh_cohort
            from .p1_data_preparation import discover_p1_projections

            owner = _selected_curation_owner(self.importer.config)
            if owner is None:
                raise BoundaryError("p1_maintenance", "curation_owner_required")
            store, _ = _selected_store(self.importer.config, require_curation=False)
            path = Path(policy["cohort_policy"])
            inputs = [load_policy(path), discover_p1_projections(store)]
            signature = content_digest(json_bytes([*inputs, _ledger_stamp(owner.path)]))
            if signature not in {state.get("cohort_signature"),
                                 state.get("cohort_failed_signature")}:
                if not self._admit_step():
                    return dict(self.importer._maintenance)
                with self.importer.lock:
                    self.importer._maintenance.update(status="refreshing_cohort")
                    self.importer._save()
                self._observe((state.get("status"), state.get("error_code")))
                # Parsing/cache work never holds the importer's status/control lock.
                try:
                    summary = refresh_cohort(self.importer.config, path,
                                             previous_N=state.get("cohort_N"))
                except Exception as error:
                    # A recount is a replaceable view, not an import/Store write.
                    # Preserve the last complete cohort, report the failure, and
                    # continue admitting/preserving new closed sources. Never
                    # silently drop a projection to manufacture a new cohort.
                    logging.getLogger(__name__).exception("Recording cohort refresh failed")
                    with self.importer.lock:
                        self.importer._maintenance.update(
                            cohort_error=getattr(error, "code", type(error).__name__),
                            cohort_error_at=datetime.now(UTC).isoformat(),
                            cohort_failed_signature=signature, status=state["status"],
                        )
                        self.importer._save()
                    return dict(self.importer._maintenance)
                with self.importer.lock:
                    self.importer._maintenance.update(
                        cohort_signature=signature, cohort_N=summary["N"],
                        cohort_configuration_id=summary["configuration_id"],
                        cohort_updated_at=summary["updated_at"], status=state["status"],
                    )
                    for key in ("cohort_error", "cohort_error_at", "cohort_failed_signature"):
                        self.importer._maintenance.pop(key, None)
                    last = self.importer._maintenance.get("last_intake")
                    if last is not None:
                        last.update(summary_updated_at=summary["updated_at"],
                                    sealed_to_summary_seconds=(
                                        datetime.fromisoformat(summary["updated_at"]) -
                                        datetime.fromisoformat(last["closed_at"])).total_seconds())
                    self.importer._save()
        # Optional diagnostics consume already admitted originals, serially after Q.
        from .recorded_managed_replay import tick as replay_tick

        replay_tick(self)
        return dict(self.importer._maintenance)

    def _admit_step(self) -> bool:
        """Recheck the original game owner immediately before each new heavy step.

        Work already admitted finishes normally. This is an admission observation,
        not a game-start interlock or permission to interrupt an unknown operation.
        """
        with self.importer.lock:
            state = self.importer._maintenance
            if not state["policy"]["enabled"] or self.stop.is_set():
                return False
            catalog = self.importer.catalog.read()
            if catalog.get("status") == "ready" and catalog.get("game_running") is False:
                return True
            state.update(
                status=("waiting_for_game_exit" if catalog.get("status") == "ready"
                        else "unavailable"),
                error_code=(None if catalog.get("status") == "ready"
                            else catalog.get("error_code", "catalog_unavailable")),
            )
            self.importer._save()
            return False

    def _tick(self) -> dict[str, Any]:
        from .collection_tool_registration import current_collection_tool
        from .local_recording_import import _selected_store
        from .p1_recording_store import _matching, projection_format

        importer = self.importer
        with importer.lock:
            state = importer._maintenance
            policy = state["policy"]
            if not policy["enabled"]:
                # Metadata-only inventory remains visible while dispatch is off.
                # No Store/pack/derive/cloud work is admitted on this path.
                if importer.catalog is not None:
                    catalog = importer.catalog.read()
                    self._pending(catalog)
                    state["catalog_status"] = catalog.get("status")
                return dict(state)
            operation = importer.operation
            if importer.thread is not None and importer.thread.is_alive():
                state["status"] = "operation_in_progress"
                return dict(state)
            if operation.get("status") in {
                "pending",
                "publication_unknown",
                "interrupted_unknown",
                "published_index_unavailable",
            }:
                if importer.catalog is not None:
                    self._pending(importer.catalog.read())
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
            if (active is not None and policy.get("scope") is not None
                    and "scope_facts" in active
                    and not _in_scope(active["scope_facts"], policy["scope"])):
                state.update(active=None, status="excluded_by_scope", error_code=None)
                importer._save()
                return dict(state)
            # Only observe Close metadata during play. Every new heavy dispatch
            # (pack, derive, remote preservation, cooling and cohort refresh) waits
            # for the owner to positively report that the game has exited.
            catalog = importer.catalog.read()
            state["space"] = catalog.get("space")
            state["space_observed_at"] = catalog.get("observed_at")
            self._pending(catalog)
            completed = state.setdefault("completed_candidates", [])
            if catalog.get("status") != "ready":
                state.update(status="unavailable",
                             error_code=catalog.get("error_code", "catalog_unavailable"),
                             owner_probe=catalog.get("owner_probe"))
                importer._save()
                return dict(state)
            state.pop("owner_probe", None)
            if catalog.get("game_running") is not False:
                state.update(status="waiting_for_game_exit", error_code=None)
                importer._save()
                return dict(state)
            if min(shutil.disk_usage(importer.config.state_dir).free,
                   (catalog.get("space") or {}).get("free_bytes", 2**63)) < 15 * 1024**3:
                state.update(status="low_space", error_code="fifteen_GiB_free_required")
                importer._save()
                return dict(state)
            if active is not None and policy.get("scope") is not None:
                if "scope_facts" not in active:
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
            for mixed in catalog.get("candidates", []):
                if mixed.get("recording_kind") != "mixed_run" or mixed["candidate_id"] in completed:
                    continue
                if self.mixed_runs is None or not self._admit_step():
                    continue
                try:
                    candidate = importer.catalog.candidate(mixed["candidate_id"])
                    if candidate is None:
                        raise BoundaryError("mixed_run", "candidate_unavailable")
                    if (not mixed.get("mixed_sealed")
                            and not self.mixed_runs.prepare_for_intake(candidate)):
                        continue
                    result = ({"artifact_id": mixed["mixed_artifact_id"], "status": "sealed"}
                              if mixed.get("mixed_sealed") else self.mixed_runs.seal(
                                  candidate))
                    if result.get("status") != "sealed":
                        continue
                    state.setdefault("mixed_sessions", {})[mixed["mixed_run_id"]] = {
                        "source_artifact_id": result["artifact_id"],
                        "session_id": mixed["session_id"], "recording_kind": "mixed_run",
                        "training_admission": "blocked_pending_user_policy",
                        "score_eligible": False}
                    completed.append(mixed["candidate_id"])
                    importer._save()
                except (BoundaryError, OSError, ValueError) as error:
                    state.setdefault("mixed_failures", {})[mixed["mixed_run_id"]] = getattr(
                        error, "code", type(error).__name__)
                    importer._save()
            if policy.get("cohort_policy"):
                if not self._admit_step():
                    return dict(state)
                from .p1_cohort_intake import load_policy, select_versions
                from .p1_data_preparation import discover_p1_projections

                intake_policy = load_policy(Path(policy["cohort_policy"]))
                _, release = current_collection_tool(importer.config)
                if release not in intake_policy["tool_release_priority"]:
                    raise BoundaryError("p1_maintenance", "unapproved_tool_release:" + release)
                select_versions(store, intake_policy, discover_p1_projections(store))
            if active is None:
                candidates = [
                    c
                    for c in catalog["candidates"]
                    if c.get("recording_type") == "p1_native"
                    and c["candidate_id"] not in completed
                    and c.get("recording_kind") != "mixed_run"
                    and _in_scope(c, policy.get("scope"))
                ]
                if not candidates:
                    if policy.get("cool_recent"):
                        sessions = [s for s in state.get("sessions", {}).values()
                                    if _in_scope(s["scope_facts"], policy.get("scope"))]
                        sessions.sort(key=lambda s: (
                            s["scope_facts"].get("closed_at", ""), s["session_id"],
                        ))
                        for session in sessions[:-2]:
                            if session.get("expanded_state", "hot") == "hot":
                                if not self._admit_step():
                                    return dict(state)
                                importer.start_p1_storage(
                                    "cool", session["source_artifact_id"], policy["peer"],
                                )
                                state.update(status="cooling", error_code=None)
                                importer._save()
                                return dict(state)
                    cache_policy = getattr(store, "_cache_policy", None)
                    if cache_policy is not None:
                        from spireagent.storage.cache import cache_usage

                        usage = cache_usage(store)
                        signature = [usage, len(store.manifest_ids()), cache_policy]
                        retry_after = state.get("cache_retry_after")
                        retry_due = (isinstance(retry_after, (int, float))
                                     and time.time() >= retry_after)
                        if (usage > cache_policy["max_bytes"] and (
                                state.get("cache_trim_signature") != signature or retry_due)):
                            recent = sorted(state.get("sessions", {}).values(),
                                            key=lambda row: row["scope_facts"].get("closed_at", ""))
                            protected = tuple(row["source_artifact_id"] for row in recent[-2:])
                            if not self._admit_step():
                                return dict(state)
                            state["cache_trim_signature"] = signature
                            importer.start_cache_trim(protected)
                            state.update(status="cache_trimming", error_code=None)
                            importer._save()
                            return dict(state)
                        if usage > cache_policy["max_bytes"]:
                            state.update(status="cache_over_budget", error_code=None)
                            importer._save()
                            return dict(state)
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
                # The catalog binds candidate IDs to the currently registered Tool.
                # A Tool upgrade must not rederive an identical, fully completed
                # original. _existing_candidate above still checks all seal hashes
                # and recording identity; policy changes clear completed IDs.
                previous = state.get("sessions", {}).get(source, {})
                previous_scope = previous.get("scope_facts", {})
                if (source is not None
                        and previous.get("source_artifact_id") == source
                        and previous.get("session_id") == candidate.get("session_id")
                        and previous.get("timeline_id") == candidate.get("timeline_id")
                        and previous_scope.get("candidate_id") in completed
                        and _in_scope(previous_scope, policy.get("scope"))):
                    completed.append(identity)
                    state.update(status="completed", error_code=None)
                    self._pending(catalog)
                    importer._save()
                    return dict(state)  # Keep original last_intake and its timings.
                if source is None and not self._admit_step():
                    return dict(state)
                active = {
                    "candidate_id": identity,
                    "source_artifact_id": source,
                    "started_at": datetime.now(UTC).isoformat(),
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
                    if not self._admit_step():
                        return dict(state)
                    importer.start_p1_storage("preserve", source, policy["peer"])
                    return dict(state)
            projection = policy["projection_id"]
            original = store.get_manifest(source).parameters.value()
            run_ids = sorted({row["run_id"] for row in original["run_journal"]
                              if row.get("run_id") not in {None, "run-unassigned"}})
            if projection is not None:
                if not run_ids and original["close"]["accepted_count"]:
                    raise BoundaryError("p1_maintenance", "original_run_identity_required")
                _, release = current_collection_tool(importer.config)
                for run_id in run_ids:
                    existing = _matching(
                        store,
                        projection_format(projection)[0],
                        source_artifact_id=source,
                        tool_release_id=release,
                        projection_id=projection,
                        run_id=run_id,
                    )
                    if existing is None:
                        if not self._admit_step():
                            return dict(state)
                        importer.start_p1_derivation(source, projection, run_id=run_id)
                        return dict(state)
                    if policy["peer"] is not None and not any(
                        row["artifact_id"] == existing.artifact_id
                        and row["peer"] == policy["peer"]
                        and row["storage_action"] == "preserve"
                        and row["storage_receipt"].get("full_readback") is True
                        for row in importer._storage_receipts.values()
                    ):
                        if not self._admit_step():
                            return dict(state)
                        importer.start_p1_storage("preserve", existing.artifact_id, policy["peer"])
                        return dict(state)
            state.setdefault("completed_candidates", []).append(active["candidate_id"])
            state.setdefault("sessions", {}).setdefault(source, {
                "source_artifact_id": source,
                "session_id": original["session_id"], "timeline_id": original["timeline_id"],
                "run_ids": run_ids,
                "scope_facts": active["scope_facts"], "expanded_state": "hot",
            })
            state.update(active=None, status="completed", error_code=None)
            closed_at = active["scope_facts"].get("closed_at")
            if closed_at:
                state["last_intake"] = {
                    "source_artifact_id": source, "closed_at": closed_at,
                    "completed_at": datetime.now(UTC).isoformat(),
                    "sealed_to_complete_seconds": (
                        datetime.now(UTC) - datetime.fromisoformat(closed_at)).total_seconds(),
                    "processing_seconds": (
                        datetime.now(UTC) - datetime.fromisoformat(
                            active.get("started_at", datetime.now(UTC).isoformat()))
                    ).total_seconds(),
                }
            importer._save()
            return dict(state)

    def _pending(self, catalog: dict[str, Any]) -> None:
        state = self.importer._maintenance
        state["catalog_status"] = catalog.get("status")
        if catalog.get("status") != "ready":
            return  # Keep the last observed queue, explicitly labelled unavailable.
        state["queue_checked_at"] = catalog.get("observed_at") or datetime.now(UTC).isoformat()
        completed = state.setdefault("completed_candidates", [])
        state["pending_candidates"] = [
            {k: c[k] for k in ("candidate_id", "session_id", "closed_at") if k in c}
            for c in catalog.get("candidates", [])
            if c.get("recording_type") == "p1_native"
            and c.get("recording_kind") != "mixed_run"
            and c["candidate_id"] not in completed and _in_scope(c, state["policy"].get("scope"))
        ]
