"""Cool only the current completed evaluation's proven R2 duplicate."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sts2_platform_evidence import DirectoryTransferManifest

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.config import open_peer
from spireagent.storage.local import LocalBlobStore

from .global_evidence_store import TYPE_ID, materialize_global_evidence
from .native_unknown import no_handles

if TYPE_CHECKING:
    from .developer_server import Application


def cool_completed(app: Application, run_id: str) -> dict[str, Any]:
    # Admission stays held during restore/check/delete: no new local Actor may
    # acquire this run between the final handle census and cooling its copy.
    with app.models.lock, app.local_recording_import.lock:
        app.reference_evaluation.check_access("creation")
        context = app.models.stopped_agent_evidence_context(run_id)
        if app.models.thread is not None and app.models.thread.is_alive():
            raise BoundaryError("evaluation_cache", "actor_worker_active")
        receipt = app.local_recording_import.status()
        readback = receipt.get("peer_readback") or {}
        if (receipt.get("status") != "completed" or receipt.get("run_id") != run_id
                or readback.get("run_id") != run_id
                or readback.get("full_readback") is not True
                or readback.get("typed_verification") != "pass"):
            raise BoundaryError("evaluation_cache", "original_R2_readback_required")
        directory = Path(context["directory"])
        root = app.models.directory / "agent-runs"
        if (directory.parent != root or directory.is_symlink()
                or directory.resolve() != directory or not directory.is_dir()
                or any(p.is_symlink() for p in directory.rglob("*"))):
            raise BoundaryError("evaluation_cache", "unsafe_original_directory")
        no_handles(directory)
        scratch = app.config.state_dir / "evaluation-cache-readback"
        scratch.mkdir(exist_ok=True)
        artifact_id = receipt["artifact_id"]
        with materialize_global_evidence(
            open_peer(receipt["peer"]), artifact_id,
            expected=context["expected"], scratch=scratch,
        ) as restored:
            if restored.run_id != run_id:
                raise BoundaryError("evaluation_cache", "restored_run_changed")
            original = DirectoryTransferManifest.from_directory(
                directory, content_id=restored.content_id, artifact_type=TYPE_ID)
            recovered = DirectoryTransferManifest.from_directory(
                restored.directory, content_id=restored.content_id, artifact_type=TYPE_ID)
            if original != recovered:
                raise BoundaryError("evaluation_cache", "restored_inventory_changed")
            no_handles(directory)
            result = {"schema": "spireagent/evaluation-cache-cooling-v1", "run_id": run_id,
                      "artifact_id": artifact_id, "content_id": restored.content_id,
                      "typed_empty_directory_readback": True, "full_inventory_equal": True,
                      "files": len(original.files),
                      "bytes": sum(item.bytes for item in original.files),
                      "status": "verified_duplicate_cooling_authorized"}
            store = LocalBlobStore(app.config.state_dir / "evaluation-cache-receipts")
            store.put_if_absent(run_id + ".json", json_bytes(result))
            if store.get(run_id + ".json") != json_bytes(result):
                raise BoundaryError("evaluation_cache", "cooling_receipt_unconfirmed")
            shutil.rmtree(directory)
        return {**result, "status": "verified_duplicate_cooled"}
