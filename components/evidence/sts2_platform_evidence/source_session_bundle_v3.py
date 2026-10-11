"""Strict Source V3 version adapter over the shared Source bundle verifier."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from .core import VerificationResult, VerifierDescriptor
from .source_session_bundle_v2 import SourceSessionBundleV2, SourceSessionBundleV2Verifier, _SourceFormat

BUNDLE_SCHEMA = "sts2.annotator/source-session-bundle-3"
TYPE_ID = "source-session-bundle-v3"


@dataclass(frozen=True)
class SourceSessionBundleV3(SourceSessionBundleV2):
    boundaries: tuple[Mapping[str, Any], ...]
    final_input_prefix_ordinal: str


DESCRIPTOR = VerifierDescriptor(TYPE_ID, BUNDLE_SCHEMA, 3, SourceSessionBundleV3)


class SourceSessionBundleV3Verifier(SourceSessionBundleV2Verifier):
    descriptor = DESCRIPTOR
    format = _SourceFormat(3)
    value_type = SourceSessionBundleV3


def verify_source_session_bundle_v3(source: str | Path, expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundleV3]:
    return SourceSessionBundleV3Verifier().verify(source, expected)
