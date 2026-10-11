"""Synthetic committed Source3 golden through the real catalog, verifier and store."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from test_local_recordings import _owner

from spireagent.artifact_contracts import Producer
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench import local_dataset, local_recording_import, local_recordings
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.managed_local_workspace import create_managed_workspace

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "components/evidence/tests/fixtures/source_session_v3/bundle"
ORIGINAL = Producer("fixture://original-source3-product", "e" * 40, "a" * 64)
DERIVED = Producer("fixture://derived-source3-product", "f" * 40, "b" * 64)
TOOL_REVISION = "c" * 40


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def reseal_pack(bundle: Path, worker: str, campaign: str) -> None:
    """Change synthetic packaging labels only; original raw/export evidence stays exact."""
    identity = json.loads((bundle / "content-identity.json").read_bytes())
    identity.update(worker_id=worker, campaign_id=campaign, packer_source_revision=TOOL_REVISION)
    write(bundle / "content-identity.json", identity)
    manifest = json.loads((bundle / "source-session-bundle-manifest.json").read_bytes())
    manifest.update(worker_id=worker, campaign_id=campaign, content_identity=identity,
                    bundle_content_id=sha(bundle / "content-identity.json"))
    write(bundle / "source-session-bundle-manifest.json", manifest)
    (bundle / "checksums.sha256").write_text("".join(
        f"{sha(path)}  {path.relative_to(bundle).as_posix()}\n"
        for path in sorted(bundle.rglob("*")) if path.is_file() and path.name != "checksums.sha256"
    ))


def setup(tmp_path: Path, monkeypatch, *, config: ProjectConfig | None = None,
          golden: Path = GOLDEN):
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = config.state_dir if config is not None else tmp_path / "state"
    state.mkdir(exist_ok=True)
    managed = create_managed_workspace(state)
    owner = managed["curation_owner"]
    config = config or ProjectConfig(state, "", "", None, combination())
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    root = tmp_path / "recordings"
    root.mkdir()
    session = root / "original-session"
    shutil.copytree(golden / "raw", session)
    original_owner = _owner(root)
    original_owner["recordings_root"] = str(state / "local-recordings-root-probe")

    class Tool:
        supported = True
        release_id = "d" * 64
        calls: list[tuple] = []
        fail = False

        def __init__(self, directory, release_id):
            assert release_id == Tool.release_id
            self.manifest = {"identity": {"source_revision": TOOL_REVISION}}

        def setup_status(self, **_kwargs):
            return original_owner

        def supports_source_v3(self):
            return Tool.supported

        def pack(self, *_args):
            raise AssertionError("Source3 cannot enter the legacy Human packer")

        def pack_source_v3(self, source, output, worker, campaign):
            assert source == session
            Tool.calls.append((source, worker, campaign))
            if Tool.fail:
                raise ValueError("synthetic pack failure")
            shutil.copytree(golden, output)
            reseal_pack(output, worker, campaign)

    for module in (local_recording_import, local_recordings):
        monkeypatch.setattr(module, "CollectionTool", Tool)
        monkeypatch.setattr(module, "current_collection_tool", lambda _config:
                            (tmp_path / "exact-synthetic-tool", Tool.release_id))
    monkeypatch.setattr(local_recording_import, "_original_recorder_producer", lambda revision:
                        ORIGINAL if revision == ORIGINAL.source_revision else None)
    monkeypatch.setattr(local_dataset, "source_identity", lambda _root: DERIVED)
    monkeypatch.setattr(local_recording_import, "source_identity", lambda _root: DERIVED)
    catalog = local_recordings.LocalRecordingCatalog(config)
    listing = catalog.read()
    assert listing["candidate_count"] == 1, listing
    candidate = listing["candidates"][0]["candidate_id"]
    importer = local_recording_import.LocalRecordingImporter(config, catalog)
    dataset = local_dataset.LocalDatasetService(config)
    return importer, dataset, catalog, candidate, store, owner, session, Tool


def settled(owner):
    assert owner.thread is not None
    owner.thread.join(timeout=15)
    assert not owner.thread.is_alive()
    status = owner.status()
    return status.get("operation", status)
