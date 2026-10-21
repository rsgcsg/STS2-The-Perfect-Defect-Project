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
    decoded = decode_json(path.read_bytes())
    if isinstance(decoded, dict) and "peer" in decoded and "peers" not in decoded:
        decoded = {**decoded, "peers": [decoded["peer"]]}
        del decoded["peer"]
    value = object_fields(decoded, {"schema", "peers", "max_bytes", "retain_recent"}, "store_cache")
    if (value["schema"] != SCHEMA or not isinstance(value["peers"], list)
            or not 1 <= len(value["peers"]) <= 2
            or any(not isinstance(peer, dict) for peer in value["peers"])
            or type(value["max_bytes"]) is not int
            or value["max_bytes"] < 0 or type(value["retain_recent"]) is not int
            or value["retain_recent"] < 2):
        raise StoreError("invalid_cache_policy")
    for peer in value["peers"]:
        if peer.get("kind") == "local" and (
                not isinstance(peer.get("directory"), str)
                or not Path(peer["directory"]).is_absolute()
                or Path(peer["directory"]).resolve() == path.parent.resolve()):
            raise StoreError("distinct_absolute_cache_peer_required")
        # Validate shape without contacting the network (S3 construction is lazy in recovery).
        if (peer.get("kind") == "local" and set(peer) != {"kind", "directory"}
                or peer.get("kind") == "s3" and set(peer) != {
                    "kind", "bucket", "endpoint", "prefix", "region"}
                or peer.get("kind") not in {"local", "s3"}):
            raise StoreError("invalid_cache_peer")
        if peer.get("kind") == "s3":
            if (any(not isinstance(peer.get(key), str) for key in ("bucket", "prefix", "region"))
                    or peer.get("endpoint") is not None
                    and not isinstance(peer["endpoint"], str)):
                raise StoreError("invalid_cache_peer")
            from spireagent.storage.s3 import S3Config

            S3Config(**{key: item for key, item in peer.items() if key != "kind"})
    return value


def cache_usage(store: Any) -> int:
    from spireagent.storage.local import LocalBlobStore

    if not isinstance(store.blobs, LocalBlobStore):
        raise StoreError("local_cache_required")
    return sum((store.blobs.root / key).stat().st_size
               for key in store.blobs.keys("objects/sha256/"))


