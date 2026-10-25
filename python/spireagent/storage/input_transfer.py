"""Exact referenced ancestry with an explicit compute payload inventory.

This is an availability projection of the existing Store, not preservation of
the whole closure. Raw payload descriptors and every parent ID stay unchanged.
"""
from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Payload

from .blobs import StoreError
from .store import ArtifactStore, ManifestArtifactStore


def copy_input_artifact(
    source: ArtifactStore, target: ManifestArtifactStore, identity: str,
    required_payloads: tuple[Payload, ...], *, scratch: Path | None = None,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Verify each required destination payload once, then publish references.

    Existing bytes are fully hashed, never accepted on object existence alone.
    A partial transfer cannot produce a successful receipt. The caller's trusted
    installed consumer determines the payload inventory; this API grants no use.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise StoreError("input_transfer_concurrency_limit")
    ordered: list[Manifest] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in seen:
            return
        if key in visiting:
            raise StoreError("lineage_cycle")
        if len(seen) + len(visiting) >= 512:
            raise StoreError("lineage_limit")
        visiting.add(key)
        manifest = source.get_manifest(key)
        for parent in manifest.parents:
            visit(parent.artifact_id)
        visiting.remove(key)
        seen.add(key)
        ordered.append(manifest)

    visit(identity)
    available = {p for m in ordered for p in m.payloads}
    required = set(required_payloads)
    if not required <= available:
        raise StoreError("input_payload_outside_lineage")
    payloads = {p.sha256: p for p in required}

    def transfer(payload: Payload) -> tuple[int, bool]:
        try:
            for _ in target.read_payload(payload):
                pass
            return payload.size, False
        except StoreError as error:
            if error.code != "object_not_found":
                raise
        with tempfile.TemporaryFile("w+b", dir=scratch) as handle:
            for chunk in source.read_payload(payload):
                handle.write(chunk)
            handle.seek(0)
            received = target.put_payload(payload.role, handle, payload.media_type)
        if received != payload:
            raise StoreError("transfer_identity_mismatch")
        for _ in target.read_payload(payload):
            pass
        return payload.size, True

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(transfer, payloads.values()))
    for manifest in ordered:
        if target.publish_reference(manifest) != manifest.artifact_id:
            raise StoreError("transfer_manifest_mismatch")
    omitted = {p.sha256: p for p in available if p.sha256 not in payloads}
    return {
        "schema": "spireagent/compute-input-transfer-1", "artifact_id": identity,
        "manifest_count": len(ordered), "payload_count": len(payloads),
        "payload_bytes": sum(size for size, _ in results),
        "copied_payload_count": sum(copied for _, copied in results),
        "copied_payload_bytes": sum(size for size, copied in results if copied),
        "reference_only_payload_count": len(omitted),
        "reference_only_payload_bytes": sum(p.size for p in omitted.values()),
        "required_payload_readback": "verified", "raw_closure_restored": False,
    }
