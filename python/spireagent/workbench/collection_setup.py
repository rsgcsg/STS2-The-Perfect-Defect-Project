"""Persistent collection setup projection over Hub consent and Platform-owned native checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sts2_platform_evidence.collection_tool import CollectionTool
from sts2_platform_evidence.delivery_config import DeliveryConfig

from spireagent.json_boundary import BoundaryError, object_fields
from spireagent.workbench.campaign_prepare import read_preparation
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.identity import private_read
from spireagent.workbench.member_client import MemberClient

from .collection_tool_registration import inspect_collection_destination


class CollectionSetup:
    def __init__(self, members: MemberClient) -> None:
        self.members = members

    @property
    def config(self) -> ProjectConfig:
        return self.members.account.config

    def enrollment(self, identity: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise BoundaryError("collection", "invalid_enrollment_id")
        value = self.members.request("campaigns/enrollments/" + identity)
        if value["device_id"] != self.members.account.device().get("device_id"):
            raise BoundaryError("collection", "matching_local_device_required")
        return value

    def _native(self, prepared: dict[str, Any], *, bind: str | None = None) -> dict[str, Any]:
        delivery = DeliveryConfig.load(Path(prepared["delivery_config"]))
        tool = CollectionTool(delivery.tool_directory, delivery.tool_release_id)
        operation = getattr(
            tool, "bind_recording_root" if bind is not None else "setup_status", None
        )
        if not callable(operation):
            raise BoundaryError("collection", "collection_tool_update_required")
        setting_path = self.config.state_dir / "collection-native-settings.json"
        setting = private_read(setting_path)
        if setting:
            object_fields(setting, {"schema", "game_directory"}, "collection.native_settings")
            if setting["schema"] != "stpd/collection-native-settings-v1":
                raise BoundaryError("collection", "native_settings_invalid")
        game = bind if bind is not None else setting.get("game_directory")
        if game is not None and (not isinstance(game, str) or not Path(game).is_absolute()):
            raise BoundaryError("collection", "absolute_game_directory_required")
        if bind is None:
            result = inspect_collection_destination(
                self.config, tool,
                recordings_root=delivery.recordings_root,
                game_directory=Path(game) if game is not None else None,
            )
        else:
            result = operation(
                recordings_root=delivery.recordings_root,
                game_directory=Path(game) if game is not None else None,
            )
        if (
            not isinstance(result, dict)
            or result.get("schema") != "sts2.platform/collection-setup-1"
            or result.get("recordings_root") != str(delivery.recordings_root)
            or result.get("status") not in {"bound", "configured", "blocked"}
        ):
            raise BoundaryError("collection", "native_setup_response_invalid")
        expected = prepared.get("required_native_identity")
        if expected is not None and result.get("bound") is True:
            observed = result.get("loaded_identity") or {}
            game = observed.get("game") or {}
            installed = result.get("installed_artifact") or {}
            if (
                any(game.get(key) != value for key, value in expected["game"].items())
                or installed.get("sha256") != expected["mod"]["sha256"]
                or installed.get("module_version_id") != expected["mod"]["mvid"]
            ):
                result = {
                    **result,
                    "status": "blocked",
                    "bound": False,
                    "reason": "activity_native_identity_mismatch",
                    "next_action": "review_runtime",
                }
        if bind is not None:
            atomic_json(
                setting_path,
                {
                    "schema": "stpd/collection-native-settings-v1",
                    "game_directory": bind,
                },
            )
        # The owner report is current process evidence. Historical preparation flags
        # never grant readiness and cannot override a blocked owner response.
        return result

    def preparation(
        self, enrollment: dict[str, Any], delivery_status: str, *, probe: bool = True
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "not_prepared",
            "configuration_saved": False,
            "delivery_selected": False,
            "delivery_status": "not_configured",
            "native_binding": {"status": "not_checked"},
            "next_action": "prepare",
        }
        try:
            prepared = read_preparation(self.config, enrollment)
            if prepared is None:
                return result
            selected = self.config.delivery_config == Path(prepared["delivery_config"])
            result.update(
                status="native_binding_required",
                configuration_saved=True,
                delivery_selected=selected,
                delivery_status=delivery_status if selected else "not_configured",
                next_action="bind_recording_root",
            )
            if probe:
                native = self._native(prepared)
                result["native_binding"] = native
                bound = native["status"] == "bound" and native.get("bound") is True
                if bound:
                    result.update(
                        status="ready"
                        if selected and delivery_status == "running"
                        else "upload_not_started",
                        next_action="none"
                        if selected and delivery_status == "running"
                        else "activate",
                    )
                else:
                    result["next_action"] = native.get("next_action", "review_runtime")
        except (BoundaryError, OSError, ValueError) as error:
            result.update(
                status="blocked",
                error=error.code if isinstance(error, BoundaryError) else "preparation_unavailable",
                next_action="review_setup",
            )
        return result

    def status(self, delivery_status: str) -> dict[str, Any]:
        settings = self.members.request("collection-settings")
        device = self.members.account.device().get("device_id")
        enrollments = self.members.request("campaigns/enrollments?limit=25")
        items = [item for item in enrollments["items"] if item["device_id"] == device]
        attached = self.config.delivery_config
        active_directory = attached.parent if attached is not None else None
        if active_directory is not None and active_directory.parent.name == "generations":
            active_directory = active_directory.parent.parent
        if (
            active_directory is not None
            and active_directory.parent == self.config.state_dir / "campaigns"
        ):
            identity = active_directory.name
            if (
                attached is not None
                and attached.name == "delivery.json"
                and re.fullmatch(r"[a-f0-9]{32}", identity)
                and not any(item["enrollment_id"] == identity for item in items)
            ):
                items.insert(0, self.enrollment(identity))
        default = settings.get("default") or {}
        preferred = next(
            (
                item
                for item in items
                if active_directory == self.config.state_dir / "campaigns" / item["enrollment_id"]
            ),
            None,
        )
        if preferred is None:
            preferred = next(
                (item for item in items if item["template_id"] == default.get("template_id")), None
            )
        if preferred is None and items:
            preferred = items[0]
        return {
            "schema": "stpd/local-collection-status-v1",
            "settings": settings,
            "device_id": device,
            "observed_at": settings["observed_at"],
            "items": [
                {
                    **item,
                    "preparation": self.preparation(
                        item,
                        delivery_status,
                        probe=item is preferred,
                    ),
                }
                for item in items
            ],
            "more_enrollments": enrollments.get("next_offset") is not None,
        }

    def bind(self, identity: str, game_directory: object) -> dict[str, Any]:
        if not isinstance(game_directory, str) or not Path(game_directory).is_absolute():
            raise BoundaryError("collection", "absolute_game_directory_required")
        selected = self.enrollment(identity)
        prepared = read_preparation(self.config, selected)
        if prepared is None:
            raise BoundaryError("collection", "prepare_required")
        if self.config.delivery_config not in {None, Path(prepared["delivery_config"])}:
            raise BoundaryError("collection", "another_collection_attached")
        return self._native(prepared, bind=game_directory)

    def activation_config(self, identity: str) -> Path:
        selected = self.enrollment(identity)
        prepared = read_preparation(self.config, selected)
        if prepared is None:
            raise BoundaryError("collection", "prepare_required")
        native = self._native(prepared)
        if native["status"] != "bound" or native.get("bound") is not True:
            raise BoundaryError("collection", "native_binding_required")
        return Path(prepared["delivery_config"])
