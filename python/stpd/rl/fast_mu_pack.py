"""R7 mu archive, local only: the bytes `MuHandoff.weights` carries and their check.

The learner publishes a temporary mu as one node-cache snapshot directory
(`NodeMuCache`: `identity.json`, `model.tensor-tree`, `model.json`,
`heads.tensor-tree`, `model-manifest.json`, `heads-manifest.json`). An R7 game
needs exactly those bytes later, to rescore its rows on the complete chain, so
the archive keeps the snapshot as one deterministic pack:

    8-byte big-endian header length | canonical JSON header | file bodies

The header is `{"schema", "identity", "files": [{"name", "size", "sha256"}]}`
with files in `FILES` order. `snapshot_of(pack)` recomputes the snapshot
identity from the bytes, exactly as `local_mu.load_behavior_mu` does (model
and heads payload digests plus the numerical settings), so
`DurableMuArchive` refuses any pack that is not that snapshot, on write and on
read. `unpack` restores the directory for a replay that loads through the
ordinary `load_behavior_mu` checks; nothing is repaired.

Production use (lead 11-12): the archive root is on the run's local disk
(never R2), only mu bound by R7-sampled games are written (the assembler
archives only while an `audit_retain_mu` game is open), and `prune` removes
entries together with the checkpoints the run prunes.

Concurrency: every inference service process archives into the same root and
the run prunes from another process. One OS file lock (`<root>/mu/.archive.lock`,
`flock`, released on process death) serializes each archive write and each
whole prune, so a prune never sees half of a re-archive (a CP restore brings a
snapshot back under a new generation while its weights file already exists).

Memory: the pack (~20 MB, one snapshot) rides on every `MuHandoff` the follower
publishes, whether or not an R7 game is open. An inference card broadcasts it to
each service process, and each process's ledger keeps one (its latest): about
N services x 20 MB per card, plus the follower's copy while a service lags, and
roughly twice the bytes per publish broadcast.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from spireagent.instance_lock import instance_lock

from .fast_handoff import DurableMuArchive, HandoffError

PACK_SCHEMA = "stpd/rl-fast-mu-pack-1"
FILES = ("model.tensor-tree", "model.json", "heads.tensor-tree", "model-manifest.json",
         "heads-manifest.json")
NUMERICAL = ("precision", "tf32", "projection", "execution", "adapter", "deduplicate_text")
MAX_HEADER = 1 << 20
LOCK = ".archive.lock"  # inside DurableMuArchive.root; never matches *.json / *.weights


def _sha(raw: bytes | memoryview) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    # Same bytes as stpd.p1.data.json_bytes (the snapshot digest input).
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode()


def snapshot_digest(identity: Mapping[str, Any]) -> str:
    """The learner's snapshot identity: weights, heads and numerical settings."""
    numerical = {key: identity[key] for key in NUMERICAL}
    return _sha(_canonical({"weights": identity["model_sha256"],
                            "heads": identity["heads_sha256"], "numerical": numerical}))


def pack(identity: Mapping[str, Any], bodies: Mapping[str, bytes]) -> bytes:
    if set(bodies) != set(FILES):
        raise HandoffError("mu_pack_files")
    files = [{"name": name, "size": len(bodies[name]), "sha256": _sha(bodies[name])}
             for name in FILES]
    header = _canonical({"schema": PACK_SCHEMA, "identity": dict(identity), "files": files})
    return b"".join([len(header).to_bytes(8, "big"), header,
                     *(bodies[name] for name in FILES)])


def read_snapshot(root: Path) -> bytes:
    """Pack one complete node-cache snapshot directory (`<cache>/<snapshot>`)."""
    if not (root / "COMPLETE").is_file():
        raise HandoffError("mu_pack_snapshot_incomplete")
    identity = json.loads((root / "identity.json").read_bytes())
    return pack(identity, {name: (root / name).read_bytes() for name in FILES})


def node_cache_weights(cache: Any, identity: Mapping[str, Any]) -> bytes:
    """`MuHandoff.weights` for a mu the service loaded from `NodeMuCache`.

    Read under the cache's shared reader lock (`materialize`), the same check the
    load used (identity file equals the grant), so a trim cannot race the read.
    """
    with cache.materialize(dict(identity)) as (root, _head, _heads_raw):
        raw = read_snapshot(Path(root))
    if _split(raw)[0]["identity"] != dict(identity):
        raise HandoffError("mu_pack_identity_changed")
    return raw


