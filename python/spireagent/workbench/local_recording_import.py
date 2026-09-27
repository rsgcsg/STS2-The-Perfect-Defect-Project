"""Explicit local-only import of one closed recording into the selected store.

The local attestation and typed bundle verification do not create a Hub receipt,
research source, Dataset, training admission, or proof of a unique Human actor.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tarfile
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sts2_platform_evidence import DirectoryTransferManifest, verify_human_session_bundle
from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.artifact_contracts import Manifest
from spireagent.hub.uploads import MAX_ARCHIVE, transfer_from_json, unpack
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes
from spireagent.source import source_identity
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.collection_tool_registration import current_collection_tool
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.local_recordings import LocalRecordingCatalog
from spireagent.workbench.local_workspace import open_registered_workspace
from spireagent.workbench.managed_local_workspace import ROOT_NAME, inspect_managed_workspace

SCHEMA = "stpd/local-recording-import-operation-v1"
EVIDENCE_SCHEMA = "stpd/local-verified-bundle-v1"
IDENTITY_SCHEMA = "stpd/local-recording-import-labels-v1"
OPERATION_FILE = "local-recording-import-operation.json"
IDENTITY_FILE = "local-recording-import-labels.json"
LABEL_PATTERN = re.compile(r"local-(?:worker|campaign)-[a-f0-9]{32}\Z")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync(store: ManifestArtifactStore, registry: SQLiteRegistry) -> None:
    cached = frozenset(item.artifact_id for item in registry.manifests()
                       if registry.is_cached(item.artifact_id))
    sync_registry(store, registry, cached)


def _selected_store(config: ProjectConfig) -> tuple[ManifestArtifactStore, SQLiteRegistry]:
    if config.research_workspace is not None:
        # The same registered legacy workspace remains the selected destination.
        if open_registered_workspace(config.research_workspace) is None:
            raise BoundaryError("local_import", "workspace_required")
        store_dir = config.research_workspace.store_dir
        registry_path = config.research_workspace.registry_path
    else:
        selected = inspect_managed_workspace(config.state_dir)
        if selected["status"] != "ready":
            raise BoundaryError("local_import", "workspace_required")
        directory = config.state_dir.resolve() / ROOT_NAME / selected["workspace_id"]
        store_dir, registry_path = directory / "store", directory / "registry.sqlite"
    if (store_dir.is_symlink() or registry_path.is_symlink()
            or not store_dir.is_dir() or not registry_path.is_file()):
        raise BoundaryError("local_import", "workspace_unavailable")
    return (ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=False)),
            SQLiteRegistry(registry_path, readonly=False))


def _labels(config: ProjectConfig) -> dict[str, str]:
    path = config.state_dir / IDENTITY_FILE
    if path.exists():
        if path.is_symlink():
            raise BoundaryError("local_import", "local_labels_invalid")
        try:
            value = json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            raise BoundaryError("local_import", "local_labels_invalid") from error
        if (not isinstance(value, dict) or set(value) != {"schema", "worker_id", "campaign_id"}
                or value.get("schema") != IDENTITY_SCHEMA
                or any(not isinstance(value.get(key), str)
                       or LABEL_PATTERN.fullmatch(value[key]) is None
                       for key in ("worker_id", "campaign_id"))):
            raise BoundaryError("local_import", "local_labels_invalid")
        return value
    value = {"schema": IDENTITY_SCHEMA,
             "worker_id": f"local-worker-{uuid.uuid4().hex}",
             "campaign_id": f"local-campaign-{uuid.uuid4().hex}"}
    atomic_json(path, value)
    return value


def _archive_verified_bundle(bundle: Path, archive: Path,
                             transfer: DirectoryTransferManifest) -> None:
    if len(transfer.files) > 50000 or sum(item.bytes for item in transfer.files) > 2 * 1024**3:
        raise BoundaryError("local_import", "bundle_size_limit")
    with (archive.open("xb") as output,
          gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as zipped,
          tarfile.open(fileobj=zipped, mode="w|") as stream):
        for item in transfer.files:
            source = bundle / item.path
            if source.is_symlink() or _sha256_file(source) != item.sha256:
                raise BoundaryError("local_import", "bundle_changed_during_archive")
            info = tarfile.TarInfo(item.path)
            info.size, info.mode, info.mtime = item.bytes, 0o644, 0
            with source.open("rb") as handle:
                stream.addfile(info, handle)
    if archive.stat().st_size > MAX_ARCHIVE:
        raise BoundaryError("local_import", "archive_size_limit")


class LocalRecordingImporter:
    """One serial background import, with durable status and no automatic retry."""

    def __init__(self, config: ProjectConfig, catalog: LocalRecordingCatalog) -> None:
        self.config, self.catalog = config, catalog
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.path = config.state_dir / OPERATION_FILE
        self.operation: dict[str, Any] = {"schema": SCHEMA, "status": "idle"}
        if self.path.exists():
            if self.path.is_symlink():
                self.operation = {"schema": SCHEMA, "status": "unavailable",
                                  "error_code": "operation_file_invalid"}
            else:
                try:
                    value = json.loads(self.path.read_bytes())
                    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
                        raise ValueError
                    self.operation = value
                    if value.get("status") == "pending":
                        self.operation = {**value, "status": "interrupted_unknown",
                                          "error_code": "previous_import_interrupted"}
                except (OSError, ValueError):
                    self.operation = {"schema": SCHEMA, "status": "unavailable",
                                      "error_code": "operation_file_invalid"}

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {**self.operation, "requires_cloud_account": False}

    def _save(self) -> None:
        atomic_json(self.path, self.operation)

    def _fresh_candidate(self, candidate_id: str) -> dict[str, Any]:
        observed = self.catalog.read()
        candidate = self.catalog.candidate(candidate_id)
        if observed.get("status") != "ready" or candidate is None:
            raise BoundaryError("local_import", "candidate_changed_or_unavailable")
        return candidate

    @staticmethod
    def _existing(store: ManifestArtifactStore, candidate_id: str) -> str | None:
        matches = []
        for artifact_id in store.manifest_ids():
            manifest = store.get_manifest(artifact_id)
            params = manifest.parameters.value()
            if (manifest.kind == "evidence" and params.get("schema") == EVIDENCE_SCHEMA
                    and params.get("candidate_id") == candidate_id):
                if (params.get("disposition") != "locally_verified"
                        or params.get("research_admission") != "not_evaluated"
                        or params.get("human_origin_attested") is not True
                        or re.fullmatch(r"[0-9a-f]{64}", str(params.get("content_id", ""))) is None
                        or {item.role for item in manifest.payloads} != {"archive", "transfer"}):
                    raise BoundaryError("local_import", "existing_local_manifest_invalid")
                matches.append(artifact_id)
        if len(matches) > 1:
            raise BoundaryError("local_import", "candidate_publication_ambiguous")
        return matches[0] if matches else None

    def start(self, candidate_id: object, human_origin_attested: object) -> dict[str, Any]:
        if human_origin_attested is not True:
            raise BoundaryError("local_import", "explicit_human_origin_attestation_required")
        identity = digest(candidate_id, "local_import.candidate_id")
        with self.lock:
            if self.operation["status"] == "unavailable":
                raise BoundaryError("local_import", "operation_file_invalid")
            if self.thread is not None and self.thread.is_alive():
                if self.operation.get("candidate_id") == identity:
                    return self.status()
                raise BoundaryError("local_import", "operation_in_progress")
            candidate = self._fresh_candidate(identity)
            store, registry = _selected_store(self.config)
            existing = self._existing(store, identity)
            if existing is not None:
                _sync(store, registry)
                self.operation = {"schema": SCHEMA, "status": "completed", "candidate_id": identity,
                                  "artifact_id": existing, "finished_at": _now()}
                self._save()
                return self.status()
            self.operation = {"schema": SCHEMA, "status": "pending", "candidate_id": identity,
                              "started_at": _now()}
            self._save()
            self.thread = threading.Thread(
                target=self._run, args=(identity, candidate), daemon=True,
            )
            self.thread.start()
            return self.status()

    def _run(self, candidate_id: str, candidate: dict[str, Any]) -> None:
        try:
            artifact_id = self._import(candidate_id, candidate)
            with self.lock:
                self.operation = {**self.operation, "status": "completed",
                                  "artifact_id": artifact_id, "finished_at": _now()}
                self._save()
        except Exception as error:
            code = error.code if isinstance(error, BoundaryError) else "local_import_failed"
            published = None
            reconciliation_unknown = False
            try:
                store, _ = _selected_store(self.config)
                published = self._existing(store, candidate_id)
            except Exception:
                reconciliation_unknown = True
            with self.lock:
                # Publication may have succeeded before an index or status failure.
                state = ("published_index_unavailable" if published else
                         "publication_unknown" if reconciliation_unknown else "failed")
                self.operation = {**self.operation, "status": state,
                                  "error_code": ("publication_state_unavailable"
                                                 if reconciliation_unknown else code),
                                  "finished_at": _now(),
                                  **({"artifact_id": published} if published else {})}
                self._save()

    def _import(self, candidate_id: str, candidate: dict[str, Any]) -> str:
        fresh = self._fresh_candidate(candidate_id)
        if fresh != candidate:
            raise BoundaryError("local_import", "candidate_changed_or_unavailable")
        store, registry = _selected_store(self.config)
        existing = self._existing(store, candidate_id)
        if existing is not None:
            _sync(store, registry)
            return existing
        producer = source_identity(ROOT)
        labels = _labels(self.config)
        tool_directory, release_id = current_collection_tool(self.config)
        tool = CollectionTool(tool_directory, release_id)
        with tempfile.TemporaryDirectory(
            prefix="local-recording-import-", dir=self.config.state_dir,
        ) as name:
            temporary = Path(name)
            bundle, archive, extracted = (temporary / "bundle", temporary / "bundle.tar.gz",
                                          temporary / "extracted")
            tool.pack(candidate["source_directory"], bundle,
                      labels["worker_id"], labels["campaign_id"])
            if self._fresh_candidate(candidate_id) != candidate:
                raise BoundaryError("local_import", "candidate_changed_during_pack")
            verification = verify_human_session_bundle(bundle)
            if not verification.passed:
                raise BoundaryError("local_import", "typed_bundle_verification_failed")
            verified = verification.require_value()
            if (verified.session_id != candidate["session_id"]
                    or verified.timeline_id != candidate["timeline_id"]
                    or verified.worker_id != labels["worker_id"]
                    or verified.campaign_id != labels["campaign_id"]):
                raise BoundaryError("local_import", "packed_bundle_identity_mismatch")
            transfer = DirectoryTransferManifest.from_directory(
                bundle, content_id=verified.bundle_content_id, artifact_type="human-session-bundle",
            )
            transfer = transfer_from_json(transfer.to_dict())
            _archive_verified_bundle(bundle, archive, transfer)
            extracted.mkdir()
            unpack(archive, extracted, transfer)
            again = verify_human_session_bundle(extracted)
            if not again.passed or again.require_value().bundle_content_id != transfer.content_id:
                raise BoundaryError("local_import", "archived_bundle_verification_failed")
            if self._fresh_candidate(candidate_id) != candidate:
                raise BoundaryError("local_import", "candidate_changed_during_archive")
            with archive.open("rb") as handle:
                payload = store.put_payload("archive", handle, "application/gzip")
            transfer_payload = store.put_bytes("transfer", json_bytes(transfer.to_dict()))
            evidence = Manifest(
                "evidence", producer,
                payloads=(payload, transfer_payload),
                parameters=FrozenObject.of({
                    "schema": EVIDENCE_SCHEMA,
                    "candidate_id": candidate_id,
                    "session_id": candidate["session_id"],
                    "timeline_id": candidate["timeline_id"],
                    "manifest_sha256": candidate["manifest_sha256"],
                    "close_sha256": candidate["close_sha256"],
                    "content_id": transfer.content_id,
                    "transfer_manifest_sha256": transfer.manifest_sha256,
                    "tool_release_id": release_id,
                    "worker_id": labels["worker_id"],
                    "campaign_id": labels["campaign_id"],
                    "human_origin_attested": True,
                    "disposition": "locally_verified",
                    "research_admission": "not_evaluated",
                    "hub_receipt": None,
                }),
            )
            artifact_id = store.publish(evidence)
            _sync(store, registry)
            return artifact_id
