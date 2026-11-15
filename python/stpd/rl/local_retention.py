"""Learner-local retention (#558): bound `learner-live/store` and `mu-network` on the GPU node.

In dataplane mode the learner's Store is node-local (`<dataplane root>/store`, an
ArchivedStore whose archive is the run's peer) and `_model_files` copies each served
mu into `<dataplane root>/mu-network/<snapshot>`. Both only grew: every mu publication
(model + heads payloads) and every CP stayed, and every served mu kept a second, whole
file copy.

`LocalRetention` (learner process) chooses; `trim` (the publisher process, the same
single worker that publishes mu, so no publication is in flight while it runs) deletes.

A mu publication's local copies (its payload chunks and its mu-network directory) go
only when all hold:
- it is not among the newest `keep_models` (0 = keep every one: the default);
- it is not in `pool.snapshots`, at this pass and at the pass before. That set is what
  `get_mu` can serve: the latest, every snapshot a node reported cached and every lease
  owner's mu (`SegmentInferencePool.retire`). A key outside it is refused (KeyError) and
  never comes back, so a slow service that still holds version n-k keeps n-k here;
- it is not the version the newest local CP names;
- its model and heads manifests carry the archive's completion receipt (full readback
  on the peer); otherwise it stays and is retried next pass.
A CP's local payload chunks go when it is not among the newest `keep_checkpoints`
(0 = keep all) and its manifest has that receipt. Resume reads CPs from the peer.

Manifests and payload indexes stay (lineage; a later read falls back to the peer, as
the Store already does). A chunk goes only when every payload naming it belongs only to
deleted artifacts. A failed delete is counted and reported, never raised.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import decode_json, json_bytes

HEADS_SCHEMA = "stpd/rl-behavior-heads-1"


class LocalRetention:
    """Learner-side bookkeeping; no filesystem work happens here."""

    def __init__(self, *, keep_models: int = 0, keep_checkpoints: int = 0) -> None:
        if type(keep_models) is not int or type(keep_checkpoints) is not int or min(
                keep_models, keep_checkpoints) < 0:
            raise ValueError("local_retention_keep_counts_must_be_non_negative_ints")
        self.keep_models, self.keep_checkpoints = keep_models, keep_checkpoints
        # snapshot -> {version, model/heads ids (own publications only), export}
        self.mu: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.checkpoints: OrderedDict[str, int] = OrderedDict()  # CP id -> mu version
        self.retired_before: set[str] = set()
        self.future: Any = None
        self.totals = {"passes": 0, "pass_failures": 0, "deleted_models": 0,
                       "deleted_checkpoints": 0, "deleted_mu_dirs": 0,
                       "kept_unarchived": 0, "failed_deletes": 0, "freed_bytes": 0}

    def served(self, identity: dict[str, Any]) -> None:
        """A mu this learner serves but did not publish (the start/restored mu)."""
        self.mu.setdefault(identity["snapshot_sha256"], {"version": identity["version"]})

    def published(self, identity: dict[str, Any], export: str) -> None:
        self.mu[identity["snapshot_sha256"]] = {
            "version": identity["version"], "model_artifact_id": identity["model_artifact_id"],
            "heads_artifact_id": identity["heads_artifact_id"], "export": export}

    def checkpoint(self, artifact_id: str, version: int) -> None:
        self.checkpoints[artifact_id] = version

    def select(self, servable: set[str]) -> dict[str, Any]:
        """The next pass's candidates; `servable` = `pool.snapshots` keys right now."""
        retired = set(self.mu) - servable
        # A request that resolved its snapshot just before retire may still be reading
        # the files: delete only what was already unservable at the previous pass.
        eligible, self.retired_before = retired & self.retired_before, retired
        models: list[dict[str, Any]] = []
        mu_dirs: list[str] = []
        if self.keep_models:
            newest = {key for key, _ in sorted(self.mu.items(), key=lambda item:
                                               item[1]["version"])[-self.keep_models:]}
            named = {version for version in list(self.checkpoints.values())[-1:]}
            for key, entry in self.mu.items():
                if key in eligible and key not in newest and entry["version"] not in named:
                    mu_dirs.append(key)
                    if "model_artifact_id" in entry:
                        models.append({"snapshot": key, **entry})
        checkpoints = (list(self.checkpoints)[:-self.keep_checkpoints]
                       if self.keep_checkpoints else [])
        return {"models": models, "checkpoints": checkpoints, "mu_dirs": mu_dirs}

    def settle(self, result: dict[str, Any]) -> dict[str, Any]:
        """Fold one finished pass in; returns the run-record fields."""
        for key in result["deleted_models"]:  # store copy gone: only the mu dir is left
            for name in ("model_artifact_id", "heads_artifact_id", "export"):
                self.mu[key].pop(name, None)
        for key in result["deleted_mu_dirs"]:
            if "model_artifact_id" not in self.mu.get(key, {"model_artifact_id": None}):
                del self.mu[key]
        for key in result["deleted_checkpoints"]:
            self.checkpoints.pop(key, None)
        self.totals["passes"] += 1
        for name in ("deleted_models", "deleted_checkpoints", "deleted_mu_dirs"):
            self.totals[name] += len(result[name])
        for name in ("kept_unarchived", "freed_bytes"):
            self.totals[name] += result[name]
        self.totals["failed_deletes"] += len(result["failures"])
        return {**{name: (len(value) if isinstance(value, list) and name != "failures"
                          else value) for name, value in result.items()},
                "failures": result["failures"][:8], "tracked_mu": len(self.mu),
                "tracked_checkpoints": len(self.checkpoints), "totals": dict(self.totals)}

    def failed(self, error: BaseException) -> dict[str, Any]:
        self.totals["passes"] += 1
        self.totals["pass_failures"] += 1
        return {"error": f"{type(error).__name__}: {error}", "totals": dict(self.totals)}


