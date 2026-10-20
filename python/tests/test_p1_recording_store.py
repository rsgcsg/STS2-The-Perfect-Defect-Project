"""Real original-reader and CAS tests; synthetic release does not qualify deployment."""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest
from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_recordings import _closed_session
from spireagent.workbench.p1_recording_store import (
    BUNDLE_SCHEMA,
    derive_p1_recording,
    materialize_p1_original,
    publish_p1_original,
)
from sts2_platform_evidence.collection_tool import (
    P1_NATIVE_SUPPORT,
    CollectionFailure,
    CollectionTool,
    digest,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def original(tmp_path):
    # Reuse the production C# store/packer fixture already frozen by its owning JS test.
    text = (ROOT / "tools/test/p1-native-bundle.test.mjs").read_text()
    match = re.search(r"const packedFixture = `([^`]+)`", text)
    assert match is not None
    files = json.loads(gzip.decompress(base64.b64decode(match[1])))
    destination = tmp_path / "original"
    destination.mkdir()
    for name, encoded in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(encoded))
    # Owning fixture is rooted at the original bundle itself.
    candidates = list(destination.rglob("session-bundle-manifest.json"))
    assert len(candidates) == 1
    return candidates[0].parent


@pytest.fixture
def tool(tmp_path):
    directory = tmp_path / "release"
    content = {
        "platform-bom.json": b"{}",
        "sts2-human-annotator.dll": b"not executed in this portable reader test",
        "p1/tools/p1-native-bundle.mjs": (ROOT / "tools/p1-native-bundle.mjs").read_bytes(),
        "p1/tools/p1-native-to-p1.mjs": b"throw Error('conversion not part of this fixture');",
        "p1/components/connector/sdk/typescript/dist/p1Global/index.js": b"export {};",
        "setup/tools/p1-native-recording.mjs": b"throw Error('no gameplay in this fixture');",
    }
    for name, raw in content.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    identity = {"worktree": "clean", "source_revision": "a" * 40,
                "entrypoint": "sts2-human-annotator.dll",
                "supported_recording_schema": P1_NATIVE_SUPPORT["recording_schema"],
                "p1_native_support": P1_NATIVE_SUPPORT,
                "files": [{"path": path, "bytes": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest()}
                          for path, raw in sorted(content.items())]}
    release = digest(identity)
    (directory / "collection-tool.json").write_text(json.dumps({
        "schema": "sts2.evidence/collection-tool-1", "release_id": release,
        "identity": identity}))
    return CollectionTool(directory, release)


def test_pinned_original_reader_and_store_roundtrip_reuse(tmp_path, original, tool):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("synthetic/import-test", "a" * 40, "b" * 64)
    before = {str(p.relative_to(original)): p.read_bytes()
              for p in original.rglob("*") if p.is_file()}
    raw = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    assert raw.kind == "evidence" and raw.parameters.value()["schema"] == BUNDLE_SCHEMA
    assert raw.parameters.value()["research_admission"] == "not_evaluated"
    assert raw.parameters.value()["human_origin_verified"] is False
    assert publish_p1_original(store, original, tool, producer,
                               scratch=tmp_path / "scratch").artifact_id == raw.artifact_id
    assert store.manifest_ids() == (raw.artifact_id,)
    with materialize_p1_original(store, raw.artifact_id, scratch=tmp_path / "scratch",
                                 tool=tool) as restored:
        assert {str(p.relative_to(restored)): p.read_bytes()
                for p in restored.rglob("*") if p.is_file()} == before
    assert list((tmp_path / "scratch").iterdir()) == []
    assert {str(p.relative_to(original)): p.read_bytes()
            for p in original.rglob("*") if p.is_file()} == before


def test_original_tampering_and_mutated_fixed_release_are_rejected(tmp_path, original, tool):
    (original / "raw/p1-native-events.jsonl").write_bytes(b"{}\n")
    with pytest.raises(CollectionFailure, match="reader/converter failed"):
        tool.verify_p1_native(original)
    (tool.directory / "p1/tools/p1-native-bundle.mjs").write_bytes(b"export {};")
    with pytest.raises(ValueError, match="bytes differ"):
        tool.verify_p1_native(original)


def test_p1_is_explicitly_catalogued_and_invalid_origin_never_falls_into_legacy(original):
    raw = original / "raw"
    metadata = _closed_session(raw)
    assert metadata is not None and metadata["recording_type"] == "p1_native"
    manifest = raw / "recording-manifest.json"
    data = json.loads(manifest.read_text())
    data["p1_native_origin"] = "agent_protocol"
    manifest.write_text(json.dumps(data))
    assert _closed_session(raw) is None


def test_unknown_projection_rejected_before_materialization(tmp_path, tool):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    with pytest.raises(BoundaryError, match="unsupported_projection"):
        derive_p1_recording(store, "f" * 64, tool,
                            Producer("test", "a" * 40, "b" * 64), scratch=tmp_path,
                            projection_id="native-timeline-targets-1")
    assert not store.manifest_ids()


def test_import_reconciliation_uses_exact_raw_fingerprints(tmp_path, original, tool):
    from spireagent.workbench.local_recording_import import LocalRecordingImporter
    from spireagent.workbench.p1_recordings_cli import _reconcile

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    producer = Producer("test", "a" * 40, "b" * 64)
    operation = {"recording_type": "p1_native", "action": "import-bundle",
                 "content_id": tool.verify_p1_native(original)["manifest"]["bundle_content_id"]}
    assert _reconcile(store, operation)["publication"] == "absent"
    raw = publish_p1_original(store, original, tool, producer, scratch=tmp_path / "scratch")
    assert _reconcile(store, operation)["artifact_id"] == raw.artifact_id
    candidate = _closed_session(original / "raw")
    importer = object.__new__(LocalRecordingImporter)
    assert importer._existing_candidate(store, "unused", candidate) == raw.artifact_id
    for field in ("manifest_sha256", "close_sha256", "p1_close_sha256"):
        changed = {**candidate, field: "0" * 64}
        assert importer._existing_candidate(store, "unused", changed) is None
