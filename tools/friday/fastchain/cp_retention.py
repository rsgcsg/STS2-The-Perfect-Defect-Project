"""Checkpoint and model-publication retention of one resume chain (supervise.py, after
each published CP), on the chain's peer Store (the learner Store's archive).

Rule (`select`): keep the newest `keep_newest` CPs of the chain, every CP `claims` returns
(evaluation claims, every evaluated CP, start CPs of the chains under the same work root)
and each generation's starting CP, plus the first CP of every
`milestone_hours` window since the chain started; the rest go. `keep_newest = 0` keeps
everything (the default, today's behaviour). When on, `keep_newest >= 2`: the CP a
resume uses is the newest (supervise.newest_checkpoint), and the one before it covers the
moment between a watcher reading the newest record and writing its claim.

What goes (`Deleter`): only the CP's payload chunks under `objects/sha256/`, and only a
chunk no other payload index of the Store names (chunks are content addressed: an
all-zero block or an unchanged tensor region can be shared with another CP or a model).
The manifest, the payload index and the CP record stay, so lineage still resolves the
id; reading a deleted CP's bytes fails loudly with `object_not_found`, never silently.
A failed unlink is the caller's to log and count; it never stops training. Accepted
race: a writer that reuses a chunk only the dropped artifact named, and whose publish
read-back finished just before the unlink, publishes an artifact missing that chunk
(loud on read). It needs non-monotonic sharing inside a millisecond window.

Model publications (`select_models`): the learner publishes one model artifact (weights +
package manifest) and one behaviour-heads artifact (parent: that model) per mu version
into its node-local Store; the archive copies both and the `models/NNNNNN.json` record to
the peer. On the peer nothing reads an old one on the live path: the learner serves mu to
inference from its local Store (the peer is only a read fallback for a missing local
chunk, and the local Store is never trimmed), a resume republishes from the CP's own
theta, an evaluation exports the CP into its own detached model, and R7 audit games keep
their mu in the mu archive. Kept: the newest `keep_newest`, and the version each retained
CP names (its `version` is the mu version published at that CP). The model and its heads
go together; records and manifests stay. `keep_newest = 0` keeps every publication.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

INDEXES = "payload-indexes/v1/"
MANIFESTS = "manifests/"


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


def select_models(records: list[dict[str, Any]], *, keep_newest: int,
                  tied: set[tuple[str | None, int]]) -> tuple[list[dict[str, Any]],
                                                              list[dict[str, Any]]]:
    """(kept, to delete) of the chain's model records, oldest first. `tied` = the
    (training_generation, version) of every retained CP."""
    ordered = sorted(records, key=lambda r: r["published_at"])
    if keep_newest == 0:
        return ordered, []
    newest = {id(r) for r in ordered[-keep_newest:]}
    keep = [r for r in ordered
            if id(r) in newest or (r.get("training_generation"), r["version"]) in tied]
    kept = {id(r) for r in keep}
    return keep, [r for r in ordered if id(r) not in kept]


def claims(chain_dir: Path) -> set[str]:
    """CPs that must stay: those the chain's evaluation watchers claim
    (`eval-*/protected-checkpoints.json`), every CP any of its evaluations names in
    `eval-*/state.json` whatever the stage (evaluated CPs are the restart and paired-
    report anchors; `prepare_submit_unknown` may still have a job), and the start CPs of
    every chain under the same work root (`chain-*/state.json`: a branch chain started
    from one of ours). An unreadable file raises: no deletion runs on a guess."""
    out: set[str] = set()
    for path in sorted(chain_dir.glob("eval-*/protected-checkpoints.json")):
        value = json.loads(path.read_text())
        listed = value.get("checkpoints") if isinstance(value, dict) else None
        if not isinstance(listed, list) or not all(isinstance(i, str) for i in listed):
            raise ValueError(f"{path}: no checkpoints list")
        out.update(listed)
    for path in sorted(chain_dir.glob("eval-*/state.json")):
        value = json.loads(path.read_text())
        evaluations = value.get("evaluations") if isinstance(value, dict) else None
        if not isinstance(evaluations, list) or not all(
                isinstance(e, dict) and isinstance(e.get("checkpoint"), str)
                for e in evaluations):
            raise ValueError(f"{path}: no evaluations list")
        out.update(e["checkpoint"] for e in evaluations)
    for path in sorted(chain_dir.parent.glob("chain-*/state.json")):
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError(f"{path}: not a chain state")
        starts = [value.get("start_checkpoint")] + [
            g.get("checkpoint") for g in value.get("generations", [])]
        out.update(i for i in starts if isinstance(i, str))
    return out


class Deleter:
    """Payload-chunk deletion on a local ManifestArtifactStore, with reference counts
    over every payload index (chunk -> payloads) and every manifest (payload ->
    artifacts). Both are immutable, so each is read once per process, then only new ones.

    Two artifacts with byte-identical payloads share one payload index, so a payload
    another artifact also names is skipped whole (heads unchanged between versions, a
    resume's mu equal to an older version, an evaluation export equal to a chain
    publication). Deleted artifacts keep their manifests and still count as owners: a
    payload or chunk shared only among deleted artifacts is left behind (safe side)."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.seen: set[str] = set()
        self.refs: dict[str, set[str]] = {}  # chunk sha256 -> payload sha256s naming it
        self.owners: dict[str, set[str]] = {}  # payload sha256 -> artifact ids naming it
        self.sizes: dict[str, int] = {}  # artifact id -> payload bytes (immutable)

    def scan(self) -> None:
        for key in self.store.blobs.keys(INDEXES):
            if key in self.seen or not key.endswith(".json"):
                continue
            index = json.loads(self.store.blobs.get(key))
            for chunk in index["chunks"]:
                self.refs.setdefault(chunk["sha256"], set()).add(index["sha256"])
            self.seen.add(key)
        for key in self.store.blobs.keys(MANIFESTS):
            if key in self.seen or not key.endswith(".json"):
                continue
            manifest = json.loads(self.store.blobs.get(key))
            for payload in manifest["payloads"]:
                self.owners.setdefault(payload["sha256"], set()).add(manifest["artifact_id"])
            self.seen.add(key)

    def payload_bytes(self, artifact_id: str) -> int:
        if artifact_id not in self.sizes:
            self.sizes[artifact_id] = sum(
                p.size for p in self.store.get_manifest(artifact_id).payloads)
        return self.sizes[artifact_id]

    def delete(self, artifact_id: str, kind: str = "checkpoint") -> dict[str, Any]:
        """Unlink the artifact's chunks no other payload names, skipping any payload
        another artifact names. Returns
        {freed_bytes, chunks, shared, failed: [(chunk, error)]}; OSErrors per chunk are
        collected, not raised, so one bad file never hides the rest."""
        manifest = self.store.get_manifest(artifact_id)
        if manifest.kind != kind:
            raise ValueError(f"{artifact_id} is a {manifest.kind}, not a {kind}")
        self.scan()
        freed, removed, shared, failed = 0, 0, 0, []
        for payload in manifest.payloads:
            if self.owners.get(payload.sha256, set()) - {artifact_id}:
                shared += 1  # another artifact names the same payload: keep it whole
                continue
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

    def delete_model(self, record: dict[str, Any]) -> dict[str, Any]:
        """A model publication: its model artifact and its behaviour-heads artifact (kind
        checkpoint, schema stpd/rl-behavior-heads-1, parent = that model), as `delete`."""
        model = record["model_artifact_id"]
        heads = self.store.get_manifest(record["heads_artifact_id"])
        if (heads.parameters.value().get("schema") != "stpd/rl-behavior-heads-1"
                or [p.artifact_id for p in heads.parents] != [model]):
            raise ValueError(f"{record['heads_artifact_id']} is not the heads of {model}")
        first = self.delete(model, "model")
        second = self.delete(record["heads_artifact_id"])
        return {"freed_bytes": first["freed_bytes"] + second["freed_bytes"],
                "chunks": first["chunks"] + second["chunks"],
                "shared": first["shared"] + second["shared"],
                "failed": first["failed"] + second["failed"]}