def receipt(store: Path, artifact_id: str) -> bool:
    """True when the archive copied this manifest (with readback) to the peer."""
    key = f"manifests/{artifact_id}.json"
    raw = (store / key).read_bytes()
    identity = hashlib.sha256(json_bytes([key, hashlib.sha256(raw).hexdigest()])).hexdigest()
    return (store / ".archive" / "completed" / f"{identity}.json").is_file()


class _References:
    """Incremental chunk/payload/manifest references of one local Store (immutable files)."""

    def __init__(self, store: Path) -> None:
        self.store = store
        self.chunks: dict[str, set[str]] = {}   # payload sha -> chunk shas
        self.users: dict[str, set[str]] = {}    # chunk -> payload shas
        self.owners: dict[str, set[str]] = {}   # payload sha -> manifest ids
        self.seen_indexes: set[str] = set()
        self.seen_manifests: set[str] = set()
        self.dead: set[str] = set()             # manifest ids whose payloads went

    def scan(self) -> None:
        indexes = self.store / "payload-indexes" / "v1"
        for entry in os.scandir(indexes) if indexes.is_dir() else ():
            name = entry.name
            if name in self.seen_indexes or not name.endswith(".json"):
                continue
            index = decode_json(Path(entry.path).read_bytes())
            sha = name.removesuffix(".json")
            if not isinstance(index, dict) or index.get("sha256") != sha:
                raise ValueError(f"payload_index_mismatch: {name}")
            chunks = {chunk["sha256"] for chunk in index["chunks"]}
            self.chunks[sha] = chunks
            for chunk in chunks:
                self.users.setdefault(chunk, set()).add(sha)
            self.seen_indexes.add(name)
        manifests = self.store / "manifests"
        for entry in os.scandir(manifests) if manifests.is_dir() else ():
            name = entry.name
            if name in self.seen_manifests or not name.endswith(".json"):
                continue
            manifest = Manifest.from_bytes(Path(entry.path).read_bytes(),
                                           name.removesuffix(".json"))
            for payload in manifest.payloads:
                self.owners.setdefault(payload.sha256, set()).add(manifest.artifact_id)
            self.seen_manifests.add(name)

    def _live(self, payload: str) -> bool:
        # A payload with no manifest yet is mid-publication: live.
        owners = self.owners.get(payload, set())
        return not owners or not owners <= self.dead

    def drop(self, manifests: list[Manifest], failures: list[dict[str, Any]]) -> int:
        """Unlink the chunks only these (now dead) manifests use; returns bytes freed."""
        self.scan()  # re-read just before unlinking: the window to a new user is tiny
        self.dead.update(m.artifact_id for m in manifests)
        freed = 0
        for payload in {p.sha256 for m in manifests for p in m.payloads}:
            if self._live(payload):
                continue
            for chunk in self.chunks.get(payload, ()):
                if any(self._live(user) for user in self.users.get(chunk, ())):
                    continue
                path = self.store / "objects" / "sha256" / chunk
                try:
                    size = path.stat().st_size
                    path.unlink()
                    freed += size
                except FileNotFoundError:
                    pass
                except OSError as error:
                    failures.append({"path": f"objects/sha256/{chunk}",
                                     "error": f"{type(error).__name__}: {error}"})
        return freed


