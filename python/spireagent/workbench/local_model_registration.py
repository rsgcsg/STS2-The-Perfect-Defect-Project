"""Explicit local text-menu policy registration from a verified offline export.

Registration records a reviewed engineering scope. Runtime readiness and loading
remain separate, and the current Connector remains the environment authority.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path
from time import monotonic
from typing import Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.package_identity import PackageIdentityError
from spireagent.policy_files import _inside, _object_file
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_model_dependencies import (
    local_models_available,
    require_local_models,
)
from spireagent.workbench.local_model_export import LocalModelExport, _ordinary
from spireagent.workbench.local_models import LocalModelService, _loopback
from spireagent.workbench.managed_model_target import managed_manifest
from spireagent.workbench.memory_recipe import (
    MEMORY_RECIPES,
    V2_MEMORY_RECIPES,
    input_profile_for_recipe,
    memory_settings_for_recipe,
)
from spireagent.workbench.runtime_install import (
    BUNDLED_LAYOUT,
    CONNECTOR_PACKAGE,
    RUNTIME_PACKAGE,
    v2_sdk_available,
    validate_runtime_install,
)


class RegistrationStorageError(BoundaryError):
    """A terminal storage failure with a safe correlation ID for the local log."""

    def __init__(self, error: sqlite3.DatabaseError) -> None:
        self.error_id = uuid.uuid4().hex
        super().__init__(
            "local_model_registration", "registration_verification_storage_failed",
            "inspect verification storage and service resource limits before an explicit retry",
        )
        logging.getLogger(__name__).error(
            "registration storage failure error_id=%s sqlite_errorcode=%s sqlite_errorname=%s",
            self.error_id, getattr(error, "sqlite_errorcode", None),
            getattr(error, "sqlite_errorname", None), exc_info=True,
        )

    def public_failure(self) -> dict[str, str]:
        return {"error": self.code, "stage": self.stage, "category": "storage",
                "status": "failed", "error_id": self.error_id}


SCHEMA = "stpd/local-model-registration-v1"
PROFILE = "text-menu-v1"
M2_PROFILE = "text-menu-m2-v1"
V2_M2_PROFILE = "text-menu-m2-v2"
PUBLIC_M0_PROFILE = "public-snapshot-m0-v1"
PUBLIC_M0_ADAPTER = "stpd-public-m0-decision-adapter"
PUBLIC_M2_PROFILE = "public-snapshot-m2-v1"
PUBLIC_M2_ADAPTER = "stpd-public-m2-decision-adapter"
PUBLIC_PROFILES = {PUBLIC_M0_PROFILE, PUBLIC_M2_PROFILE}
RECIPE_LABELS = {"stage1a.b.s.v2": "B", "stage1a.dsimple.s.v1": "D-Simple"}


def _memory_recipe_label(recipe: str) -> str:
    settings = memory_settings_for_recipe(recipe)
    name = f"{'Reset' if settings.reset_each_step else 'M2'}-K{settings.slots}"
    if settings.input_profile.endswith("confirmed-interaction"):
        return name + (" 已确认交互对照版" if settings.reset_each_step else
                       " 已确认交互训练版")
    if settings.input_profile == "text-menu-v2":
        return name + (" v2 工程对照版" if settings.reset_each_step else " v2 工程训练版")
    return name + (" 独立训练对照版" if settings.reset_each_step else " 训练版")


MEMORY_RECIPE_LABELS = {recipe: _memory_recipe_label(recipe) for recipe in MEMORY_RECIPES}


def _export_memory_input_profile(export: Path) -> str:
    """Read a closed package projection for display; POST rechecks full lineage."""
    from stpd.fullrun.memory_projection_config import (
        parse_episode_projection_config,
        projection_input_profile,
    )

    value = _object_file(export / "model.json")
    try:
        profile = projection_input_profile(parse_episode_projection_config(
            value.get("projection_config")))
    except (TypeError, ValueError) as error:
        raise BoundaryError("local_model_registration", "export_profile_invalid") from error
    return profile


def _export_memory_profile(export: Path) -> str:
    return (V2_M2_PROFILE if _export_memory_input_profile(export).startswith("text-menu-v2")
            else M2_PROFILE)


def _connector_sdk_path(node_modules: Path, pin: dict[str, Any]) -> Path:
    """Resolve Connector from the dependency layout already checked against its pin."""
    layout = pin.get("dependency_layout")
    if layout == BUNDLED_LAYOUT:
        root = node_modules / RUNTIME_PACKAGE / "node_modules"
    elif layout is None:
        root = node_modules
    else:
        raise BoundaryError("local_model_registration", "runtime_package_not_pinned")
    return root / CONNECTOR_PACKAGE / "dist" / "index.js"


_v2_sdk_available = v2_sdk_available
REGISTRY = "token-policies-v1.json"
LOCK = "token-policies-v1.lock"
REGISTRATIONS = "model-registrations"
# Local admission reprojects the dataset and verifies its immutable completion bytes.
# Keep that data-sized phase separate from Runtime/Connector checks and roster binding.
EXPORT_VERIFICATION_SECONDS = 300.0
REGISTRATION_SECONDS = 45.0
# Source-reviewed engineering support: LiveObservationReader registrations,
# SnapshotBuilder/providers and NativeTextMenu overrides. This is not native
# or full-run qualification; Runtime remains the current-menu authority.
KINDS = (
    "deck_enchant_selection", "combat_hand_card_selection", "card_bundle_selection",
    "card_reward_selection", "reward_claim", "map_navigation", "combat_turn",
    "shop_inventory", "shop_room", "treasure_room", "event_dialogue", "event_option",
    "native_generated_card_choice", "native_boss_relic_selection",
    "native_simple_card_selection", "deck_upgrade_selection", "deck_transform_selection",
    "native_combat_pile_selection", "native_deck_card_selection", "rest_site",
    "potion_popup", "potion_targeting", "combat_card_operation", "native_map",
    "run_deck", "combat_draw_pile", "combat_discard_pile", "combat_exhaust_pile",
    "inspect_card", "relic_inspect", "relic_tips", "card_tips", "power_tips",
    "intent_tips", "orb_tips", "topbar_tips",
)
# Global text-menu capabilities from PlayerEnvironmentService.GetCapabilities.
VERBS = (
    "activate", "select", "deselect", "confirm", "cancel", "play", "target",
    "use", "end_turn", "skip", "open", "close", "purchase", "navigate",
    "begin_card_play", "cancel_card_play", "focus_target", "confirm_target",
    "confirm_card", "open_potion_popup", "choose_potion_use", "discard_potion",
    "close_potion_popup", "select_potion_target", "cancel_potion_target",
    "claim_reward", "claim_linked_reward", "proceed_rewards", "skip_rewards",
    "open_information", "open_relic_inspect", "open_relic_tips", "open_card_tips",
    "open_power_tips", "open_intent_tips", "open_orb_tips", "open_topbar_tips", "back",
    "show_relic_tips", "show_card_tips", "show_power_tips", "show_intent_tips",
    "show_orb_tips", "show_topbar_tips", "open_run_deck", "open_native_map",
    "inspect_relic", "open_combat_draw_pile", "open_combat_discard_pile",
    "open_combat_exhaust_pile", "return_native_information", "return_native_map",
    "return_relic_inspect", "return_native_tips", "inspect_deck_card",
    "inspect_bundle_card", "return_card_inspect", "previous_inspect_card",
    "next_inspect_card", "toggle_card_upgrade_preview", "previous_relic", "next_relic",
)
_SDK_SCRIPT = """
const {PlayerEnvironmentRestClient} = await import(process.argv[1]);
const client = new PlayerEnvironmentRestClient(process.argv[2], 5000);
const result = await (process.argv[3] === 'text-menu-v2'
  ? client.textMenuV2Capabilities()
  : process.argv[3] === 'public-snapshot-m0-v1'
    ? client.capabilities() : client.textMenuCapabilities());
