"""Reviewed structured code scopes; artifact data never chooses executable paths.

Source closure and the complete dependency lock are separate exact identities.
The numerical scope includes input/configuration, recovery and publication owners;
final package loading/export additionally verifies the independent inference scope.
"""

from __future__ import annotations

import hashlib
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError

from .canonical import semantic_hash

ROOT = Path(__file__).resolve().parents[1]
LEGACY_SCOPE = "python-structured-owner-source-and-lock-v1"
INFERENCE_SCOPE = "structured-inference-code-closure-v2"
TRAINING_SCOPE = "structured-numerical-training-code-closure-v2"
IDENTITY_SCHEMA = "stpd/structured-code-identity-v2"
SCOPED_PACKAGE_SCHEMA = "stpd/structured-m2-package-v2"
SCOPED_CONFIG_SCHEMA = "stpd/structured-policy-config-v2"
SCOPED_CHECKPOINT_SCHEMA = "stpd/structured-m2-training-checkpoint-v3"
SCOPED_RUN_SCHEMA = "stpd/structured-m2-run-v3"
SCOPED_MODEL_SCHEMA = "stpd/structured-m2-model-v3"
SCOPED_REPORT_SCHEMA = "stpd/structured-m2-training-report-v3"
SCOPED_ADAPTER_VERSION = "1.1.0"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_WEIGHTS_BYTES = 32 * 1024 * 1024

# Include package initializers even when they expose unused lazy legacy exports.
# Their exact source is part of the boundary, rather than inferred from one run.
_SHARED_PATHS = (
    "spireagent/__init__.py",
    "spireagent/artifact_contracts.py",
    "spireagent/encoding.py",
    "spireagent/json_boundary.py",
    "stpd/__init__.py",
    "stpd/canonical.py",
    "stpd/contracts.py",
    "stpd/fullrun/__init__.py",
    "stpd/fullrun/contracts.py",
    "stpd/fullrun/representation.py",
    "stpd/fullrun/semantic_projection.py",
    "stpd/fullrun/structured_inputs.py",
    "stpd/fullrun/structured_tree.py",
    "stpd/fullrun/text_menu_inputs.py",
    "stpd/linear_q.py",
    "stpd/models/__init__.py",
    "stpd/models/structured_m2.py",
    "stpd/models/structured_weights.py",
    "stpd/representation.py",
    "stpd/structured_code_scope.py",
    "stpd/workers/__init__.py",
    "stpd/workers/checkpoint_codec.py",
)
INFERENCE_PATHS = tuple(sorted((*_SHARED_PATHS,
    "stpd/policy/__init__.py",
    "stpd/policy/structured_export.py",
    "stpd/policy/structured_port.py",
)))
TRAINING_PATHS = tuple(sorted((*_SHARED_PATHS,
    "spireagent/storage/__init__.py",
    "spireagent/storage/blobs.py",
    "spireagent/storage/local.py",
    "spireagent/storage/run_reporter.py",
    "spireagent/storage/store.py",
    "stpd/fullrun/structured_sequences.py",
    "stpd/models/structured_engine.py",
    "stpd/models/structured_training.py",
    "stpd/structured_workload_contracts.py",
    "stpd/workers/reporting.py",
    "stpd/workers/structured_control.py",
    "stpd/workers/structured_execution.py",
    "stpd/workers/structured_run.py",
)))


def _regular_bytes(root: Path, relative: str) -> bytes:
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise BoundaryError("structured_code_scope", "trusted_source_missing_or_unsafe")
    if not current.is_file():
        raise BoundaryError("structured_code_scope", "trusted_source_missing_or_unsafe")
    return current.read_bytes()


def code_identity(scope: str, root: Path = ROOT) -> dict[str, str]:
    """Only these reviewed fixed inventories are executable identity scopes."""
    if scope not in {INFERENCE_SCOPE, TRAINING_SCOPE}:
        raise BoundaryError("structured_code_scope", "unsupported_scope")
    paths = INFERENCE_PATHS if scope == INFERENCE_SCOPE else TRAINING_PATHS
    root = root.resolve()
    rows = [{"path": relative, "sha256": hashlib.sha256(_regular_bytes(root, relative)).hexdigest()}
            for relative in paths]
    return {"schema": IDENTITY_SCHEMA, "scope": scope,
            "source_sha256": semantic_hash(rows),
            "dependency_lock_sha256": hashlib.sha256(_regular_bytes(root, "uv.lock")).hexdigest()}


def code_sha256(scope: str, root: Path = ROOT) -> str:
    return semantic_hash(code_identity(scope, root))


def inference_runtime(torch_version: str) -> dict[str, Any]:
    """Declared portable ABI; Torch local-version suffixes remain exact."""
    return {"profile": "structured-inference-cpu-float32-codec-v2",
            "device": "cpu", "dtype": "float32", "torch_version": torch_version,
            "safetensors": version("safetensors")}


def exporter_runtime() -> dict[str, str]:
    """Exporter platform provenance does not restrict portable inference admission."""
    return {"python": platform.python_version(), "system": platform.system(),
            "machine": platform.machine()}


def run_code_scope(schema: object) -> str:
    if schema == "stpd/structured-m2-run-v2":
        return LEGACY_SCOPE
    if schema == SCOPED_RUN_SCHEMA:
        return TRAINING_SCOPE
    raise BoundaryError("structured_workload", "exact_run_identity_mismatch_or_v1_not_resumable")


def checkpoint_schema(scope: str) -> str:
    if scope == LEGACY_SCOPE:
        return "stpd/structured-m2-training-checkpoint-v2"
    if scope == TRAINING_SCOPE:
        return SCOPED_CHECKPOINT_SCHEMA
    raise BoundaryError("structured_code_scope", "unsupported_scope")