_REFERENCES: dict[str, _References] = {}  # per publisher process, per Store


def _tree_bytes(root: Path) -> int:
    total = 0
    for directory, _, files in os.walk(root):
        for name in files:
            with suppress(FileNotFoundError):
                total += os.lstat(os.path.join(directory, name)).st_size
    return total


def trim(root: str, *, models: list[dict[str, Any]], checkpoints: list[str],
         mu_dirs: list[str]) -> dict[str, Any]:
    """One deletion pass (publisher process). Errors per item are counted, not raised."""
    began = time.perf_counter()
    store, mu_root = Path(root) / "store", Path(root) / "mu-network"
    failures: list[dict[str, Any]] = []
    result: dict[str, Any] = {"candidate_models": len(models),
                              "candidate_checkpoints": len(checkpoints),
                              "deleted_models": [], "deleted_checkpoints": [],
                              "deleted_mu_dirs": [], "kept_unarchived": 0, "freed_bytes": 0}
    if models or checkpoints:
        references = _REFERENCES.setdefault(str(store), _References(store))
        references.scan()

        def manifest(identity: str) -> Manifest:
            return Manifest.from_bytes((store / "manifests" / f"{identity}.json").read_bytes(),
                                       identity)

        for item in models:
            try:
                model, heads = manifest(item["model_artifact_id"]), manifest(
                    item["heads_artifact_id"])
                if (heads.parameters.value().get("schema") != HEADS_SCHEMA
                        or heads.parents != (Parent("policy", model.artifact_id),)):
                    raise ValueError("heads_manifest_does_not_belong_to_model")
                if not (receipt(store, model.artifact_id) and receipt(store, heads.artifact_id)):
                    result["kept_unarchived"] += 1
                    continue
                result["freed_bytes"] += references.drop([model, heads], failures)
                result["deleted_models"].append(item["snapshot"])
                shutil.rmtree(item["export"], ignore_errors=True)  # disposable export leftovers
            except (OSError, ValueError) as error:
                failures.append({"model": item["snapshot"],
                                 "error": f"{type(error).__name__}: {error}"})
        for identity in checkpoints:
            try:
                checkpoint = manifest(identity)
                if not receipt(store, identity):
                    result["kept_unarchived"] += 1
                    continue
                result["freed_bytes"] += references.drop([checkpoint], failures)
                result["deleted_checkpoints"].append(identity)
            except (OSError, ValueError) as error:
                failures.append({"checkpoint": identity,
                                 "error": f"{type(error).__name__}: {error}"})
    own = {item["snapshot"] for item in models}
    for key in mu_dirs:
        if key in own and key not in result["deleted_models"]:
            continue  # store copy kept (unarchived/failed): keep its mu-network copy too
        path = mu_root / key
        errors: list[str] = []

        def note(_function: Any, failed: str, info: Any, errors: list[str] = errors) -> None:
            errors.append(f"{failed}: {info[1]}")

        if path.exists():
            shutil.rmtree(path, onerror=note)
        if errors:
            failures.append({"mu_dir": key, "error": "; ".join(errors[:3])})
        else:
            result["deleted_mu_dirs"].append(key)
    result["store_object_bytes"] = _tree_bytes(store / "objects")
    result["mu_network_bytes"] = _tree_bytes(mu_root)
    result["failures"] = failures
    result["seconds"] = time.perf_counter() - began
    return result
