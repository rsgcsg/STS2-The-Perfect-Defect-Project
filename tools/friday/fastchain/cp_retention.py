"""Checkpoint retention of one resume chain (supervise.py, after each published CP).

Rule (`select`): keep the newest `keep_newest` CPs of the chain, every CP an evaluation
watcher has claimed (`<chain>/eval-*/protected-checkpoints.json`, fast_eval's pruning
contract) and each generation's starting CP, plus the first CP of every
`milestone_hours` window since the chain started; the rest go. `keep_newest = 0` keeps
everything (the default, today's behaviour). When on, `keep_newest >= 2`: the CP a
resume uses is the newest (supervise.newest_checkpoint), and the one before it covers the
moment between a watcher reading the newest record and writing its claim.

What goes (`Deleter`): only the CP's payload chunks under `objects/sha256/`, and only a
chunk no other payload index of the Store names (chunks are content addressed: an
all-zero block or an unchanged tensor region can be shared with another CP or a model).
The manifest, the payload index and the CP record stay, so lineage still resolves the
id; reading a deleted CP's bytes fails loudly with `object_not_found`, never silently.
A failed unlink is the caller's to log and count; it never stops training.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

INDEXES = "payload-indexes/v1/"


def select(records: list[dict[str, Any]], *, keep_newest: int, milestone_hours: float,
           started_at: float, protected: Iterable[str]) -> tuple[list[str], list[str]]:
    """(kept ids, ids to delete) of the chain's CP records, oldest first."""
    ordered = sorted(records, key=lambda r: r["published_at"])
    ids = [r["checkpoint_artifact_id"] for r in ordered]
    if keep_newest == 0:
        return ids, []
    keep = set(ids[-keep_newest:]) | set(protected)
    if milestone_hours > 0:
        seen: set[int] = set()
        for record in ordered:
            window = int((record["published_at"] - started_at) // (milestone_hours * 3600))
            if window not in seen:
                seen.add(window)
                keep.add(record["checkpoint_artifact_id"])
    return [i for i in ids if i in keep], [i for i in ids if i not in keep]


def claims(chain_dir: Path) -> set[str]:
    """CPs the chain's evaluation watchers may still read. An unreadable claim file
    raises: no deletion runs on a guess."""
    out: set[str] = set()
    for path in sorted(chain_dir.glob("eval-*/protected-checkpoints.json")):
        value = json.loads(path.read_text())
        listed = value.get("checkpoints") if isinstance(value, dict) else None
        if not isinstance(listed, list) or not all(isinstance(i, str) for i in listed):
            raise ValueError(f"{path}: no checkpoints list")
        out.update(listed)
    return out


class Deleter:
    """Payload-chunk deletion on a local ManifestArtifactStore, with a chunk reference
    count over every payload index (immutable, so read once per process and then only
    the new ones)."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.seen: set[str] = set()
        self.refs: dict[str, set[str]] = {}  # chunk sha256 -> payload sha256s naming it

    def scan(self) -> None:
        for key in self.store.blobs.keys(INDEXES):
            if key in self.seen or not key.endswith(".json"):
                continue
            index = json.loads(self.store.blobs.get(key))
            for chunk in index["chunks"]:
                self.refs.setdefault(chunk["sha256"], set()).add(index["sha256"])
            self.seen.add(key)

    def payload_bytes(self, artifact_id: str) -> int:
        return sum(p.size for p in self.store.get_manifest(artifact_id).payloads)

    def delete(self, artifact_id: str) -> dict[str, Any]:
        """Unlink the CP's chunks no other payload names. Returns
        {freed_bytes, chunks, shared, failed: [(chunk, error)]}; OSErrors per chunk are
        collected, not raised, so one bad file never hides the rest."""
        manifest = self.store.get_manifest(artifact_id)
        if manifest.kind != "checkpoint":
            raise ValueError(f"{artifact_id} is a {manifest.kind}, not a checkpoint")
        self.scan()
        freed, removed, shared, failed = 0, 0, 0, []
        for payload in manifest.payloads:
            index = json.loads(self.store.blobs.get(f"{INDEXES}{payload.sha256}.json"))
            for chunk in index["chunks"]:
                key = chunk["sha256"]
                if self.refs.get(key, set()) - {payload.sha256}:
                    shared += 1
                    continue
                path = Path(self.store.blobs.root) / "objects" / "sha256" / key
                try:
                    size = path.stat().st_size
                    os.unlink(path)
                except FileNotFoundError:
                    continue  # already gone (an earlier, partly failed attempt)
                except OSError as error:
                    failed.append((key, repr(error)))
                    continue
                freed += size
                removed += 1
        return {"freed_bytes": freed, "chunks": removed, "shared": shared, "failed": failed}
