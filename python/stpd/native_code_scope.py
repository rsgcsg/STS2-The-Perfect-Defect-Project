"""Fixed native structured inference closure, distinct from legacy S0 scopes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .canonical import semantic_hash
from .structured_code_scope import INFERENCE_PATHS, ROOT, _regular_bytes

SCOPE = "native-structured-inference-code-closure-v1"
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


def native_code_identity(root: Path = ROOT) -> dict[str, str]:
    root = root.resolve()
    rows = [
        {"path": relative, "sha256": hashlib.sha256(_regular_bytes(root, relative)).hexdigest()}
        for relative in PATHS
    ]
    return {
        "schema": "stpd/native-structured-code-identity-v1",
        "scope": SCOPE,
        "source_sha256": semantic_hash(rows),
        "dependency_lock_sha256": hashlib.sha256(_regular_bytes(root, "uv.lock")).hexdigest(),
    }


def native_code_sha256(root: Path = ROOT) -> str:
    return semantic_hash(native_code_identity(root))
