"""Explicit, stopped-owner switching of private text Runtime generations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.package_identity import PackageIdentityError
from spireagent.workbench.developer import ProjectConfig
from spireagent.workbench.developer_server import instance_lock, running
from spireagent.workbench.kit_runtime import strict_text_runtime_profile, text_runtime_pin
from spireagent.workbench.model_state_migration import (
    _ordinary_file,
    _publish_profile,
    _sync_directory,
    _write_new_file,
)
from spireagent.workbench.runtime_install import (
    CONNECTOR_PACKAGE,
    RUNTIME_PACKAGE,
    install_runtime,
    v2_sdk_available,
    validate_runtime_install,
)

SCHEMA = "stpd/local-runtime-generation-v1"


def _profile_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(profile).encode()).hexdigest()


def _safe_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise BoundaryError("local_model", "runtime_generation_path_unsafe")


def generation_profile(value: dict[str, Any], schema: str, profile_id: str,
                       slot: Path) -> tuple[Path, dict[str, Any]] | None:
    """Resolve only the fixed digest slot; never accept an operator path."""
    if value.get("schema") != SCHEMA:
        return None
    if set(value) != {"schema", "profile", "generation"}:
        raise BoundaryError("local_model", "runtime_generation_profile_invalid")
    profile = value["profile"]
    if (not isinstance(profile, dict) or set(profile) != {"schema", "runtime_package"}
            or profile["schema"] != schema or not isinstance(profile["runtime_package"], dict)
            or value["generation"] != _profile_hash(profile)):
        raise BoundaryError("local_model", "runtime_generation_profile_invalid")
    generation = digest(value["generation"], "local_model.runtime_generation")
    if strict_text_runtime_profile(canonical_json(profile).encode(),
                                   required_profile=profile_id) != profile["runtime_package"]:
        raise BoundaryError("local_model", "runtime_generation_profile_invalid")
    _safe_directory(slot)
    _safe_directory(slot / "generations")
    directory = slot / "generations" / generation
    _safe_directory(directory)
    return directory, profile["runtime_package"]


def _verify(directory: Path, pin: dict[str, Any], connector: dict[str, Any],
            profile_id: str) -> dict[str, str]:
    _safe_directory(directory)
    try:
        observed = validate_runtime_install(directory / "runtime/node_modules", pin, connector)
    except (OSError, ValueError, StopIteration, PackageIdentityError) as error:
        raise BoundaryError("local_model", "runtime_generation_install_invalid") from error
    if profile_id == "text-menu-m2-v2":
        sdk = (directory / "runtime/node_modules" / RUNTIME_PACKAGE / "node_modules" /
               CONNECTOR_PACKAGE / "dist/index.js")
        if not v2_sdk_available(sdk):
            raise BoundaryError("local_model", "v2_runtime_contract_unavailable")
    return observed


def _active_path(service: Any, profile_id: str) -> tuple[Path, Path, str]:
    from spireagent.workbench.local_models import TEXT_PROFILES

    if profile_id not in TEXT_PROFILES:
        raise BoundaryError("local_model", "unsupported_runtime_profile")
    _, name, schema, slot = TEXT_PROFILES[profile_id]
    if service.private_root.is_symlink():
        raise BoundaryError("local_model", "private_model_state_unsafe")
    return service.private_root / Path(name).name, service.directory / slot, schema


def _read_active(path: Path) -> bytes:
    try:
        return _ordinary_file(path)
    except (OSError, BoundaryError) as error:
        raise BoundaryError("local_model", "runtime_active_profile_unsafe") from error


def _atomic_raw(path: Path, raw: bytes) -> None:
    """Preserve archived bytes exactly across the single active-name replacement."""
    temporary = path.with_name(".pending-" + uuid4().hex)
    try:
        _write_new_file(temporary, raw)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _switch(path: Path, before: bytes, after: bytes) -> tuple[str, str | None]:
    if _read_active(path) != before:
        raise BoundaryError("local_model", "runtime_active_profile_changed")
    try:
        _atomic_raw(path, after)
    except OSError:
        # replace can succeed and its directory fsync can fail. Never infer rollback.
        try:
            current = _read_active(path)
        except BoundaryError:
            current = None
        state = ("new_after_io_error" if current == after else
                 "old_after_io_error" if current == before else "unknown_after_io_error")
        return state, hashlib.sha256(current).hexdigest() if current is not None else None
    return "switched", hashlib.sha256(after).hexdigest()


def _admit(config: ProjectConfig) -> Any:
    from spireagent.workbench.local_models import LocalModelService, _check_runtime_port

    if running(config) is not None:
        raise BoundaryError("local_model", "close_workbench_before_runtime_generation_change")
    service = LocalModelService(config)
    if service.state["status"] == "recovery_required":
        raise BoundaryError("local_model", "previous_operation_requires_recovery")
    _check_runtime_port(15527)
    return service


def _affected(service: Any, profile_id: str) -> list[str]:
    return sorted(entry["id"] for entry in service.registry()["policies"]
                  if entry.get("runtime_profile") == profile_id)


def _receipt(affected: list[str], profile_id: str, state: str, generation: str,
             prior_sha256: str | None, intended_sha256: str,
             observed_sha256: str | None) -> dict[str, Any]:
    return {"schema": SCHEMA, "status": "OK" if state == "switched" else "BLOCKED",
            "runtime_profile": profile_id,
            "switch_state": state,
            "activated": (True if state in {"switched", "new_after_io_error"} else
                          False if state in {"old_after_io_error", "absent_after_io_error"}
                          else "unknown"),
            "generation": generation,
            "previous_profile_sha256": prior_sha256,
            "intended_profile_sha256": intended_sha256,
            "readback_profile_sha256": observed_sha256,
            "affected_selections": affected}


def _check_initial_slot(slot: Path, generation: str) -> None:
    """An absent active name permits only an empty slot or this exact orphan."""
    _safe_directory(slot)
    if not slot.exists():
        return
    for entry in slot.iterdir():
        if entry.name != "generations" or entry.is_symlink():
            raise BoundaryError("local_model", "runtime_initial_slot_unsafe")
    generations = slot / "generations"
    _safe_directory(generations)
    if generations.exists() and any(
            child.name != generation or child.is_symlink() or not child.is_dir()
            for child in generations.iterdir()):
        raise BoundaryError("local_model", "runtime_initial_slot_unsafe")


def _prepare_new_generation(service: Any, profile_id: str, slot: Path, schema: str,
                            new_profile_file: Path, expected_new_profile_sha256: str,
                            archive: Path, *, initial: bool = False) -> tuple[str, bytes]:
    """Verify one operator-selected package and stage or reverify its immutable slot."""
    digest(expected_new_profile_sha256, "local_model.expected_new")
    try:
        if new_profile_file.is_symlink() or not new_profile_file.is_file():
            raise BoundaryError("local_model", "runtime_new_profile_unsafe")
        raw = _ordinary_file(new_profile_file)
        if hashlib.sha256(raw).hexdigest() != expected_new_profile_sha256:
            raise BoundaryError("local_model", "runtime_new_profile_changed")
        profile = decode_json(raw)
        if (not isinstance(profile, dict) or set(profile) != {"schema", "runtime_package"}
                or profile["schema"] != schema):
            raise BoundaryError("local_model", "runtime_new_profile_invalid")
        from spireagent.workbench.runtime_install import ARCHIVE_LIMIT

        if (archive.is_symlink() or not archive.is_file()
                or archive.stat().st_size > ARCHIVE_LIMIT):
            raise BoundaryError("local_model", "runtime_archive_missing_or_unsafe")
        pin = text_runtime_pin(raw, archive.read_bytes(),
                               **({"required_profile": profile_id}
                                  if profile_id == "text-menu-m2-v2" else
                                  {"memory": profile_id == "text-menu-m2-v1"}))
        generation = _profile_hash(profile)
        directory = slot / "generations" / generation
        if initial:
            _check_initial_slot(slot, generation)
        _safe_directory(slot)
        _safe_directory(slot / "generations")
        _safe_directory(directory)
        connector = service._connector_pin()
        if directory.exists():
            _verify(directory, pin, connector, profile_id)
        else:
            (slot / "generations").mkdir(parents=True, exist_ok=True)
            stage = slot / "generations" / (".stage-" + uuid4().hex)
            try:
                install_runtime(stage, pin, connector, archive=archive,
                                **({"required_profile": profile_id}
                                   if profile_id == "text-menu-m2-v2" else {}))
                _verify(stage, pin, connector, profile_id)
                if directory.exists() or directory.is_symlink():
                    raise BoundaryError("local_model", "runtime_generation_collision")
                os.rename(stage, directory)
                _sync_directory(directory.parent)
            finally:
                # A published generation is never removed or repaired in place.
                if stage.exists():
                    import shutil

                    shutil.rmtree(stage)
        after = (json.dumps({"schema": SCHEMA, "profile": profile,
                             "generation": generation}, sort_keys=True, indent=2) + "\n").encode()
        return generation, after
    except OSError as error:
        raise BoundaryError("local_model", "runtime_generation_install_invalid") from error


def _publish_initial(path: Path, after: bytes) -> tuple[str, str | None]:
    """Create the active name exclusively; never reinterpret a racing writer as ours."""
    temporary = path.with_name(".pending-" + uuid4().hex)
    linked = False
    io_failed = False
    try:
        _write_new_file(temporary, after)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise BoundaryError("local_model", "runtime_active_profile_exists") from None
        linked = True
        _sync_directory(path.parent)
    except OSError:
        io_failed = True
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            io_failed = True
    if io_failed:
        try:
            current = _read_active(path)
        except BoundaryError:
            current = None
        state = ("new_after_io_error" if linked and current == after else
                 "absent_after_io_error" if current is None and not path.exists()
                 and not path.is_symlink()
                 else "unknown_after_io_error")
        return state, hashlib.sha256(current).hexdigest() if current is not None else None
    return "switched", hashlib.sha256(after).hexdigest()


def initialize(config: ProjectConfig, profile_id: str, *, new_profile_file: Path,
               expected_new_profile_sha256: str, archive: Path) -> dict[str, Any]:
    """Explicit first generation: absent active name and exclusive publication."""
    with instance_lock(config.state_dir / "instance.lock"):
        service = _admit(config)
        active, slot, schema = _active_path(service, profile_id)
        if active.exists() or active.is_symlink():
            raise BoundaryError("local_model", "runtime_active_profile_exists")
        generation, after = _prepare_new_generation(
            service, profile_id, slot, schema, new_profile_file,
            expected_new_profile_sha256, archive, initial=True)
        _safe_directory(active.parent)
        active.parent.mkdir(parents=True, exist_ok=True)
        affected = _affected(service, profile_id)
        state, observed = _publish_initial(active, after)
        receipt = _receipt(affected, profile_id, state, generation, None,
                           hashlib.sha256(after).hexdigest(), observed)
        receipt["origin"] = "operator_selected"
        return receipt


def upgrade(config: ProjectConfig, profile_id: str, *, expected_active_sha256: str,
            new_profile_file: Path, expected_new_profile_sha256: str,
            archive: Path) -> dict[str, Any]:
    """Install a separate immutable generation, then switch the one active pin."""
    digest(expected_active_sha256, "local_model.expected_active")
    with instance_lock(config.state_dir / "instance.lock"):
        service = _admit(config)
        active, slot, schema = _active_path(service, profile_id)
        before = _read_active(active)
        if hashlib.sha256(before).hexdigest() != expected_active_sha256:
            raise BoundaryError("local_model", "runtime_active_profile_changed")
        # The current pin must be structurally bound; explicit recovery may replace
        # a damaged installed generation with a separately verified one.
        service.text_runtime_profile(profile_id)
        generation, after = _prepare_new_generation(
            service, profile_id, slot, schema, new_profile_file,
            expected_new_profile_sha256, archive)
        if after == before:
            raise BoundaryError("local_model", "runtime_generation_already_active")
        affected = _affected(service, profile_id)
        archive_dir = slot / "profiles"
        _safe_directory(archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        _publish_profile(archive_dir / (expected_active_sha256 + ".json"), before)
        state, observed = _switch(active, before, after)
        return _receipt(affected, profile_id, state, generation, expected_active_sha256,
                        hashlib.sha256(after).hexdigest(), observed)


def rollback(config: ProjectConfig, profile_id: str, *, expected_active_sha256: str,
             archived_profile_sha256: str) -> dict[str, Any]:
    digest(expected_active_sha256, "local_model.expected_active")
    digest(archived_profile_sha256, "local_model.archived_profile")
    if archived_profile_sha256 == expected_active_sha256:
        raise BoundaryError("local_model", "runtime_generation_already_active")
    with instance_lock(config.state_dir / "instance.lock"):
        service = _admit(config)
        active, slot, _ = _active_path(service, profile_id)
        before = _read_active(active)
        if hashlib.sha256(before).hexdigest() != expected_active_sha256:
            raise BoundaryError("local_model", "runtime_active_profile_changed")
        archive_dir = slot / "profiles"
        _safe_directory(slot)
        _safe_directory(archive_dir)
        after = _read_active(archive_dir / (archived_profile_sha256 + ".json"))
        if hashlib.sha256(after).hexdigest() != archived_profile_sha256:
            raise BoundaryError("local_model", "runtime_archived_profile_changed")
        # Check both currently active and archived target installs before switching.
        service.text_runtime_profile(profile_id)
        parsed = decode_json(after)
        if not isinstance(parsed, dict):
            raise BoundaryError("local_model", "runtime_archived_profile_invalid")
        from spireagent.workbench.local_models import TEXT_PROFILES

        _, _, schema, _ = TEXT_PROFILES[profile_id]
        resolved = generation_profile(parsed, schema, profile_id, slot)
        if resolved is None:
            # Raw legacy profiles use the original installation slot.
            from spireagent.workbench.local_models import _validate_text_profile

            pin = _validate_text_profile(parsed, schema, profile_id)
            directory = slot
            generation = "legacy"
        else:
            directory, pin = resolved
            generation = parsed["generation"]
        _verify(directory, pin, service._connector_pin(), profile_id)
        affected = _affected(service, profile_id)
        _publish_profile(archive_dir / (expected_active_sha256 + ".json"), before)
        state, observed = _switch(active, before, after)
        return _receipt(affected, profile_id, state, generation, expected_active_sha256,
                        archived_profile_sha256, observed)
