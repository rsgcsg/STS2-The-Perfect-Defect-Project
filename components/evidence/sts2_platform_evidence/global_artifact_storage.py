"""Lossless GLOBAL blob storage v1; original references bind decoded bytes.

The immutable directory inventory separately binds the physical compressed file.
This reader never changes input/causal semantics or selects executable code.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .agent_session_run_evidence import _require


def read_global_artifact(directory: Path, reference: Mapping[str, object], *,
                         maximum: int = 96 * 1024**2) -> bytes:
    name, size, digest = (reference.get(k) for k in ("path", "bytes", "sha256"))
    _require(isinstance(digest, str) and bool(re.fullmatch(r"[a-f0-9]{64}", digest))
             and isinstance(name, str)
             and name in {f"global-{digest}.bin", f"global-{digest}.bin.gz"}
             and type(size) is int and 0 < size <= min(maximum, 96 * 1024**2),
             "global_artifact_reference")
    path = directory / str(name)
    _require(path.is_file() and not path.is_symlink()
             and 0 < path.stat().st_size <= 96 * 1024**2, "global_artifact_file")
    opener = gzip.open if str(name).endswith(".gz") else open
    try:
        with opener(path, "rb") as stream:
            raw = stream.read(cast(int, size) + 1)
    except (OSError, EOFError, zlib.error):
        _require(False, "global_artifact_bytes")
        raise AssertionError("unreachable")
    _require(len(raw) == size and hashlib.sha256(raw).hexdigest() == digest,
             "global_artifact_bytes")
    return raw