def configure_cache(store: Any, peer: str | dict[str, Any], *, max_bytes: int,
                    additional_peer: str | dict[str, Any] | None = None,
                    retain_recent: int = 2) -> dict[str, Any]:
    from spireagent.storage.local import LocalBlobStore

    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.readonly:
        raise StoreError("writable_local_cache_required")
    root = store.blobs.root
    binding = peer_binding(peer) if isinstance(peer, str) else dict(peer)
    bindings = [binding]
    if additional_peer is not None:
        bindings.append(peer_binding(additional_peer) if isinstance(additional_peer, str)
                        else dict(additional_peer))
    value = {"schema": SCHEMA, "peers": bindings, "max_bytes": max_bytes,
             "retain_recent": retain_recent}
    descriptor, name = tempfile.mkstemp(prefix=".pending-cache-", dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        read_policy(temporary)
        lease_root = root / ".cache-leases"
        lease_root.mkdir(exist_ok=True)
        for identity in store.manifest_ids():
            for payload in store.get_manifest(identity).payloads:
                (lease_root / (payload.sha256 + ".lock")).touch(exist_ok=True)
        os.replace(temporary, root / "cache-policy.json")
    finally:
        temporary.unlink(missing_ok=True)
    store._cache_policy, store._peers = value, None
    return value


def trim_cache(store: Any, *, protected: tuple[str, ...] = ()) -> dict[str, Any]:
    """Remove only remote-verified old chunks; retain immutable identities/indexes.

    Inventory/remote readback happens before the short per-payload unlink leases.
    Every artifact outside this candidate set, recent artifact and explicit pin
    protects its shared chunks. An active reader prevents all involved deletion.
    """
    import hashlib
    from contextlib import ExitStack

    from spireagent.instance_lock import instance_lock
    from spireagent.json_boundary import BoundaryError, digest
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import PAYLOAD_SCHEMA

    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.readonly:
        raise StoreError("writable_local_cache_required")
    policy = store._cache_policy
    if policy is None:
        raise StoreError("explicit_cache_policy_required")
    peers = store.cache_peers()
    if not peers:
        raise StoreError("explicit_cache_peer_required")
    root = store.blobs.root
    identities = sorted(store.manifest_ids(), key=lambda identity:
                        (root / "manifests" / (identity + ".json")).stat().st_mtime_ns,
                        reverse=True)
    retained = set(protected) | set(identities[:policy["retain_recent"]])
    manifests = {identity: store.get_manifest(identity) for identity in identities}
    retained.update(identity for identity, manifest in manifests.items()
                    if manifest.kind in {"model", "checkpoint", "run_result"})
    # Metadata ancestry stays local. Parent payloads recover independently by ID;
    # pinning a model must not pin its entire historical input corpus in RAM/disk.
    if not retained <= manifests.keys():
        raise StoreError("cache_protected_manifest_missing")
    retained_payloads = {p.sha256 for identity in retained for p in manifests[identity].payloads}
    payloads = {p.sha256: p for m in manifests.values() for p in m.payloads}
    chunks: dict[str, set[str]] = {}
    for identity, payload in payloads.items():
        raw, _ = store._get(f"payload-indexes/v1/{identity}.json")
        index = decode_json(raw)
        # The ordinary reader verifies the complete local or peer bytes below;
        # this inventory only chooses keys, never supplies semantic evidence.
        if (not isinstance(index, dict) or index.get("schema") != PAYLOAD_SCHEMA
                or index.get("sha256") != identity or index.get("size") != payload.size
                or not isinstance(index.get("chunks"), list)):
            raise StoreError("payload_index_mismatch")
        for chunk in index["chunks"]:
            key = chunk.get("sha256") if isinstance(chunk, dict) else None
            if not isinstance(key, str):
                raise StoreError("payload_index_mismatch")
            chunks.setdefault(digest(key, "cache.chunk"), set()).add(identity)
    files = {key: root / "objects" / "sha256" / key for key in chunks}
    all_objects = [root / key for key in store.blobs.keys("objects/sha256/")]
    before = sum(p.stat().st_size for p in all_objects if p.is_file())
    remaining, removed, busy, unverified = before, [], [], []
    # Prefer oldest payload files, preserving shared chunks of every retained payload.
    candidates = sorted((key for key, refs in chunks.items() if not refs & retained_payloads),
                        key=lambda key: files[key].stat().st_mtime_ns if files[key].exists() else 0)
    verified: set[tuple[int, str]] = set()
    remote_manifests: dict[int, set[str]] = {index: set() for index in range(len(peers))}

    def verify_manifest(peer: Any, peer_index: int, identity: str) -> None:
        if identity in remote_manifests[peer_index]:
            return
        expected = manifests[identity]
        if peer.get_manifest(identity) != expected:
            raise StoreError("cache_remote_manifest_mismatch")
        remote_manifests[peer_index].add(identity)
        for parent in expected.parents:
            verify_manifest(peer, peer_index, parent.artifact_id)

    for key in candidates:
        if remaining <= policy["max_bytes"]:
            break
        path = files[key]
        if not path.is_file():
            continue
        refs = chunks[key]
        selected_peer = None
        for peer_index, peer in enumerate(peers):
            try:
                for identity, manifest in manifests.items():
                    if any(p.sha256 in refs for p in manifest.payloads):
                        verify_manifest(peer, peer_index, identity)
                for identity in sorted(refs):
                    verification = (peer_index, identity)
                    if verification not in verified:
                        for _ in peer.read_payload(payloads[identity]):
                            pass
                        verified.add(verification)
                selected_peer = peer
                break
            except StoreError as error:
                if error.code != "object_not_found":
                    raise
            except (BoundaryError, OSError):
                raise
        if selected_peer is None:
            unverified.append(key)
            continue
        remote_raw = selected_peer.blobs.get("objects/sha256/" + key)
        if hashlib.sha256(remote_raw).hexdigest() != key:
            raise StoreError("payload_chunk_integrity_failure")
        try:
            with ExitStack() as stack:
                for identity in sorted(refs):
                    lock = root / ".cache-leases" / (identity + ".lock")
                    lock.parent.mkdir(exist_ok=True)
                    lock.touch(exist_ok=True)
                    stack.enter_context(instance_lock(lock, create=False))
                # Compare remote bytes immediately before unlink; corruption never cools.
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != key:
                    raise StoreError("payload_chunk_integrity_failure")
                if remote_raw != raw:
                    raise StoreError("cache_remote_chunk_mismatch")
                path.unlink()
                remaining -= len(raw)
                removed.append(key)
        except BoundaryError as error:
            if error.code != "already_running":
                raise
            busy.append(key)
    return {"schema": "spireagent/store-cache-trim-1", "before_bytes": before,
            "after_bytes": remaining, "max_bytes": policy["max_bytes"],
            "within_budget": remaining <= policy["max_bytes"], "removed_chunks": removed,
            "active_chunks": busy, "unverified_chunks": unverified,
            "retained_artifact_ids": sorted(retained), "manifests_and_indexes_retained": True,
            "unmanaged_object_count": sum(p.name not in chunks for p in all_objects)}
