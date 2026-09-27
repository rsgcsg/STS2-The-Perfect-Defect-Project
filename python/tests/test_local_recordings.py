from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_recordings import LocalRecordingCatalog


def _config(tmp_path: Path) -> ProjectConfig:
    return ProjectConfig(tmp_path / "state", "", "", None, combination())


def _sealed(directory: Path, session: str, timeline: str, *, close: bool = True) -> None:
    directory.mkdir(parents=True)
    manifest = {
        "schema": "sts2.human-annotator/recording-manifest-2",
        "session_id": session,
        "timeline_id": timeline,
        "created_at": "synthetic",
    }
    (directory / "recording-manifest.json").write_text(json.dumps(manifest))
    if close:
        receipt = {
            "schema": "sts2.human-annotator/session-close-1",
            "status": "closed",
            "session_id": session,
            "timeline_id": timeline,
            "closed_at": "2026-01-01T00:00:00Z",
        }
        (directory / "session-close-receipt.json").write_text(json.dumps(receipt))


def _owner(root: Path, **changes: object) -> dict:
    game_directory = root.parent / "synthetic-game"
    game_directory.mkdir(exist_ok=True)
    value = {
        "schema": "sts2.platform/collection-setup-1",
        "status": "blocked",
        "reason": "recording_root_not_configured",
        "game_running": False,
        "configured": False,
        "connected": False,
        "bound": False,
        "recordings_root": str(root.parent / "state" / "local-recordings-root-probe"),
        "game_directory": str(game_directory),
        "configured_recordings_root": str(root),
        "actual_recordings_root": None,
        "installed_artifact": {"sha256": "a" * 64, "module_version_id": "synthetic-mvid"},
        "errors": [],
    }
    value.update(changes)
    return value


def _service(
    tmp_path: Path, monkeypatch, status: dict | None
) -> tuple[LocalRecordingCatalog, list]:
    from spireagent.workbench import local_recordings

    calls = []

    class Tool:
        def __init__(self, directory, release_id):
            calls.append(("construct", directory, release_id))

        def setup_status(self, *, recordings_root):
            calls.append(("setup_status", recordings_root))
            return status

    monkeypatch.setattr(local_recordings, "current_collection_tool",
                        lambda config: (tmp_path / "verified-tool", "b" * 64))
    monkeypatch.setattr(local_recordings, "CollectionTool", Tool)
    return LocalRecordingCatalog(_config(tmp_path)), calls