process.stdout.write(JSON.stringify(result.data));
"""
_CONTEXT_SCRIPT = """
const {PlayerEnvironmentRestClient} = await import(process.argv[1]);
const client = new PlayerEnvironmentRestClient(process.argv[2], 5000);
const result = await (process.argv[3] === 'text-menu-v2'
  ? client.observeTextMenuV2Context() : client.observeTextMenuContext());
process.stdout.write(JSON.stringify({schema:result.data.schema,
  continuity_available:result.data.game_continuity_id !== null}));
"""


def _remaining(deadline: float, maximum: float = 12.0) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise BoundaryError("local_model_registration", "registration_timeout")
    return min(maximum, remaining)


def _public(model_id: str, status: str, *, selection_id: str | None = None,
            reason_code: str | None = None, profile: str = PROFILE) -> dict[str, Any]:
    result: dict[str, Any] = {"schema": SCHEMA, "model_id": model_id, "status": status,
                              "loaded": False, "runtime_profile": profile}
    if selection_id is not None:
        result["selection_id"] = selection_id
    if reason_code is not None:
        result["reason_code"] = reason_code
    return result


def _requirements(value: Any, *, input_profile: str = PROFILE
                  ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project only typed SDK capabilities; never derive support from a page."""
    try:
        if input_profile == PUBLIC_M0_PROFILE:
            return _public_m0_requirements(value)
        v2 = input_profile == "text-menu-v2"
        if (input_profile not in {PROFILE, "text-menu-v2"}
                or not isinstance(value, dict) or value["input_profile"] != input_profile
                or value["snapshot_schema"] != (
                    "sts2.player-environment/text-menu-snapshot-2" if v2 else
                    "sts2.player-environment/text-menu-snapshot-1")
                or value["receipt_schema"] != (
                    "sts2.player-environment/text-menu-action-result-2" if v2 else
                    "sts2.player-environment/text-menu-action-result-1")
                or value["execution_available"] is not True
                or value["single_controller"] is not True
                or not isinstance(value["verbs"], list)
                or not set(VERBS) | ({"select_card", "select_target", "cancel_selection"}
                                       if v2 else set()) <= set(value["verbs"])):
            raise ValueError
        host, game = value["host"], value["game"]
        implementation, modset = host["implementation"], game["modset"]
        fields = (value["protocol_version"], host["host_kind"], host["version"],
                  implementation["source_revision"], implementation["artifact_sha256"],
                  implementation["module_version_id"], game["version"], game["commit"],
                  modset["status"], modset["fingerprint"])
        if (not all(isinstance(item, str) and item for item in fields)
                or not re.fullmatch(r"[a-f0-9]{64}", implementation["artifact_sha256"])
                or not isinstance(modset["loaded_mod_ids"], list)
                or not all(isinstance(item, str) and item for item in modset["loaded_mod_ids"])
                or len(set(modset["loaded_mod_ids"])) != len(modset["loaded_mod_ids"])):
            raise ValueError
        environment = {"host_kind": host["host_kind"],
                       "connector_version": host["version"],
                       "connector_source_revision": implementation["source_revision"],
                       "connector_artifact_sha256": implementation["artifact_sha256"],
                       "connector_module_version_id": implementation["module_version_id"],
                       "modset_status": modset["status"],
                       "modset_fingerprint": modset["fingerprint"],
                       "loaded_mod_ids": modset["loaded_mod_ids"]}
        requirements = {"connector_protocol_version": value["protocol_version"],
                        "environment": environment, "reads": [],
                        "whole_decision_admission": True,
                        "candidate_order_digest": "sha256-json-menu-action-id-order",
                        "score_count_matches_candidate_count": True,
                        "selected_index": True, "successor_required": True}
        support = {"game_versions": [game["version"]],
                   "game_commits": [game["commit"]],
                   "interaction_kinds": list(KINDS),
                   "action_verbs": [*VERBS, *(["select_card", "select_target",
                                               "cancel_selection"] if v2 else [])]}
        return requirements, support
    except BoundaryError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError(
            "local_model_registration", "text_menu_capabilities_incompatible",
        ) from error


