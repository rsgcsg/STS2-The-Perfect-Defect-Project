"""One command's private reuse of an owner-verified immutable curated selection.

The cache has no admission or ledger facts. Every hit streams and hashes the original
artifact closure again. The first load still runs the installed Evidence verifier and
selection owner. Copies own their temporary rows, so ordinary caller cleanup is safe.
"""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import sts2_platform_evidence

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore

from .decision_dataset import DecisionDataset
from .decision_spool import DecisionSpool, SpoolSelection

# At most one selection, with rows on disk and bounded report/row retention.
MAX_RETAINED_ROW_BYTES = 1024 * 1024**2
MAX_RETAINED_REPORT_BYTES = 64 * 1024**2
MAX_CLOSURE_NODES = 512


@dataclass
class SelectionReuseStats:
    hits: int = 0
    misses: int = 0
    bypassed: int = 0


@dataclass
class _Session:
    store_identity: tuple[str, int, int] | None
    stats: SelectionReuseStats
    identity: tuple[str, str, str] | None = None
    selection: DecisionDataset | None = None

    def clear(self) -> None:
        if self.selection is not None:
            _close(self.selection)
        self.identity = self.selection = None


_CURRENT: ContextVar[_Session | None] = ContextVar("verified_dataset_sources", default=None)


@contextmanager
def verified_dataset_sources(store: ArtifactStore) -> Iterator[SelectionReuseStats]:
    """Opt in for one owner command; nested scopes have independent retention."""
    session = _Session(_local_store_identity(store), SelectionReuseStats())
    token = _CURRENT.set(session)
    try:
        yield session.stats
    finally:
        try:
            session.clear()
        finally:
            _CURRENT.reset(token)


def _local_store_identity(store: ArtifactStore) -> tuple[str, int, int] | None:
    """Bind reconstructed local wrappers to the same actual directory, not a path label.

    Other store implementations have no admitted physical identity in this helper.
    Callers still read/hash current bytes through the currently selected wrapper.
    """
    if type(store) is not ManifestArtifactStore or type(store.blobs) is not LocalBlobStore:
        return None
    root = store.blobs.root
    metadata = root.lstat()
    if (root != root.resolve() or not stat.S_ISDIR(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
        return None
    return str(root), metadata.st_dev, metadata.st_ino


def _implementation_identity() -> str:
    """Hash current verifier and selection implementation, without a process memo."""
    root = Path(__file__).parent
    evidence = Path(sts2_platform_evidence.__file__).parent
    files = [("research/" + path.name, path) for path in root.glob("*.py")]
    files.extend(("evidence/" + path.relative_to(evidence).as_posix(), path)
                 for path in evidence.rglob("*.py"))
    files.append(("research/canonical.py", root.parent / "canonical.py"))
    app = root.parents[1] / "spireagent"
    files.extend(("app/" + name, app / name) for name in (
        "encoding.py", "json_boundary.py", "local_verified_bundle.py",
        "artifact_contracts.py", "hub/uploads.py", "storage/store.py",
    ))
    return hashlib.sha256(json_bytes([
        [name, hashlib.sha256(path.read_bytes()).hexdigest()]
        for name, path in sorted(files)
    ])).hexdigest()


def _closure_identity(store: ArtifactStore, selected: Manifest) -> str:
    pending = [selected.artifact_id]
    seen: set[str] = set()
    manifests: list[tuple[str, str]] = []
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= MAX_CLOSURE_NODES:
            raise BoundaryError("selection_session", "lineage_limit")
        seen.add(identity)
        manifest = store.get_manifest(identity)
        if manifest.artifact_id != identity or (
            identity == selected.artifact_id and manifest != selected
        ):
            raise BoundaryError("selection_session", "manifest_identity_mismatch")
        manifests.append((identity, hashlib.sha256(manifest.to_bytes()).hexdigest()))
        for payload in manifest.payloads:
            whole = hashlib.sha256()
            size = 0
            for chunk in store.read_payload(payload):
                whole.update(chunk)
                size += len(chunk)
                if size > payload.size:
                    raise BoundaryError("selection_session", "payload_identity_mismatch")
            if size != payload.size or whole.hexdigest() != payload.sha256:
                raise BoundaryError("selection_session", "payload_identity_mismatch")
        pending.extend(parent.artifact_id for parent in manifest.parents)
    return hashlib.sha256(json_bytes(sorted(manifests))).hexdigest()


def _close(dataset: DecisionDataset) -> None:
    if isinstance(dataset.records, SpoolSelection):
        dataset.records.owner.close()


def _copy(dataset: DecisionDataset) -> DecisionDataset:
    # Keep only selected rows; neither full parent projections nor unpacked bundles.
    if not isinstance(dataset.records, SpoolSelection):
        return DecisionDataset(tuple(dataset.records), dataset.report)
    spool = DecisionSpool()
    try:
        spool.db.executemany(
            "INSERT INTO records VALUES(?,?,?,?,?,1)",
            dataset.records.owner.db.execute(
                "SELECT id,run,sequence,body,summary FROM records WHERE selected=1"
            ),
        )
        return DecisionDataset(spool.selected(), dataset.report)
    except BaseException:
        spool.close()
        raise


def _retainable(dataset: DecisionDataset) -> bool:
    if not isinstance(dataset.records, SpoolSelection):
        return False
    if len(dataset.report.encoded.encode("utf-8")) > MAX_RETAINED_REPORT_BYTES:
        return False
    size = dataset.records.owner.db.execute(
        "SELECT coalesce(sum(length(cast(body AS BLOB)) + "
        "length(cast(summary AS BLOB))),0) FROM records WHERE selected=1"
    ).fetchone()[0]
    return int(size) <= MAX_RETAINED_ROW_BYTES


def _load_selection(
    store: ArtifactStore, manifest: Manifest, loader: Callable[[bool], DecisionDataset],
) -> DecisionDataset:
    """Private loader hook; only a successful owning loader seeds retention."""
    session = _CURRENT.get()
    if session is None or session.store_identity is None or (
        _local_store_identity(store) != session.store_identity
    ):
        return loader(False)
    try:
        implementation = _implementation_identity()
        closure = _closure_identity(store, manifest)
        identity = (manifest.artifact_id, implementation, closure)
        if session.identity == identity and session.selection is not None:
            result = _copy(session.selection)
            session.stats.hits += 1
            return result
        session.clear()
        session.stats.misses += 1
        # Never seed private verification from a caller's pre-populated memo.
        result = loader(True)
        try:
            if (implementation != _implementation_identity()
                    or closure != _closure_identity(store, manifest)):
                raise BoundaryError("selection_session", "verification_identity_changed")
            if _retainable(result):
                session.selection = _copy(result)
                session.identity = identity
            else:
                session.stats.bypassed += 1
            return result
        except BaseException:
            _close(result)
            raise
    except BaseException:
        # Failed original evidence stays failed even if a caller catches the error.
        session.clear()
        raise
