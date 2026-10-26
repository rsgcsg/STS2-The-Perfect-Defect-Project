from __future__ import annotations

import hashlib
import json
from pathlib import Path

from spireagent.workbench.initial_progress_original import capture_owned_progress


def native(tmp_path: Path):
    state = tmp_path / "native-host"
    owner = state / "profiles" / "fixture-profile"
    profile = owner / "user-data" / "default" / "1" / "profile1"
    (profile / "saves").mkdir(parents=True)
    (owner / "headless-profile.json").write_text(
        json.dumps({"generation_id": "native-generation", "profile_id": "fixture-profile"})
    )
    identity = {
        "host": {"runtime_instance_id": "native-runtime"},
        "profile": {
            "profile_root": str(owner),
            "profile_id": "fixture-profile",
            "generation_id": "native-generation",
        },
        "baseline": {
            "ready": True,
            "runtime_instance_id": "native-runtime",
            "profile_generation_id": "native-generation",
            "native_profile_id": 1,
            "native_profile_path": str(profile),
        },
    }
    return state, owner, profile / "saves" / "progress.save", identity


def test_progress_original_copies_literal_bytes_into_resource_generation(tmp_path):
    state, owner, source, identity = native(tmp_path)
    raw = b'\xff\x00\r\n { "untouched": true } \n'
    source.write_bytes(raw)
    result = capture_owned_progress(
        identity, state, generation="resource-generation", seed="fixture-seed"
    )
    descriptor = Path(result["path"])
    assert hashlib.sha256(descriptor.read_bytes()).hexdigest() == result["sha256"]
    metadata = json.loads(descriptor.read_bytes())
    assert metadata["status"] == "captured"
    assert metadata["runtime_instance_id"] == "native-runtime"
    assert metadata["profile_generation_id"] == "native-generation"
    assert metadata["bytes"] == len(raw)
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert (descriptor.parent / "initial-progress.bin").read_bytes() == raw
    assert source.read_bytes() == raw
    assert descriptor.parent.name == "resource-generation"


def test_progress_original_missing_or_foreign_binding_stays_unknown(tmp_path):
    state, owner, source, identity = native(tmp_path)
    outside = tmp_path / "personal-progress"
    outside.write_bytes(b"personal original")
    source.symlink_to(outside)
    result = capture_owned_progress(
        identity, state, generation="resource-symlink", seed="fixture-seed"
    )
    metadata = json.loads(Path(result["path"]).read_bytes())
    assert metadata["status"] == "unknown"
    assert metadata["sha256"] is None
    assert not (Path(result["path"]).parent / "initial-progress.bin").exists()
    assert outside.read_bytes() == b"personal original"
    identity["baseline"]["profile_generation_id"] = "foreign"
    result = capture_owned_progress(
        identity, state, generation="resource-foreign", seed="fixture-seed"
    )
    assert result["status"] == "unknown"
