"""Explicit, read-only preview of one locally verified Human bundle artifact.

This rebuildable view is not a Dataset, research admission, Human uniqueness
proof, complete trajectory, or training authorization.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from typing import Any

from sts2_platform_evidence import HumanSessionBundleV3

from spireagent.json_boundary import BoundaryError, digest
from spireagent.local_verified_bundle import verified_local_bundle
from spireagent.workbench.local_workspace import LocalWorkspace

SCHEMA = "stpd/local-recording-sample-preview-v1"


def preview_artifact(workspace: LocalWorkspace, artifact_id: str) -> dict[str, Any]:
    """Recheck exact immutable bytes and return only verified aggregate facts."""
    digest(artifact_id, "local_preview.artifact_id")
    manifest = workspace.store.get_manifest(artifact_id)
    with verified_local_bundle(workspace.store, manifest) as verified:
        bundle = verified.bundle
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
