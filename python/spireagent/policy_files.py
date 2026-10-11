"""Bounded local model metadata access; paths never authorize executable code."""

from pathlib import Path
from typing import Any

from .json_boundary import BoundaryError, decode_json

JSON_LIMIT = 1024 * 1024


def require_policy_decision_evidence_protocol(
    protocol: str, *, stage: str, code: str,
) -> None:
    """Require the installed reader's explicit immutable protocol capability.

    This is a model-neutral Evidence fact. Caller-owned error names remain at
    their existing API boundary; no version guess or source fallback is used.
    """
    try:
        import sts2_platform_evidence
    except ImportError as error:
        raise BoundaryError(stage, code) from error
    protocols = getattr(sts2_platform_evidence, "SUPPORTED_POLICY_DECISION_PROTOCOLS", None)
    if not isinstance(protocols, tuple) or protocol not in protocols:
        raise BoundaryError(stage, code)


def _object_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > JSON_LIMIT:
        raise BoundaryError("local_model", "metadata_missing_or_unsafe")
    value = decode_json(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundaryError("local_model", "invalid_metadata")
    return value


def _inside(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise BoundaryError("local_model", "invalid_registry_path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise BoundaryError("local_model", "invalid_registry_path")
    return path
