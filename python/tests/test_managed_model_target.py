from __future__ import annotations

import json
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.managed_model_target import (
    BUILD_FIELDS,
    PACKAGE_FIELDS,
    TARGET_FIELDS,
    confirm_target,
    public_binding,
    public_target,
    runtime_arguments,
)


def target(tmp_path: Path) -> dict:
    return {
        "input_profile": "text-menu-v2", "profile_sha256": "a" * 64,
        "client_attachment": tmp_path / "private-client.json",
        "service_instance_id": "service-one", "runtime_instance_id": "runtime-one",
        "game_continuity_id": "episode-one",
        "host_package_pin": {"schema": "stpd/platform-host-runtime-pin-v1",
                             **dict.fromkeys(PACKAGE_FIELDS, "public-package-field")},
        "candidate_build": {**dict.fromkeys(BUILD_FIELDS, "public-build-field"),
                            "artifact": "/private/native.dll"},
        "token": "must-not-be-sealed",
    }


def test_explicit_load_seals_only_public_binding_and_exact_selected_instance(tmp_path):
    value = target(tmp_path)
    args = runtime_arguments(value, tmp_path / "models")
    path = Path(args[args.index("--managed-binding") + 1])
    payload = path.read_bytes()
    assert json.loads(payload) == public_binding(value)
    assert b"must-not-be-sealed" not in payload
    assert b"/private/native.dll" not in payload
    assert b"stpd/platform-host-runtime-pin-v1" not in payload
    assert args[args.index("--managed-attachment") + 1] == str(value["client_attachment"])
    for field in TARGET_FIELDS:
        flag = "--managed-expected-" + field.replace("_", "-")
        assert args[args.index(flag) + 1] == value[field]
    before = path.stat().st_mtime_ns
    assert runtime_arguments(value, tmp_path / "models") == args
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize("field", [*TARGET_FIELDS, "profile_sha256"])
def test_new_target_cannot_replace_loaded_target_without_explicit_load(tmp_path, field):
    original = target(tmp_path)
    expected = public_target(original)
    confirm_target(expected, original)
    changed = {**original, field: "different"}
    with pytest.raises(BoundaryError, match="managed_environment_target_changed"):
        confirm_target(expected, changed)


def test_binding_file_is_not_silently_replaced(tmp_path):
    value = target(tmp_path)
    args = runtime_arguments(value, tmp_path / "models")
    path = Path(args[args.index("--managed-binding") + 1])
    path.write_text('{}', encoding="utf-8")
    with pytest.raises(BoundaryError, match="managed_binding_collision"):
        runtime_arguments(value, tmp_path / "models")
    assert path.read_text(encoding="utf-8") == '{}'
