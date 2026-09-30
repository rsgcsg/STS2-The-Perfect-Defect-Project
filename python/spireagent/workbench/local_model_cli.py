"""Local CLI client for the already-owned workbench model service."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, decode_json, digest
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock, running
from spireagent.workbench.hub_client import NoRedirect


def model_command(
    config: ProjectConfig,
    action: str,
    *,
    selection: str | None = None,
    artifact: str | None = None,
    runtime_archive: Path | None = None,
    runtime_profile: str | None = None,
    expected_active_sha256: str | None = None,
    new_runtime_profile: Path | None = None,
    expected_new_profile_sha256: str | None = None,
    archived_profile_sha256: str | None = None,
) -> dict[str, Any]:
    generation_action = action in {"initialize-runtime-generation",
                                   "upgrade-runtime-generation", "rollback-runtime-generation"}
    if generation_action:
        if runtime_profile is None:
            raise BoundaryError("local_model", "runtime_generation_arguments_required")
        from spireagent.workbench.runtime_generation import initialize, rollback, upgrade

        if action == "initialize-runtime-generation":
            if (expected_active_sha256 is not None or runtime_archive is None
                    or new_runtime_profile is None or expected_new_profile_sha256 is None
                    or archived_profile_sha256 is not None):
                raise BoundaryError("local_model", "runtime_generation_arguments_required")
            return initialize(config, runtime_profile, new_profile_file=new_runtime_profile,
                              expected_new_profile_sha256=expected_new_profile_sha256,
                              archive=runtime_archive)
        if expected_active_sha256 is None:
            raise BoundaryError("local_model", "runtime_generation_arguments_required")

        if action == "upgrade-runtime-generation":
            if (runtime_archive is None or new_runtime_profile is None
                    or expected_new_profile_sha256 is None or archived_profile_sha256 is not None):
                raise BoundaryError("local_model", "runtime_generation_arguments_required")
            return upgrade(config, runtime_profile,
                           expected_active_sha256=expected_active_sha256,
                           new_profile_file=new_runtime_profile,
                           expected_new_profile_sha256=expected_new_profile_sha256,
                           archive=runtime_archive)
        if (archived_profile_sha256 is None or runtime_archive is not None
                or new_runtime_profile is not None or expected_new_profile_sha256 is not None):
            raise BoundaryError("local_model", "runtime_generation_arguments_required")
        return rollback(config, runtime_profile, expected_active_sha256=expected_active_sha256,
                        archived_profile_sha256=archived_profile_sha256)
    if any(value is not None for value in (expected_active_sha256, new_runtime_profile,
                                            expected_new_profile_sha256,
                                            archived_profile_sha256)):
        raise BoundaryError("local_model", "runtime_generation_arguments_require_action")
    if runtime_profile is not None and (action != "install-runtime" or runtime_archive is None):
        raise BoundaryError("local_model", "runtime_profile_requires_offline_install")
    if runtime_profile not in {None, "text-menu-v1", "text-menu-m2-v1",
                               "text-menu-m2-v2"}:
        raise BoundaryError("local_model", "unsupported_runtime_profile")
    if runtime_archive is not None:
        if action != "install-runtime":
            raise BoundaryError("local_model", "archive_requires_install_runtime_action")
        with instance_lock(config.state_dir / "instance.lock"):
            if running(config) is not None:
                raise BoundaryError("local_model", "close_workbench_before_offline_runtime_install")
            from spireagent.workbench.local_models import LocalModelService
            from spireagent.workbench.runtime_install import install_runtime

            service = LocalModelService(config)
            if service.state["status"] == "recovery_required":
                raise BoundaryError("local_model", "previous_operation_requires_recovery")
            if runtime_profile in {"text-menu-v1", "text-menu-m2-v1",
                                   "text-menu-m2-v2"}:
                # Legacy profiles keep their original staged-source behavior.
                # A new v2 pin comes only from the selected kit's verified pair.
                from spireagent.policy_files import _object_file
                from spireagent.workbench.kit_runtime import KIT_RUNTIME_PAIRS, text_runtime_pin
                from spireagent.workbench.local_models import TEXT_PROFILES

                _, legacy_name, _, _ = TEXT_PROFILES[runtime_profile]
                destination = service.private_root / Path(legacy_name).name
                if destination.is_symlink():
                    raise BoundaryError("local_model", "private_model_state_unsafe")
                selected_v2_archive = (service.root.parent / KIT_RUNTIME_PAIRS["text-menu-m2-v2"][3]
                                       if runtime_profile == "text-menu-m2-v2" else None)
                if (runtime_profile == "text-menu-m2-v2" and not destination.exists()):
                    from spireagent.workbench.model_state_migration import _publish_profile

                    raw, selected_archive, _ = service._selected_kit_text_runtime(runtime_profile)
                    if runtime_archive != selected_archive or runtime_archive.is_symlink():
                        raise BoundaryError("local_model", "runtime_profile_staging_unsafe")
                    if service.private_root.is_symlink():
                        raise BoundaryError("local_model", "private_model_state_unsafe")
                    service.private_root.mkdir(parents=True, exist_ok=True)
                    _publish_profile(destination, raw)
                elif (runtime_profile == "text-menu-m2-v2"
                      and runtime_archive == selected_v2_archive):
                    # initialize passes this fixed path. A previous private pin
                    # must not silently replace the included kit's identity.
                    _, _, kit_pin = service._selected_kit_text_runtime(runtime_profile)
                    existing_directory, existing_pin = service.text_runtime_profile(runtime_profile)
                    if existing_directory.parent.name != "generations" and existing_pin != kit_pin:
                        raise BoundaryError("local_model", "private_profile_collision")
                if (runtime_profile != "text-menu-m2-v2"
                        and not destination.exists() and not destination.is_symlink()):
                    staged = service.root / legacy_name
                    if staged.exists() or staged.is_symlink():
                        if (staged.is_symlink() or runtime_archive.is_symlink()
                                or runtime_archive.stat().st_size > 32 * 1024 * 1024):
                            raise BoundaryError("local_model", "runtime_profile_staging_unsafe")
                        raw = staged.read_bytes()
                        text_runtime_pin(raw, runtime_archive.read_bytes(),
                                         memory=runtime_profile == "text-menu-m2-v1")
                        service.private_root.mkdir(parents=True, exist_ok=True)
                        if service.private_root.is_symlink():
                            raise BoundaryError("local_model", "private_model_state_unsafe")
                        atomic_json(destination, _object_file(staged))
                if selection is not None:
                    selected = service.selection(selection)
                    if selected.get("runtime_profile") != runtime_profile:
                        raise BoundaryError("local_model", "selection_runtime_profile_mismatch")
                directory, pin = (service.text_runtime_profile()
                                  if runtime_profile == "text-menu-v1"
                                  else service.text_runtime_profile(runtime_profile))
            else:
                directory, pin = service.runtime_profile(selection)
            if directory.parent.name == "generations":
                from spireagent.workbench.runtime_generation import _verify

                profile_id = runtime_profile
                if profile_id is None and selection is not None:
                    profile_id = service.selection(selection).get("runtime_profile")
                if profile_id is None:
                    raise BoundaryError("local_model", "runtime_generation_profile_required")
                return {"status": "runtime_installed", "reused": True,
                        **_verify(directory, pin, service._connector_pin(), profile_id)}
            installed = install_runtime(
                directory,
                pin,
                service._connector_pin(),
                archive=runtime_archive,
                **({"required_profile": runtime_profile}
                   if runtime_profile == "text-menu-m2-v2" else {}),
            )
            return installed
    current = running(config)
    if current is None:
        raise BoundaryError("local_model", "open_workbench_first")
    route = "/api/local-models"
    body = None
    if action == "status":
        route += "/status"
    elif action == "readiness":
        if not selection:
            raise BoundaryError("local_model", "selection_required")
        route += "/readiness?" + urlencode({"selection_id": selection})
    elif action == "start":
        if not selection:
            raise BoundaryError("local_model", "selection_required")
        route += "/start"
        body = {"selection_id": selection}
    elif action == "download":
        route += "/download"
        body = {"artifact_id": digest(artifact, "local_model.artifact")}
    elif action == "install-runtime":
        route += "/install-runtime"
        body = {}
    elif action in {"human", "shadow", "one_step", "auto", "stop"}:
        route += "/command"
        body = {"action": action}
    elif action != "catalog":
        raise BoundaryError("local_model", "unsupported_local_command")
    request = Request(
        f"http://127.0.0.1:{current['port']}" + route,
        data=canonical_json(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + current["control_token"],
        },
    )
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError
        result = decode_json(raw)
        if not isinstance(result, dict) or result.get("schema") != "stpd/local-models-v1":
            raise ValueError
        return result
    except (OSError, ValueError):
        raise BoundaryError(
            "local_model", "workbench_request_failed_check_status_before_retry"
        ) from None
