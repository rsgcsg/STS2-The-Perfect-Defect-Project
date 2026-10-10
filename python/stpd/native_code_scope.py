"""Fixed native structured inference closure, distinct from legacy S0 scopes."""

from __future__ import annotations

import copy
import hashlib
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, digest, object_fields

from .canonical import semantic_hash
from .native_graph_spec import MODEL_SCHEMA as GRAPH_MODEL_SCHEMA
from .native_graph_spec import TRAINING_SCOPE as GRAPH_TRAINING_SCOPE
from .native_training_source_spec import MODEL_SCHEMA as COMMON_MODEL_SCHEMA
from .ordered_source_spec import MODEL_SCHEMA as ORDERED_MODEL_SCHEMA
from .ordered_source_spec import SCOPE as ORDERED_SCOPE
from .structured_code_scope import INFERENCE_PATHS, ROOT, TRAINING_PATHS, _regular_bytes

SCOPE = "native-structured-inference-code-closure-v1"
MODEL_SCHEMA = "stpd/native-structured-m2-model-v1"
COMPATIBLE_MODEL_SCHEMA = "stpd/native-compatible-model-v1"
GRAPH_SCOPE = "native-structured-graph-inference-code-closure-v1"
REQUIRED_METHODS = (
    "capabilities",
    "attach",
    "current",
    "events",
    "await",
    "cancel_wait",
    "detach",
    "renew",
    "read",
    "catalog",
    "resolve",
    "retain",
    "release",
    "submit",
    "result",
)
PATHS = tuple(
    sorted(
        set(INFERENCE_PATHS) - {"stpd/policy/structured_port.py"}
        | {
            "spireagent/storage/__init__.py",
            "spireagent/storage/blobs.py",
            "stpd/native_code_scope.py",
            "stpd/ordered_source_spec.py",
            "stpd/native_sampled_carry_spec.py",
            "stpd/native_agent_sampled_source_spec.py",
            "stpd/native_training_source_spec.py",
            "stpd/fullrun/native_structured_inputs.py",
            "stpd/fullrun/native_structured_sequences.py",
            "stpd/models/native_structured_scorer.py",
            "stpd/policy/native_structured_export.py",
            "stpd/policy/native_agent.py",
            "stpd/policy/native_operational_outcome.py",
            "stpd/policy/native_task.py",
        }
    )
)


def is_native_model_schema(schema: object) -> bool:
    """Lightweight exact artifact gate; importing it requires no numerical backend."""
    return schema in (MODEL_SCHEMA, GRAPH_MODEL_SCHEMA, ORDERED_MODEL_SCHEMA, COMMON_MODEL_SCHEMA,
                      COMPATIBLE_MODEL_SCHEMA)


def native_code_identity(root: Path = ROOT, *, graph: bool = False) -> dict[str, str]:
    root = root.resolve()
    rows = [
        {"path": relative, "sha256": hashlib.sha256(_regular_bytes(root, relative)).hexdigest()}
        for relative in PATHS
    ]
    return {
        "schema": "stpd/native-structured-code-identity-v1",
        "scope": GRAPH_SCOPE if graph else SCOPE,
        "source_sha256": semantic_hash(rows),
        "dependency_lock_sha256": hashlib.sha256(_regular_bytes(root, "uv.lock")).hexdigest(),
    }


def native_code_sha256(root: Path = ROOT, *, graph: bool = False) -> str:
    return semantic_hash(native_code_identity(root, graph=graph))


# One reviewed historical basis. Artifact fields never select a source ref,
# executable path, ignored dependency or arbitrary compatibility predicate.
COMPATIBILITY_PROFILE = "native-evidence-rc27-to-rc29-v1"
COMPATIBILITY_REPOSITORY = "rsgcsg/STS2-The-Perfect-Defect-Project"
ORIGINAL_EXPORT_REVISION = "9c1c14f447f7f6dd03c50f78ed8380da74e6a82f"
ORIGINAL_SOURCE_SHA256 = "516782ca56d9a1c8ad218804fd111731507175bd93855f0896a6e608387c25b2"
ORIGINAL_LOCK_SHA256 = "46eb838668b984bc4f0cab203a7b3c759115cf05fe2107b21a1730dd9808feea"
TARGET_LOCK_SHA256 = "6a14b4e84cd6317f474c0d247a7a99138d21e8f300b8f3c42199db5e5f270cd3"
_MIGRATION_OWNERS = frozenset({
    "stpd/native_code_scope.py", "stpd/policy/native_structured_export.py"})
_EVIDENCE = "rsgcsg-sts2-platform-evidence"
_PROJECT = "sts2-the-perfect-defect"


