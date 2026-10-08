"""Fixed native structured inference closure, distinct from legacy S0 scopes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .canonical import semantic_hash
from .native_graph_spec import MODEL_SCHEMA as GRAPH_MODEL_SCHEMA
from .native_graph_spec import TRAINING_SCOPE as GRAPH_TRAINING_SCOPE
from .structured_code_scope import INFERENCE_PATHS, ROOT, TRAINING_PATHS, _regular_bytes

SCOPE = "native-structured-inference-code-closure-v1"
MODEL_SCHEMA = "stpd/native-structured-m2-model-v1"
GRAPH_SCOPE = "native-structured-graph-inference-code-closure-v1"
REQUIRED_METHODS = (
    "capabilities", "attach", "current", "events", "await", "cancel_wait", "detach",
    "renew", "read", "catalog", "resolve", "retain", "release", "submit", "result",
)
PATHS = tuple(
    sorted(
        set(INFERENCE_PATHS) - {"stpd/policy/structured_port.py"}
        | {
            "stpd/native_code_scope.py",
            "stpd/fullrun/native_structured_inputs.py",
            "stpd/fullrun/native_structured_sequences.py",
            "stpd/models/native_structured_scorer.py",
            "stpd/policy/native_structured_export.py",
            "stpd/policy/native_agent.py",
        }
    )
)


def is_native_model_schema(schema: object) -> bool:
    """Lightweight exact artifact gate; importing it requires no numerical backend."""
    return schema in (MODEL_SCHEMA, GRAPH_MODEL_SCHEMA)


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


TRAINING_SCOPE = "native-structured-numerical-training-code-closure-v1"
TRAINING_PATHS_NATIVE = tuple(sorted(set(TRAINING_PATHS) | set(PATHS) | {
    "stpd/fullrun/native_training_sequences.py",
}))


def native_training_code_identity(root: Path = ROOT, *, graph: bool = False) -> dict[str, str]:
    root = root.resolve()
    rows = [{"path": relative, "sha256": hashlib.sha256(_regular_bytes(root, relative)).hexdigest()}
            for relative in TRAINING_PATHS_NATIVE]
    return {"schema": "stpd/native-structured-training-code-identity-v1",
            "scope": GRAPH_TRAINING_SCOPE if graph else TRAINING_SCOPE,
            "source_sha256": semantic_hash(rows),
            "dependency_lock_sha256": hashlib.sha256(_regular_bytes(root, "uv.lock")).hexdigest()}
