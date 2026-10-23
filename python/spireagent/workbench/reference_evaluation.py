"""Application-owned small Reference groups over the existing serial suite.

Only the group thread and the original Model worker may mutate its resources.
The suite report remains the evidence authority; operation.json only navigates it.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.reference_suite import (
    SuiteLimits,
    WorkbenchReferenceClient,
    _Artifacts,
    _clean,
    _read,
    _seal,
    load_manifest,
    run_suite,
)
from stpd.evaluation.suites import diagnostic_reference_manifest

if TYPE_CHECKING:
    from spireagent.workbench.developer_server import Application

SCHEMA = "spireagent/reference-evaluation-operation-v1"
_SCOPE: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "reference_group", default=None)
ACTIVE = {"running", "pause_requested", "paused", "stopping"}


def reject(code: str) -> NoReturn:
    raise BoundaryError("reference_evaluation", code)


class ApplicationReferenceClient(WorkbenchReferenceClient):
    """Original bounded HTTP client, admitted into this Application's group scope."""
    def __init__(self, app: Application) -> None:
        super().__init__(app.config, _execution_scope=app.reference_evaluation._http_nonce)
        if self.identity["application_instance_id"] != app.instance_id:
            reject("application_instance_mismatch")


class ReferenceEvaluationService:
    def __init__(self, app: Application) -> None:
        self.app = app
        self.reservation = object()
        self._http_nonce = secrets.token_hex(32)
        self.root = app.config.state_dir / "reference-evaluations"
        self.path = self.root / "operation.json"
        self.thread: threading.Thread | None = None
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.pause = False
        self.closed = False
        self.operation: dict[str, Any] = {"schema": SCHEMA, "status": "idle"}
        if self.path.exists():
            value = json.loads(self.path.read_bytes())
            if value.get("schema") != SCHEMA:
                reject("operation_recovery_required")
            self.operation = value
            if value.get("status") in ACTIVE:
                # Original files stay unchanged after process loss. No automatic replay.
                self.operation = {**value, "status": "recovery_required"}

    @contextmanager
    def scope(self) -> Iterator[None]:
        token = _SCOPE.set(self.reservation)
        try:
            yield
        finally:
            _SCOPE.reset(token)

    @contextmanager
    def request_scope(self, nonce: str) -> Iterator[None]:
        # Transport-private correlation only. The handler still requires the
        # original local control authentication and every body/owner validator.
        if (len(nonce) != 64 or not nonce.isascii()
                or not secrets.compare_digest(nonce, self._http_nonce)):
            reject("reference_group_scope_invalid")
        with self.scope():
            yield

    def check_access(self, action: str = "creation") -> None:
        """Called under the Model owner lock at actual admission, including Stop."""
        if (self.operation["status"] in ACTIVE | {"recovery_required"}
                and _SCOPE.get() is not self.reservation
                and not (self.operation["status"] == "recovery_required"
                         and action in {"human", "stop", "reconcile"})):
            reject("reference_group_owns_execution_use_group_controls")

    def _save(self) -> None:
        atomic_json(self.path, self.operation)

    def status(self) -> dict[str, Any]:
        with self.lock:
            value = copy.deepcopy(self.operation)
        report = value.get("report_path")
        if report and Path(report).is_file():
            raw = Path(report).read_bytes()
            value["report"] = json.loads(raw)
            value["report_sha256"] = hashlib.sha256(raw).hexdigest()
        value["capabilities"] = {"host": "Reference", "seed_count": {"min": 1, "max": 10},
                                 "display_modes": ["headless", "native-ui"],
                                 "speeds": ["normal", "fast", "instant"],
                                 "pause": "between_finished_sealed_preserved_episodes",
                                 "restart_resume": False}
        return value

    def _validate_selection(self, selection: str, profile: str) -> None:
        from spireagent.policy_files import _object_file
        from spireagent.workbench.local_models import GLOBAL_MODEL_ADAPTERS
        from spireagent.workbench.managed_model_target import managed_manifest

        entry = self.app.models.selection(selection)
        if (entry["adapter"] not in GLOBAL_MODEL_ADAPTERS
                or managed_manifest(_object_file(self.app.models.entry_path(entry, "manifest")))):
            reject("reference_group_requires_native_p1_model_selection")
        self.app.models._run_profile(selection, profile)

    def start(self, body: dict[str, Any]) -> dict[str, Any]:
        required = {"selection_id", "seed_count", "game_version", "run_profile",
                    "display_mode", "speed", "limits"}
        if set(body) not in (required, required | {"evidence_peer"}):
            reject("invalid_reference_group_request")
        group_id = uuid.uuid4().hex
        manifest = diagnostic_reference_manifest(body["game_version"], group_id, body["seed_count"])
        if (not isinstance(body["selection_id"], str) or not body["selection_id"]
                or body["run_profile"] not in {"short", "extended"}
                or body["display_mode"] not in {"headless", "native-ui"}
                or body["speed"] not in {"normal", "fast", "instant"}
                or not isinstance(body["limits"], dict)):
            reject("invalid_reference_group_request")
        self._validate_selection(body["selection_id"], body["run_profile"])
        limits = SuiteLimits(**body["limits"])
        peer = body.get("evidence_peer")
        if peer is None and self.app.config.remote_compute is not None:
            peer = self.app.config.remote_compute.compute_store
        if not isinstance(peer, dict):
            reject("configured_evidence_peer_required")
        from spireagent.storage.config import open_peer

        open_peer(peer)  # Validate the existing binding, without writing or publishing.
        with self.app.models.lock, self.lock, self.app.local_recording_import.lock:
            self.app.check_environment_instance()
            if self.closed or self.operation["status"] in ACTIVE | {"recovery_required"}:
                reject("reference_group_active_or_requires_recovery")
            self.app._admit_native_preparation()
            native = self.app.native_episode_status()
            if native.get("phase") not in {"idle", "closed"}:
                reject("native_episode_must_be_finished")
            self.app.local_recording_import._native_recovery_guard()
            importer = self.app.local_recording_import
            if (importer.status().get("status") == "pending"
                    or importer.thread is not None and importer.thread.is_alive()):
                reject("evidence_operation_in_progress")
            raw = json_bytes(manifest)
            directory = self.root / group_id
            directory.mkdir(parents=True, exist_ok=False)
            manifest_path = directory / "manifest.json"
            manifest_path.write_bytes(raw)
            self.operation = {"schema": SCHEMA, "operation_id": group_id, "status": "running",
                              "request": copy.deepcopy(body), "evidence_peer": copy.deepcopy(peer),
                              "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                              "report_path": str(directory / "attempt" / "report.json")}
            self.reservation = object()
            self._http_nonce = secrets.token_hex(32)
            self.stop.clear()
            self.pause = False
            self._save()
            self.thread = threading.Thread(
                target=self._scoped_run, args=(manifest_path, limits), daemon=True)
            self.thread.start()
        return self.status()

    def _boundary(self, report: dict[str, Any], deadline: float) -> bool:
        while not self.stop.is_set():
            with self.lock:
                if not self.pause:
                    return True
                if self.operation["status"] != "paused":
                    self.operation["status"] = "paused"
                    self._save()
                self.wake.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True  # The suite records its original wall limit.
            self.wake.wait(min(0.25, remaining))
        return False

    def _scoped_run(self, manifest_path: Path, limits: SuiteLimits) -> None:
        with self.scope():
            self._run(manifest_path, limits)

    def _run(self, manifest_path: Path, limits: SuiteLimits) -> None:
        try:
            body = self.operation["request"]
            deadline = time.monotonic() + limits.suite_seconds
            report = run_suite(
                manifest_path, self.operation["manifest_sha256"],
                client=ApplicationReferenceClient(self.app),
                run_dir=manifest_path.parent / "attempt",
                selection=body["selection_id"], run_profile=body["run_profile"], limits=limits,
                max_episodes=body["seed_count"], evidence_peer=self.operation["evidence_peer"],
                display_mode=body["display_mode"], speed=body["speed"],
                should_stop=self.stop.is_set,
                before_episode=lambda report: self._boundary(report, deadline))
            with self.lock:
                self.operation.update(status=("recovery_required" if any(
                    row["status"] == "unknown" or "preservation_error" in row
                    for row in report["episodes"]) else report["status"]),
                    stop_reason=report["stop_reason"])
                self._save()
        except (OSError, ValueError, KeyError, TypeError) as error:
            with self.lock:
                self.operation.update(status="recovery_required", error_code=(
                    error.code if isinstance(error, BoundaryError) else type(error).__name__))
                self._save()

    def reconcile(self, body: dict[str, Any]) -> dict[str, Any]:
        """Retire only this original group after its owners prove closure/readback.

        This observes, never finishes, stops, republishes or rewrites the report.
        """
        if set(body) != {"operation_id", "report_sha256"}:
            reject("invalid_reference_group_reconciliation")
        expected_sha = digest(body["report_sha256"], "reference_evaluation.report")
        with self.app.models.lock, self.lock, self.scope():
            if (body["operation_id"] != self.operation.get("operation_id")
                    or self.operation["status"] != "recovery_required"
                    or self.thread is not None and self.thread.is_alive()):
                reject("reference_group_not_recoverable")
            directory = self.root / digest(body["operation_id"],
                                            "reference_evaluation.operation", length=32)
            report_path = directory / "attempt" / "report.json"
            raw = _read(report_path)
            if hashlib.sha256(raw).hexdigest() != expected_sha:
                reject("original_report_changed")
            report = decode_json(raw)
            if not isinstance(report, dict):
                reject("original_report_invalid")
            _, manifest = load_manifest(directory / "manifest.json",
                                        self.operation["manifest_sha256"])
            if (manifest.get("group_id") != self.operation["operation_id"]
                    or report.get("planned") != manifest["episode_count"]
                    or report.get("selection_id") != self.operation["request"]["selection_id"]):
                reject("original_group_binding_changed")
            rows = [row for row in report["episodes"] if row["status"] != "not_started"]
            if not rows or not rows[-1].get("environment_ref") or not rows[-1].get("request"):
                reject("original_run_reference_required")
            row = copy.deepcopy(rows[-1])
            self.app.models._require_stopped_runtime()
            if (self.app.models.thread is not None and self.app.models.thread.is_alive()):
                reject("original_model_still_active")
            state = self.app.models.status()
            native = self.app.native_episode_status()
            if (state.get("status") != "stopped"
                    or state.get("operation", {}).get("status") == "pending"
                    or native.get("environment_ref") != row["environment_ref"]
                    or native.get("generation_id") != row["generation_id"]
                    or native.get("intent_id") != row["intent_id"]
                    or any(native.get(key) != value
                           for key, value in report["application"].items())):
                reject("original_owners_not_closed_or_changed")
            generated_raw = _read(report_path.parent / row["request"]["path"])
            generated = decode_json(generated_raw)
            if (not isinstance(generated, dict)
                    or hashlib.sha256(generated_raw).hexdigest() != row["request"]["sha256"]):
                # Suite request bytes use canonical JSON with no trailing newline.
                reject("original_generated_request_changed")
            recovery = _Artifacts(directory / ("recovery-" + uuid.uuid4().hex))
            evaluation = _seal(ApplicationReferenceClient(self.app), recovery, row,
                               state, native, generated)
            if not _clean(native, evaluation):
                reject("original_cleanup_or_seal_unproved")
            preservation = self.app.local_recording_import.status()
            run_id = row["run_id"]
            if (preservation.get("status") != "completed"
                    or preservation.get("run_id") != run_id
                    or preservation.get("peer") != self.operation["evidence_peer"]
                    or not isinstance(preservation.get("artifact_id"), str)):
                reject("original_preservation_unproved")
            for key in ("local_readback", "peer_readback"):
                readback = preservation.get(key)
                if (not isinstance(readback, dict) or readback.get("full_readback") is not True
                        or readback.get("typed_verification") != "pass"
                        or readback.get("run_id") != run_id
                        or readback.get("artifact_id") != preservation["artifact_id"]
                        or readback.get("content_id") != evaluation.get("evidence_content_id")):
                    reject("original_full_readback_required")
            if _read(report_path) != raw:
                reject("original_report_changed")
            observation = recovery.put("owner-reconciliation.json", json_bytes({
                "schema": "spireagent/reference-group-recovery-observation-v1",
                "operation_id": self.operation["operation_id"],
                "original_report_sha256": expected_sha, "original_report_preserved": True,
                "original_operation": copy.deepcopy(self.operation),
                "resource": native, "sealed_originals": row,
                "preservation": preservation, "purpose": "retire_group_reservation_only",
                "original_unknown_reclassified": False, "replayed_episodes": 0,
            }))
            previous = copy.deepcopy(self.operation)
            self.operation.update(status="stopped", recovery_observation={
                **observation, "path": str(recovery.root / observation["path"])})
            try:
                self._save()
            except (OSError, ValueError):
                self.operation = previous  # A lost activation acknowledgement stays fenced.
                raise
        return self.status()

    def control(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if (set(body) != {"operation_id"}
                    or body["operation_id"] != self.operation.get("operation_id")):
                reject("reference_group_operation_changed")
            if self.operation["status"] not in ACTIVE:
                reject("reference_group_not_running")
            if action == "pause" and self.operation["status"] in {"running", "pause_requested"}:
                self.pause = True
                self.operation["status"] = "pause_requested"
            elif action == "resume" and self.operation["status"] in {"pause_requested", "paused"}:
                self.pause = False
                self.operation["status"] = "running"
            elif action == "stop":
                self.stop.set()
                self.operation["status"] = "stopping"
            else:
                reject("invalid_reference_group_control")
            self._save()
            self.wake.set()
        return self.status()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.stop.set()
            self.wake.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
