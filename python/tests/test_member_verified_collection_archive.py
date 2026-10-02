"""Synthetic member-export inventory checks for the local archive bridge."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import atomic_json
from spireagent.workbench.member_client import MemberClient


def saved_export(
    state: Path,
    archive: bytes,
    *,
    selected: bool = True,
    row_changes: dict | None = None,
    file_id_override: str | None = None,
    stored_bytes: bytes | None = None,
    symlink: bool = False,
) -> tuple[MemberClient, str, str]:
    artifact_id, upload_id = "a" * 64, "b" * 32
    row = {
        "artifact_id": artifact_id,
        "type": "payload",
        "role": "archive",
        "sha256": hashlib.sha256(archive).hexdigest(),
        "size": len(archive),
        "media_type": "application/gzip",
        "upload_id": upload_id,
    }
    row.update(row_changes or {})
    canonical_file_id = hashlib.sha256(
        json_bytes([row["artifact_id"], row["type"], row["role"]])
    ).hexdigest()
    row["file_id"] = file_id_override or canonical_file_id
    row["filename"] = row["file_id"] + (".json" if row["type"] == "manifest" else ".bin")
    content = {
        "schema": "stpd/project-export-v1",
        "policy": "stpd/project-sharing-v2",
        "selection": {
            "collections": [upload_id] if selected else [],
            "artifacts": [],
        },
        "files": [row],
        "total_bytes": row["size"],
        "files_count": 1,
        "scope": "selected_own_payloads",
        "non_claims": ["complete lineage cache", "research admission", "training permission"],
    }
    export_id = hashlib.sha256(json_bytes(content)).hexdigest()
    directory = state / "downloads" / export_id
    directory.mkdir(parents=True)
    atomic_json(directory / "inventory.json", {
        **content, "export_id": export_id, "created_at": "2026-01-01T00:00:00Z",
    })
    target = directory / row["file_id"]
    if symlink:
        other = directory / "archive-bytes"
        other.write_bytes(archive if stored_bytes is None else stored_bytes)
        target.symlink_to(other)
    else:
        target.write_bytes(archive if stored_bytes is None else stored_bytes)
    account = SimpleNamespace(config=SimpleNamespace(state_dir=state))
    return MemberClient(account), export_id, row["file_id"]


def test_selected_export_returns_exact_hash_verified_archive(tmp_path: Path) -> None:
    archive = b"synthetic collection gzip bytes"
    client, export_id, file_id = saved_export(tmp_path, archive)
    source = client.verified_collection_archive(
        export_id, file_id, maximum_archive_bytes=1024,
    )
    assert source.archive == archive
    assert source.archive_bytes == len(archive)
    assert source.archive_sha256 == hashlib.sha256(archive).hexdigest()
    assert source.file_id == file_id
    assert source.upload_id == "b" * 32


def test_saved_download_catalog_projects_only_collection_archive_metadata(tmp_path: Path) -> None:
    archive = b"synthetic private archive bytes"
    client, export_id, file_id = saved_export(tmp_path, archive)
    directory = tmp_path / "downloads" / export_id
    atomic_json(directory / "download.json", {
        "status": "verified", "export_id": export_id,
        "verified_files": 1, "verified_bytes": len(archive),
        "total_files": 1, "total_bytes": len(archive),
        "directory": str(directory), "training_admitted": False,
    })

    catalog = client.verified_collection_archive_catalog()

    assert catalog["schema"] == "stpd/local-member-collection-archive-catalog-v1"
    assert catalog["status"] == "ready"
    assert catalog["items"] == [{
        "export_id": export_id,
        "name": "项目成员集合归档",
        "file_id": file_id,
        "artifact_id": "a" * 64,
        "upload_id": "b" * 32,
        "size": len(archive),
    }]
    assert "directory" not in catalog and "archive" not in catalog
    encoded = json_bytes(catalog).decode()
    assert str(directory) not in encoded
    assert archive.decode() not in encoded


def test_saved_download_catalog_ignores_missing_or_unverified_receipts(tmp_path: Path) -> None:
    client, export_id, _ = saved_export(tmp_path, b"synthetic archive bytes")
    assert client.verified_collection_archive_catalog()["items"] == []

    directory = tmp_path / "downloads" / export_id
    atomic_json(directory / "download.json", {
        "status": "downloading", "export_id": export_id,
        "verified_files": 1, "verified_bytes": 24,
        "total_files": 1, "total_bytes": 24,
        "directory": str(directory), "training_admitted": False,
    })
    catalog = client.verified_collection_archive_catalog()
    assert catalog["items"] == []
    assert catalog["excluded_count"] == 1


@pytest.mark.parametrize(
    "row_changes",
    [
        {"upload_id": None},
        {"role": "records"},
        {"type": "manifest", "role": None, "upload_id": None,
         "media_type": "application/json"},
    ],
)
def test_saved_download_catalog_excludes_artifacts_and_non_archive_files(
    tmp_path: Path, row_changes: dict,
) -> None:
    archive = b"synthetic artifact bytes"
    client, export_id, _ = saved_export(tmp_path, archive, row_changes=row_changes)
    directory = tmp_path / "downloads" / export_id
    atomic_json(directory / "download.json", {
        "status": "verified", "export_id": export_id,
        "verified_files": 1, "verified_bytes": len(archive),
        "total_files": 1, "total_bytes": len(archive),
        "directory": str(directory), "training_admitted": False,
    })

    catalog = client.verified_collection_archive_catalog()

    assert catalog["items"] == []


@pytest.mark.parametrize(
    ("kwargs", "maximum", "expected_code"),
    [
        ({"selected": False}, 1024, "collection_archive_not_selected"),
        ({"file_id_override": "c" * 64}, 1024, "collection_archive_not_selected"),
        ({"row_changes": {"sha256": "d" * 64}}, 1024, "download_checksum_mismatch"),
        ({}, 2, "collection_archive_not_selected"),
        ({"symlink": True}, 1024, "download_file_unavailable"),
        ({"row_changes": {"type": "manifest", "role": None, "upload_id": None,
                          "media_type": "application/json"}},
         1024, "invalid_export_inventory"),
    ],
)
def test_inventory_archive_and_path_rejections(
    tmp_path: Path, kwargs: dict, maximum: int, expected_code: str,
) -> None:
    client, export_id, file_id = saved_export(tmp_path, b"synthetic bytes", **kwargs)
    with pytest.raises(BoundaryError) as failure:
        client.verified_collection_archive(
            export_id, file_id, maximum_archive_bytes=maximum,
        )
    assert failure.value.code == expected_code