def _public_m0_requirements(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind generic Snapshot identity and the M0/Connector common verb scope."""
    from stpd.fullrun.public_inputs import PUBLIC_VERBS
    from stpd.public_m0_policy_installation import (
        GENERIC_ACTION_VERBS,
        GENERIC_INTERACTION_KINDS,
    )

    try:
        if (not isinstance(value, dict)
                or set(value) != {
                    "protocol_version", "snapshot_schema", "action_schema", "receipt_schema",
                    "control_schema", "status", "host", "game", "environment_fingerprint",
                    "verbs", "snapshot_bound", "single_controller", "execution_available",
                    "control", "evidence_profiles", "non_claims",
                }
                or value["snapshot_schema"] != "sts2.player-environment/snapshot-1"
                or value["action_schema"] != "sts2.player-environment/action-1"
                or value["receipt_schema"] != "sts2.player-environment/receipt-1"
                or value["control_schema"] != "sts2.player-environment/control-1"
                or value["snapshot_bound"] is not True
                or value["single_controller"] is not True
                or value["execution_available"] is not True
                or not isinstance(value["status"], str) or not value["status"]
                or not isinstance(value["environment_fingerprint"], str)
                or not value["environment_fingerprint"]
                or not isinstance(value["verbs"], list) or not value["verbs"]
                or any(not isinstance(item, str) or not item for item in value["verbs"])
                or len(set(value["verbs"])) != len(value["verbs"])
                or not isinstance(value["non_claims"], list)
                or any(not isinstance(item, str) or not item for item in value["non_claims"])):
            raise ValueError
        host, game = value["host"], value["game"]
        if not isinstance(host, dict) or set(host) != {
            "id", "name", "version", "runtime_instance_id", "host_kind", "implementation",
        }:
            raise ValueError
        implementation = host["implementation"]
        if not isinstance(implementation, dict) or set(implementation) != {
            "source_revision", "artifact_sha256", "module_version_id",
        }:
            raise ValueError
        if not isinstance(game, dict) or set(game) != {
            "version", "commit", "branch", "main_assembly_hash", "compatibility", "modset",
        }:
            raise ValueError
        modset, compatibility, control = game["modset"], game["compatibility"], value["control"]
        if (not isinstance(modset, dict) or set(modset) != {
                "status", "fingerprint", "scope", "loaded_mod_ids", "detail",
            }
                or not isinstance(compatibility, dict) or set(compatibility) != {
                    "status", "observation_allowed", "detail",
                }
                or compatibility["observation_allowed"] is not True
                or not isinstance(control, dict) or set(control) != {"recommended_renewal_ms"}
                or type(control["recommended_renewal_ms"]) is not int
                or control["recommended_renewal_ms"] < 0
                or not isinstance(value["evidence_profiles"], list)):
            raise ValueError
        evidence_keys = {
            "id", "enabled", "supported_kinds", "snapshot_bound", "runtime_bound",
            "default_in_consumer_flow", "creates_mutation_authority", "enters_action_ledger",
        }
        for profile in value["evidence_profiles"]:
            if (not isinstance(profile, dict) or set(profile) != evidence_keys
                    or not isinstance(profile["id"], str) or not profile["id"]
                    or type(profile["enabled"]) is not bool
                    or not isinstance(profile["supported_kinds"], list)
                    or any(not isinstance(item, str) or not item
                           for item in profile["supported_kinds"])
                    or profile["snapshot_bound"] is not True
                    or profile["runtime_bound"] is not True
                    or profile["default_in_consumer_flow"] is not False
                    or profile["creates_mutation_authority"] is not False
                    or profile["enters_action_ledger"] is not False):
                raise ValueError
        strings = (
            value["protocol_version"], host["host_kind"], host["version"],
            implementation["source_revision"], implementation["artifact_sha256"],
            implementation["module_version_id"], game["version"], game["commit"],
            modset["status"], modset["fingerprint"],
        )
        if (not all(isinstance(item, str) and item for item in strings)
                or host["host_kind"] not in {"live_ui", "headless", "replay", "test"}
                or not re.fullmatch(r"[a-f0-9]{64}", implementation["artifact_sha256"])
                or not isinstance(modset["loaded_mod_ids"], list)
                or any(not isinstance(item, str) or not item
                       for item in modset["loaded_mod_ids"])
                or len(set(modset["loaded_mod_ids"])) != len(modset["loaded_mod_ids"])):
            raise ValueError
        action_verbs = sorted(set(value["verbs"]) & GENERIC_ACTION_VERBS & set(PUBLIC_VERBS))
        if not action_verbs:
            raise ValueError
        environment = {
            "host_kind": host["host_kind"], "connector_version": host["version"],
            "connector_source_revision": implementation["source_revision"],
            "connector_artifact_sha256": implementation["artifact_sha256"],
            "connector_module_version_id": implementation["module_version_id"],
            "modset_status": modset["status"], "modset_fingerprint": modset["fingerprint"],
            "loaded_mod_ids": modset["loaded_mod_ids"],
        }
        requirements = {
            "connector_protocol_version": value["protocol_version"], "environment": environment,
            "reads": [], "whole_decision_admission": True,
            "candidate_order_digest": "sha256-json-bound-action-id-order",
            "score_count_matches_candidate_count": True, "selected_index": True,
            "successor_required": True,
        }
        support = {
            "game_versions": [game["version"]], "game_commits": [game["commit"]],
            "interaction_kinds": sorted(GENERIC_INTERACTION_KINDS),
            "action_verbs": action_verbs,
        }
        return requirements, support
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError(
            "local_model_registration", "generic_capabilities_incompatible",
        ) from error


def bind_memory_export(*args: Any, **kwargs: Any) -> Any:
    """Load the ML binding owner only for an explicit registration operation."""
    from stpd.memory_policy_installation import bind_memory_export as bind

    return bind(*args, **kwargs)


def bind_managed_memory_export(*args: Any, **kwargs: Any) -> Any:
    from stpd.memory_policy_installation import bind_managed_memory_export as bind

    return bind(*args, **kwargs)


def _target_kind(value: str) -> None:
    if not isinstance(value, str) or value not in {"native", "managed"}:
        raise BoundaryError("local_model_registration", "unsupported_environment_target")


def _managed_requirements(target: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the Host's declared contract, never extrapolate from the current page."""
    try:
        contracts = target["text_menu_contracts"]
        if not isinstance(contracts, list) or any(not isinstance(item, dict)
                                                  for item in contracts):
            raise ValueError
        matches = [item for item in contracts if item.get("input_profile") == "text-menu-v2"]
        if len(matches) != 1:
            raise ValueError
        contract = matches[0]
        if (contract.get("snapshot_schema") != "sts2.player-environment/text-menu-snapshot-2"
                or contract.get("receipt_schema")
                != "sts2.player-environment/text-menu-action-result-2"
                or contract.get("protocol_version") != "1.0.0"):
            raise ValueError
        game = target["exact_game"]
        if any(not isinstance(game[key], str) or not game[key] for key in ("version", "commit")):
            raise ValueError
        for key in ("interaction_kinds", "action_verbs"):
            values = contract[key]
            if (not isinstance(values, list) or not values
                    or any(not isinstance(item, str) or not item for item in values)
                    or len(set(values)) != len(values)):
                raise ValueError
        return ({
            "environment": {"kind": "managed_text_v2", "text_protocol_version": "1.0.0",
                            "input_profile": "text-menu-v2"},
            "reads": [], "whole_decision_admission": True,
            "candidate_order_digest": "sha256-json-menu-action-id-order",
            "score_count_matches_candidate_count": True, "selected_index": True,
            "successor_required": True,
        }, {"game_versions": [game["version"]], "game_commits": [game["commit"]],
            "interaction_kinds": list(contract["interaction_kinds"]),
            "action_verbs": list(contract["action_verbs"])})
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError("local_model_registration", "managed_contract_unavailable") from error


def bind_text_menu_export(*args: Any, **kwargs: Any) -> Any:
    from stpd.token_policy_installation import bind_text_menu_export as bind

    return bind(*args, **kwargs)


def bind_public_m0_export(*args: Any, **kwargs: Any) -> Any:
    """Load the M0 binding owner only for an explicit registration operation."""
    from stpd.public_m0_policy_installation import bind_public_m0_export as bind

    return bind(*args, **kwargs)


def bind_public_m2_export(*args: Any, **kwargs: Any) -> Any:
    from stpd.public_m2_policy_installation import bind_public_m2_export as bind

    return bind(*args, **kwargs)


class LocalModelRegistration:
    def __init__(self, config: ProjectConfig, export: LocalModelExport,
                 models: LocalModelService) -> None:
        self.config, self.export, self.models = config, export, models

    def _entries(self) -> list[dict[str, Any]]:
        # LocalModelService validates the shipped and private catalog together.
        self.models.registry()
        path = self.models.private_root / REGISTRY
        if not path.exists() and not path.is_symlink():
            return []
        value = _object_file(path)
        if value.get("schema") != "stpd/local-token-policies-v1" or not isinstance(
            value.get("policies"), list
        ):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        return cast(list[dict[str, Any]], value["policies"])

    def _matching(self, model_id: str, export: Path,
                  requirements: dict[str, Any] | None = None,
                  support: dict[str, Any] | None = None, *,
                  profile: str = PROFILE, environment_kind: str = "native",
                  metadata_only: bool = False,
                  ) -> tuple[str | None, bool]:
        from stpd.token_policy_installation import code_digest, validate

        stale = False
        memory = profile in {M2_PROFILE, V2_M2_PROFILE}
        public_m0 = profile in PUBLIC_PROFILES
        if memory:
            from stpd.memory_policy_installation import code_digest as memory_code_digest
            from stpd.memory_policy_installation import validate as validate_memory
        if public_m0:
            from spireagent.policies import policy_support

            public_owner = policy_support(PUBLIC_M2_ADAPTER if profile == PUBLIC_M2_PROFILE
                                          else PUBLIC_M0_ADAPTER)
            public_code_digest = public_owner.code_digest
            validate_public_m0 = public_owner.validate

        current_code = (memory_code_digest(self.models.root) if memory else
                        public_code_digest(self.models.root) if public_m0 else
                        code_digest(self.models.root))
        for entry in reversed(self._entries()):
            if entry.get("runtime_profile") != profile:
                continue
            try:
                config = _object_file(_inside(self.models.private_root, entry["config"]))
                if config.get("model_id") != model_id or config.get("export_path") != str(
                    export.resolve()
                ):
                    continue
                manifest = _object_file(_inside(self.models.private_root, entry["manifest"]))
                if managed_manifest(manifest) != (environment_kind == "managed"):
                    continue
                if manifest.get("adapter", {}).get("code_sha256") != current_code:
                    stale = True
                    continue
                validator = (validate_memory if memory else
                             validate_public_m0 if public_m0 else validate)
                validator(self.models.root,
                          _inside(self.models.private_root, entry["config"]),
                          _inside(self.models.private_root, entry["manifest"]),
                          binding_root=self.models.private_root,
                          **({"verify_payloads": False}
                             if profile == PUBLIC_M2_PROFILE and metadata_only else {}))
                if (requirements is not None and manifest.get("requirements") != requirements
                        or support is not None and manifest.get("support") != support):
                    continue
                return entry["id"], stale
            except (AttributeError, BoundaryError, KeyError, TypeError, ValueError) as error:
                raise BoundaryError("local_model_registration",
                                    "registration_metadata_invalid") from error
        return None, stale

    def status(self, model_id: object, *, environment_kind: str = "native") -> dict[str, Any]:
        _target_kind(environment_kind)
        result = self._status(model_id, environment_kind=environment_kind)
        if environment_kind == "managed":
            result["environment_kind"] = environment_kind
        return result

    def _status(self, model_id: object, *, environment_kind: str) -> dict[str, Any]:
        identity = digest(model_id, "local_model_registration.model_id")
        status_for_model = getattr(self.export, "status_for_model", None)
        observed = (status_for_model(identity) if callable(status_for_model)
                    else self.export.status())
        operation = observed["operation"]
        memory = (operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        public_m0 = (operation.get("model_id") == identity
                     and operation.get("model_type") == "public_m0")
        public_m2 = (operation.get("model_id") == identity
                     and operation.get("model_type") == "public_m2")
        profile = (PUBLIC_M2_PROFILE if public_m2 else
                   M2_PROFILE if memory else PUBLIC_M0_PROFILE if public_m0 else PROFILE)
        if public_m2 and (
            observed.get("schema") != "stpd/local-model-export-operation-v4"
            or operation.get("profile") != PUBLIC_M2_PROFILE
        ):
            return _public(identity, "unavailable", reason_code="registration_metadata_invalid",
                           profile=PUBLIC_M2_PROFILE)
        if public_m0 and (
            observed.get("schema") != "stpd/local-model-export-operation-v3"
            or operation.get("profile") != PUBLIC_M0_PROFILE
        ):
            return _public(identity, "unavailable", reason_code="registration_metadata_invalid",
                           profile=PUBLIC_M0_PROFILE)
        if observed.get("availability") == "workspace_changed":
            return _public(identity, "unavailable", reason_code="workspace_changed",
                           profile=profile)
        if (observed.get("availability") != "ready"
                or operation.get("status") != "completed"
                or operation.get("model_id") != identity):
            return _public(identity, "unavailable", reason_code="verified_export_required",
                           profile=profile)
        try:
            export = self.config.state_dir / "model-exports" / identity
            if memory:
                profile = _export_memory_profile(export)
            elif public_m2:
                from stpd.policy.public_m2_export import validate_package

                artifact, _config, _lineage = validate_package(export, check_payloads=False)
                if artifact.artifact_id != identity:
                    raise BoundaryError("local_model_registration", "export_identity_mismatch")
            elif public_m0:
                from spireagent.artifact_contracts import Manifest
                from stpd.policy.token_decision import (
                    PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA,
                    PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
                    check_light_action_m0_model,
                )

                envelope = _object_file(export / "model.json")
                if (envelope.get("schema") != PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
                        or envelope.get("model_id") != identity):
                    return _public(identity, "unavailable",
                                   reason_code="public_m0_export_verification_required",
                                   profile=PUBLIC_M0_PROFILE)
                artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
                _, info = check_light_action_m0_model(artifact)
                if info.get("schema") != PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA:
                    return _public(identity, "unavailable",
                                   reason_code="public_m0_export_verification_required",
                                   profile=PUBLIC_M0_PROFILE)
            if not local_models_available():
                return _public(identity, "unavailable",
                               reason_code="local_models_extra_required", profile=profile)
            if environment_kind == "managed" and (
                not memory or _export_memory_input_profile(export)
                != "text-menu-v2-confirmed-interaction"
            ):
                return _public(identity, "unavailable",
                               reason_code="managed_requires_confirmed_interaction_model",
                               profile=profile)
            found, stale = self._matching(identity, export, profile=profile,
                                          environment_kind=environment_kind, metadata_only=True)
            if found is not None:
                return _public(identity, "registered", selection_id=found, profile=profile)
            if stale:
                return _public(identity, "not_registered", reason_code="source_binding_changed",
                               profile=profile)
            if profile == V2_M2_PROFILE:
                try:
                    directory, pin = self.models.text_runtime_profile(profile)
                except BoundaryError as error:
                    return _public(identity, "unavailable", reason_code=error.code,
                                   profile=profile)
                try:
                    node_modules = directory / "runtime" / "node_modules"
                    validate_runtime_install(node_modules, pin, self.models._connector_pin())
                    sdk = _connector_sdk_path(node_modules, pin)
                    if environment_kind == "native" and not _v2_sdk_available(sdk):
                        return _public(identity, "unavailable",
                                       reason_code="v2_runtime_contract_unavailable",
                                       profile=profile)
                except (BoundaryError, OSError, PackageIdentityError, ValueError):
                    return _public(identity, "unavailable",
                                   reason_code="text_runtime_local_install_required",
                                   profile=profile)
            return _public(identity, "not_registered", profile=profile)
        except (BoundaryError, OSError, ValueError):
            return _public(identity, "unavailable", reason_code="registration_metadata_invalid",
                           profile=profile)

    def _capabilities(self, sdk: Path, *, deadline: float,
                      input_profile: str = PROFILE) -> dict[str, Any]:
        unavailable = ("generic_capabilities_unavailable"
                       if input_profile == PUBLIC_M0_PROFILE
                       else "text_menu_capabilities_unavailable")
        node = shutil.which("node")
        if node is None:
            raise BoundaryError("local_model_registration", unavailable)
        endpoint = _loopback(self.config.platform_url or "http://127.0.0.1:15526")
        environment = {key: value for key, value in os.environ.items() if key in
                       {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
        try:
            result = subprocess.run(
                [node, "--input-type=module", "-e", _SDK_SCRIPT, sdk.as_uri(), endpoint,
                 input_profile],
                capture_output=True, check=False, timeout=_remaining(deadline),
                env=environment,
            )
            if result.returncode or len(result.stdout) > 65536:
                raise ValueError
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _remaining(deadline)
            raise BoundaryError("local_model_registration", unavailable) from error

    def _context_available(self, sdk: Path, *, deadline: float,
                           input_profile: str = PROFILE) -> None:
        """Require the opt-in atomic context route; a menu may have no current run."""
        node = shutil.which("node")
        if node is None:
            raise BoundaryError("local_model_registration", "observation_context_unavailable")
        endpoint = _loopback(self.config.platform_url or "http://127.0.0.1:15526")
        environment = {key: value for key, value in os.environ.items() if key in
                       {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
        try:
            result = subprocess.run(
                [node, "--input-type=module", "-e", _CONTEXT_SCRIPT, sdk.as_uri(), endpoint,
                 input_profile],
                capture_output=True, check=False, timeout=_remaining(deadline),
                env=environment,
            )
            if result.returncode or len(result.stdout) > 1024:
                raise ValueError
            value = json.loads(result.stdout)
            if (not isinstance(value, dict) or set(value) != {"schema", "continuity_available"}
                    or value["schema"] !=
                    ("sts2.player-environment/text-menu-observation-context-2"
                     if input_profile == "text-menu-v2" else
                     "sts2.player-environment/text-menu-observation-context-1")
                    or type(value["continuity_available"]) is not bool):
                raise ValueError
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _remaining(deadline)
            raise BoundaryError("local_model_registration",
                                "observation_context_unavailable") from error

    def _m2_runtime_manifest_compatible(self, node_modules: Path,
                                        manifest_path: Path, *, deadline: float) -> None:
        node = shutil.which("node")
        if node is None:
            raise BoundaryError("local_model_registration", "m2_runtime_contract_unavailable")
        script = ("import {readFile} from 'node:fs/promises';"
                  "const runtime=await import(process.argv[1]);"
                  "const manifest=JSON.parse(await readFile(process.argv[2],'utf8'));"
                  "runtime.validatePolicyManifest(manifest);"
                  "if(manifest.adapter.protocol==='sts2.policy-runtime/decision-only-ndjson-4'"
                  "&&runtime.PUBLIC_STATEFUL_WORKBENCH_CONTROL_PROFILE!=="
                  "'sts2.policy-runtime/public-stateful-workbench-control-v1')"
                  "throw new Error('public stateful workbench control unavailable');")
        environment = {key: value for key, value in os.environ.items() if key in
                       {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
        try:
            result = subprocess.run(
                [node, "--input-type=module", "-e", script,
                 (node_modules / RUNTIME_PACKAGE / "dist/index.js").as_uri(),
                 str(manifest_path)],
                capture_output=True, check=False, timeout=_remaining(deadline),
                env=environment,
            )
            if result.returncode:
                raise ValueError
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _remaining(deadline)
            raise BoundaryError("local_model_registration",
                                "m2_runtime_contract_unavailable") from error

    def register(self, model_id: object, *, environment_kind: str = "native") -> dict[str, Any]:
        _target_kind(environment_kind)
        try:
            result = self._register(model_id, environment_kind=environment_kind)
        except sqlite3.DatabaseError as error:
            # Verification can use private, disk-backed decision rows before any
            # registration is published. A storage fault must finish the POST as
            # a classified failure, rather than disconnecting the browser thread.
            raise RegistrationStorageError(error) from error
        except BoundaryError as error:
            cause = error.__cause__
            for _ in range(8):
                if isinstance(cause, sqlite3.DatabaseError):
                    raise RegistrationStorageError(cause) from error
                if cause is None:
                    break
                cause = cause.__cause__
            raise
        if environment_kind == "managed":
            result["environment_kind"] = environment_kind
        return result

    def _register(self, model_id: object, *, environment_kind: str) -> dict[str, Any]:
        identity = digest(model_id, "local_model_registration.model_id")

        deadline = monotonic() + EXPORT_VERIFICATION_SECONDS
        # Weight/scorer verification and current-store binding are explicit POST work.
        status_for_model = getattr(self.export, "status_for_model", None)
        observed = (status_for_model(identity) if callable(status_for_model)
                    else self.export.status())
        operation = observed["operation"]
        memory = (observed.get("schema") == "stpd/local-model-export-operation-v2"
                  and operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        public_m0 = (observed.get("schema") == "stpd/local-model-export-operation-v3"
                     and operation.get("model_id") == identity
                     and operation.get("model_type") == "public_m0"
                     and operation.get("profile") == PUBLIC_M0_PROFILE)
        public_m2 = (observed.get("schema") == "stpd/local-model-export-operation-v4"
                     and operation.get("model_id") == identity
                     and operation.get("model_type") == "public_m2"
                     and operation.get("profile") == PUBLIC_M2_PROFILE)
        if operation.get("model_type") == "public_m0" and not public_m0:
            raise BoundaryError("local_model_registration",
                                "public_m0_export_verification_required")
        if operation.get("model_type") == "public_m2" and not public_m2:
            raise BoundaryError("local_model_registration",
                                "public_m2_export_verification_required")
        require_local_models("local_model_registration")
        if public_m2:
            export = self.export.verified_public_m2_for_registration(identity, deadline=deadline)
        elif memory:
            export = self.export.verified_memory_for_registration(identity, deadline=deadline)
        elif public_m0:
            verify_public = getattr(self.export, "verified_public_m0_for_registration", None)
            if verify_public is None:
                raise BoundaryError("local_model_registration",
                                    "public_m0_export_verification_required")
            export = verify_public(identity, deadline=deadline)
        else:
            export = self.export.verified_for_registration(identity)
        _remaining(deadline)
        if public_m2:
            from stpd.policy.public_m2_export import validate_package

            artifact, m2_config, _lineage = validate_package(export)
            if artifact.artifact_id != identity:
                raise BoundaryError("local_model_registration", "export_identity_mismatch")
            profile, recipe = PUBLIC_M2_PROFILE, "public-m2-observation-stateful-v1"
        elif memory:
            # The isolated verification child has checked exact model/run lineage
            # and the parent has rebound the response to unchanged package bytes.
            recipe = self.export.verified_memory_recipe_for_registration(
                identity, deadline=deadline)
            if recipe not in MEMORY_RECIPE_LABELS:
                raise BoundaryError("local_model_registration", "unsupported_model_recipe")
            profile = V2_M2_PROFILE if recipe in V2_MEMORY_RECIPES else M2_PROFILE
        elif public_m0:
            from stpd.models.stage1a import recipe_for
            from stpd.policy.token_decision import (
                PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA,
                PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
                check_light_action_m0_model,
            )

            profile = PUBLIC_M0_PROFILE
            envelope = _object_file(export / "model.json")
            if (envelope.get("schema") != PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
                    or envelope.get("model_id") != identity):
                raise BoundaryError("local_model_registration",
                                    "public_m0_export_verification_required")
            artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
            m0_config, info = check_light_action_m0_model(artifact)
            recipe = m0_config.recipe
            if (info.get("schema") != PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA
                    or recipe_for(recipe).graph != "dsimple.light-action.m0.v1"):
                raise BoundaryError("local_model_registration", "unsupported_public_m0_model")
        else:
            from stpd.policy.token_decision import check_model

            profile = PROFILE
            envelope = _object_file(export / "model.json")
            if envelope.get("model_id") != identity:
                raise BoundaryError("local_model_registration", "export_identity_mismatch")
            artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
            recipe = check_model(artifact)[0].recipe
            if recipe not in RECIPE_LABELS:
                raise BoundaryError("local_model_registration", "unsupported_model_recipe")
        _remaining(deadline)
        # Only a fully verified export reaches the bounded Runtime/registration phase.
        deadline = monotonic() + REGISTRATION_SECONDS
        managed = environment_kind == "managed"
        if managed and (not memory or input_profile_for_recipe(recipe)
                        != "text-menu-v2-confirmed-interaction"):
            raise BoundaryError("local_model_registration",
                                "managed_requires_confirmed_interaction_model")
        if profile in PUBLIC_PROFILES:
            directory, pin = self.models.directory, self.models.registry()["runtime_package"]
            if not isinstance(pin, dict) or pin.get("package") != RUNTIME_PACKAGE:
                raise BoundaryError("local_model_registration", "runtime_package_not_pinned")
        else:
            try:
                directory, pin = self.models.text_runtime_profile(profile)
            except BoundaryError as error:
                if error.code == "text_runtime_profile_required":
                    raise BoundaryError("local_model_registration",
                                        "text_runtime_profile_required") from error
                raise
        try:
            node_modules = directory / "runtime" / "node_modules"
            validate_runtime_install(node_modules, pin, self.models._connector_pin())
        except (BoundaryError, OSError, PackageIdentityError, ValueError) as error:
            _remaining(deadline)
            code = ("runtime_package_not_pinned" if profile in PUBLIC_PROFILES
                    else "text_runtime_local_install_required")
            raise BoundaryError("local_model_registration", code) from error
        sdk = _connector_sdk_path(node_modules, pin)
        if not managed and profile == V2_M2_PROFILE and not _v2_sdk_available(sdk):
            raise BoundaryError("local_model_registration", "v2_runtime_contract_unavailable")
        _remaining(deadline)
        input_profile = ("text-menu-v2" if profile == V2_M2_PROFILE else
                         PUBLIC_M0_PROFILE if profile in PUBLIC_PROFILES else PROFILE)
        if managed:
            requirements, support = _managed_requirements(self.models._managed_runtime_target())
        else:
            capabilities = (self._capabilities(sdk, deadline=deadline,
                                               input_profile=input_profile)
                            if profile in {V2_M2_PROFILE, *PUBLIC_PROFILES} else
                            self._capabilities(sdk, deadline=deadline))
            requirements, support = _requirements(capabilities,
                                                  input_profile=input_profile)
        if memory and not managed:
            if profile == V2_M2_PROFILE:
                self._context_available(sdk, deadline=deadline,
                                        input_profile=input_profile)
            else:
                self._context_available(sdk, deadline=deadline)
        _remaining(deadline)
        private = self.models.private_root
        if private.exists() or private.is_symlink():
            if not _ordinary(private, directory=True):
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        else:
            private.mkdir(mode=0o700)
        lock_path = self.models.private_root / LOCK
        if lock_path.is_symlink() or (lock_path.exists()
                                      and not _ordinary(lock_path, directory=False)):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        try:
            with instance_lock(lock_path):
                _remaining(deadline)
                found, _ = self._matching(identity, export, requirements, support,
                                           profile=profile, environment_kind=environment_kind)
                if found is not None:
                    _remaining(deadline)
                    return _public(identity, "registered", selection_id=found,
                                   profile=profile)
                folder = self.models.private_root / REGISTRATIONS
                if folder.exists() or folder.is_symlink():
                    if not _ordinary(folder, directory=True):
                        raise BoundaryError(
                            "local_model_registration", "registration_metadata_invalid",
                        )
                else:
                    folder.mkdir(mode=0o700)
                selection = ("local-text-m2-" if memory else
                             "local-public-m2-" if public_m2 else
                             "local-public-m0-" if public_m0 else
                             "local-text-b-") + uuid.uuid4().hex
                target = folder / selection
                target.mkdir(mode=0o700)
                config_path, manifest_path = target / "config.json", target / "manifest.json"
                try:
                    binder = (bind_managed_memory_export if managed else
                              bind_memory_export if memory else
                              bind_public_m2_export if public_m2 else
                              bind_public_m0_export if public_m0 else bind_text_menu_export)
                    binding: dict[str, Any] = {"manifest_id": selection,
                               "policy": {"id": selection, "version": "1.0.0",
                                          "provider": "stpd", "architecture": recipe},
                               "requirements": requirements, "support": support,
                               "binding_root": self.models.private_root}
                    if memory and not managed and input_profile_for_recipe(recipe) != PROFILE:
                        binding["input_profile"] = input_profile_for_recipe(recipe)
                    binder(self.models.root, export, config_path, manifest_path,
                           **binding)
                    if memory or public_m2:
                        self._m2_runtime_manifest_compatible(node_modules, manifest_path,
                                                             deadline=deadline)
                    entries = self._entries()
                    label = (MEMORY_RECIPE_LABELS[recipe] if memory else
                             f"Public M2 carry{m2_config.slots}/window{m2_config.window_steps} "
                             f"epoch{artifact.parameters.value()['epoch']}" if public_m2 else
                             "D-Simple public M0" if public_m0 else RECIPE_LABELS[recipe])
                    entry = {"id": selection,
                             "label": ("独立游戏环境 " if managed else
                                       "本机公开快照 " if public_m0 or public_m2
                                       else "本机文字菜单 ")
                                      + label + " " + identity[:8],
                             "adapter": ("stpd-m2-decision-adapter" if memory else
                                         PUBLIC_M2_ADAPTER if public_m2 else
                                         PUBLIC_M0_ADAPTER if public_m0 else "token-v1"),
                             "runtime_profile": profile,
                             "manifest": manifest_path.relative_to(
                                 self.models.private_root).as_posix(),
                             "config": config_path.relative_to(
                                 self.models.private_root).as_posix()}
                    _remaining(deadline)
                    atomic_json(self.models.private_root / REGISTRY,
                                {"schema": "stpd/local-token-policies-v1",
                                 "policies": [*entries, entry]})
                except Exception:
                    config_path.unlink(missing_ok=True)
                    manifest_path.unlink(missing_ok=True)
                    target.rmdir()
                    raise
                return _public(identity, "registered", selection_id=selection,
                               profile=profile)
        except BoundaryError as error:
            if error.code == "already_running":
                raise BoundaryError(
                    "local_model_registration", "registration_in_progress",
                ) from error
            raise
        except OSError as error:
            raise BoundaryError("local_model_registration", "registration_write_failed") from error
