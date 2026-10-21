"""Local working-set policy for the existing immutable ArtifactStore."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from spireagent.json_boundary import decode_json, json_bytes, object_fields
from spireagent.storage.blobs import StoreError
from spireagent.storage.config import peer_binding

SCHEMA = "spireagent/store-cache-policy-1"


def read_policy(path: Path) -> dict[str, Any]:
    value = object_fields(decode_json(path.read_bytes()),
                          {"schema", "peer", "max_bytes", "retain_recent"}, "store_cache")
    if (value["schema"] != SCHEMA or not isinstance(value["peer"], dict)
            or type(value["max_bytes"]) is not int
            or value["max_bytes"] < 0 or type(value["retain_recent"]) is not int
            or value["retain_recent"] < 2):
        raise StoreError("invalid_cache_policy")
    peer = value["peer"]
    if peer.get("kind") == "local" and (
            not Path(peer.get("directory", "")).is_absolute()
            or Path(peer["directory"]).resolve() == path.parent.resolve()):
        raise StoreError("distinct_absolute_cache_peer_required")
    # Validate shape without contacting the network (S3 construction is lazy in recovery).
    if (peer.get("kind") == "local" and set(peer) != {"kind", "directory"}
            or peer.get("kind") == "s3" and set(peer) != {
                "kind", "bucket", "endpoint", "prefix", "region"}
            or peer.get("kind") not in {"local", "s3"}):
        raise StoreError("invalid_cache_peer")
    return value


def configure_cache(store: Any, peer: str | dict[str, Any], *, max_bytes: int,
                    retain_recent: int = 2) -> dict[str, Any]:
    from spireagent.storage.local import LocalBlobStore

    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.readonly:
        raise StoreError("writable_local_cache_required")
    root = store.blobs.root
    binding = peer_binding(peer) if isinstance(peer, str) else dict(peer)
    value = {"schema": SCHEMA, "peer": binding, "max_bytes": max_bytes,
             "retain_recent": retain_recent}
    descriptor, name = tempfile.mkstemp(prefix=".pending-cache-", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        read_policy(temporary)
        os.replace(temporary, root / "cache-policy.json")
    finally:
        temporary.unlink(missing_ok=True)
    store._cache_policy, store._peer = value, None
    return value