def _split(raw: bytes) -> tuple[dict[str, Any], dict[str, memoryview]]:
    view = memoryview(raw)
    if len(view) < 8:
        raise HandoffError("mu_pack_truncated")
    length = int.from_bytes(view[:8], "big")
    if not 0 < length <= MAX_HEADER or len(view) < 8 + length:
        raise HandoffError("mu_pack_truncated")
    try:
        header = json.loads(bytes(view[8:8 + length]))
    except ValueError as error:
        raise HandoffError("mu_pack_header") from error
    if (not isinstance(header, dict) or header.get("schema") != PACK_SCHEMA
            or set(header) != {"schema", "identity", "files"}
            or not isinstance(header["identity"], dict)
            or not isinstance(header["files"], list)
            or [f.get("name") if isinstance(f, dict) else None
                for f in header["files"]] != list(FILES)
            or _canonical(header) != bytes(view[8:8 + length])):
        raise HandoffError("mu_pack_header")
    offset, bodies = 8 + length, {}
    for item in header["files"]:
        size = item.get("size")
        if type(size) is not int or size < 0 or offset + size > len(view):
            raise HandoffError("mu_pack_truncated")
        body = view[offset:offset + size]
        if _sha(body) != item.get("sha256"):
            raise HandoffError("mu_pack_file_digest")
        bodies[item["name"]] = body
        offset += size
    if offset != len(view):
        raise HandoffError("mu_pack_trailing_bytes")
    return header, bodies


def snapshot_of(raw: bytes) -> str:
    """Recompute the snapshot identity from a pack; never trusts the header's claim.

    The model and heads digests are recomputed from their bodies; a pack whose
    identity disagrees with its own bytes is refused.
    """
    header, bodies = _split(raw)
    identity = header["identity"]
    try:
        if (_sha(bodies["model.tensor-tree"]) != identity["model_sha256"]
                or _sha(bodies["heads.tensor-tree"]) != identity["heads_sha256"]):
            raise HandoffError("mu_pack_payload_digest")
        computed = snapshot_digest(identity)
    except (KeyError, TypeError) as error:
        raise HandoffError("mu_pack_identity") from error
    if computed != identity.get("snapshot_sha256"):
        raise HandoffError("mu_pack_identity")
    return computed


def unpack(raw: bytes, cache_root: Path) -> Path:
    """Restore `<cache_root>/<snapshot>` in node-cache layout, for an R7 replay."""
    snapshot = snapshot_of(raw)
    header, bodies = _split(raw)
    target = Path(cache_root) / snapshot
    if target.exists():
        raise HandoffError("mu_pack_target_exists")
    target.mkdir(parents=True, mode=0o700)
    for name in FILES:
        (target / name).write_bytes(bytes(bodies[name]))
    (target / "identity.json").write_bytes(_canonical(header["identity"]))
    (target / "readers.lock").touch()
    (target / "COMPLETE").write_text(snapshot)
    return target


class LocalMuArchive(DurableMuArchive):
    """`DurableMuArchive` whose writes hold the archive's cross-process lock."""

    def __call__(self, handoff: Any) -> None:
        with instance_lock(self.root / LOCK, blocking=True):
            super().__call__(handoff)


def local_mu_archive(root: Path | str) -> LocalMuArchive:
    """The production R7 `mu_archive`: a local directory, checked by `snapshot_of`."""
    return LocalMuArchive(root, snapshot_of=snapshot_of)


def prune(archive: DurableMuArchive, keep: Callable[[dict[str, Any]], bool]) -> list[str]:
    """Remove archived mu that `keep(mu identity)` rejects; return removed file names.

    The run decides the rule (lead 11-12: entries go with the checkpoints it
    prunes, after their R7 replays). A manifest goes first, then the weights of a
    snapshot whose last manifest it was. One unreadable manifest refuses the
    whole call before anything is removed; nothing is deleted blindly. The whole
    call holds the archive lock, so an archive write in any process happens
    entirely before or entirely after it.
    """
    with instance_lock(archive.root / LOCK, blocking=True):
        return _prune(archive, keep)


def _prune(archive: DurableMuArchive, keep: Callable[[dict[str, Any]], bool]) -> list[str]:
    entries: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(archive.root.glob("*.json")):  # all checked before any removal
        try:
            mu = json.loads(path.read_bytes())["mu"]
            snapshot = mu["snapshot_sha256"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise HandoffError("mu_archive_unreadable_manifest") from error
        if not isinstance(snapshot, str) or not path.name.startswith(snapshot + "."):
            raise HandoffError("mu_archive_unreadable_manifest")
        entries.append((path, dict(mu)))
    removed: list[str] = []
    remaining: set[str] = set()
    dropped: set[str] = set()
    for path, mu in entries:
        if keep(mu):
            remaining.add(mu["snapshot_sha256"])
            continue
        os.unlink(path)
        removed.append(path.name)
        dropped.add(mu["snapshot_sha256"])
    # Only weights of a snapshot whose every manifest was removed here. Under the
    # lock no archive write is in progress, so this scan saw every manifest.
    for snapshot in sorted(dropped - remaining):
        weights = archive.root / (snapshot + ".weights")
        if weights.exists():
            os.unlink(weights)
            removed.append(weights.name)
    return removed