def _evidence_only_lock_delta(original: str, target: str) -> None:
    """Compare complete parsed locks; permit exactly the reviewed two records."""
    try:
        old, new = tomllib.loads(original), tomllib.loads(target)
        baseline, current = copy.deepcopy(old), copy.deepcopy(new)
        for value, version, revision in (
            (baseline, "0.1.0rc27", "cd319ba9146eddb39bb09439fc9b2e366061360e"),
            (current, "0.1.0rc29", "ba05adce73d067f4fb400fb2ac3af6a2b2279a80"),
        ):
            packages = value["package"]
            if not isinstance(packages, list) or len(packages) != 109:
                raise ValueError
            evidence = [p for p in packages if p.get("name") == _EVIDENCE]
            root = [p for p in packages if p.get("name") == _PROJECT]
            if len(evidence) != 1 or len(root) != 1:
                raise ValueError
            record = evidence[0]
            if record["version"] != version or record.get("dependencies", []):
                raise ValueError
            expected_git = ("https://github.com/rsgcsg/STS2-The-Perfect-Defect-Project.git"
                            "?subdirectory=components%2Fevidence&rev=" + revision)
            if record["source"] != {"git": expected_git + "#" + revision}:
                raise ValueError
            requirements = [r for r in root[0]["metadata"]["requires-dist"]
                            if r.get("name") == _EVIDENCE]
            if len(requirements) != 1 or requirements[0].get("git") != expected_git:
                raise ValueError
            record["version"] = "<reviewed-evidence-version>"
            record["source"] = {"git": "<reviewed-evidence-source>"}
            requirements[0]["git"] = "<reviewed-evidence-requirement>"
        if baseline != current:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("native_compatibility", "not_exact_evidence_only_lock_delta") from error


def compatibility_original_identity() -> dict[str, str]:
    return {"schema": "stpd/native-structured-code-identity-v1", "scope": GRAPH_SCOPE,
            "source_sha256": ORIGINAL_SOURCE_SHA256,
            "dependency_lock_sha256": ORIGINAL_LOCK_SHA256}


def checked_native_compatibility_proof(value: object) -> dict[str, Any]:
    proof = object_fields(value, {"profile", "original_export_producer", "source_files",
                                 "original_lock"}, "native_compatibility.proof")
    origin = Producer(COMPATIBILITY_REPOSITORY, ORIGINAL_EXPORT_REVISION, ORIGINAL_LOCK_SHA256)
    if (proof["profile"] != COMPATIBILITY_PROFILE
            or proof["original_export_producer"] != origin.to_dict()):
        raise BoundaryError("native_compatibility", "unsupported_original_basis")
    rows = proof["source_files"]
    if not isinstance(rows, list) or len(rows) != len(PATHS):
        raise BoundaryError("native_compatibility", "original_source_inventory")
    for row, path in zip(rows, PATHS, strict=True):
        entry = object_fields(row, {"path", "sha256"}, "native_compatibility.source_file")
        if entry["path"] != path:
            raise BoundaryError("native_compatibility", "original_source_inventory")
        digest(entry["sha256"], "native_compatibility.source_file.sha256")
        if (path not in _MIGRATION_OWNERS
                and hashlib.sha256(_regular_bytes(ROOT, path)).hexdigest() != entry["sha256"]):
            raise BoundaryError("native_compatibility", "inference_source_changed")
    if semantic_hash(rows) != ORIGINAL_SOURCE_SHA256:
        raise BoundaryError("native_compatibility", "original_source_identity")
    lock = proof["original_lock"]
    if not isinstance(lock, str) or len(lock.encode("utf-8")) > 256 * 1024:
        raise BoundaryError("native_compatibility", "original_lock_size")
    target = _regular_bytes(ROOT, "uv.lock")
    if (hashlib.sha256(lock.encode("utf-8")).hexdigest() != ORIGINAL_LOCK_SHA256
            or hashlib.sha256(target).hexdigest() != TARGET_LOCK_SHA256):
        raise BoundaryError("native_compatibility", "exact_lock_basis_required")
    _evidence_only_lock_delta(lock, target.decode("utf-8"))
    return proof


def native_compatibility_proof(origin: Producer) -> dict[str, Any]:
    """Read the code-owned original ref, never caller-chosen source bytes or paths."""
    expected = Producer(COMPATIBILITY_REPOSITORY, ORIGINAL_EXPORT_REVISION,
                        ORIGINAL_LOCK_SHA256)
    if origin != expected:
        raise BoundaryError("native_compatibility", "unsupported_original_basis")
    def original(relative: str) -> bytes:
        try:
            return subprocess.run(["git", "-C", str(ROOT.parent), "show",
                                   ORIGINAL_EXPORT_REVISION + ":python/" + relative],
                                  check=True, capture_output=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise BoundaryError("native_compatibility",
                                "original_git_source_unavailable") from error
    proof = {"profile": COMPATIBILITY_PROFILE, "original_export_producer": origin.to_dict(),
             "source_files": [{"path": path, "sha256": hashlib.sha256(original(path)).hexdigest()}
                              for path in PATHS],
             "original_lock": original("uv.lock").decode("utf-8")}
    return checked_native_compatibility_proof(proof)


TRAINING_SCOPE = "native-structured-numerical-training-code-closure-v1"
TRAINING_PATHS_NATIVE = tuple(
    sorted(
        set(TRAINING_PATHS)
        | set(PATHS)
        | {
            "stpd/fullrun/native_training_sequences.py",
            "stpd/fullrun/native_agent_sampled_source.py",
        }
    )
)


def native_training_code_identity(
    root: Path = ROOT, *, graph: bool = False, ordered: bool = False
) -> dict[str, str]:
    root = root.resolve()
    rows = [
        {"path": relative, "sha256": hashlib.sha256(_regular_bytes(root, relative)).hexdigest()}
        for relative in TRAINING_PATHS_NATIVE
    ]
    return {
        "schema": "stpd/native-structured-training-code-identity-v1",
        "scope": ORDERED_SCOPE if ordered else GRAPH_TRAINING_SCOPE if graph else TRAINING_SCOPE,
        "source_sha256": semantic_hash(rows),
        "dependency_lock_sha256": hashlib.sha256(_regular_bytes(root, "uv.lock")).hexdigest(),
    }
