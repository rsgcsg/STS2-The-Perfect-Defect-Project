"""Exact historical source-read fixture shared by pure proof and package tests.

No numerical imports, executable historical module loading, or production waiver.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from spireagent.json_boundary import decode_json
from stpd import native_code_scope as scope_owner


def historical_operational_source(tmp_path: Path) -> Path:
    current = Path(__file__).resolve().parents[1]
    target = tmp_path / "historical-target-source"
    fixtures = Path(__file__).parent / "fixtures/native_compatibility"
    proof = decode_json((fixtures / "evidence_rc27_basis.json").read_bytes())
    for relative in (*scope_owner.PATHS, "uv.lock"):
        raw = (
            (fixtures / "evidence_rc27_structured_code_scope.py.txt").read_bytes()
            if relative == "stpd/structured_code_scope.py"
            else (current / relative).read_bytes()
        )
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    for row in proof["source_files"]:
        if row["path"] not in scope_owner._OPERATIONAL_MIGRATION_OWNERS:
            assert hashlib.sha256((target / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    # This is the reviewed pre-repair operational target, never today's changed source.
    assert scope_owner.native_code_identity(target, graph=True)["source_sha256"] == (
        "849219b978a0712647a09b025999a81c09b79d4b2b69f5c473db4856e4d223e9"
    )
    assert hashlib.sha256((target / "uv.lock").read_bytes()).hexdigest() == (
        scope_owner.OPERATIONAL_TARGET_LOCK_SHA256
    )
    return target
