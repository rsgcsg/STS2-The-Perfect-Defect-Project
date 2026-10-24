import gzip
import hashlib

import pytest

from sts2_platform_evidence import read_global_artifact
from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceError


@pytest.mark.parametrize("compressed", [False, True])
def test_original_bytes_survive_physical_storage_and_detect_changes(tmp_path, compressed):
    original = b'{"source":"public original"}\r\n' * 4000
    digest = hashlib.sha256(original).hexdigest()
    name = f"global-{digest}.bin" + (".gz" if compressed else "")
    path = tmp_path / name
    path.write_bytes(gzip.compress(original) if compressed else original)
    reference = {"path": name, "bytes": len(original), "sha256": digest}
    assert read_global_artifact(tmp_path, reference) == original
    if compressed:
        stored = path.read_bytes()
        for broken in (stored[:-8], b"broken gzip", stored[:10] + b"\xff" * 30):
            path.write_bytes(broken)
            with pytest.raises(AgentRunEvidenceError):
                read_global_artifact(tmp_path, reference)
        path.write_bytes(stored)
    with pytest.raises(AgentRunEvidenceError):
        read_global_artifact(tmp_path, {**reference, "bytes": 10})
    path.write_bytes(gzip.compress(b"changed") if compressed else b"changed")
    with pytest.raises(AgentRunEvidenceError):
        read_global_artifact(tmp_path, reference)


def test_no_foreign_paths_or_symlinked_payloads(tmp_path):
    digest = hashlib.sha256(b"x").hexdigest()
    original = tmp_path / "private"
    original.write_bytes(b"x")
    reference = {"path": f"global-{digest}.bin", "bytes": 1, "sha256": digest}
    (tmp_path / reference["path"]).symlink_to(original)
    with pytest.raises(AgentRunEvidenceError):
        read_global_artifact(tmp_path, reference)
    with pytest.raises(AgentRunEvidenceError):
        read_global_artifact(tmp_path, {**reference, "path": "../private"})
