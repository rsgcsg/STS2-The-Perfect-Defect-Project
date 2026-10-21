"""Explicit local-only import of one closed recording into the selected store.

The local attestation and typed bundle verification do not create a Hub receipt,
research source, Dataset, training admission, or proof of a unique Human actor.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import sts2_platform_evidence as evidence_owner
from sts2_platform_evidence import (
    DirectoryTransferManifest,
    verify_human_session_bundle,
)
from sts2_platform_evidence.collection_tool import CollectionTool

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.hub.uploads import MAX_ARCHIVE, transfer_from_json, unpack
from spireagent.json_boundary import BoundaryError, FrozenObject, digest, json_bytes
from spireagent.local_verified_bundle import EVIDENCE_SCHEMA
from spireagent.source import REPOSITORY, source_identity
from spireagent.storage.archives import MAX_BYTES, _extract
from spireagent.storage.blobs import StoreError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.collection_tool_registration import current_collection_tool
from spireagent.workbench.developer import ROOT, ProjectConfig, atomic_json
from spireagent.workbench.inplace_curation import configured_owner
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.local_recordings import LocalRecordingCatalog
from spireagent.workbench.local_workspace import open_registered_workspace
from spireagent.workbench.managed_local_workspace import ROOT_NAME, inspect_managed_workspace

SCHEMA = "stpd/local-recording-import-operation-v1"
IDENTITY_SCHEMA = "stpd/local-recording-import-labels-v1"
SOURCE3_RECEIPT_SCHEMA = "stpd/local-source3-import-receipt-v1"
OPERATION_FILE = "local-recording-import-operation.json"
IDENTITY_FILE = "local-recording-import-labels.json"
LABEL_PATTERN = re.compile(r"local-(?:worker|campaign)-[a-f0-9]{32}\Z")
MAX_NATIVE_IMPORT_INTENTS = 128
NATIVE_IMPORT_UNCERTAIN = frozenset({
    "publication_unknown", "published_index_unavailable", "interrupted_unknown",
})


def native_agent_import_choices() -> dict[str, Any]:
    """Closed producer metadata for the reviewed native data UI capability."""
    from stpd.native_agent_sampled_source_spec import (
        AGGREGATE_TEACHER_RELATION_SPEC,
        FIXTURE_COHORT,
        FOCUS_TEACHER_RELATION_SPEC,
        MAP_READY_TEACHER_RELATION_SPEC,
        MAP_TEACHER_RELATION_SPEC,
        MAX_RAW_REFERENCES,
        OWNED_STALE_TEACHER_RELATION_SPEC,
        PARTITION_SCHEMA,
        PROFILE,
        RAW_SCHEMA,
        RELATION_SPEC,
        SOURCE_SCHEMA,
        TEACHER_COHORT,
        TEACHER_RELATION_SPEC,
    )
    from stpd.native_training_source_spec import RECIPE

    return copy.deepcopy({
        "source_profile": PROFILE, "product_entry_enabled": True,
        "cohorts": [TEACHER_COHORT, FIXTURE_COHORT], "default_cohort": TEACHER_COHORT,
        "cohort_labels": {TEACHER_COHORT: "程序示范（声明来源）",
                          FIXTURE_COHORT: "合成协议测试"},
        "raw_schema": RAW_SCHEMA, "max_raw_references": MAX_RAW_REFERENCES,
        "training_source_schema": SOURCE_SCHEMA, "partition_schema": PARTITION_SCHEMA,
        "recommended_recipe_id": RECIPE,
        "default_relation_id": OWNED_STALE_TEACHER_RELATION_SPEC["id"],
        "relations": [
            {"relation": OWNED_STALE_TEACHER_RELATION_SPEC, "label": "公开规则示范程序 1.0.6",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": FOCUS_TEACHER_RELATION_SPEC, "label": "公开规则示范程序 1.0.5",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": MAP_TEACHER_RELATION_SPEC, "label": "公开规则示范程序 1.0.4",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": TEACHER_RELATION_SPEC, "label": "公开规则示范程序 1.0.2",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": MAP_READY_TEACHER_RELATION_SPEC, "label": "公开规则示范程序 1.0.10",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": AGGREGATE_TEACHER_RELATION_SPEC, "label": "已审查程序片段聚合（仅训练）",
             "cohorts": [TEACHER_COHORT, FIXTURE_COHORT]},
            {"relation": RELATION_SPEC, "label": "合成协议测试", "cohorts": [FIXTURE_COHORT]},
        ],
        "automatic_training": False, "human_origin_verified": False,
    })


def _native_agent_relation(relation_id: object, cohort: object) -> dict[str, Any]:
    from stpd.native_agent_sampled_source_spec import checked_relation

    choices = native_agent_import_choices()
    if not isinstance(cohort, str) or cohort not in choices["cohorts"]:
        raise BoundaryError("local_import", "native_agent_cohort_not_supported")
    for choice in choices["relations"]:
        if isinstance(relation_id, str) and choice["relation"]["id"] == relation_id:
            return checked_relation(choice["relation"], cohort)
    raise BoundaryError("local_import", "native_agent_relation_not_supported")

if TYPE_CHECKING:
    from sts2_platform_evidence import SourceSessionBundleV3


def _source3_api() -> tuple[Any, type]:
    verifier = getattr(evidence_owner, "verify_source_session_bundle_v3", None)
    value_type = getattr(evidence_owner, "SourceSessionBundleV3", None)
    if not callable(verifier) or not isinstance(value_type, type):
        raise BoundaryError("local_import", "source3_evidence_api_required")
    return verifier, value_type


def _verified_source3(directory: Path) -> SourceSessionBundleV3:
    verifier, value_type = _source3_api()
    result = verifier(directory)
    if not result.passed:
        raise BoundaryError("local_import", "typed_source3_verification_failed")
    verified = result.require_value()
    if not isinstance(verified, value_type):
        raise BoundaryError("local_import", "typed_source3_verification_failed")
    return cast("SourceSessionBundleV3", verified)


def _original_recorder_producer(revision: str) -> Producer:
    """Associate the original native commit with its immutable committed lock."""
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise BoundaryError("local_import", "original_recorder_provenance_required")
    try:
        kind = subprocess.check_output(
            ["git", "cat-file", "-t", revision], cwd=ROOT, timeout=10,
            stderr=subprocess.DEVNULL,
        ).strip()
        if kind != b"commit":
            raise ValueError
        original_lock = subprocess.check_output(
            ["git", "show", revision + ":python/uv.lock"], cwd=ROOT, timeout=10,
            stderr=subprocess.DEVNULL,
        )
        if not original_lock:
            raise ValueError
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BoundaryError("local_import", "original_recorder_provenance_unavailable") from None
    return Producer(REPOSITORY, revision, hashlib.sha256(original_lock).hexdigest())


def _source3_identity(bundle: Path, candidate: dict[str, Any],
                      verified: SourceSessionBundleV3) -> None:
    if (
        verified.manifest["session_id"] != candidate["session_id"]
        or verified.manifest["timeline_id"] != candidate["timeline_id"]
        or verified.recording["capture_profile_id"] != "native-logical-source-v3"
        or verified.recording["recorder_source_revision"] != candidate["recorder_source_revision"]
        or _sha256_file(bundle / "raw" / "recording-manifest.json") != candidate["manifest_sha256"]
        or _sha256_file(bundle / "raw" / "source-close-receipt.json") != candidate["close_sha256"]
        or verified.human_origin_verified is not False
    ):
        raise BoundaryError("local_import", "packed_source3_identity_mismatch")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync(store: ManifestArtifactStore, registry: SQLiteRegistry) -> None:
    cached = frozenset(item.artifact_id for item in registry.manifests()
                       if registry.is_cached(item.artifact_id))
    sync_registry(store, registry, cached)


def _selected_store(config: ProjectConfig, *, require_curation: bool = True
                    ) -> tuple[ManifestArtifactStore, SQLiteRegistry]:
    if config.research_workspace is not None:
        # All profiles using this store must resolve its one persistent owner.
        if require_curation:
            configured_owner(config)
        if open_registered_workspace(config.research_workspace) is None:
            raise BoundaryError("local_import", "workspace_required")
        store_dir = config.research_workspace.store_dir
        registry_path = config.research_workspace.registry_path
    else:
        selected = inspect_managed_workspace(config.state_dir)
        if selected["status"] != "ready":
            raise BoundaryError("local_import", "workspace_required")
        if require_curation and selected["curation_owner"] is None:
            raise BoundaryError("local_import", "curation_owner_recovery_required")
        directory = config.state_dir.resolve() / ROOT_NAME / selected["workspace_id"]
        store_dir, registry_path = directory / "store", directory / "registry.sqlite"
    if (store_dir.is_symlink() or registry_path.is_symlink()
            or not store_dir.is_dir() or not registry_path.is_file()):
        raise BoundaryError("local_import", "workspace_unavailable")
    return (ManifestArtifactStore(LocalBlobStore(store_dir, create=False, readonly=False)),
            SQLiteRegistry(registry_path, readonly=False))


def _selected_curation_owner(config: ProjectConfig) -> LocalCurationOwner | None:
    if config.research_workspace is not None:
        return configured_owner(config)
    selected = inspect_managed_workspace(config.state_dir)
    if selected["status"] != "ready":
        raise BoundaryError("local_import", "workspace_required")
    owner = selected["curation_owner"]
    if not isinstance(owner, LocalCurationOwner):
        raise BoundaryError("local_import", "curation_owner_recovery_required")
    return owner


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
                             transfer: DirectoryTransferManifest, *,
                             max_bytes: int = 2 * 1024**3, max_files: int = 50000,
                             max_archive_bytes: int = MAX_ARCHIVE) -> None:
    if len(transfer.files) > max_files or sum(item.bytes for item in transfer.files) > max_bytes:
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
    if archive.stat().st_size > max_archive_bytes:
        raise BoundaryError("local_import", "archive_size_limit")


class LocalRecordingImporter:
    """One serial background import, with durable status and no automatic retry."""

    def __init__(self, config: ProjectConfig, catalog: LocalRecordingCatalog) -> None:
        self.config, self.catalog = config, catalog
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.path = config.state_dir / OPERATION_FILE
        self.operation: dict[str, Any] = {"schema": SCHEMA, "status": "idle"}
        self._native_intents: dict[str, dict[str, Any]] = {}
        self._storage_receipts: dict[str, dict[str, Any]] = {}
        self._maintenance: dict[str, Any] = {"policy": {
            "enabled": False, "peer": None, "projection_id": "native-timeline-targets-1"},
            "active": None, "completed_candidates": [], "status": "disabled"}
        if self.path.exists():
            if self.path.is_symlink():
                self.operation = {"schema": SCHEMA, "status": "unavailable",
                                  "error_code": "operation_file_invalid"}
            else:
                try:
                    value = json.loads(self.path.read_bytes())
                    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
                        raise ValueError
                    intents = value.get("_native_agent_intents", {})
                    if not isinstance(intents, dict) or len(intents) > MAX_NATIVE_IMPORT_INTENTS:
                        raise ValueError
                    for identity, snapshot in intents.items():
                        digest(identity, "local_import.intent_id", length=32)
                        if not isinstance(snapshot, dict):
                            raise ValueError
                        request = snapshot.get("_native_agent_request")
                        if (snapshot.get("schema") != SCHEMA
                                or snapshot.get("intent_id") != identity
                                or snapshot.get("recording_type") != "native_agent_sampled"
                                or snapshot.get("status") not in {
                                    "pending", "completed", "failed", *NATIVE_IMPORT_UNCERTAIN}
                                or "_native_agent_intents" in snapshot
                                or not isinstance(request, dict)
                                or set(request) != {"directory", "cohort", "relation_id"}
                                or any(not isinstance(item, str) for item in request.values())
                                or not 0 < len(request["directory"]) <= 4096
                                or not Path(request["directory"]).is_absolute()
                                or request["cohort"] != snapshot.get("cohort")
                                or not isinstance(snapshot.get("producer_student_relation"), dict)
                                or request["relation_id"] != snapshot[
                                    "producer_student_relation"]["id"]
                                or not isinstance(snapshot.get("_owner"), (list, tuple))
                                or len(snapshot["_owner"]) != 4):
                            raise ValueError
                        digest(snapshot.get("candidate_id"), "local_import.candidate_id")
                        digest(snapshot.get("bundle_content_id"), "local_import.bundle_content_id")
                        Producer.decode(snapshot.get("_producer"))
                    self._maintenance = value.get("_maintenance", self._maintenance)
                    if not isinstance(self._maintenance, dict) or "policy" not in self._maintenance:
                        raise ValueError
                    self._storage_receipts = value.get("_storage_receipts", {})
                    if not isinstance(self._storage_receipts, dict):
                        raise ValueError
                    self._native_intents = copy.deepcopy(intents)
                    for snapshot in self._native_intents.values():
                        if snapshot["status"] == "pending":
                            snapshot.update(status="interrupted_unknown",
                                            error_code="previous_import_interrupted")
                    self.operation = value
                    if value.get("status") == "pending":
                        self.operation = {**value, "status": "interrupted_unknown",
                                          "error_code": "previous_import_interrupted"}
                    if self.operation.get("intent_id") is not None:
                        identity = digest(self.operation["intent_id"], "local_import.intent_id",
                                          length=32)
                        if identity not in self._native_intents:
                            raise ValueError
                        self._native_intents[identity] = {
                            key: copy.deepcopy(item) for key, item in self.operation.items()
                            if key != "_native_agent_intents"}
                    elif self.operation.get("recording_type") == "native_agent_sampled":
                        request = self._legacy_native_request()
                        if (any(not isinstance(item, str) for item in request.values())
                                or not 0 < len(request["directory"]) <= 4096
                                or not Path(request["directory"]).is_absolute()
                                or request["cohort"] != self.operation["cohort"]
                                or request["relation_id"] != self.operation[
                                    "producer_student_relation"]["id"]
                                or len(self.operation["_owner"]) != 4):
                            raise ValueError
                        digest(self.operation["candidate_id"], "local_import.candidate_id")
                        digest(self.operation["bundle_content_id"],
                               "local_import.bundle_content_id")
                        Producer.decode(self.operation["_producer"])
                except (OSError, ValueError, TypeError, KeyError, AttributeError, BoundaryError):
                    self.operation = {"schema": SCHEMA, "status": "unavailable",
                                      "error_code": "operation_file_invalid"}

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {**{key: value for key, value in self.operation.items()
                       if not key.startswith("_")}, "requires_cloud_account": False,
                    "storage_receipts": list(self._storage_receipts.values()),
                    "maintenance": copy.deepcopy(self._maintenance),
                    "native_agent_support": native_agent_import_choices(),
                    "native_legacy_recovery": ({"required": True,
                        "status": self.operation["status"],
                        "body_binding": "exact_literal" if "_native_agent_request" in self.operation
                            else "canonical_original_directory",
                        "requires_original_three_fields": True}
                        if self._legacy_native_unresolved() else None),
                    "native_intent_recovery": [
                        {"intent_id": key, "status": snapshot["status"],
                         "cohort": snapshot["cohort"],
                         "relation_id": snapshot["producer_student_relation"]["id"]}
                        for key, snapshot in self._native_intents.items()
                        if snapshot["status"] in {"pending", *NATIVE_IMPORT_UNCERTAIN}]}

    def _legacy_native_unresolved(self) -> bool:
        return (self.operation.get("recording_type") == "native_agent_sampled"
                and self.operation.get("intent_id") is None
                and self.operation["status"] in {"pending", *NATIVE_IMPORT_UNCERTAIN})

    def _legacy_native_request(self) -> dict[str, Any]:
        # Before opaque intents existed, only the canonical directory was
        # retained. Do not guess aliases or invent the original literal body.
        if "_native_agent_request" in self.operation:
            request = self.operation["_native_agent_request"]
            if not isinstance(request, dict) or set(request) != {
                    "directory", "cohort", "relation_id"}:
                raise ValueError
            return request
        return {"directory": self.operation["_directory"], "cohort": self.operation["cohort"],
                "relation_id": self.operation["producer_student_relation"]["id"]}

    def _native_recovery_guard(self, intent: str | None = None,
                               requested: dict[str, Any] | None = None) -> None:
        """All journaled unresolved intents survive changes of the current caller."""
        if (self.operation.get("recording_type") in {"p1_native", "p1_global"}
                and self.operation.get("status") in {
                    "pending", "publication_unknown", "interrupted_unknown"}):
            raise BoundaryError("local_import", "original_intent_reconciliation_required")
        blocked = {key for key, snapshot in self._native_intents.items()
                   if snapshot["status"] in {"pending", *NATIVE_IMPORT_UNCERTAIN}}
        record = self._native_intents.get(intent) if intent is not None else None
        if (record is not None and record["status"] == "completed"
                and record["_native_agent_request"] == requested):
            return
        if self._legacy_native_unresolved():
            if intent is not None or requested is None:
                raise BoundaryError("local_import", "original_intent_reconciliation_required")
            if requested != self._legacy_native_request():
                raise BoundaryError("local_import", "intent_payload_mismatch")
            return
        if not blocked:
            return
        # Exact reconciliation may resolve one without discarding other
        # snapshots. A known completed outcome is read-only and idempotent.
        if (record is not None and record["_native_agent_request"] == requested
                and record["status"] in {"completed", "pending", *NATIVE_IMPORT_UNCERTAIN}):
            return
        raise BoundaryError("local_import", "original_intent_reconciliation_required")

    @staticmethod
    def _native_agent_original(directory: Path, cohort: str, relation: object) -> Any:
        from stpd.fullrun.native_agent_sampled_source import _events, _producer, _verified

        bundle = _verified(directory)
        _producer(bundle, cohort, relation)
        events = _events(directory)
        if not events or events[-1]["kind"] != "stopped":
            raise BoundaryError("local_import", "native_agent_closed_run_required")
        return bundle

    def _native_agent_existing(self, store: ManifestArtifactStore,
                               content_id: str, cohort: str, relation: object) -> Manifest | None:
        from spireagent.storage.blobs import MAX_BLOB_BYTES
        from stpd.fullrun.native_agent_sampled_source import (
            _bytes,
            _inventory,
            _producer,
            _verified,
        )
        from stpd.native_agent_sampled_source_spec import (
            MAX_ORIGINAL_BYTES,
            MAX_ORIGINAL_FILES,
            RAW_SCHEMA,
        )

        matches = []
        for identity in store.manifest_ids():
            raw = store.get_manifest(identity)
            info = raw.parameters.value()
            if (raw.kind == "evidence" and info.get("schema") == RAW_SCHEMA
                    and info.get("bundle_content_id") == content_id
                    and info.get("cohort") == cohort
                    and info.get("producer_student_relation") == relation):
                if (set(info) != {"schema", "source_profile", "source_kind", "cohort",
                        "producer_student_relation", "original", "bundle_content_id", "inventory"}
                        or info["source_profile"] != "native_agent_sampled_v1"
                        or info["source_kind"] != "agent_protocol" or raw.parents
                        or len(raw.payloads) != 1
                        or raw.payload("archive").media_type != "application/gzip"):
                    raise BoundaryError("local_import", "native_agent_raw_identity")
                archive = _bytes(store, raw, "archive", MAX_BLOB_BYTES)
                with tempfile.TemporaryDirectory(prefix="native-agent-import-reconcile-") as name:
                    extracted = Path(name)
                    _extract(archive, extracted, max_bytes=MAX_ORIGINAL_BYTES,
                             max_files=MAX_ORIGINAL_FILES)
                    verified = _verified(extracted)
                    if (verified.content_id != content_id
                            or _inventory(extracted) != info["inventory"]
                            or _producer(verified, cohort, relation) != info["original"]):
                        raise BoundaryError("local_import", "native_agent_raw_identity")
                matches.append(raw)
        if len(matches) > 1:
            raise BoundaryError("local_import", "native_agent_original_ambiguous")
        return matches[0] if matches else None

    def start_native_agent_run(self, directory: object, cohort: object,
                               relation_id: object, intent_id: object = None) -> dict[str, Any]:
        intent = (None if intent_id is None else
                  digest(intent_id, "local_import.intent_id", length=32))
        requested = {"directory": directory, "cohort": cohort, "relation_id": relation_id}
        with self.lock:
            if self.operation["status"] == "unavailable":
                raise BoundaryError("local_import", "operation_file_invalid")
            record = self._native_intents.get(intent) if intent is not None else None
            if record is not None and record["_native_agent_request"] != requested:
                raise BoundaryError("local_import", "intent_payload_mismatch")
            self._native_recovery_guard(intent, requested)
            if self.thread is not None and self.thread.is_alive():
                if intent is not None and self.operation.get("intent_id") == intent:
                    return self.status()
                if intent is None and self._legacy_native_unresolved():
                    return self.status()
                if intent is not None:
                    raise BoundaryError("local_import", "operation_in_progress")
            if record is not None and record["status"] == "completed":
                store, _ = _selected_store(self.config)
                owner = _selected_curation_owner(self.config)
                if not isinstance(store.blobs, LocalBlobStore) \
                        or owner is None or tuple(record["_owner"]) != owner.identity \
                        or owner.store_dir.resolve() != store.blobs.root:
                    raise BoundaryError("local_import", "source3_workspace_owner_changed")
                return {**{key: value for key, value in record.items() if not key.startswith("_")},
                        "requires_cloud_account": False,
                        "native_agent_support": native_agent_import_choices()}
            if (intent is not None and record is None
                    and len(self._native_intents) >= MAX_NATIVE_IMPORT_INTENTS):
                raise BoundaryError("local_import", "native_import_intent_capacity")
        relation = _native_agent_relation(relation_id, cohort)
        if (not isinstance(directory, str) or not 0 < len(directory) <= 4096
                or not Path(directory).is_absolute() or Path(directory).is_symlink()
                or not Path(directory).is_dir()):
            raise BoundaryError("local_import", "native_agent_directory_required")
        source_directory = Path(directory).resolve()
        cohort = cast(str, cohort)
        bundle = self._native_agent_original(source_directory, cohort, relation)
        from stpd.canonical import semantic_hash

        identity = semantic_hash({"bundle_content_id": bundle.content_id, "cohort": cohort,
                                  "producer_student_relation": relation})
        with self.lock:
            if self.operation["status"] == "unavailable":
                raise BoundaryError("local_import", "operation_file_invalid")
            record = self._native_intents.get(intent) if intent is not None else None
            if record is not None and record["_native_agent_request"] != requested:
                raise BoundaryError("local_import", "intent_payload_mismatch")
            self._native_recovery_guard(intent, requested)
            if self.thread is not None and self.thread.is_alive():
                if (self.operation.get("intent_id") == intent if intent is not None else
                        self.operation.get("candidate_id") == identity):
                    return self.status()
                raise BoundaryError("local_import", "operation_in_progress")
            if (intent is not None and record is None
                    and len(self._native_intents) >= MAX_NATIVE_IMPORT_INTENTS):
                raise BoundaryError("local_import", "native_import_intent_capacity")
            store, _ = _selected_store(self.config)
            owner = self._source3_owner(store)
            previous = self.operation
            if self._legacy_native_unresolved() and (
                    previous["candidate_id"] != identity
                    or previous["bundle_content_id"] != bundle.content_id):
                raise BoundaryError("local_import", "native_agent_original_changed")
            if record is not None:
                if tuple(record["_owner"]) != owner.identity:
                    raise BoundaryError("local_import", "source3_workspace_owner_changed")
                if (record["candidate_id"] != identity
                        or record["bundle_content_id"] != bundle.content_id):
                    raise BoundaryError("local_import", "native_agent_original_changed")
                previous = record
            producer = (Producer.decode(previous["_producer"])
                        if previous.get("candidate_id") == identity and previous.get("_producer")
                        else source_identity(ROOT))
            owner.begin_source(identity)
            self.operation = {"schema": SCHEMA, "status": "pending", "candidate_id": identity,
                "recording_type": "native_agent_sampled", "started_at": _now(),
                "cohort": cohort, "producer_student_relation": relation,
                "bundle_content_id": bundle.content_id, "_directory": str(source_directory),
                "_producer": producer.to_dict(), "_owner": owner.identity,
                "_native_agent_request": copy.deepcopy(requested),
                **({"intent_id": intent} if intent is not None else {})}
            self._save()
            self.thread = threading.Thread(target=self._run_native_agent, args=(identity,),
                                           daemon=True)
            self.thread.start()
            return self.status()

    def _run_native_agent(self, identity: str) -> None:
        from stpd.fullrun.native_agent_sampled_source import publish_native_agent_sampled_raw

        published: Manifest | None = None
        try:
            with self.lock:
                request = dict(self.operation)
            store, registry = _selected_store(self.config)
            self._source3_owner(store)
            bundle = self._native_agent_original(Path(request["_directory"]), request["cohort"],
                                                  request["producer_student_relation"])
            if bundle.content_id != request["bundle_content_id"]:
                raise BoundaryError("local_import", "native_agent_original_changed")
            published = self._native_agent_existing(store, bundle.content_id, request["cohort"],
                                                     request["producer_student_relation"])
            if published is None:
                published = publish_native_agent_sampled_raw(
                    store, Path(request["_directory"]), Producer.decode(request["_producer"]),
                    cohort=request["cohort"], relation=request["producer_student_relation"])
            if published.parameters.value()["bundle_content_id"] != request["bundle_content_id"]:
                raise BoundaryError("local_import", "native_agent_original_changed")
            owner = self._source3_owner(store)
            owner.published_source(identity, published.artifact_id)
            with owner.transaction() as db:
                indexed = (db.execute("SELECT 1 FROM curation_sources s "
                    "JOIN curation_exact_source_index i ON i.source=s.id "
                    "WHERE s.id=? AND s.complete=1", (published.artifact_id,)).fetchone()
                    is not None)
            if indexed:
                owner.complete_index(identity, published.artifact_id)
            _sync(store, registry)
            update = {"status": "completed", "artifact_id": published.artifact_id,
                "finished_at": _now(), "source_profile": "native_agent_sampled_v1",
                "next_action": "datasets.native-agent-preview", "human_origin_verified": False}
        except Exception as error:
            unknown = False
            if published is None:
                try:
                    store, _ = _selected_store(self.config)
                    self._source3_owner(store)
                    published = self._native_agent_existing(store, request["bundle_content_id"],
                        request["cohort"], request["producer_student_relation"])
                except Exception:
                    unknown = True
            update = {"status": "publication_unknown" if unknown else
                      "published_index_unavailable" if published else "failed",
                "error_code": "publication_state_unavailable" if unknown else
                error.code if isinstance(error, BoundaryError) else "local_import_failed",
                "finished_at": _now(),
                **({"artifact_id": published.artifact_id} if published else {})}
        with self.lock:
            if self.operation.get("candidate_id") != identity:
                return
            self.operation.update(update)
            self._save()

    def _source3_owner(self, store: ManifestArtifactStore) -> LocalCurationOwner:
        owner = _selected_curation_owner(self.config)
        expected = self.operation.get("_owner")
        if (owner is None or not isinstance(store.blobs, LocalBlobStore)
                or owner.store_dir.resolve() != store.blobs.root
                or expected is not None and tuple(expected) != owner.identity):
            raise BoundaryError("local_import", "source3_workspace_owner_changed")
        return owner

    def _save(self) -> None:
        self.operation["_maintenance"] = copy.deepcopy(self._maintenance)
        if self.operation.get("action") == "storage" and self.operation.get("storage_receipt"):
            self._storage_receipts[self.operation["operation_id"]] = {
                key: copy.deepcopy(self.operation[key]) for key in (
                    "operation_id", "storage_action", "peer", "artifact_id", "storage_receipt")}
        if self._storage_receipts:
            self.operation["_storage_receipts"] = copy.deepcopy(self._storage_receipts)
        intent = self.operation.get("intent_id")
        if intent is not None:
            self._native_intents[intent] = {
                key: copy.deepcopy(item) for key, item in self.operation.items()
                if key != "_native_agent_intents"}
        if self._native_intents:
            self.operation["_native_agent_intents"] = copy.deepcopy(self._native_intents)
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

    def _existing_candidate(self, store: ManifestArtifactStore, candidate_id: str,
                            candidate: dict[str, Any], *,
                            verify_payloads: bool = True) -> str | None:
        if candidate.get("recording_type") == "p1_native":
            from .p1_recording_store import BUNDLE_SCHEMA

            p1_matches = [m.artifact_id for identity in store.manifest_ids()
                       if (m := store.get_manifest(identity)).kind == "evidence"
                       and m.parameters.value().get("schema") == BUNDLE_SCHEMA
                       and m.parameters.value().get("session_id") == candidate["session_id"]
                       and m.parameters.value().get("timeline_id") == candidate["timeline_id"]
                       and m.parameters.value().get("original_recording", {}).get(
                           "recorder_source_revision") == candidate["recorder_source_revision"]
                       and all(m.parameters.value().get("raw_file_sha256", {}).get(name)
                               == candidate[key]
                               for name, key in (("recording-manifest.json", "manifest_sha256"),
                                                 ("session-close-receipt.json", "close_sha256"),
                                                 ("p1-native-close.json", "p1_close_sha256")))]
            if len(p1_matches) > 1:
                raise BoundaryError("local_import", "candidate_publication_ambiguous")
            if p1_matches and verify_payloads:
                for payload in store.get_manifest(p1_matches[0]).payloads:
                    for _ in store.read_payload(payload):
                        pass
            return p1_matches[0] if p1_matches else None
        if candidate.get("recording_type") != "source3":
            return self._existing(store, candidate_id)
        from stpd.ordered_source_spec import RAW_SCHEMA

        owner = self._source3_owner(store)
        bound: str | None = None
        if owner is not None:
            with owner.transaction() as db:
                row = db.execute("SELECT artifact FROM local_source_pending WHERE candidate=?",
                                 (candidate_id,)).fetchone()
                bound = row[0] if row is not None else None
        matches: list[str] = []
        for identity in ((bound,) if bound else store.manifest_ids()):
            raw = store.get_manifest(identity)
            info = raw.parameters.value()
            recording = info.get("original_recording", {})
            if (raw.kind != "evidence" or info.get("schema") != RAW_SCHEMA
                    or not isinstance(recording, dict)
                    or recording.get("session_id") != candidate["session_id"]
                    or recording.get("timeline_id") != candidate["timeline_id"]):
                if bound:
                    raise BoundaryError("local_import", "existing_source3_manifest_invalid")
                continue
            payload = raw.payload("archive")
            if payload.size > MAX_BYTES or payload.sha256 != info.get("archive_sha256"):
                raise BoundaryError("local_import", "existing_source3_manifest_invalid")
            with tempfile.TemporaryDirectory(prefix="source3-import-reconcile-",
                                             dir=self.config.state_dir) as name:
                directory = Path(name)
                _extract(store.bytes(payload), directory)
                verified = _verified_source3(directory)
                _source3_identity(directory, candidate, verified)
                if (verified.content_id != info.get("bundle_content_id")
                        or info.get("human_origin_verified") is not False
                        or raw.producer != _original_recorder_producer(
                            candidate["recorder_source_revision"])):
                    raise BoundaryError("local_import", "existing_source3_manifest_invalid")
            matches.append(identity)
        if len(matches) > 1:
            raise BoundaryError("local_import", "candidate_publication_ambiguous")
        return matches[0] if matches else None

    @staticmethod
    def _receipt_metadata(candidate_id: str, candidate: dict[str, Any], raw: Manifest,
                          invocation: str, packing_release: str | None,
                          original_receipt: str | None) -> dict[str, Any]:
        return {
            "schema": SOURCE3_RECEIPT_SCHEMA, "candidate_id": candidate_id,
            "session_id": candidate["session_id"], "timeline_id": candidate["timeline_id"],
            "manifest_sha256": candidate["manifest_sha256"],
            "close_sha256": candidate["close_sha256"], "raw_artifact_id": raw.artifact_id,
            "bundle_content_id": raw.parameters.value()["bundle_content_id"],
            "registered_tool_release_id": candidate["tool_release_id"],
            "tool_invocation": invocation, "packing_tool_release_id": packing_release,
            "original_packing_receipt_id": original_receipt, "human_origin_verified": False,
        }

    @staticmethod
    def _source3_receipts(store: ManifestArtifactStore, raw: Manifest,
                          candidate: dict[str, Any]) -> list[Manifest]:
        matches = []
        for identity in store.manifest_ids():
            receipt = store.get_manifest(identity)
            info = receipt.parameters.value()
            if (info.get("schema") != SOURCE3_RECEIPT_SCHEMA
                    or info.get("raw_artifact_id") != raw.artifact_id):
                continue
            invocation = info.get("tool_invocation")
            if not isinstance(invocation, str) or invocation not in {
                "packed", "reused_no_tool_invocation"
            }:
                raise BoundaryError("local_import", "source3_import_receipt_invalid")
            receipt_candidate = digest(info.get("candidate_id"), "local_import.receipt_candidate")
            expected = LocalRecordingImporter._receipt_metadata(
                receipt_candidate, candidate, raw, invocation,
                info.get("packing_tool_release_id"), info.get("original_packing_receipt_id"))
            # Registration is a prerequisite of this particular import intent;
            # it may differ from the currently registered release during reuse.
            expected["registered_tool_release_id"] = info.get("registered_tool_release_id")
            if (receipt.kind != "analysis" or receipt.payloads
                    or receipt.parents != (Parent("raw", raw.artifact_id),)
                    or info != expected or info.get("human_origin_verified") is not False):
                raise BoundaryError("local_import", "source3_import_receipt_invalid")
            for key in ("candidate_id", "registered_tool_release_id"):
                digest(info[key], "local_import.receipt." + key)
            for key in ("packing_tool_release_id", "original_packing_receipt_id"):
                if info[key] is not None:
                    digest(info[key], "local_import.receipt." + key)
            if (info["tool_invocation"] == "packed"
                    and (info["packing_tool_release_id"] != info["registered_tool_release_id"]
                         or info["original_packing_receipt_id"] is not None)):
                raise BoundaryError("local_import", "source3_import_receipt_invalid")
            if (info["tool_invocation"] == "reused_no_tool_invocation"
                    and ((info["packing_tool_release_id"] is None)
                         != (info["original_packing_receipt_id"] is None))):
                raise BoundaryError("local_import", "source3_import_receipt_invalid")
            matches.append(receipt)
        return matches

    def _ensure_receipt(self, store: ManifestArtifactStore, raw: Manifest,
                        params: dict[str, Any], existing: list[Manifest]) -> Manifest:
        matches = [receipt for receipt in existing if receipt.parameters.value() == params]
        if len(matches) > 1:
            raise BoundaryError("local_import", "source3_import_receipt_ambiguous")
        if matches:
            producer = matches[0].producer
            if (self.operation.get("_receipt_reconciliation") is True
                    and producer != Producer.decode(self.operation["_receipt_producer"])):
                raise BoundaryError("local_import", "source3_receipt_producer_mismatch")
            # A first encounter can reuse an already immutable receipt. Its
            # existing producer then becomes the captured fact of this intent.
            self.operation["_receipt_producer"] = producer.to_dict()
            return matches[0]
        producer = Producer.decode(self.operation["_receipt_producer"])
        receipt = Manifest("analysis", producer, parents=(Parent("raw", raw.artifact_id),),
                           parameters=FrozenObject.of(params))
        self._source3_owner(store)
        store.publish(receipt)
        return receipt

    def _source3_receipt(self, candidate_id: str, candidate: dict[str, Any],
                         artifact_id: str, store: ManifestArtifactStore,
                         *, packed: bool = False) -> Manifest:
        raw = store.get_manifest(artifact_id)
        receipts = self._source3_receipts(store, raw, candidate)
        originals = [receipt for receipt in receipts
                     if receipt.parameters.value()["tool_invocation"] == "packed"]
        if len(originals) > 1:
            raise BoundaryError("local_import", "source3_original_packing_receipt_ambiguous")
        fact = self.operation.get("_packing_fact")
        if isinstance(fact, dict):
            # Successful pack facts and the derived producer were committed to
            # the existing private operation before raw publication. Reconcile
            # that exact fact, never infer a release from packer source revision.
            packed_candidate = digest(fact.get("candidate_id"), "local_import.packed_candidate")
            packed_release = digest(fact.get("tool_release_id"), "local_import.packed_release")
            fact_candidate = {**candidate, "tool_release_id": packed_release}
            expected = self._receipt_metadata(
                packed_candidate, fact_candidate, raw, "packed", packed_release, None)
            if fact.get("closed_metadata") != {
                    key: value for key, value in expected.items() if key != "raw_artifact_id"}:
                raise BoundaryError("local_import", "source3_packing_fact_mismatch")
            if originals:
                # A prior receipt can survive a lost publication reply. Its
                # presence does not supersede the exact successful-pack fact
                # already saved before raw publication, including its Producer.
                if originals[0].parameters.value() != expected:
                    raise BoundaryError("local_import", "source3_packing_fact_mismatch")
                if originals[0].producer != Producer.decode(self.operation["_receipt_producer"]):
                    raise BoundaryError("local_import", "source3_receipt_producer_mismatch")
            else:
                originals = [self._ensure_receipt(store, raw, expected, receipts)]
                receipts += originals
        for previous in receipts:
            info = previous.parameters.value()
            if info["original_packing_receipt_id"] is not None and (
                not originals or info["original_packing_receipt_id"] != originals[0].artifact_id
                or info["packing_tool_release_id"] != originals[0].parameters.value()[
                    "packing_tool_release_id"]
            ):
                raise BoundaryError("local_import", "source3_import_receipt_original_mismatch")
        if packed:
            if not originals:
                raise BoundaryError("local_import", "source3_packing_fact_required")
            receipt = originals[0]
            if receipt.producer != Producer.decode(self.operation["_receipt_producer"]):
                raise BoundaryError("local_import", "source3_receipt_producer_mismatch")
        else:
            original = originals[0] if originals else None
            params = self._receipt_metadata(
                candidate_id, candidate, raw, "reused_no_tool_invocation",
                original.parameters.value()["packing_tool_release_id"] if original else None,
                original.artifact_id if original else None)
            receipt = self._ensure_receipt(store, raw, params, receipts)
        self.operation["_import_receipt_id"] = receipt.artifact_id
        self._save()
        return receipt

    def _completed(self, candidate_id: str, artifact_id: str,
                   candidate: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": SCHEMA, "status": "completed", "candidate_id": candidate_id,
            "artifact_id": artifact_id, "finished_at": _now(),
        }
        if candidate.get("recording_type") == "p1_native":
            store, _ = _selected_store(self.config, require_curation=False)
            raw = store.get_manifest(artifact_id)
            result.update(recording_type="p1_native", session_id=candidate["session_id"],
                          timeline_id=candidate["timeline_id"],
                          bundle_content_id=raw.parameters.value()["content_id"],
                          human_origin_verified=False, research_admission="not_evaluated",
                          next_action="recordings.p1_derive")
        if candidate.get("recording_type") == "source3":
            store, _ = _selected_store(self.config)
            raw = store.get_manifest(artifact_id)
            receipt_id = digest(self.operation.get("_import_receipt_id"), "local_import.receipt")
            receipt = store.get_manifest(receipt_id)
            if receipt not in self._source3_receipts(store, raw, candidate):
                raise BoundaryError("local_import", "source3_import_receipt_required")
            info = receipt.parameters.value()
            if info["candidate_id"] != candidate_id:
                raise BoundaryError("local_import", "source3_import_receipt_candidate_mismatch")
            result.update(
                recording_type="source3", source_profile="native-logical-source-v3",
                session_id=candidate["session_id"], timeline_id=candidate["timeline_id"],
                tool_release_id=info["packing_tool_release_id"], human_origin_verified=False,
                registered_tool_release_id=info["registered_tool_release_id"],
                packing_tool_release_id=info["packing_tool_release_id"],
                tool_invocation=info["tool_invocation"], import_receipt_id=receipt_id,
                bundle_content_id=raw.parameters.value()["bundle_content_id"],
                next_action="datasets.source3-preview",
            )
        return result

    def _prepare_source3_receipt(self, candidate_id: str, candidate: dict[str, Any],
                                owner: LocalCurationOwner) -> None:
        # Persist the producer before any immutable receipt write. Retain the
        # captured producer/fact across explicit reconciliation of a partial import.
        fact = self.operation.get("_packing_fact")
        closed = fact.get("closed_metadata", {}) if isinstance(fact, dict) else {}
        related = (self.operation.get("candidate_id") == candidate_id
                   or all(closed.get(key) == candidate[key] for key in (
                       "session_id", "timeline_id", "manifest_sha256", "close_sha256")))
        producer = (Producer.decode(self.operation["_receipt_producer"])
                    if related and self.operation.get("_receipt_producer")
                    else source_identity(ROOT))
        self.operation = {"schema": SCHEMA, "status": "pending", "started_at": _now(),
                          "candidate_id": candidate_id, "recording_type": "source3",
                          "_owner": owner.identity, "_receipt_producer": producer.to_dict(),
                          "_receipt_reconciliation": bool(
                              related and self.operation.get("_receipt_producer")),
                          **({"_packing_fact": fact} if related and fact is not None else {})}
        self._save()

    def _bind_source3_candidate(self, candidate_id: str, artifact_id: str,
                                store: ManifestArtifactStore) -> None:
        owner = self._source3_owner(store)
        owner.begin_source(candidate_id)
        owner.published_source(candidate_id, artifact_id)
        with owner.transaction() as db:
            indexed = db.execute(
                "SELECT 1 FROM curation_sources s JOIN curation_exact_source_index i "
                "ON i.source=s.id WHERE s.id=? AND s.complete=1", (artifact_id,),
            ).fetchone() is not None
        if indexed:
            owner.complete_index(candidate_id, artifact_id)

    def start(self, candidate_id: object, human_origin_attested: object = None) -> dict[str, Any]:
        identity = digest(candidate_id, "local_import.candidate_id")
        with self.lock:
            self._native_recovery_guard()
            if self.operation["status"] == "unavailable":
                raise BoundaryError("local_import", "operation_file_invalid")
            candidate = self._fresh_candidate(identity)
            if candidate.get("recording_type") == "source3":
                if human_origin_attested is not None and human_origin_attested is not False:
                    raise BoundaryError("local_import", "source3_attestation_not_supported")
                if candidate.get("source3_tool_supported") is not True:
                    raise BoundaryError("local_import", "source3_tool_support_required")
                _source3_api()
            elif candidate.get("recording_type") == "p1_native":
                if candidate.get("p1_tool_supported") is not True:
                    raise BoundaryError("local_import", "p1_tool_support_required")
                if human_origin_attested is not None and type(human_origin_attested) is not bool:
                    raise BoundaryError("local_import", "invalid_human_origin_attestation")
                if (candidate["declared_origin"] != "declared_human"
                        and human_origin_attested not in {None, False}):
                    raise BoundaryError("local_import", "p1_origin_attestation_mismatch")
            elif human_origin_attested is not True:
                raise BoundaryError("local_import", "explicit_human_origin_attestation_required")
            if self.thread is not None and self.thread.is_alive():
                if self.operation.get("candidate_id") == identity:
                    return self.status()
                raise BoundaryError("local_import", "operation_in_progress")
            store, registry = _selected_store(self.config,
                require_curation=candidate.get("recording_type") != "p1_native")
            existing = self._existing_candidate(store, identity, candidate)
            if existing is not None:
                if candidate.get("recording_type") == "source3":
                    self._prepare_source3_receipt(identity, candidate, self._source3_owner(store))
                    try:
                        self._source3_receipt(identity, candidate, existing, store)
                        self._bind_source3_candidate(identity, existing, store)
                        _sync(store, registry)
                        self.operation = {**self.operation,
                                          **self._completed(identity, existing, candidate)}
                        self._save()
                    except Exception as error:
                        self._failed(identity, candidate, error)
                    return self.status()
                _sync(store, registry)
                self.operation = self._completed(identity, existing, candidate)
                self._save()
                return self.status()
            owner = (_selected_curation_owner(self.config)
                     if candidate.get("recording_type") != "p1_native" else None)
            if owner is not None:
                # Commit before any payload or manifest write. A crash leaves the
                # pending row visible to the Gold inventory transaction.
                owner.begin_source(identity)
            self.operation = {"schema": SCHEMA, "status": "pending", "candidate_id": identity,
                              "started_at": _now(),
                              "recording_type": candidate.get("recording_type", "legacy_human"),
                              **({"action": "import",
                                  "human_origin_attested": human_origin_attested is True,
                                  "_p1_candidate": {
                                  key: candidate[key] for key in (
                                      "recording_type", "session_id", "timeline_id",
                                      "recorder_source_revision", "manifest_sha256",
                                      "close_sha256", "p1_close_sha256")}}
                                 if candidate.get("recording_type") == "p1_native" else {}),
                              **({"_owner": owner.identity} if owner is not None
                                 and candidate.get("recording_type") == "source3" else {})}
            self._save()
            self.thread = threading.Thread(
                target=self._run, args=(identity, candidate), daemon=True,
            )
            self.thread.start()
            return self.status()

    def reconcile_p1_import(self) -> dict[str, Any]:
        """Resolve only the retained original candidate; do not pack or publish again."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            if (self.operation.get("recording_type") != "p1_native"
                    or self.operation.get("action") not in {"import", "import-bundle"}):
                raise BoundaryError("local_import", "p1_import_intent_required")
            intent = self.operation
            checked = False
            try:
                store, registry = _selected_store(self.config, require_curation=False)
                if intent["action"] == "import-bundle":
                    from .p1_recording_store import BUNDLE_SCHEMA, _matching

                    manifest = _matching(store, BUNDLE_SCHEMA, content_id=intent["content_id"])
                    identity = manifest.artifact_id if manifest else None
                    if manifest is not None:
                        for payload in manifest.payloads:
                            for _ in store.read_payload(payload):
                                pass
                else:
                    identity = self._existing_candidate(store, intent["candidate_id"],
                                                         intent["_p1_candidate"])
                if identity is None:
                    self.operation = {**intent, "status": "failed", "publication": "absent",
                                      "finished_at": _now()}
                else:
                    checked = True
                    self.operation = {**intent, "status": "published_index_unavailable",
                                      "artifact_id": identity}
                    _sync(store, registry)
                    self.operation.update(status="completed", finished_at=_now())
            except Exception as error:
                if not checked:
                    self.operation["status"] = "publication_unknown"
                self.operation["error_code"] = getattr(error, "code", "p1_reconciliation_failed")
            self._save()
            return self.status()

    def start_global_evidence(self, context: dict[str, Any],
                              peer: dict[str, Any] | None = None) -> dict[str, Any]:
        """Import only the stopped context resolved by the Application Model owner."""
        from .p1_recording_storage import open_peer

        with self.lock:
            self._native_recovery_guard()
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            if self.operation.get("status") in {
                    "pending", "publication_unknown", "interrupted_unknown"}:
                raise BoundaryError("local_import", "original_intent_reconciliation_required")
            if peer is not None:
                open_peer(peer)
            producer = source_identity(ROOT)
            frozen = copy.deepcopy(context)
            digest(frozen["model_artifact_id"], "global_store.model_artifact_id")
            self.operation = {
                "schema": SCHEMA, "recording_type": "p1_global", "action": "global-evidence",
                "operation_id": uuid.uuid4().hex, "status": "pending", "started_at": _now(),
                "run_id": frozen["expected"]["run_id"], "peer": copy.deepcopy(peer),
                "model_artifact_id": frozen["model_artifact_id"],
                "_global_context": frozen, "_producer": producer.to_dict(),
            }
            self._save()
            self.thread = threading.Thread(target=self._run_global_evidence, daemon=True)
            self.thread.start()
            return self.status()

    def _run_global_evidence(self) -> None:
        from spireagent.storage.store import copy_artifact

        from .global_evidence_store import (
            _runtime_files,
            _verified,
            publish_global_evidence,
            publish_global_runtime,
            verify_global_artifact,
        )
        from .p1_recording_storage import open_peer

        intent = copy.deepcopy(self.operation)
        context = intent["_global_context"]
        phase = "validation"
        try:
            store, registry = _selected_store(self.config, require_curation=False)
            producer = Producer.decode(intent["_producer"])
            # Reuse the owner checks before the first persistent write. Known
            # validation rejection is failed, not uncertain delivery.
            _verified(Path(context["directory"]), context["expected"])
            _runtime_files(context["expected"])
            phase = "runtime_publication"
            runtime = publish_global_runtime(store, producer, context["expected"])
            with self.lock:
                self.operation["runtime_artifact_id"] = runtime.artifact_id
                self._save()
            def before_publish(manifest: Manifest) -> None:
                with self.lock:
                    self.operation["artifact_id"] = manifest.artifact_id
                    self._save()

            phase = "global_publication"
            artifact = publish_global_evidence(
                store, Path(context["directory"]), producer, expected=context["expected"],
                model_artifact_id=context["model_artifact_id"],
                runtime_artifact_id=runtime.artifact_id, scratch=self.config.state_dir,
                before_publish=before_publish,
            )
            phase = "local_readback"
            local = verify_global_artifact(
                store, artifact.artifact_id, expected=context["expected"],
                scratch=self.config.state_dir,
            )
            remote = None
            if intent["peer"] is not None:
                peer = open_peer(intent["peer"])
                phase = "peer_delivery"
                copy_artifact(store, peer, artifact.artifact_id)
                phase = "peer_readback"
                remote = verify_global_artifact(
                    peer, artifact.artifact_id, expected=context["expected"],
                    scratch=self.config.state_dir,
                )
            phase = "registry"
            _sync(store, registry)
            with self.lock:
                self.operation.update(status="completed", finished_at=_now(),
                                      local_readback=local, peer_readback=remote)
                self._save()
        except Exception as error:
            with self.lock:
                uncertain = phase != "validation" and (
                    isinstance(error, StoreError) or not isinstance(error, BoundaryError))
                status = "publication_unknown" if uncertain else "failed"
                self.operation.update(status=status,
                    failure_phase=phase,
                    error_code=getattr(error, "code", "global_evidence_import_failed"))
                self._save()

    def reconcile_global_evidence(self) -> dict[str, Any]:
        """Read the original ID only; never repeat publication or cloud delivery."""
        from .global_evidence_store import verify_global_artifact
        from .p1_recording_storage import open_peer

        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            if self.operation.get("action") != "global-evidence":
                raise BoundaryError("local_import", "global_evidence_intent_required")
            identity = self.operation.get("artifact_id")
            if identity is None:
                return self.status()
            context = self.operation["_global_context"]
            try:
                store, registry = _selected_store(self.config, require_curation=False)
                local = verify_global_artifact(
                    store, identity, expected=context["expected"], scratch=self.config.state_dir,
                )
                remote = None
                if self.operation["peer"] is not None:
                    remote = verify_global_artifact(
                        open_peer(self.operation["peer"]), identity, expected=context["expected"],
                        scratch=self.config.state_dir,
                    )
                _sync(store, registry)
                self.operation.update(status="completed", finished_at=_now(),
                                      local_readback=local, peer_readback=remote)
                self.operation.pop("error_code", None)
            except Exception as error:
                self.operation.update(status="publication_unknown",
                    error_code=getattr(error, "code", "global_evidence_readback_failed"))
            self._save()
            return self.status()

    def start_p1_storage(self, action: str, artifact_id: str,
                         peer: dict[str, Any]) -> dict[str, Any]:
        from .p1_recording_storage import storage_intent

        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            restoring_interrupted = (
                action == "restore-expanded"
                and self.operation.get("recording_type") == "p1_native"
                and self.operation.get("action") == "storage"
                and self.operation.get("storage_action") in {"cool", "restore-expanded"}
                and self.operation.get("artifact_id") == artifact_id
                and self.operation.get("peer") == peer
                and self.operation.get("status") in {"publication_unknown", "interrupted_unknown"}
            )
            if not restoring_interrupted:
                self._native_recovery_guard()
            if not restoring_interrupted and self.operation.get("status") in {
                    "pending", "publication_unknown", "interrupted_unknown"}:
                raise BoundaryError("local_import", "original_intent_reconciliation_required")
            operation = {"schema": SCHEMA, **storage_intent(action, artifact_id, peer)}
            if action in {"cool", "restore-expanded"}:
                from .p1_recording_maintenance import _in_scope

                sessions = self._maintenance.get("sessions", {})
                session = sessions.get(artifact_id)
                if session is None:
                    raise BoundaryError("p1_storage", "maintained_session_required")
                policy = self._maintenance["policy"]
                if action == "cool" and (
                    not policy.get("cool_recent") or policy.get("peer") != peer
                    or not _in_scope(session["scope_facts"], policy.get("scope"))
                ):
                    raise BoundaryError("p1_storage", "cooling_scope_required")
                if action == "cool":
                    ordered = sorted(
                        (s for s in sessions.values()
                         if _in_scope(s["scope_facts"], policy.get("scope"))),
                        key=lambda s: (s["scope_facts"].get("closed_at", ""), s["session_id"]),
                    )
                    if artifact_id not in {s["source_artifact_id"] for s in ordered[:-2]}:
                        raise BoundaryError("p1_storage", "recent_two_sessions_retained")
                observed = self.catalog.read()
                matches = []
                if observed.get("status") == "ready":
                    for row in observed["candidates"]:
                        candidate = self.catalog.candidate(row["candidate_id"])
                        if (candidate is not None and candidate.get("recording_type") == "p1_native"
                                and candidate["session_id"] == session["session_id"]
                                and candidate["timeline_id"] == session["timeline_id"]):
                            matches.append(candidate)
                if len(matches) != 1:
                    raise BoundaryError("p1_storage", "original_closed_source_required")
                operation["_expanded_directory"] = str(matches[0]["source_directory"])
                session["expanded_state"] = "cooling" if action == "cool" else "restoring"
            self.operation = operation
            self._save()
            self.thread = threading.Thread(target=self._run_p1_storage, daemon=True)
            self.thread.start()
            return self.status()

    def reconcile_p1_storage(self) -> dict[str, Any]:
        from .p1_recording_storage import reconcile_storage

        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            if self.operation.get("action") != "storage":
                raise BoundaryError("local_import", "storage_intent_required")
            store, registry = _selected_store(self.config, require_curation=False)
            try:
                self.operation = reconcile_storage(
                    store, self.operation, scratch=self.config.state_dir)
                _sync(store, registry)
            except Exception as error:
                self.operation.update(status="publication_unknown",
                    error_code=getattr(error, "code", "p1_storage_reconciliation_failed"))
            self._save()
            return self.status()

    def _run_p1_storage(self) -> None:
        from .p1_recording_storage import expanded_storage, run_storage, storage_error_status

        intent = dict(self.operation)
        try:
            store, registry = _selected_store(self.config, require_curation=False)
            if intent["storage_action"] in {"cool", "restore-expanded"}:
                directory, release = current_collection_tool(self.config)
                result = expanded_storage(
                    store, intent, scratch=self.config.state_dir,
                    tool=CollectionTool(directory, release),
                )
            else:
                result = run_storage(store, intent, scratch=self.config.state_dir)
            _sync(store, registry)
            with self.lock:
                self.operation = result
                if intent["storage_action"] in {"cool", "restore-expanded"}:
                    self._maintenance["sessions"][intent["artifact_id"]].update(
                        expanded_state=result["storage_receipt"]["expanded_state"],
                        storage_receipt=result["storage_receipt"],
                    )
                self._save()
        except Exception as error:
            with self.lock:
                self.operation = {**intent, "status": storage_error_status(error),
                    "error_code": getattr(error, "code", "p1_storage_failed")}
                self._save()

    def start_p1_derivation(self, source_artifact_id: str, projection_id: str,
                            run_id: str = "run-0001") -> dict[str, Any]:
        from .p1_recording_store import BUNDLE_SCHEMA, PROJECTIONS

        source_id = digest(source_artifact_id, "p1.source_artifact_id")
        if (projection_id not in PROJECTIONS or not isinstance(run_id, str)
                or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", run_id) is None):
            raise BoundaryError("local_import", "invalid_p1_derivation_request")
        with self.lock:
            self._native_recovery_guard()
            if self.thread is not None and self.thread.is_alive():
                raise BoundaryError("local_import", "operation_in_progress")
            if (self.operation.get("recording_type") == "p1_native"
                    and self.operation["status"] in {
                        "pending", "publication_unknown", "interrupted_unknown"}):
                raise BoundaryError("local_import", "original_intent_reconciliation_required")
            store, _ = _selected_store(self.config, require_curation=False)
            source = store.get_manifest(source_id)
            if (source.kind != "evidence"
                    or source.parameters.value().get("schema") != BUNDLE_SCHEMA):
                raise BoundaryError("local_import", "p1_original_required")
            directory, release = current_collection_tool(self.config)
            tool = CollectionTool(directory, release)
            if not tool.supports_p1_native():
                raise BoundaryError("local_import", "p1_tool_support_required")
            self.operation = {"schema": SCHEMA, "recording_type": "p1_native", "action": "derive",
                              "status": "pending", "source_artifact_id": source_id,
                              "projection_id": projection_id, "run_id": run_id,
                              "tool_release_id": release, "started_at": _now()}
            self._save()
            self.thread = threading.Thread(target=self._run_p1_derivation,
                                           args=(tool,), daemon=True)
            self.thread.start()
            return self.status()

    def reconcile_p1_derivation(self) -> dict[str, Any]:
        from .p1_recording_store import _matching, projection_format

        with self.lock:
            if (self.thread is not None and self.thread.is_alive()
                    and self.thread is not threading.current_thread()):
                raise BoundaryError("local_import", "operation_in_progress")
            if (self.operation.get("recording_type") != "p1_native"
                    or self.operation.get("action") != "derive"):
                raise BoundaryError("local_import", "p1_derivation_intent_required")
            store, registry = _selected_store(self.config, require_curation=False)
            intent = self.operation
            verified_publication = False
            try:
                manifest = _matching(store, projection_format(intent["projection_id"])[0],
                    source_artifact_id=intent["source_artifact_id"],
                    projection_id=intent["projection_id"], run_id=intent["run_id"],
                    tool_release_id=intent["tool_release_id"])
                if manifest is None:
                    self.operation = {**intent, "status": "failed", "publication": "absent",
                                      "finished_at": _now()}
                else:
                    for payload in manifest.payloads:
                        for _ in store.read_payload(payload):
                            pass
                    verified_publication = True
                    self.operation = {**intent, "status": "published_index_unavailable",
                                      "artifact_id": manifest.artifact_id}
                    _sync(store, registry)
                    self.operation.update(status="completed", finished_at=_now())
            except Exception as error:
                if not verified_publication:
                    self.operation["status"] = "publication_unknown"
                self.operation["error_code"] = getattr(error, "code", "p1_reconciliation_failed")
            self._save()
            return self.status()

    def _run_p1_derivation(self, tool: CollectionTool) -> None:
        from .p1_recording_store import derive_p1_recording

        intent = dict(self.operation)
        try:
            store, registry = _selected_store(self.config, require_curation=False)
            manifest = derive_p1_recording(store, intent["source_artifact_id"], tool,
                source_identity(ROOT), scratch=self.config.state_dir,
                run_id=intent["run_id"], projection_id=intent["projection_id"])
            with self.lock:
                self.operation.update(status="published_index_unavailable",
                                      artifact_id=manifest.artifact_id)
                _sync(store, registry)
                self.operation.update(status="completed", finished_at=_now())
                self._save()
        except Exception as error:
            with self.lock:
                self.operation["error_code"] = getattr(error, "code", "p1_derivation_failed")
                self.operation["_diagnostic"] = getattr(error, "diagnostic", "")[-16384:]
            # Only read back the original intent; never re-execute an unknown write.
            if self.operation.get("artifact_id") is not None:
                with self.lock:
                    self._save()
            else:
                self.reconcile_p1_derivation()

    def _run(self, candidate_id: str, candidate: dict[str, Any]) -> None:
        try:
            artifact_id = self._import(candidate_id, candidate)
            with self.lock:
                self.operation = {**self.operation,
                                  **self._completed(candidate_id, artifact_id, candidate)}
                self._save()
        except Exception as error:
            self._failed(candidate_id, candidate, error)

    def _failed(self, candidate_id: str, candidate: dict[str, Any], error: Exception) -> None:
        code = error.code if isinstance(error, BoundaryError) else "local_import_failed"
        published = None
        reconciliation_unknown = False
        try:
            store, _ = _selected_store(self.config,
                require_curation=candidate.get("recording_type") != "p1_native")
            published = self._existing_candidate(store, candidate_id, candidate)
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
        store, registry = _selected_store(self.config,
            require_curation=candidate.get("recording_type") != "p1_native")
        existing = self._existing_candidate(store, candidate_id, candidate)
        if existing is not None:
            if candidate.get("recording_type") == "source3":
                self._prepare_source3_receipt(candidate_id, candidate, self._source3_owner(store))
                self._source3_receipt(candidate_id, candidate, existing, store)
                self._bind_source3_candidate(candidate_id, existing, store)
            _sync(store, registry)
            return existing
        if candidate.get("recording_type") == "source3":
            return self._import_source3(candidate_id, candidate, store, registry)
        if candidate.get("recording_type") == "p1_native":
            return self._import_p1(candidate_id, candidate, store, registry)
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
            owner = _selected_curation_owner(self.config)
            if owner is not None:
                owner.published_source(candidate_id, artifact_id)
            _sync(store, registry)
            return artifact_id

    def _import_p1(self, candidate_id: str, candidate: dict[str, Any],
                   store: ManifestArtifactStore, registry: SQLiteRegistry) -> str:
        from .p1_recording_storage import expanded_consumer

        with expanded_consumer(Path(candidate["source_directory"])):
            return self._import_p1_owned(candidate_id, candidate, store, registry)

    def _import_p1_owned(self, candidate_id: str, candidate: dict[str, Any],
                         store: ManifestArtifactStore, registry: SQLiteRegistry) -> str:
        from .p1_recording_store import publish_p1_original

        directory, release_id = current_collection_tool(self.config)
        if release_id != candidate["tool_release_id"]:
            raise BoundaryError("local_import", "candidate_tool_changed")
        tool = CollectionTool(directory, release_id)
        labels = _labels(self.config)
        from .p1_recording_storage import require_workspace_space

        directory = Path(candidate["source_directory"])
        files = [path for path in directory.rglob("*") if path.is_file()]
        expanded_estimate = sum(path.stat().st_size for path in files)
        # One packed working tree plus archive and one durable compressed copy.
        budget = require_workspace_space(self.config.state_dir,
            2 * expanded_estimate + 4096 * len(files), persistent_bytes=expanded_estimate,
            destination=(store.blobs.root if isinstance(store.blobs, LocalBlobStore) else None))
        self.operation["space_budget"] = budget
        self._save()
        with tempfile.TemporaryDirectory(prefix="p1-recording-import-",
                                         dir=self.config.state_dir) as name:
            bundle = Path(name) / "bundle"
            tool.pack_p1_native(candidate["source_directory"], bundle,
                                labels["worker_id"], labels["campaign_id"],
                                human_origin_attested=(
                                    self.operation.get("human_origin_attested") is True))
            if self._fresh_candidate(candidate_id) != candidate:
                raise BoundaryError("local_import", "candidate_changed_during_pack")
            verified = tool.verify_p1_native(bundle)
            recording = verified["recording"]
            if (recording["session_id"] != candidate["session_id"]
                    or recording["timeline_id"] != candidate["timeline_id"]
                    or _sha256_file(bundle / "raw/recording-manifest.json")
                    != candidate["manifest_sha256"]
                    or _sha256_file(bundle / "raw/session-close-receipt.json")
                    != candidate["close_sha256"]
                    or _sha256_file(bundle / "raw/p1-native-close.json")
                    != candidate["p1_close_sha256"]):
                raise BoundaryError("local_import", "packed_p1_identity_mismatch")
            raw = publish_p1_original(store, bundle, tool, source_identity(ROOT),
                                      scratch=Path(name))
            # This is preservation, not a legacy ResearchTransition source index.
            # P1 N/use indexing belongs to the explicitly selected projection consumer.
            _sync(store, registry)
            return raw.artifact_id

    def _import_source3(self, candidate_id: str, candidate: dict[str, Any],
                        store: ManifestArtifactStore, registry: SQLiteRegistry) -> str:
        from stpd.fullrun.ordered_source import publish_ordered_source_raw

        original = _original_recorder_producer(candidate["recorder_source_revision"])
        owner = self._source3_owner(store)
        tool_directory, release_id = current_collection_tool(self.config)
        if release_id != candidate["tool_release_id"]:
            raise BoundaryError("local_import", "candidate_tool_changed")
        tool = CollectionTool(tool_directory, release_id)
        if not tool.supports_source_v3():
            raise BoundaryError("local_import", "source3_tool_support_required")
        self._prepare_source3_receipt(candidate_id, candidate, owner)
        labels = _labels(self.config)
        with tempfile.TemporaryDirectory(prefix="source3-recording-import-",
                                         dir=self.config.state_dir) as name:
            bundle = Path(name) / "bundle"
            tool.pack_source_v3(candidate["source_directory"], bundle,
                                labels["worker_id"], labels["campaign_id"])
            if self._fresh_candidate(candidate_id) != candidate:
                raise BoundaryError("local_import", "candidate_changed_during_pack")
            verified = _verified_source3(bundle)
            _source3_identity(bundle, candidate, verified)
            if (verified.manifest["worker_id"] != labels["worker_id"]
                    or verified.manifest["campaign_id"] != labels["campaign_id"]
                    or verified.manifest["content_identity"]["packer_source_revision"]
                    != tool.manifest["identity"]["source_revision"]):
                raise BoundaryError("local_import", "packed_source3_tool_identity_mismatch")
            self._source3_owner(store)
            # The raw identity is deterministic, but the actual packing fact
            # must precede publication so a crash cannot erase its provenance.
            fact = {
                "schema": SOURCE3_RECEIPT_SCHEMA, "candidate_id": candidate_id,
                "session_id": candidate["session_id"], "timeline_id": candidate["timeline_id"],
                "manifest_sha256": candidate["manifest_sha256"],
                "close_sha256": candidate["close_sha256"],
                "bundle_content_id": verified.content_id,
                "registered_tool_release_id": release_id, "tool_invocation": "packed",
                "packing_tool_release_id": release_id, "original_packing_receipt_id": None,
                "human_origin_verified": False,
            }
            self.operation["_packing_fact"] = {
                "candidate_id": candidate_id, "tool_release_id": release_id,
                "closed_metadata": fact,
            }
            self._save()
            raw = publish_ordered_source_raw(store, bundle, original)
            self._source3_owner(store)
            # Recovery matches the immutable bundle and original seal even if
            # raw publication raised after writing its manifest.
            self._source3_receipt(candidate_id, candidate, raw.artifact_id, store, packed=True)
            owner.published_source(candidate_id, raw.artifact_id)
            _sync(store, registry)
            return raw.artifact_id