def test_catalog_lists_only_matching_close_metadata_and_keeps_paths_server_side(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "native-recordings"
    root.mkdir()
    _sealed(root / "closed", "session-001", "timeline-001")
    _sealed(root / "open", "session-002", "timeline-002", close=False)
    bad = root / "mismatched"
    _sealed(bad, "session-003", "timeline-003")
    (bad / "session-close-receipt.json").write_text(json.dumps({
        "schema": "sts2.human-annotator/session-close-1", "status": "closed",
        "session_id": "session-other", "timeline_id": "timeline-003",
        "closed_at": "2026-01-01T00:00:00Z",
    }))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    catalog, calls = _service(tmp_path, monkeypatch, _owner(root))

    result = catalog.read()

    assert result["status"] == "ready"
    assert result["root_basis"] == "configured_only"
    assert result["candidate_count"] == 1
    assert result["unsealed_count"] == 1
    assert result["candidates"][0]["state"] == "close_metadata_present_pending_bundle_verification"
    assert str(root) not in json.dumps(result)
    candidate_id = result["candidates"][0]["candidate_id"]
    internal = catalog.candidate(candidate_id)
    assert internal["source_directory"] == root / "closed"
    assert catalog.candidate("../outside") is None
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert calls[1] == ("setup_status", tmp_path / "state" / "local-recordings-root-probe")
    assert not (tmp_path / "state" / "local-recordings-root-probe").exists()


def test_catalog_uses_current_runtime_actual_root_and_keeps_last_closed_session_visible(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "runtime-recordings"
    root.mkdir()
    _sealed(root / "last-closed", "session-closed", "timeline-closed")
    _sealed(root / "currently-open", "session-open", "timeline-open", close=False)
    status = _owner(root, game_running=True, reason="current_runtime_root_mismatch",
                    actual_recordings_root=str(root), connected=True,
                    loaded_identity={"runtime_instance_id": "synthetic", "process_id": 42,
                                     "game": {"version": "synthetic"}})
    catalog, _ = _service(tmp_path, monkeypatch, status)

    result = catalog.read()

    assert result["status"] == "ready"
    assert result["root_basis"] == "current_runtime"
    assert [row["session_id"] for row in result["candidates"]] == ["session-closed"]
    assert result["unsealed_count"] == 1


@pytest.mark.parametrize("changes", [
    {"errors": ["runtime_identity_unconfirmed"]},
    {"connected": False},
    {"loaded_identity": None},
    {"actual_recordings_root": None},
    {"reason": "runtime_identity_unconfirmed"},
])
def test_runtime_owner_failures_do_not_expose_candidates(
    tmp_path: Path, monkeypatch, changes: dict
) -> None:
    root = tmp_path / "native-recordings"
    root.mkdir()
    _sealed(root / "closed", "session-001", "timeline-001")
    owner_changes = {
        "game_running": True,
        "reason": "current_runtime_root_mismatch",
        "actual_recordings_root": str(root),
        "connected": True,
        "loaded_identity": {"runtime_instance_id": "synthetic", "process_id": 42,
                             "game": {"version": "synthetic"}},
    }
    owner_changes.update(changes)
    status = _owner(root, **owner_changes)
    catalog, _ = _service(tmp_path, monkeypatch, status)

    result = catalog.read()

    assert result["status"] == "owner_unavailable"
    assert "candidate_count" not in result and "candidates" not in result
    assert result["error_code"] == "native_recording_root_unavailable"


def test_missing_tool_registration_is_distinct_from_empty_recordings(
    tmp_path: Path, monkeypatch
) -> None:
    from spireagent.workbench import local_recordings

    monkeypatch.setattr(local_recordings, "current_collection_tool",
                        lambda config: (_ for _ in ()).throw(BoundaryError(
                            "collection_tool", "collection_tool_registration_missing_or_unsafe")))
    catalog = LocalRecordingCatalog(_config(tmp_path))

    result = catalog.read()

    assert result["status"] == "tool_registration_missing"
    assert result["error_code"] == "collection_tool_registration_missing"
    assert "candidate_count" not in result


def test_present_but_invalid_tool_registration_is_not_reported_as_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from spireagent.workbench import local_recordings
    from spireagent.workbench.collection_tool_registration import REGISTRATION_FILE

    config = _config(tmp_path)
    config.state_dir.mkdir()
    (config.state_dir / REGISTRATION_FILE).write_text("invalid")
    monkeypatch.setattr(local_recordings, "current_collection_tool",
                        lambda config: (_ for _ in ()).throw(BoundaryError(
                            "collection_tool", "collection_tool_registration_missing_or_unsafe")))

    result = LocalRecordingCatalog(config).read()

    assert result["status"] == "tool_unavailable"
    assert result["error_code"] == "collection_tool_registration_unavailable"
    assert "candidate_count" not in result


def test_symlinked_candidate_is_not_listed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "native-recordings"
    root.mkdir()
    source = tmp_path / "outside"
    _sealed(source, "session-001", "timeline-001")
    try:
        os.symlink(source, root / "linked", target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable")
    catalog, _ = _service(tmp_path, monkeypatch, _owner(root))

    result = catalog.read()

    assert result["status"] == "ready"
    assert result["candidate_count"] == 0
    assert result["candidates"] == []


def test_symlinked_seal_metadata_is_not_read(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "native-recordings"
    root.mkdir()
    source = tmp_path / "outside-session"
    _sealed(source, "session-001", "timeline-001")
    candidate = root / "linked-metadata"
    candidate.mkdir()
    try:
        (candidate / "recording-manifest.json").symlink_to(source / "recording-manifest.json")
        (candidate / "session-close-receipt.json").symlink_to(source / "session-close-receipt.json")
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are unavailable")
    catalog, _ = _service(tmp_path, monkeypatch, _owner(root))

    result = catalog.read()

    assert result["status"] == "ready"
    assert result["candidate_count"] == 0


def test_recording_catalog_stops_at_bounded_directory_limit(
    tmp_path: Path, monkeypatch
) -> None:
    from spireagent.workbench import local_recordings

    root = tmp_path / "native-recordings"
    root.mkdir()
    for index in range(4):
        _sealed(root / f"closed-{index}", f"session-{index:03}", f"timeline-{index:03}")
    monkeypatch.setattr(local_recordings, "MAX_ROOT_ENTRIES", 2)
    catalog, _ = _service(tmp_path, monkeypatch, _owner(root))

    result = catalog.read()

    assert result["status"] == "ready"
    assert result["candidate_count"] <= 2
    assert result["truncated"] is True


def test_invalid_native_root_owner_result_does_not_fall_back_to_default(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "must-not-scan"
    root.mkdir()
    _sealed(root / "closed", "session-001", "timeline-001")
    catalog, _ = _service(
        tmp_path, monkeypatch, _owner(root, errors=["installed_artifact_mismatch"])
    )

    result = catalog.read()

    assert result["status"] == "owner_unavailable"
    assert "candidate_count" not in result and "candidates" not in result
    assert result["error_code"] == "native_recording_root_unavailable"
