from __future__ import annotations

import hashlib
import json
import tarfile
from io import BytesIO

import pytest
from private_host_fixture import derived_private_host_fixture, private_host_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.kit_runtime import (
    PRIVATE_HOST_MANIFEST_KEY,
    PRIVATE_HOST_PACKAGE_DESTINATION,
    PRIVATE_HOST_PROFILE,
    _private_host_archive_records,
    _tree_sha256,
    _verify_private_host_provenance_report,
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


def _append_archive_member(raw: bytes, name: str, payload: bytes) -> bytes:
    """Keep the original tar entries and append a second regular member."""
    source = BytesIO(raw)
    output = BytesIO()
    with tarfile.open(fileobj=source, mode="r:gz") as reader, tarfile.open(
            fileobj=output, mode="w:gz") as writer:
        for member in reader:
            handle = reader.extractfile(member) if member.isfile() else None
            writer.addfile(member, handle)
        entry = tarfile.TarInfo(name)
        entry.size = len(payload)
        entry.mode = 0o644
        entry.mtime = 0
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


def test_private_host_archive_rejects_duplicate_regular_members_with_matching_outer_hash(
        tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as source:
        original = source.extractfile("package/package.json").read()

    for duplicate in (original, original + b"\nchanged"):
        repeated = _append_archive_member(archive, "package/package.json", duplicate)
        value = json.loads(profile)
        value["host_runtime"]["release_asset_sha256"] = hashlib.sha256(repeated).hexdigest()
        with pytest.raises(BoundaryError, match="private_host_archive_unsafe_or_invalid"):
            private_host_runtime_pin(json.dumps(value).encode(), repeated, bom)


def test_private_host_archive_preserves_exact_posix_case_in_member_identity(tmp_path):
    _, archive, _ = private_host_fixture(tmp_path)
    archive = _append_archive_member(archive, "package/CaseProbe.js", b"upper")
    archive = _append_archive_member(archive, "package/caseprobe.js", b"lower")
    records = _private_host_archive_records(archive)
    assert records["CaseProbe.js"][0] == b"upper"
    assert records["caseprobe.js"][0] == b"lower"


def test_private_host_archive_rejects_file_path_ancestor_conflicts(tmp_path):
    profile, archive, bom = private_host_fixture(tmp_path)
    conflict = _append_archive_member(archive, "package/package.json/child", b"child")
    value = json.loads(profile)
    value["host_runtime"]["release_asset_sha256"] = hashlib.sha256(conflict).hexdigest()
    with pytest.raises(BoundaryError, match="private_host_archive_unsafe_or_invalid"):
        private_host_runtime_pin(json.dumps(value).encode(), conflict, bom)


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


def test_derived_private_host_records_original_and_selected_current_sdk_identity(tmp_path):
    profile_raw, archive_raw, bom_raw = derived_private_host_fixture(tmp_path)
    identity = private_host_runtime_pin(profile_raw, archive_raw, bom_raw)
    derivation = identity["derivation"]
    assert derivation["source_archive"]["archive_sha256"] != hashlib.sha256(
        archive_raw).hexdigest()
    assert derivation["source_archive"]["filename"].endswith("rc.22.tgz")
    assert derivation["source_archive"]["package_content_sha256"] != identity[
        "host_runtime"]["package_content_sha256"]
    assert derivation["manifest_transform"]["source_dependencies"][
        "@rsgcsg/sts2-connector-client"].endswith("rc.1.tgz")
    assert derivation["selected_sdk"]["version"] == "1.3.0-rc.5"
    assert derivation["selected_sdk"]["source_revision"] == "d" * 40
    assert derivation["zod"]["version"] == "3.25.76"
    assert derivation["zod"]["integrity"].startswith("sha512-")


def test_derived_private_host_rejects_receipt_drift_manifest_edits_and_extra_files(tmp_path):
    profile_raw, archive_raw, bom_raw = derived_private_host_fixture(tmp_path)
    profile = json.loads(profile_raw)
    profile["derivation"]["source_archive"]["archive_sha256"] = "f" * 64
    with pytest.raises(BoundaryError, match="private_host_derivation_mismatch"):
        private_host_runtime_pin(json.dumps(profile).encode(), archive_raw, bom_raw)

    profile_raw, archive_raw, bom_raw = derived_private_host_fixture(tmp_path / "extra")
    changed_archive = _append_archive_member(archive_raw, "package/untracked.txt", b"extra")
    profile = json.loads(profile_raw)
    entries = {name: raw for name, (raw, _mode) in
               _private_host_archive_records(changed_archive).items()}
    profile["host_runtime"]["release_asset_sha256"] = hashlib.sha256(
        changed_archive).hexdigest()
    profile["host_runtime"]["package_content_sha256"] = _tree_sha256(entries)
    with pytest.raises(BoundaryError, match="private_host_unexpected_package_files"):
        private_host_runtime_pin(json.dumps(profile).encode(), changed_archive, bom_raw)

    profile_raw, archive_raw, bom_raw = derived_private_host_fixture(tmp_path / "manifest")
    with tarfile.open(fileobj=BytesIO(archive_raw), mode="r:gz") as archive:
        package = json.loads(archive.extractfile("package/package.json").read())
    package["author"] = "unapproved metadata"
    changed_archive = _replace_archive_member(
        archive_raw, "package/package.json", json.dumps(package).encode())
    profile = json.loads(profile_raw)
    entries = {name: raw for name, (raw, _mode) in
               _private_host_archive_records(changed_archive).items()}
    profile["host_runtime"]["release_asset_sha256"] = hashlib.sha256(
        changed_archive).hexdigest()
    profile["host_runtime"]["package_content_sha256"] = _tree_sha256(entries)
    with pytest.raises(BoundaryError, match="private_host_derivation_invalid"):
        private_host_runtime_pin(json.dumps(profile).encode(), changed_archive, bom_raw)


def test_derived_private_host_bundle_installs_offline_and_imports_exact_selected_sdk(tmp_path):
    profile, archive, bom = derived_private_host_fixture(tmp_path)
    verify_private_host_offline_install(profile, archive, bom)


def test_private_host_source_binding_rejects_unbound_provenance_claims():
    source_revision = "9" * 40
    identity_report = {
        "workspace_revision": source_revision,
        "components": {"host-runtime": {"source_file_count": 10}},
    }
    derivation = {
        "host_source": {"source_file_count": 10},
        "producer": {"workspace_revision": source_revision},
    }

    _verify_private_host_provenance_report(identity_report, derivation)

    wrong_count = {
        **derivation,
        "host_source": {"source_file_count": 11},
    }
    with pytest.raises(BoundaryError, match="private_host_source_file_count_mismatch"):
        _verify_private_host_provenance_report(identity_report, wrong_count)

    wrong_workspace = {
        **derivation,
        "producer": {"workspace_revision": "8" * 40},
    }
    with pytest.raises(BoundaryError, match="private_host_producer_workspace_mismatch"):
        _verify_private_host_provenance_report(identity_report, wrong_workspace)


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
