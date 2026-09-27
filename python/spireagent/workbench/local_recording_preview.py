"""Explicit, read-only preview of one locally verified Human bundle artifact.

This rebuildable view is not a Dataset, research admission, Human uniqueness
proof, complete trajectory, or training authorization.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sts2_platform_evidence import (
    DirectoryTransferManifest,
    HumanSessionBundleV3,
    verify_human_session_bundle,
)

from spireagent.hub.uploads import MAX_ARCHIVE, transfer_from_json, unpack
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.workbench.local_recording_import import EVIDENCE_SCHEMA
from spireagent.workbench.local_workspace import LocalWorkspace

SCHEMA = "stpd/local-recording-sample-preview-v1"
MAX_TRANSFER = 16 * 1024 * 1024


def preview_artifact(workspace: LocalWorkspace, artifact_id: str) -> dict[str, Any]:
    """Recheck exact immutable bytes and return only verified aggregate facts."""
    digest(artifact_id, "local_preview.artifact_id")
    manifest = workspace.store.get_manifest(artifact_id)
    parameters = manifest.parameters.value()
    if (manifest.kind != "evidence" or manifest.parents
            or parameters.get("schema") != EVIDENCE_SCHEMA
            or parameters.get("disposition") != "locally_verified"
            or parameters.get("research_admission") != "not_evaluated"
            or parameters.get("human_origin_attested") is not True
            or parameters.get("hub_receipt") is not None
            or {item.role for item in manifest.payloads} != {"archive", "transfer"}
            or len(manifest.payloads) != 2):
        raise BoundaryError("local_preview", "not_local_verified_bundle")
    payloads = {item.role: item for item in manifest.payloads}
    if (payloads["archive"].size > MAX_ARCHIVE or payloads["archive"].size == 0
            or payloads["transfer"].size > MAX_TRANSFER or payloads["transfer"].size == 0
            or payloads["archive"].media_type != "application/gzip"
            or payloads["transfer"].media_type != "application/json"):
        raise BoundaryError("local_preview", "payload_size_or_type_invalid")
    raw_transfer = b"".join(workspace.store.read_payload(payloads["transfer"]))
    try:
        transfer_json = json.loads(raw_transfer)
        transfer = transfer_from_json(transfer_json)
    except (ValueError, TypeError, KeyError) as error:
        raise BoundaryError("local_preview", "transfer_invalid") from error
    if (raw_transfer != json_bytes(transfer.to_dict())
            or parameters.get("transfer_manifest_sha256") != transfer.manifest_sha256
            or parameters.get("content_id") != transfer.content_id):
        raise BoundaryError("local_preview", "transfer_identity_mismatch")
    with tempfile.TemporaryDirectory(prefix="stpd-local-preview-") as name:
        temporary = Path(name)
        archive = temporary / "bundle.tar.gz"
        with archive.open("xb") as target:
            for chunk in workspace.store.read_payload(payloads["archive"]):
                target.write(chunk)
        extracted = temporary / "extracted"
        extracted.mkdir()
        unpack(archive, extracted, transfer)
        verification = verify_human_session_bundle(extracted)
        if not verification.passed:
            raise BoundaryError("local_preview", "typed_bundle_verification_failed")
        bundle = verification.require_value()
        for field, relative in (("manifest_sha256", "raw/recording-manifest.json"),
                                ("close_sha256", "raw/session-close-receipt.json")):
            path = extracted / relative
            if (not path.is_file() or path.stat().st_size > MAX_TRANSFER
                    or parameters.get(field) != hashlib.sha256(path.read_bytes()).hexdigest()):
                raise BoundaryError("local_preview", "recording_source_hash_mismatch")
        if (bundle.bundle_content_id != transfer.content_id
                or bundle.session_id != parameters.get("session_id")
                or getattr(bundle, "timeline_id", None) != parameters.get("timeline_id")
                or bundle.worker_id != parameters.get("worker_id")
                or bundle.campaign_id != parameters.get("campaign_id")
                or DirectoryTransferManifest.from_directory(
                    extracted, content_id=transfer.content_id,
                    artifact_type="human-session-bundle",
                ) != transfer):
            raise BoundaryError("local_preview", "bundle_identity_mismatch")
        if not isinstance(bundle, HumanSessionBundleV3):
            return {"schema": SCHEMA, "artifact_id": artifact_id,
                    "availability": "archival_format", "available_types": [],
                    "human_input_labels": None, "canonical_decisions": None,
                    "independent_run_qualification": "unknown",
                    "research_admission": "not_evaluated"}
        dispositions = Counter(str(row["disposition"]) for row in bundle.human_text_inputs)
        exclusions = Counter(
            f"{row['disposition']}:{row['reason_code']}"
            for row in bundle.human_text_inputs if row["disposition"] != "accepted_input"
        )
        counts = bundle.summary["counts"]
        decision_exclusions = {
            key: counts[key] for key in (
                "cancelled", "aborted", "real_failures", "unsupported",
                "diagnostics", "unresolved",
            ) if counts.get(key) is not None
        }
        accepted = dispositions["accepted_input"]
        canonical = bundle.canonical_count
        return {
            "schema": SCHEMA, "artifact_id": artifact_id, "availability": "available",
            "available_types": (["human_input_label"] if accepted else [])
                               + (["canonical_decision"] if canonical else []),
            "human_input_labels": accepted, "canonical_decisions": canonical,
            "human_input_total": len(bundle.human_text_inputs),
            "human_input_exclusions": dict(sorted(exclusions.items())),
            "decision_exclusions": decision_exclusions,
            "run_ids_observed": len(bundle.run_ids),
            "independent_run_qualification": (
                "insufficient_canonical_decisions" if not canonical else "unknown"
            ),
            "research_admission": "not_evaluated",
        }


class LocalRecordingPreview:
    """One bounded background read; refresh and restart never trigger projection."""

    def __init__(self, workspace: Callable[[], LocalWorkspace | None]) -> None:
        self.workspace = workspace
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.operation: dict[str, Any] = {"schema": SCHEMA, "status": "idle"}

    def status(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.operation)

    def start(self, artifact_id: object) -> dict[str, Any]:
        identity = digest(artifact_id, "local_preview.artifact_id")
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                if self.operation.get("artifact_id") == identity:
                    return self.status()
                raise BoundaryError("local_preview", "preview_in_progress")
            selected = self.workspace()
            if selected is None:
                raise BoundaryError("local_preview", "workspace_required")
            self.operation = {"schema": SCHEMA, "status": "pending", "artifact_id": identity}
            self.thread = threading.Thread(
                target=self._run, args=(selected, identity), daemon=True,
            )
            self.thread.start()
            return self.status()

    def _run(self, workspace: LocalWorkspace, artifact_id: str) -> None:
        try:
            result = preview_artifact(workspace, artifact_id)
            result["status"] = "completed"
        except Exception as error:
            code = error.code if isinstance(error, BoundaryError) else "preview_failed"
            result = {"schema": SCHEMA, "status": "failed", "artifact_id": artifact_id,
                      "error_code": code}
        with self.lock:
            self.operation = result
