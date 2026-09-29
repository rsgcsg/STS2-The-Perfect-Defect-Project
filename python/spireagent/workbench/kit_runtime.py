"""Optional text Runtime identities carried by an approved developer kit."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.workbench.runtime_install import ARCHIVE_LIMIT, BUNDLED_LAYOUT, RUNTIME_PACKAGE

TEXT_RUNTIME_PROFILE = "text-runtime/profile.json"
TEXT_RUNTIME_ARCHIVE = "text-runtime/runtime.tgz"
TEXT_RUNTIME_DESTINATION = "python/.local/text-menu-runtime-v1.json"
TEXT_ARCHIVE_DESTINATION = "python/.local/text-menu-runtime-v1.tgz"
M2_RUNTIME_PROFILE = "m2-runtime/profile.json"
M2_RUNTIME_ARCHIVE = "m2-runtime/runtime.tgz"
M2_RUNTIME_DESTINATION = "python/.local/text-menu-m2-runtime-v1.json"
M2_ARCHIVE_DESTINATION = "python/.local/text-menu-m2-runtime-v1.tgz"


def text_runtime_pin(profile_raw: bytes, archive_raw: bytes, *,
                     memory: bool = False) -> dict[str, Any]:
    """Check the externally approved profile against inventoried archive bytes."""
    if len(archive_raw) > ARCHIVE_LIMIT:
        raise BoundaryError("developer_kit", "text_runtime_archive_too_large")
    profile = decode_json(profile_raw)
    if not isinstance(profile, dict) or set(profile) != {"schema", "runtime_package"}:
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    pin = profile["runtime_package"]
    if (profile["schema"] != ("stpd/local-text-m2-runtime-v1" if memory
                              else "stpd/local-text-runtime-v1")
            or not isinstance(pin, dict)
            or set(pin) != {"package", "version", "source_revision",
                            "component_tree_revision", "release_asset_sha256",
                            "package_content_sha256", "dependency_layout",
                            "bundled_connector_pin"}
            or pin.get("package") != RUNTIME_PACKAGE
            or pin.get("dependency_layout") != BUNDLED_LAYOUT):
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    for field in ("source_revision", "component_tree_revision"):
        digest(pin.get(field), "developer_kit.text_runtime_source", length=40)
    digest(pin.get("package_content_sha256"), "developer_kit.text_runtime_content")
    if (not isinstance(pin.get("version"), str)
            or not re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", pin["version"])
            or not isinstance(pin.get("bundled_connector_pin"), dict)):
        raise BoundaryError("developer_kit", "text_runtime_profile_invalid")
    digest(pin.get("release_asset_sha256"), "developer_kit.text_runtime_archive")
    if pin["release_asset_sha256"] != hashlib.sha256(archive_raw).hexdigest():
        raise BoundaryError("developer_kit", "text_runtime_archive_checksum_mismatch")
    return pin
