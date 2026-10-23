"""Finalized GLOBAL originals in the existing immutable ArtifactStore.

Evidence owns the format and replay. The caller supplies the trusted Runtime
installation; an archived path can never select executable replay code.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sts2_platform_evidence import AgentSessionRunEvidence, DirectoryTransferManifest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.encoding import semantic_hash
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.archives import _extract_tar
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import CHUNK_BYTES, ManifestArtifactStore
from stpd.p1.spec import MSTD_PACKAGE_SCHEMAS

from .p1_recording_storage import require_workspace_space
from .p1_recording_store import _verify_archive

RUN_SCHEMA = "sts2.policy-runtime/agent-session-run-1"
TYPE_ID = "policy-runtime-agent-session-run"
PROFILE = "p1-global-r1"
MAX_BYTES = 768 * 1024**2
MAX_FILES = 65542


def publish_detached_p1_model(
    store: ManifestArtifactStore,
    package_directory: Path,
    producer: Producer,
) -> Manifest:
    """Preserve an existing engineering export without inventing formal ancestry.

    The normal model owner still loads/checks tensor and InputSpec semantics.
    This import only binds the original descriptor and exact weight bytes.
    """
    descriptor_path, weights_path = (
        package_directory / "model.json",
        package_directory / "model.tensor-tree",
    )
    if (
        descriptor_path.is_symlink()
        or weights_path.is_symlink()
        or descriptor_path.stat().st_size > 1024**2
    ):
        raise BoundaryError("global_store", "original_model_package_required")
    descriptor = json.loads(descriptor_path.read_bytes())
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("schema") not in MSTD_PACKAGE_SCHEMAS
        or not isinstance(descriptor.get("input_spec"), dict)
    ):
        raise BoundaryError("global_store", "original_model_package_required")
    with weights_path.open("rb") as source:
        weights = store.put_payload("weights", source, "application/vnd.stpd.tensor-tree")
    if weights.sha256 != descriptor.get("package_sha256"):
        raise BoundaryError("global_store", "original_model_package_hash_mismatch")
    with descriptor_path.open("rb") as source:
        metadata = store.put_payload("package_manifest", source, "application/json")
    manifest = Manifest(
        "model",
        producer,
        payloads=(weights, metadata),
        parameters=FrozenObject.of(
            {
                "schema": descriptor["schema"],
                "package_sha256": weights.sha256,
                "input_spec": descriptor["input_spec"],
                "training_ancestry": "unknown_detached_engineering_export",
                "research_admission": "not_evaluated",
            }
        ),
    )
    store.publish(manifest)
    return manifest


def _verified(directory: Path, expected: Mapping[str, object]) -> AgentSessionRunEvidence:
    from sts2_platform_evidence import verify_agent_session_run_evidence

    if not isinstance(expected.get("global_runtime_directory"), str):
        raise BoundaryError("global_store", "trusted_runtime_required")
    result = verify_agent_session_run_evidence(directory, expected)
    if not result.passed or result.value is None:
        raise BoundaryError(
            "global_store",
            "typed_global_verification_failed",
            ",".join(f.code for f in result.findings),
        )
    if result.value.agent_manifest.get("input", {}).get("profile") != PROFILE:
        raise BoundaryError("global_store", "global_profile_required")
    return result.value


def _parents(
    store: ManifestArtifactStore,
    model_id: str,
    runtime_id: str,
    verified: AgentSessionRunEvidence,
    expected: Mapping[str, object],
) -> tuple[Parent, ...]:
    run = verified.manifest
    model, runtime = store.get_manifest(model_id), store.get_manifest(runtime_id)
    if model.kind != "model" or model.payload("weights").sha256 != run["agent_artifact_sha256"]:
        raise BoundaryError("global_store", "original_model_parent_mismatch")
    baseline = verified.agent_manifest.get("adapter", {}).get("id") == "stpd-p1-baseline-agent"
    package_schemas = {"stpd/p1-baseline-model-1"} if baseline else MSTD_PACKAGE_SCHEMAS
    metadata = model.payload("package_manifest")
    if metadata.size > 1024**2:
        raise BoundaryError("global_store", "model_descriptor_capacity")
    descriptor = json.loads(b"".join(store.read_payload(metadata)))
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("schema") not in package_schemas
        or descriptor.get("package_sha256") != model.payload("weights").sha256
        or model.parameters.value().get("input_spec") != descriptor.get("input_spec")
    ):
        raise BoundaryError("global_store", "original_model_descriptor_mismatch")
    binding = expected.get("model_binding")
    if binding is None:
        # The original sealed engineering run predates the explicit migration.
        if descriptor["input_spec"] != verified.agent_manifest["input"]["input_spec"]:
            raise BoundaryError("global_store", "original_model_descriptor_mismatch")
    else:
        if baseline:
            from stpd.p1.baseline_export import INPUT_PROJECTION

            projection = INPUT_PROJECTION
        else:
            from stpd.p1.input import INPUT_SPEC

            projection = INPUT_SPEC
        if (
            not isinstance(binding, dict)
            or binding.get("package_input_spec") != descriptor["input_spec"]
            or binding.get("input_spec") != verified.agent_manifest["input"]["input_spec"]
            or binding.get("input_projection") != projection
        ):
            raise BoundaryError("global_store", "trusted_model_binding_mismatch")
    if (
        runtime.kind != "protocol"
        or runtime.parameters.value().get("runtime_code_sha256") != run["runtime_code_sha256"]
        or runtime.parameters.value().get("runtime_version") != run["runtime_version"]
    ):
        raise BoundaryError("global_store", "original_runtime_parent_mismatch")
    root, files = _runtime_files(expected)
    runtime_files = {"runtime/" + p.relative_to(root).as_posix(): p for p in files}
    runtime_files["package_manifest"] = root.parent / "package.json"
    if {p.role for p in runtime.payloads} != set(runtime_files):
        raise BoundaryError("global_store", "original_runtime_parent_inventory")
    for payload in runtime.payloads:
        with runtime_files[payload.role].open("rb") as source:
            original = hashlib.file_digest(source, "sha256").hexdigest()
        if original != payload.sha256:
            raise BoundaryError("global_store", "original_runtime_parent_bytes")
    for manifest in (model, runtime):
        for payload in manifest.payloads:
            for _ in store.read_payload(payload):
                pass
    return (Parent("model", model_id), Parent("runtime", runtime_id))


def _runtime_files(expected: Mapping[str, object]) -> tuple[Path, list[Path]]:
    value = expected.get("global_runtime_directory")
    if not isinstance(value, str):
        raise BoundaryError("global_store", "trusted_runtime_required")
    root = Path(value)
    if (
        not root.is_absolute()
        or root.resolve() != root
        or not root.is_dir()
        or any(p.is_symlink() for p in (root, *root.parents))
    ):
        raise BoundaryError("global_store", "trusted_runtime_required")
    modules = sorted(root.glob("*.js"))
    digest = hashlib.sha256()
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise BoundaryError("global_store", "runtime_source_unsafe")
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    if not modules or digest.hexdigest() != expected.get("runtime_code_sha256"):
        raise BoundaryError("global_store", "original_runtime_code_mismatch")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    if any(p.is_symlink() for p in root.rglob("*")):
        raise BoundaryError("global_store", "runtime_source_unsafe")
    package_path = root.parent / "package.json"
    if package_path.is_symlink() or package_path.stat().st_size > 1024**2:
        raise BoundaryError("global_store", "original_runtime_package_required")
    package = json.loads(package_path.read_bytes())
    if (
        not isinstance(package, dict)
        or package.get("name") != "@rsgcsg/sts2-policy-runtime"
        or package.get("version") != expected.get("runtime_version")
    ):
        raise BoundaryError("global_store", "original_runtime_package_mismatch")
    return root, files


def publish_global_runtime(
    store: ManifestArtifactStore,
    producer: Producer,
    expected: Mapping[str, object],
) -> Manifest:
    """Preserve the exact trusted compiled Runtime as an ordinary protocol parent.

    This is provenance, not an installer or permission to execute stored bytes.
    Existing package installation and trust checks still own replay selection.
    """
    root, files = _runtime_files(expected)
    payloads = []
    for path in files:
        with path.open("rb") as source:
            payloads.append(
                store.put_payload("runtime/" + path.relative_to(root).as_posix(), source)
            )
    package = root.parent / "package.json"
    with package.open("rb") as source:
        payloads.append(store.put_payload("package_manifest", source, "application/json"))
    manifest = Manifest(
        "protocol",
        producer,
        payloads=tuple(payloads),
        parameters=FrozenObject.of(
            {
                "package": "@rsgcsg/sts2-policy-runtime",
                "runtime_version": expected["runtime_version"],
                "runtime_code_sha256": expected["runtime_code_sha256"],
                "purpose": "original_compiled_runtime_provenance_not_execution_authority",
            }
        ),
    )
    store.publish(manifest)
    return manifest


def publish_global_evidence(
    store: ManifestArtifactStore,
    directory: Path,
    producer: Producer,
    *,
    expected: Mapping[str, object],
    model_artifact_id: str,
    runtime_artifact_id: str,
    scratch: Path,
    before_publish: Callable[[Manifest], None] | None = None,
) -> Manifest:
    """Verify a stopped original, preserve every file, publish its real ancestry."""
    verified = _verified(directory, expected)
    parents = _parents(store, model_artifact_id, runtime_artifact_id, verified, expected)
    transfer = DirectoryTransferManifest.from_directory(
        directory,
        content_id=verified.content_id,
        artifact_type=TYPE_ID,
    )
    # GLOBAL content identity covers the complete sealed directory, including
    # checksums and immutable-manifest files. Bind the copied inventory before
    # publication, so a changed source cannot borrow a prior typed verdict.
    content = {
        "schema": "sts2.evidence/store-directory-1",
        "files": [f.to_dict() for f in transfer.files],
    }
    if semantic_hash(content) != verified.content_id:
        raise BoundaryError("global_store", "original_changed_after_verification")
    size = sum(f.bytes for f in transfer.files)
    if len(transfer.files) > MAX_FILES or size > MAX_BYTES:
        raise BoundaryError("global_store", "global_inventory_capacity")
    # tar headers and gzip incompressible overhead; reserve is kept separately.
    archive_bound = size + 4096 * len(transfer.files) + 1024**2
    require_workspace_space(
        scratch,
        archive_bound + CHUNK_BYTES,
        persistent_bytes=archive_bound,
        destination=store.blobs.root if isinstance(store.blobs, LocalBlobStore) else None,
    )
    from .local_recording_import import _archive_verified_bundle

    with tempfile.TemporaryDirectory(prefix="global-import-", dir=scratch) as name:
        archive = Path(name) / "original.tar.gz"
        _archive_verified_bundle(
            directory,
            archive,
            transfer,
            max_bytes=MAX_BYTES,
            max_files=MAX_FILES,
            max_archive_bytes=archive_bound,
        )
        _verify_archive(archive, transfer)
        with archive.open("rb") as source:
            payload = store.put_payload("archive", source, "application/gzip")
    index = store.put_bytes("transfer", json_bytes(transfer.to_dict()))
    manifest = Manifest(
        "evidence",
        producer,
        parents=parents,
        payloads=(payload, index),
        parameters=FrozenObject.of(_facts(verified)),
    )
    if before_publish is not None:
        before_publish(manifest)
    store.publish(manifest)
    return manifest


def _facts(verified: AgentSessionRunEvidence) -> dict[str, Any]:
    return {
        "schema": RUN_SCHEMA,
        "profile": PROFILE,
        "content_id": verified.content_id,
        "run_id": verified.run_id,
        "event_count": verified.event_count,
        "agent_artifact_sha256": verified.manifest["agent_artifact_sha256"],
        "runtime_code_sha256": verified.manifest["runtime_code_sha256"],
        "runtime_version": verified.manifest["runtime_version"],
        "status": verified.manifest["status"],
        "tainted": verified.manifest["tainted"],
        "purpose": "verified_original_preservation",
        "human_origin_verified": False,
        "research_admission": "not_evaluated",
    }


def _transfer(
    store: ManifestArtifactStore,
    manifest: Manifest,
    scratch: Path,
) -> DirectoryTransferManifest:
    info = manifest.parameters.value()
    if (
        manifest.kind != "evidence"
        or info.get("schema") != RUN_SCHEMA
        or info.get("profile") != PROFILE
        or {p.role for p in manifest.payloads} != {"archive", "transfer"}
        or {p.role for p in manifest.parents} != {"model", "runtime"}
    ):
        raise BoundaryError("global_store", "global_original_required")
    if manifest.payload("transfer").size > 16 * 1024**2:
        raise BoundaryError("global_store", "inventory_size_limit")
    with tempfile.TemporaryDirectory(prefix="global-index-", dir=scratch) as name:
        path = store.materialize(manifest.payload("transfer"), Path(name) / "transfer.json")
        transfer = DirectoryTransferManifest.read(path)
    if (
        transfer.artifact_type != TYPE_ID
        or transfer.content_id != info.get("content_id")
        or len(transfer.files) > MAX_FILES
        or sum(f.bytes for f in transfer.files) > MAX_BYTES
    ):
        raise BoundaryError("global_store", "inventory_identity_or_capacity")
    return transfer


@contextmanager
def materialize_global_evidence(
    store: ManifestArtifactStore,
    artifact_id: str,
    *,
    expected: Mapping[str, object],
    scratch: Path,
) -> Iterator[AgentSessionRunEvidence]:
    """By-ID full readback through the original typed verifier, never archive code."""
    scratch.mkdir(parents=True, exist_ok=True)
    manifest = store.get_manifest(artifact_id)
    transfer = _transfer(store, manifest, scratch)
    require_workspace_space(
        scratch, manifest.payload("archive").size + sum(f.bytes for f in transfer.files)
    )
    with tempfile.TemporaryDirectory(prefix="global-read-", dir=scratch) as name:
        root = Path(name)
        archive = store.materialize(manifest.payload("archive"), root / "original.tar.gz")
        directory = root / "original"
        directory.mkdir()
        with gzip.open(archive, "rb") as source:
            _extract_tar(source, directory, max_bytes=MAX_BYTES, max_files=MAX_FILES)
        actual = DirectoryTransferManifest.from_directory(
            directory,
            content_id=transfer.content_id,
            artifact_type=TYPE_ID,
        )
        if actual != transfer:
            raise BoundaryError("global_store", "materialized_original_mismatch")
        verified = _verified(directory, expected)
        info = manifest.parameters.value()
        if verified.content_id != transfer.content_id or _facts(verified) != info:
            raise BoundaryError("global_store", "typed_original_binding_mismatch")
        _parents(store, manifest.parent("model"), manifest.parent("runtime"), verified, expected)
        yield verified


def verify_global_artifact(
    store: ManifestArtifactStore,
    artifact_id: str,
    *,
    expected: Mapping[str, object],
    scratch: Path,
) -> dict[str, Any]:
    with materialize_global_evidence(
        store, artifact_id, expected=expected, scratch=scratch
    ) as value:
        manifest = store.get_manifest(artifact_id)
        return {
            "artifact_id": artifact_id,
            "content_id": value.content_id,
            "run_id": value.run_id,
            "event_count": value.event_count,
            "archive_bytes": manifest.payload("archive").size,
            "full_readback": True,
            "typed_verification": "pass",
            "human_origin_verified": False,
            "research_admission": "not_evaluated",
        }
