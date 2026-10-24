"""GLOBAL blob storage checks use the component's standard-library test runner."""
import gzip
import hashlib
import tempfile
import unittest
from pathlib import Path

from sts2_platform_evidence import read_global_artifact
from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceError


class GlobalArtifactStorageTests(unittest.TestCase):
    def check_roundtrip(self, compressed):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = b'{"source":"public original"}\r\n' * 4000
            digest = hashlib.sha256(original).hexdigest()
            name = f"global-{digest}.bin" + (".gz" if compressed else "")
            path = root / name
            path.write_bytes(gzip.compress(original) if compressed else original)
            reference = {"path": name, "bytes": len(original), "sha256": digest}
            self.assertEqual(read_global_artifact(root, reference), original)
            if compressed:
                stored = path.read_bytes()
                for broken in (stored[:-8], b"broken gzip", stored[:10] + b"\xff" * 30):
                    path.write_bytes(broken)
                    with self.assertRaises(AgentRunEvidenceError):
                        read_global_artifact(root, reference)
                path.write_bytes(stored)
            with self.assertRaises(AgentRunEvidenceError):
                read_global_artifact(root, {**reference, "bytes": 10})
            path.write_bytes(gzip.compress(b"changed") if compressed else b"changed")
            with self.assertRaises(AgentRunEvidenceError):
                read_global_artifact(root, reference)

    def test_raw_original_roundtrip_and_changes(self):
        self.check_roundtrip(False)

    def test_gzip_original_roundtrip_and_changes(self):
        self.check_roundtrip(True)

    def test_no_foreign_paths_or_symlinked_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = hashlib.sha256(b"x").hexdigest()
            original = root / "private"
            original.write_bytes(b"x")
            reference = {"path": f"global-{digest}.bin", "bytes": 1, "sha256": digest}
            (root / reference["path"]).symlink_to(original)
            with self.assertRaises(AgentRunEvidenceError):
                read_global_artifact(root, reference)
            with self.assertRaises(AgentRunEvidenceError):
                read_global_artifact(root, {**reference, "path": "../private"})
