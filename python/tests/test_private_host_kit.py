from __future__ import annotations

import hashlib
import json
import tarfile
from io import BytesIO

import pytest
from private_host_fixture import private_host_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.kit_runtime import (
    PRIVATE_HOST_MANIFEST_KEY,
    PRIVATE_HOST_PACKAGE_DESTINATION,
    PRIVATE_HOST_PROFILE,
    private_host_files,
    private_host_runtime_pin,
    stage_private_host_runtime,
    validate_private_host_package_directory,
    verify_private_host_offline_install,
)


def _replace_archive_member(raw: bytes, name: str, payload: bytes | None,
                            *, symlink: str | None = None) -> bytes:
    source = BytesIO(raw)
    output = BytesIO()
    with tarfile.open(fileobj=source, mode="r:gz") as reader, tarfile.open(
            fileobj=output, mode="w:gz") as writer:
        for member in reader.getmembers():
            if member.name == name:
                continue
            handle = reader.extractfile(member) if member.isfile() else None
            writer.addfile(member, handle)
        if symlink is not None:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.SYMTYPE
            entry.linkname = symlink
            writer.addfile(entry)
        elif payload is not None:
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            entry.mode = 0o644
            writer.addfile(entry, BytesIO(payload))
    return output.getvalue()


def test_private_host_candidate_is_bound_to_selected_bom_and_bundle_bytes(tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    identity = private_host_runtime_pin(profile, archive, bom)
    assert identity["distribution"] == "private_kit_candidate"
    assert identity["host_runtime"]["release_asset_sha256"] == hashlib.sha256(
        archive).hexdigest()
    assert identity["bundled_connector_pin"]["transitive_zod"]["version"] == "3.25.76"

    changed = json.loads(profile)
    changed["host_runtime"]["source_revision"] = "9" * 40
    with pytest.raises(BoundaryError, match="private_host_bom_identity_mismatch"):
        private_host_runtime_pin(json.dumps(changed).encode(), archive, bom)

    changed = json.loads(profile)
    changed["bundled_connector_pin"]["component_source_digest_sha256"] = "9" * 64
    with pytest.raises(BoundaryError, match="private_host_connector_bom_identity_mismatch"):
        private_host_runtime_pin(json.dumps(changed).encode(), archive, bom)

    with pytest.raises(BoundaryError, match="private_host_archive_checksum_mismatch"):
        private_host_runtime_pin(profile, archive + b"changed", bom)

    stale_profile, stale_archive, current_bom = private_host_fixture(
        tmp_path / "stale-sdk", sdk_version="1.1.0-rc.1")
    with pytest.raises(BoundaryError, match="private_host_connector_bom_identity_mismatch"):
        private_host_runtime_pin(stale_profile, stale_archive, current_bom)


def test_private_host_archive_rejects_traversal_and_symlinks(tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    traversal = _replace_archive_member(archive, "package/../outside", b"private")
    value = json.loads(profile)
    value["host_runtime"]["release_asset_sha256"] = hashlib.sha256(traversal).hexdigest()
    with pytest.raises(BoundaryError, match="private_host_archive_unsafe_or_invalid"):
        private_host_runtime_pin(json.dumps(value).encode(), traversal, bom)

    linked = _replace_archive_member(
        archive, "package/node_modules/zod/escape", None, symlink="../../../../outside")
    value = json.loads(profile)
    value["host_runtime"]["release_asset_sha256"] = hashlib.sha256(linked).hexdigest()
    with pytest.raises(BoundaryError, match="private_host_archive_unsafe_or_invalid"):
        private_host_runtime_pin(json.dumps(value).encode(), linked, bom)


def test_optional_private_host_group_fails_closed_when_either_input_or_identity_is_missing(
        tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    assert private_host_files({}, {}, bom) is None
    with pytest.raises(BoundaryError, match="private_host_inventory_incomplete"):
        private_host_files({}, {PRIVATE_HOST_PROFILE: profile}, bom)
    with pytest.raises(BoundaryError, match="private_host_inventory_incomplete"):
        private_host_files({PRIVATE_HOST_MANIFEST_KEY: {"profile_sha256": "0" * 64,
                                                       "archive_sha256": "1" * 64}},
                           {PRIVATE_HOST_PROFILE: profile}, bom)
    with pytest.raises(BoundaryError, match="private_host_inventory_incomplete"):
        private_host_files({PRIVATE_HOST_MANIFEST_KEY: None}, {}, bom)


def test_private_host_bundle_installs_offline_without_scripts_and_imports_host_sdk_zod(
        tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    verify_private_host_offline_install(profile, archive, bom)


def test_private_host_runtime_stages_exact_tree_and_rejects_symlinked_bundle(tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    source = tmp_path / "release-source"
    source.mkdir()
    stage_private_host_runtime(profile, archive, bom, source)
    package = source / PRIVATE_HOST_PACKAGE_DESTINATION
    identity = private_host_runtime_pin(profile, archive, bom)
    assert validate_private_host_package_directory(package, identity) == (
        identity["host_runtime"]["package_content_sha256"])

    outside = tmp_path / "outside.js"
    outside.write_bytes((package / "node_modules/zod/index.js").read_bytes())
    linked = package / "node_modules/zod/index.js"
    linked.unlink()
    linked.symlink_to(outside)
    with pytest.raises(BoundaryError, match="private_host_installed_package_unsafe"):
        validate_private_host_package_directory(package, identity)
