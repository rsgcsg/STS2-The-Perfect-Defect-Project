"""Explicit local text-menu policy registration from a verified offline export.

Registration records a reviewed engineering scope. Runtime readiness and loading
remain separate, and the current Connector remains the environment authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from pathlib import Path
from time import monotonic
from typing import Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, digest, json_bytes
from spireagent.package_identity import PackageIdentityError
from spireagent.policies import policy_support
from spireagent.policy_files import _inside, _object_file
from spireagent.workbench.developer import ProjectConfig, atomic_json
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_model_dependencies import (
    local_models_available,
    native_models_available,
    require_local_models,
    require_native_models,
)
from spireagent.workbench.local_model_export import LocalModelExport, _ordinary
from spireagent.workbench.local_models import (
    NATIVE_ADAPTER,
    NATIVE_PROFILE,
    PUBLIC_M0_ADAPTER,
    PUBLIC_M0_PROFILE,
    PUBLIC_M2_ADAPTER,
    PUBLIC_M2_PROFILE,
    LocalModelService,
    _loopback,
)
from spireagent.workbench.managed_model_target import managed_manifest
from spireagent.workbench.memory_recipe import (
    MEMORY_RECIPES,
    V2_MEMORY_RECIPES,
    input_profile_for_recipe,
    memory_settings_for_recipe,
)
from spireagent.workbench.runtime_install import (
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
RECIPE_LABELS = {"stage1a.b.s.v2": "B", "stage1a.dsimple.s.v1": "D-Simple"}
_DEFAULT_PUBLIC_TASK = object()


def _public_m0_task(value: object) -> dict[str, str] | None:
    from stpd.policy.public_m0_task import ready_summary_task_spec, validate_task_spec

    return (ready_summary_task_spec() if value is _DEFAULT_PUBLIC_TASK
            else validate_task_spec(value))


def _public_m2_task(value: object) -> dict[str, str] | None:
    from stpd.policy.public_m2_task import ready_summary_task_spec, validate_task_spec

    return (ready_summary_task_spec() if value is _DEFAULT_PUBLIC_TASK
            else validate_task_spec(value))


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


_v2_sdk_available = v2_sdk_available
REGISTRY = "token-policies-v1.json"
LOCK = "token-policies-v1.lock"
REGISTRATIONS = "model-registrations"
REGISTRATION_SECONDS = 22.0
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
const {PlayerEnvironmentRestClient,EnvironmentControllerSession,NativeLogicalSession} =
  await import(process.argv[1]);
const client = new PlayerEnvironmentRestClient(process.argv[2], 5000);
const result = await (process.argv[3] === 'native-logical-v1'
  ? new NativeLogicalSession(client, new EnvironmentControllerSession(client,
      {productId:'spireagent-native-registration',productName:'SpireAgent',productVersion:'1.0.0'}))
      .capabilities() : process.argv[3] === 'text-menu-v2'
  ? client.textMenuV2Capabilities()
  : ['public-snapshot-m0-v1','public-snapshot-m2-v1'].includes(process.argv[3])
  ? client.capabilities() : client.textMenuCapabilities());
if(process.argv[3] === 'native-logical-v1'){
  const {NATIVE_LOGICAL_PUBLICATION_PROFILE,NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256} =
    await import(process.argv[1]);
  if(!NATIVE_LOGICAL_PUBLICATION_PROFILE)throw Error('fixed_native_profile_unavailable');
  process.stdout.write(JSON.stringify({capabilities:result.data,
    publication_profile:NATIVE_LOGICAL_PUBLICATION_PROFILE,
    publication_profile_sha256:NATIVE_LOGICAL_PUBLICATION_PROFILE_SHA256}));
}else{process.stdout.write(JSON.stringify(result.data));}
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
    if input_profile in {PUBLIC_M0_PROFILE, PUBLIC_M2_PROFILE}:
        return _public_m0_requirements(value)
    try:
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


def _native_requirements(value: Any, *, execution_policy: dict[str, Any] | None = None,
                         program_agent: bool = False
                         ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Bind the fixed code-owned publication target, never an advertised subset."""
    from spireagent.json_boundary import object_fields
    from spireagent.workbench.native_agent_support import (
        PUBLICATION_PROFILE_ID,
        PUBLICATION_PROFILE_SHA256,
    )
    from stpd.native_code_scope import REQUIRED_METHODS
    if program_agent:
        from stpd.policy.native_teacher_manifest import required_methods

        method_requirements = required_methods(execution_policy)
        interaction_kinds, action_verbs = ["*"], ["*"]
    else:
        from stpd.policy.native_agent import SUPPORTED_ACTION_VERBS, SUPPORTED_INTERACTION_KINDS

        method_requirements = list(REQUIRED_METHODS)
        interaction_kinds, action_verbs = (
            list(SUPPORTED_INTERACTION_KINDS),
            list(SUPPORTED_ACTION_VERBS),
        )

    try:
        reply = object_fields(
            value,
            {"capabilities", "publication_profile", "publication_profile_sha256"},
            "native_registration",
        )
        capabilities = reply["capabilities"]
        profile = object_fields(
            reply["publication_profile"],
            {
                "schema",
                "profile_id",
                "input_profile",
                "delivery_mode",
                "eager_scope",
                "required_seams",
            },
            "native_registration.profile",
        )
        if (
            profile["schema"] != "sts2.player-environment/native-logical-publication-profile-1"
            or profile["profile_id"] != PUBLICATION_PROFILE_ID
            or reply["publication_profile_sha256"] != PUBLICATION_PROFILE_SHA256
            or profile["input_profile"] != NATIVE_PROFILE
            or profile["delivery_mode"] != "full_reference"
            or profile["eager_scope"] != ["persistent", "interaction", "referents", "catalog"]
            or not isinstance(capabilities, dict)
            or capabilities.get("schema") != "sts2.player-environment/native-logical-capabilities-1"
            or capabilities.get("input_profile") != NATIVE_PROFILE
            or capabilities.get("protocol_version") != "1.0.0"
        ):
            raise ValueError
        methods = list(method_requirements)
        if execution_policy is not None:
            from stpd.policy.native_execution_policy import checked_execution_policy
            checked_execution_policy(execution_policy)
            if "current_owned" not in methods:
                methods.append("current_owned")
            mechanisms = capabilities.get("implemented_mechanisms")
            if (
                not isinstance(mechanisms, list)
                or any(not isinstance(item, str) or not item for item in mechanisms)
                or "native_current_reader_owned_v1" not in mechanisms
            ):
                raise ValueError
        advertised_methods = capabilities.get("supported_methods")
        if not isinstance(advertised_methods, list) or not set(methods) <= set(advertised_methods):
            raise ValueError
        required = profile["required_seams"]
        coverage = capabilities["capture_coverage"]
        if not isinstance(required, list) or not required or not isinstance(coverage, list):
            raise ValueError
        advertised: dict[str, dict[str, str]] = {}
        for seam in coverage:
            seam = object_fields(
                seam, {"source_seam", "version", "coverage"}, "native_registration.coverage"
            )
            if (not all(isinstance(item, str) and item for item in seam.values())
                    or seam["source_seam"] in advertised):
                raise ValueError
            advertised[seam["source_seam"]] = seam
        seen = set()
        for seam in required:
            seam = object_fields(
                seam, {"source_seam", "version", "coverage"}, "native_registration.required"
            )
            seam_id = seam["source_seam"]
            if (
                seam_id in seen
                or seam["version"] != "1"
                or seam["coverage"] != "complete_at_seam"
                or advertised.get(seam["source_seam"]) != seam
            ):
                raise BoundaryError("local_model_registration", "native_required_seam_unavailable")
            seen.add(seam_id)
        host, game = capabilities["host"], capabilities["game"]
        implementation, modset = host["implementation"], game["modset"]
        environment = {
            "host_kind": host["host_kind"],
            "connector_version": host["version"],
            "connector_source_revision": implementation["source_revision"],
            "connector_artifact_sha256": implementation["artifact_sha256"],
            "connector_module_version_id": implementation["module_version_id"],
            "modset_status": modset["status"],
            "modset_fingerprint": modset["fingerprint"],
            "loaded_mod_ids": modset["loaded_mod_ids"],
        }
        if (
            environment["host_kind"] not in {"live_ui", "headless", "replay", "test"}
            or any(
                not isinstance(environment[key], str) or not environment[key]
                for key in (
                    "connector_version",
                    "connector_source_revision",
                    "connector_module_version_id",
                    "modset_status",
                )
            )
            or not isinstance(environment["loaded_mod_ids"], list)
            or any(not isinstance(item, str) or not item for item in environment["loaded_mod_ids"])
            or len(set(environment["loaded_mod_ids"])) != len(environment["loaded_mod_ids"])
        ):
            raise ValueError
        digest(environment["connector_artifact_sha256"], "native_registration.connector")
        digest(environment["modset_fingerprint"], "native_registration.modset")
        if any(not isinstance(game[key], str) or not game[key] or game[key] == "*"
               for key in ("version", "commit")):
            raise ValueError
        requirements = {"connector_protocol_version": capabilities["protocol_version"],
                        "environment": environment, "required_methods": methods}
        support = {"game_versions": [game["version"]], "game_commits": [game["commit"]],
                   "interaction_kinds": interaction_kinds,
                   "action_verbs": action_verbs}
        return requirements, support, required
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, BoundaryError) and error.code == "native_required_seam_unavailable":
            raise
        raise BoundaryError(
            "local_model_registration", "native_capabilities_incompatible"
        ) from error


def _selected_native_execution_policy(
    manifest: dict[str, Any], policy: dict[str, Any] | None
) -> None:
    if (("execution_policy" in manifest) != (policy is not None)
            or manifest.get("execution_policy") != policy):
        raise BoundaryError("local_model_registration", "selected_package_execution_policy_changed")


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


class LocalModelRegistration:
    def __init__(self, config: ProjectConfig, export: LocalModelExport,
                 models: LocalModelService) -> None:
        self.config, self.export, self.models = config, export, models

    def _export_status(self, identity: str) -> dict[str, Any]:
        status_for_model = getattr(self.export, "status_for_model", None)
        return (status_for_model(identity) if callable(status_for_model)
                else self.export.status())

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
                  structured: bool = False
                  ) -> tuple[str | None, bool]:
        stale = False
        if structured:
            model_id = _object_file(export / "model.json")["model_id"]
        memory = profile in {M2_PROFILE, V2_M2_PROFILE} and not structured
        adapter_id = ("stpd-s0-structured-adapter" if structured else
                      "stpd-m2-decision-adapter" if memory else "token-v1")
        owner = policy_support(adapter_id)
        current_code = owner.code_digest(self.models.root) if not structured else None
        for entry in reversed(self._entries()):
            if (entry.get("runtime_profile") != profile
                    or (entry.get("adapter") == "stpd-s0-structured-adapter") != structured):
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
                if (not structured and
                        manifest.get("adapter", {}).get("code_sha256") != current_code):
                    stale = True
                    continue
                try:
                    # The owning validator selects config-v1 broad or config-v2
                    # inference closure identity from its verified package schema.
                    owner.validate(self.models.root,
                                   _inside(self.models.private_root, entry["config"]),
                                   _inside(self.models.private_root, entry["manifest"]),
                                   binding_root=self.models.private_root)
                except BoundaryError as error:
                    if structured and error.code == "trusted_policy_identity_drift":
                        stale = True
                        continue
                    raise
                if (requirements is not None and manifest.get("requirements") != requirements
                        or support is not None and manifest.get("support") != support):
                    continue
                return entry["id"], stale
            except (AttributeError, BoundaryError, KeyError, TypeError, ValueError) as error:
                raise BoundaryError("local_model_registration",
                                    "registration_metadata_invalid") from error
        return None, stale

    def _matching_native(self, artifact_id: str, export: Path, *,
                         requirements: dict[str, Any] | None = None,
                         support: dict[str, Any] | None = None,
                         required_seams: list[dict[str, str]] | None = None
                         ) -> tuple[str | None, bool]:
        from spireagent.workbench.native_agent_support import validate

        stale = False
        for entry in reversed(self._entries()):
            if (
                entry.get("adapter") != NATIVE_ADAPTER
                or entry.get("runtime_profile") != NATIVE_PROFILE
            ):
                continue
            config_path = _inside(self.models.private_root, entry["config"])
            manifest_path = _inside(self.models.private_root, entry["manifest"])
            config = _object_file(config_path)
            if (config.get("artifact_id") != artifact_id
                    or config.get("export_path") != str(export.resolve())):
                continue
            try:
                _, manifest = validate(self.models.root, config_path, manifest_path,
                                       binding_root=self.models.private_root)
            except BoundaryError:
                stale = True
                continue
            if (requirements is not None and manifest["requirements"] != requirements
                    or support is not None and manifest["support"] != support
                    or required_seams is not None
                    and manifest["input"]["attachment"]["required_seams"] != required_seams):
                stale = True
                continue
            return entry["id"], stale
        return None, stale

    def status(self, model_id: object, *, environment_kind: str = "native",
               task: object = _DEFAULT_PUBLIC_TASK,
               program: object = None) -> dict[str, Any]:
        _target_kind(environment_kind)
        result = self._status(model_id, environment_kind=environment_kind, task=task,
                              **({"program": program} if program is not None else {}))
        if environment_kind == "managed":
            result["environment_kind"] = environment_kind
        return result

    def _status(self, model_id: object, *, environment_kind: str,
                task: object = _DEFAULT_PUBLIC_TASK,
                program: object = None) -> dict[str, Any]:
        identity = digest(model_id, "local_model_registration.model_id")
        if self._p1_supported(identity):
            return self._status_p1(identity, environment_kind)
        observed = self._export_status(identity)
        operation = observed["operation"]
        if program is not None and operation.get("model_type") != "public_m0":
            raise BoundaryError("local_model_registration", "program_requires_public_m0")
        if operation.get("model_id") == identity and operation.get("model_type") == "public_m2":
            return self._status_public_m2(identity, observed, environment_kind,
                                         task=_public_m2_task(task))
        if operation.get("model_id") == identity and operation.get("model_type") == "native":
            return self._status_native(identity, observed, environment_kind)
        if operation.get("model_id") == identity and operation.get("model_type") == "public_m0":
            from stpd.policy.public_m0_agent_program import validate_program_spec

            return self._status_public_m0(identity, observed, environment_kind,
                                          task=_public_m0_task(task),
                                          program=validate_program_spec(program))
        memory = (operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        structured = operation.get("model_type") == "structured"
        profile = V2_M2_PROFILE if structured else M2_PROFILE if memory else PROFILE
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
                                          environment_kind=environment_kind,
                                          structured=structured)
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
                    sdk = (node_modules / RUNTIME_PACKAGE / "node_modules" /
                           CONNECTOR_PACKAGE / "dist" / "index.js")
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
        node = shutil.which("node")
        if node is None:
            raise BoundaryError("local_model_registration", "text_menu_capabilities_unavailable")
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
            raise BoundaryError("local_model_registration",
                                "text_menu_capabilities_unavailable") from error

    def _native_runtime(self, *, deadline: float) -> tuple[Path, Path]:
        directory, pin = self.models.runtime_profile()
        node_modules = directory / "runtime" / "node_modules"
        if not (directory / "runtime").exists():
            node_modules = self.models._node_modules()
        try:
            validate_runtime_install(node_modules, pin, self.models._connector_pin())
        except (BoundaryError, OSError, PackageIdentityError, ValueError) as error:
            _remaining(deadline)
            raise BoundaryError("local_model_registration",
                                "native_runtime_local_install_required") from error
        sdk = node_modules / RUNTIME_PACKAGE / "node_modules" / CONNECTOR_PACKAGE / "dist/index.js"
        return node_modules, sdk

    def _status_native(self, identity: str, observed: dict[str, Any],
                       environment_kind: str) -> dict[str, Any]:
        if environment_kind != "native":
            return _public(identity, "unavailable", profile=NATIVE_PROFILE,
                           reason_code="native_managed_environment_not_supported")
        if (observed.get("availability") != "ready"
                or observed["operation"].get("status") != "completed"):
            return _public(identity, "unavailable", profile=NATIVE_PROFILE,
                           reason_code="verified_export_required")
        if not native_models_available():
            return _public(identity, "unavailable", profile=NATIVE_PROFILE,
                           reason_code="native_models_extra_required")
        try:
            export = self.config.state_dir / "model-exports" / identity
            found, stale = self._matching_native(identity, export)
            return _public(identity, "registered" if found else "not_registered",
                           selection_id=found, profile=NATIVE_PROFILE,
                           reason_code="source_binding_changed" if stale and not found else None)
        except (BoundaryError, OSError, ValueError, KeyError, TypeError):
            return _public(identity, "unavailable", profile=NATIVE_PROFILE,
                           reason_code="registration_metadata_invalid")

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
                  "const {validatePolicyManifest}=await import(process.argv[1]);"
                  "validatePolicyManifest(JSON.parse(await readFile(process.argv[2],'utf8')));")
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

    def register_composition(
        self, model_id: object, *, environment_kind: str = "native",
        task_option: str = "default", program_option: str = "none",
    ) -> dict[str, Any]:
        """Closed product choices select real owning specs before registration."""
        if (task_option not in {"default", "none", "ready-standard-summary"}
                or program_option not in {"none", "card-reward-take"}):
            raise BoundaryError("local_model_registration", "unsupported_composition_option")
        identity = digest(model_id, "local_model_registration.model_id")
        model_type = self._export_status(identity)["operation"].get("model_type")
        if model_type not in {"public_m0", "public_m2"}:
            raise BoundaryError("local_model_registration", "public_composition_required")
        task = (_DEFAULT_PUBLIC_TASK if task_option == "default" else None
                if task_option == "none" else _public_m0_task(_DEFAULT_PUBLIC_TASK)
                if model_type == "public_m0" else _public_m2_task(_DEFAULT_PUBLIC_TASK))
        program = None
        if program_option == "card-reward-take":
            if model_type != "public_m0":
                raise BoundaryError("local_model_registration", "program_requires_public_m0")
            from stpd.policy.public_m0_agent_program import card_reward_take_program_spec

            program = card_reward_take_program_spec()
        return self.register(identity, environment_kind=environment_kind,
                             task=task, program=program)

    def register(self, model_id: object, *, environment_kind: str = "native",
                 task: object = _DEFAULT_PUBLIC_TASK,
                 program: object = None) -> dict[str, Any]:
        _target_kind(environment_kind)
        try:
            result = self._register(model_id, environment_kind=environment_kind, task=task,
                                    **({"program": program} if program is not None else {}))
        except sqlite3.DatabaseError as error:
            # Verification can use private, disk-backed decision rows before any
            # registration is published. A storage fault must finish the POST as
            # a classified failure, rather than disconnecting the browser thread.
            raise RegistrationStorageError(error) from error
        if environment_kind == "managed":
            result["environment_kind"] = environment_kind
        return result

    def _register(self, model_id: object, *, environment_kind: str,
                  task: object = _DEFAULT_PUBLIC_TASK,
                  program: object = None) -> dict[str, Any]:
        identity = digest(model_id, "local_model_registration.model_id")
        if self._p1_supported(identity):
            return self._register_p1(identity, environment_kind)

        deadline = monotonic() + REGISTRATION_SECONDS
        # Weight/scorer verification and current-store binding are explicit POST work.
        observed = self._export_status(identity)
        operation = observed["operation"]
        if program is not None and operation.get("model_type") != "public_m0":
            raise BoundaryError("local_model_registration", "program_requires_public_m0")
        if operation.get("model_id") == identity and operation.get("model_type") == "public_m2":
            return self._register_public_m2(identity, environment_kind=environment_kind,
                                           task=_public_m2_task(task))
        if operation.get("model_id") == identity and operation.get("model_type") == "native":
            return self._register_native(identity, environment_kind=environment_kind,
                                         deadline=deadline)
        if operation.get("model_id") == identity and operation.get("model_type") == "public_m0":
            from stpd.policy.public_m0_agent_program import validate_program_spec

            return self._register_public_m0(identity, environment_kind=environment_kind,
                                            task=_public_m0_task(task),
                                            program=validate_program_spec(program))
        memory = (observed.get("schema") == "stpd/local-model-export-operation-v2"
                  and operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        structured = operation.get("model_type") == "structured"
        require_local_models("local_model_registration")
        export = (self.export.verified_memory_for_registration(identity, deadline=deadline)
                  if memory else self.export.verified_for_registration(identity))
        _remaining(deadline)
        if structured:
            profile = V2_M2_PROFILE
            recipe = "stpd.structured-observation-only.s-m2-0.v1"
        elif memory:
            # The isolated verification child has checked exact model/run lineage
            # and the parent has rebound the response to unchanged package bytes.
            recipe = self.export.verified_memory_recipe_for_registration(
                identity, deadline=deadline)
            if recipe not in MEMORY_RECIPE_LABELS:
                raise BoundaryError("local_model_registration", "unsupported_model_recipe")
            profile = V2_M2_PROFILE if recipe in V2_MEMORY_RECIPES else M2_PROFILE
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
        managed = environment_kind == "managed"
        if managed and (not memory or input_profile_for_recipe(recipe)
                        != "text-menu-v2-confirmed-interaction"):
            raise BoundaryError("local_model_registration",
                                "managed_requires_confirmed_interaction_model")
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
            raise BoundaryError("local_model_registration",
                                "text_runtime_local_install_required") from error
        sdk = (node_modules / RUNTIME_PACKAGE / "node_modules" / CONNECTOR_PACKAGE
               / "dist" / "index.js")
        if not managed and profile == V2_M2_PROFILE and not _v2_sdk_available(sdk):
            raise BoundaryError("local_model_registration", "v2_runtime_contract_unavailable")
        _remaining(deadline)
        input_profile = "text-menu-v2" if profile == V2_M2_PROFILE else PROFILE
        if managed:
            requirements, support = _managed_requirements(self.models._managed_runtime_target())
        else:
            capabilities = (self._capabilities(sdk, deadline=deadline,
                                               input_profile=input_profile)
                            if profile == V2_M2_PROFILE else
                            self._capabilities(sdk, deadline=deadline))
            requirements, support = _requirements(capabilities,
                                                  input_profile=input_profile)
        if (memory or structured) and not managed:
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
                                           profile=profile, environment_kind=environment_kind,
                                           structured=structured)
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
                selection = ("local-text-structured-" if structured else
                             "local-text-m2-" if memory else "local-text-b-") + uuid.uuid4().hex
                target = folder / selection
                target.mkdir(mode=0o700)
                config_path, manifest_path = target / "config.json", target / "manifest.json"
                try:
                    if structured:
                        from stpd.structured_policy_installation import bind_structured_export

                    binder = (bind_structured_export if structured else
                              bind_managed_memory_export if managed else
                              bind_memory_export if memory else bind_text_menu_export)
                    binding: dict[str, Any] = {"manifest_id": selection,
                               "policy": {"id": selection, "version": "1.0.0",
                                          "provider": "stpd", "architecture": recipe},
                               "requirements": requirements, "support": support,
                               "binding_root": self.models.private_root}
                    if memory and not managed and input_profile_for_recipe(recipe) != PROFILE:
                        binding["input_profile"] = input_profile_for_recipe(recipe)
                    binder(self.models.root, export, config_path, manifest_path,
                           **binding)
                    if memory or structured:
                        self._m2_runtime_manifest_compatible(node_modules, manifest_path,
                                                             deadline=deadline)
                    entries = self._entries()
                    label = ("结构 M2 S0 text-v2（I/F 关闭）" if structured else
                             MEMORY_RECIPE_LABELS[recipe] if memory
                             else RECIPE_LABELS[recipe])
                    entry = {"id": selection,
                             "label": ("独立游戏环境 " if managed else "本机文字菜单 ")
                                      + label + " " + identity[:8],
                             "adapter": ("stpd-s0-structured-adapter" if structured else
                                         "stpd-m2-decision-adapter" if memory else "token-v1"),
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

    def _p1_supported(self, identity: str) -> bool:
        from spireagent.storage.blobs import StoreError

        if not isinstance(self.export, LocalModelExport):
            return False
        try:
            model = self.export._source(identity).get_manifest(identity)
        except StoreError as error:
            if error.code == "object_not_found":
                return False
            raise
        return model.kind == "model" and model.parameters.value().get("schema") in {
            "stpd/p1-mstd-trained-model-1", "stpd/p1-mstd-model-1",
            "stpd/p1-baseline-trained-model-1"}

    def _p1_source(self, identity: str) -> tuple[Any, Manifest]:
        source = self.export._source(identity)
        model = source.get_manifest(identity)
        if (model.kind != "model" or model.parameters.value().get("schema") not in {
                "stpd/p1-mstd-trained-model-1", "stpd/p1-mstd-model-1",
                "stpd/p1-baseline-trained-model-1"}):
            raise BoundaryError("local_model_registration", "P1_model_required")
        return source, model

    def _status_p1(self, identity: str, environment_kind: str) -> dict[str, Any]:
        _, model = self._p1_source(identity)
        baseline = model.parameters.value()["schema"] == "stpd/p1-baseline-trained-model-1"
        adapter = "stpd-p1-baseline-agent" if baseline else "stpd-p1-mstd-agent"
        package_sha = model.parameters.value().get("package_sha256")
        for entry in reversed(self._entries()):
            if entry.get("adapter") != adapter:
                continue
            config = _object_file(self.models.entry_path(entry, "config"))
            manifest = _object_file(self.models.entry_path(entry, "manifest"))
            if (config.get("model_artifact_id") == identity
                    and config.get("package_sha256") == package_sha
                    and managed_manifest(manifest) == (environment_kind == "managed")):
                from stpd.p1 import agent, baseline_agent

                installation = baseline_agent.installation if baseline else agent.installation

                try:
                    installation(self.models.entry_path(entry, "config"),
                                 self.models.entry_path(entry, "manifest"),
                                 self.models.private_root)
                except (OSError, ValueError, KeyError, TypeError):
                    return _public(identity, "not_registered", profile="p1-global-r1",
                                   reason_code="source_binding_changed")
                return _public(identity, "registered", selection_id=entry["id"],
                               profile="p1-global-r1")
        return _public(identity, "not_registered", profile="p1-global-r1")

    def _register_p1(self, identity: str, environment_kind: str) -> dict[str, Any]:
        """Materialize the original Model, then bind it to the existing Runtime owner."""
        source, model = self._p1_source(identity)
        baseline = model.parameters.value()["schema"] == "stpd/p1-baseline-trained-model-1"
        from stpd.p1 import agent, baseline_agent, baseline_export, baseline_worker, export
        from stpd.workers import p1_run

        installation = baseline_agent.installation if baseline else agent.installation
        adapter_identity = baseline_export.adapter_identity if baseline else export.adapter_identity
        register = baseline_export.register if baseline else export.register
        load_model = export.load_model
        materialize_p1_model = (baseline_worker.materialize_baseline_model if baseline
                                else p1_run.materialize_p1_model)

        if baseline:
            from spireagent.workbench.local_model_dependencies import require_recipe_dependencies

            require_recipe_dependencies("p1-baselines-N-v1", "local_model_registration")
        else:
            require_local_models("local_model_registration")
        deadline = monotonic() + REGISTRATION_SECONDS
        node_modules, _ = self._native_runtime(deadline=deadline)
        runtime = node_modules / RUNTIME_PACKAGE / "dist"
        options = self._global_environment_options(environment_kind)
        private = self.models.private_root
        if private.is_symlink():
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        private.mkdir(mode=0o700, exist_ok=True)
        with instance_lock(private / LOCK):
            folder = private / REGISTRATIONS
            if folder.is_symlink():
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
            folder.mkdir(mode=0o700, exist_ok=True)
            selection = ("local-p1-baseline-" if baseline else "local-p1-mstd-") + uuid.uuid4().hex
            target = folder / selection
            with tempfile.TemporaryDirectory(prefix="p1-registration-", dir=private) as temporary:
                package = Path(temporary) / "package"
                if baseline or model.parameters.value()["schema"] == "stpd/p1-mstd-trained-model-1":
                    descriptor = materialize_p1_model(source, identity, package)
                else:
                    # Detached historical engineering packages have no fabricated
                    # training Run/Input/Checkpoint ancestry.
                    package.mkdir()
                    for role, name in (("weights", "model.tensor-tree"),
                                       ("package_manifest", "model.json")):
                        with (package / name).open("wb") as output:
                            for chunk in source.read_payload(model.payload(role)):
                                output.write(chunk)
                    descriptor = _object_file(package / "model.json")
                    _, payload = load_model(package / "model.tensor-tree",
                                            model.parameters.value()["package_sha256"])
                    if (descriptor.get("schema") != "stpd/p1-mstd-model-1"
                            or descriptor.get("package_sha256")
                            != model.parameters.value()["package_sha256"]
                            or descriptor.get("input_spec") != payload["input_spec"]
                            or descriptor.get("checkpoint_sha256") != payload["checkpoint_sha256"]):
                        raise BoundaryError("local_model_registration",
                                            "model_package_binding_mismatch")
                envelope = self._global_environment_manifest(
                    runtime, options, package / "model.tensor-tree", adapter_identity(),
                    selection, deadline)
                try:
                    label = (("P1 baseline · " if baseline else "P1 M-std · ")
                             + ("Reference" if environment_kind == "native"
                                              else "Managed") + " · " + identity[:8])
                    entry = register(package, target, envelope, entry_id=selection, label=label)
                    manifest_path = target / "manifest.json"
                    manifest = _object_file(manifest_path)
                    manifest["manifest_id"] = selection
                    atomic_json(manifest_path, manifest)
                    shutil.copyfile(package / "model.json", target / "model.json")
                    config_path = target / "config.json"
                    config = _object_file(config_path)
                    config["model_artifact_id"] = identity
                    config["package_manifest_sha256"] = hashlib.sha256(
                        (target / "model.json").read_bytes()).hexdigest()
                    atomic_json(config_path, config)
                    installation(config_path, target / "manifest.json", private)
                    self.models._public_manifest_contract(target / "manifest.json")
                    entry["manifest"] = (target / "manifest.json").relative_to(private).as_posix()
                    entry["config"] = config_path.relative_to(private).as_posix()
                    atomic_json(private / REGISTRY, {
                        "schema": "stpd/local-token-policies-v1",
                        "policies": [*self._entries(), entry],
                    })
                except Exception:
                    if target.exists():
                        shutil.rmtree(target)
                    raise
        return _public(identity, "registered", selection_id=selection, profile="p1-global-r1")

    def _global_environment_options(self, environment_kind: str) -> dict[str, str]:
        if environment_kind == "managed":
            from .managed_model_target import runtime_arguments
            managed_target = {
                **self.models._managed_runtime_target(), "input_profile": "public-basis-1",
            }
            args = runtime_arguments(managed_target, self.models.private_root)
            options = {args[i].removeprefix("--"): args[i+1] for i in range(0, len(args), 2)}
        else:
            options = {"connector-endpoint": self.models.config.platform_url or "http://127.0.0.1:15526"}
        return options

    def _global_environment_manifest(self, runtime: Path, options: dict[str, str],
                                     artifact_path: Path, adapter: dict[str, str],
                                     selection: str, deadline: float) -> dict[str, Any]:
        """Negotiate the owner-supplied complete public P1 environment envelope."""
        script = (
            "import{pathToFileURL}from'node:url';"
            "import{readFile}from'node:fs/promises';import{randomUUID}from'node:crypto';"
            "const d=process.argv[1],o=JSON.parse(process.argv[2]),"
            "p=process.argv[3],a=JSON.parse(process.argv[4]);"
            "const r=await import(pathToFileURL(d+'/index.js'));let t;"
            "if(o['managed-binding']){const m=await import(pathToFileURL(d+'/managed.js'));"
            "t=await m.ManagedServicePolicyClient.attachGlobal("
            "o['managed-binding'],o['managed-attachment'],"
            "{serviceInstanceId:o['managed-expected-service-instance-id'],"
            "runtimeInstanceId:o['managed-expected-runtime-instance-id'],"
            "gameContinuityId:o['managed-expected-game-continuity-id']},'run-'+randomUUID());}"
            "else{const s=await import(pathToFileURL("
            "d+'/../node_modules/@rsgcsg/sts2-connector-client/dist/index.js'));"
            "t=new r.ReferenceGlobalTransport("
            "new s.PlayerEnvironmentRestClient(o['connector-endpoint'],3000),"
            "{productVersion:'1',clientInstanceId:'global-registration'});}"
            "const identity=await t.negotiate(AbortSignal.timeout(5000));"
            "console.log(JSON.stringify(r.createGlobalProgramManifest("
            "{episodeId:process.argv[5],manifestId:process.argv[5],"
            "identity,artifactPath:p,artifactBytes:await readFile(p),adapter:a})));"
        )
        reply = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                str(runtime),
                json.dumps(options),
                str(artifact_path),
                json.dumps(adapter),
                selection,
            ],
            capture_output=True,
            text=True,
            timeout=_remaining(deadline),
            check=False,
        )
        if reply.returncode != 0 or len(reply.stdout) > 1024 * 1024:
            raise BoundaryError("global_program", "public_environment_unavailable")
        value = json.loads(reply.stdout)
        if not isinstance(value, dict):
            raise BoundaryError("global_program", "public_environment_unavailable")
        return value

    def register_global_program(self, *, environment_kind: str = "native") -> dict[str, Any]:
        """Fixed public program registration; no model, downloaded command or inference."""
        from .global_program_agent import ADAPTER, adapter_identity, descriptor
        from .global_program_support import CONFIG_SCHEMA, PROFILE, validate
        if environment_kind not in {"native", "managed"}:
            raise BoundaryError("global_program", "unsupported_environment")
        deadline = monotonic() + REGISTRATION_SECONDS
        node_modules, _ = self._native_runtime(deadline=deadline)
        runtime = node_modules / RUNTIME_PACKAGE / "dist"
        options = self._global_environment_options(environment_kind)
        private = self.models.private_root
        if private.is_symlink():
            raise BoundaryError("global_program", "unsafe_registration_owner")
        private.mkdir(mode=0o700, exist_ok=True)
        with instance_lock(private / LOCK):
            folder = private / REGISTRATIONS
            if folder.is_symlink():
                raise BoundaryError("global_program", "unsafe_registration_owner")
            folder.mkdir(mode=0o700, exist_ok=True)
            selection = "local-global-program-" + uuid.uuid4().hex
            target = folder / selection
            target.mkdir(mode=0o700)
            artifact_path, config_path, manifest_path = (
                target / n for n in ("program.json", "config.json", "manifest.json")
            )
            try:
                raw = json_bytes(descriptor())
                artifact_path.write_bytes(raw)
                manifest = self._global_environment_manifest(
                    runtime, options, artifact_path, adapter_identity(), selection, deadline)
                config = {
                    "schema": CONFIG_SCHEMA,
                    "artifact_path": str(artifact_path),
                    "artifact_sha256": hashlib.sha256(raw).hexdigest(),
                }
                manifest_path.write_bytes(json_bytes(manifest))
                config_path.write_bytes(json_bytes(config))
                validate(self.models.root, config_path, manifest_path, binding_root=private)
                self.models._public_manifest_contract(manifest_path)
                entry = {
                    "id": selection,
                    "label": "GLOBAL 公共信息程序 · "
                    + ("Reference" if environment_kind == "native" else "Managed")
                    + "（无学习模型）",
                    "adapter": ADAPTER,
                    "runtime_profile": PROFILE,
                    "manifest": manifest_path.relative_to(private).as_posix(),
                    "config": config_path.relative_to(private).as_posix(),
                }
                atomic_json(
                    private / REGISTRY,
                    {
                        "schema": "stpd/local-token-policies-v1",
                        "policies": [*self._entries(), entry],
                    },
                )
            except Exception:
                for path in (artifact_path, config_path, manifest_path):
                    path.unlink(missing_ok=True)
                target.rmdir()
                raise
        return {
            "schema": "spireagent/local-agent-registration-v1",
            "status": "registered",
            "selection_id": selection,
            "models": [],
            "reused": False,
        }

    def register_teacher(
        self, *, variant: str = "continuous", environment_kind: str = "native"
    ) -> dict[str, Any]:
        """Register the builtin program, without Model/export/backend authority."""
        from stpd.policy.native_operational_outcome import owned_current_known_stale_policy
        from stpd.policy.native_operational_outcome_v2 import progress_aware_strict_policy
        from stpd.policy.native_teacher_agent import descriptor, write_artifact
        from stpd.policy.native_teacher_manifest import teacher_manifest

        from .native_teacher_support import ADAPTER, installation_config, validate

        if environment_kind != "native" or variant not in {"baseline", "owned", "continuous"}:
            raise BoundaryError("local_model_registration", "unsupported_teacher_composition")
        policy = (progress_aware_strict_policy() if variant == "continuous" else
                  owned_current_known_stale_policy() if variant == "owned" else None)
        deadline = monotonic() + REGISTRATION_SECONDS
        _, sdk = self._native_runtime(deadline=deadline)
        observed = self._capabilities(sdk, deadline=deadline, input_profile=NATIVE_PROFILE)
        _, _, seams = _native_requirements(observed, execution_policy=policy, program_agent=True)
        capabilities = observed["capabilities"]
        current_descriptor = descriptor(execution_policy=policy)
        private = self.models.private_root
        if private.is_symlink() or (private.exists() and not _ordinary(private, directory=True)):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        private.mkdir(mode=0o700, exist_ok=True)
        lock_path = private / LOCK
        if lock_path.is_symlink():
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        with instance_lock(lock_path):
            for entry in reversed(self._entries()):
                if entry.get("adapter") != ADAPTER:
                    continue
                try:
                    config, manifest = validate(
                        self.models.root,
                        self.models.entry_path(entry, "config"),
                        self.models.entry_path(entry, "manifest"),
                        binding_root=private,
                    )
                    raw = _object_file(Path(config["artifact_path"]))
                    if raw == current_descriptor:
                        candidate = teacher_manifest(raw, manifest["artifact"], capabilities, seams,
                            manifest_id=entry["id"], execution_policy=policy)
                        if candidate == manifest:
                            self.models._public_manifest_contract(
                                self.models.entry_path(entry, "manifest")
                            )
                            return {
                                "schema": "spireagent/local-agent-registration-v1",
                                "status": "registered",
                                "selection_id": entry["id"],
                                "models": [],
                                "reused": True,
                            }
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            folder = private / REGISTRATIONS
            if folder.is_symlink():
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
            folder.mkdir(mode=0o700, exist_ok=True)
            selection = "local-native-teacher-" + uuid.uuid4().hex
            target = folder / selection
            target.mkdir(mode=0o700)
            artifact_path = target / "program.json"
            config_path, manifest_path = target / "config.json", target / "manifest.json"
            try:
                artifact = write_artifact(artifact_path, execution_policy=policy)
                manifest = teacher_manifest(current_descriptor, artifact, capabilities, seams,
                                             manifest_id=selection, execution_policy=policy)
                config = installation_config(artifact_path, artifact["sha256"])
                manifest_path.write_bytes(json_bytes(manifest))
                config_path.write_bytes(json_bytes(config))
                validate(self.models.root, config_path, manifest_path, binding_root=private)
                self.models._public_manifest_contract(manifest_path)
                entry = {"id": selection, "label": "原生程序 Teacher（无学习模型） " + variant,
                         "adapter": ADAPTER, "runtime_profile": NATIVE_PROFILE,
                         "manifest": manifest_path.relative_to(private).as_posix(),
                         "config": config_path.relative_to(private).as_posix()}
                entries = self._entries()
                _remaining(deadline)
                atomic_json(private / REGISTRY, {"schema": "stpd/local-token-policies-v1",
                                                "policies": [*entries, entry]})
            except Exception:
                for path in (artifact_path, manifest_path, config_path):
                    path.unlink(missing_ok=True)
                target.rmdir()
                raise
            return {"schema": "spireagent/local-agent-registration-v1", "status": "registered",
                    "selection_id": selection, "models": [], "reused": False}

    def _register_native(self, identity: str, *, environment_kind: str,
                         deadline: float) -> dict[str, Any]:
        from spireagent.workbench.native_agent_support import (
            bind_native_export,
            native_package_execution_policy,
        )

        if environment_kind != "native":
            raise BoundaryError(
                "local_model_registration", "native_managed_environment_not_supported"
            )
        require_native_models("local_model_registration")
        export = self.export.verified_for_registration(identity)
        # The completed immutable export was just reverified by its owning loader.
        # Binding loads it again; no config flag upgrades an historical package.
        execution_policy = native_package_execution_policy(_object_file(export / "model.json"))
        node_modules, sdk = self._native_runtime(deadline=deadline)
        del node_modules
        capabilities = self._capabilities(sdk, deadline=deadline, input_profile=NATIVE_PROFILE)
        requirements, support, required_seams = _native_requirements(
            capabilities, execution_policy=execution_policy
        )
        private = self.models.private_root
        if private.exists() or private.is_symlink():
            if not _ordinary(private, directory=True):
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        else:
            private.mkdir(mode=0o700)
        lock_path = private / LOCK
        if lock_path.is_symlink() or (
            lock_path.exists() and not _ordinary(lock_path, directory=False)
        ):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        try:
            with instance_lock(lock_path):
                _remaining(deadline)
                found, _ = self._matching_native(identity, export, requirements=requirements,
                                                 support=support, required_seams=required_seams)
                if found is not None:
                    found_entry = self.models.selection(found)
                    _selected_native_execution_policy(
                        _object_file(self.models.entry_path(found_entry, "manifest")),
                        execution_policy,
                    )
                    self.models._public_manifest_contract(
                        self.models.entry_path(found_entry, "manifest")
                    )
                    return _public(identity, "registered", selection_id=found,
                                   profile=NATIVE_PROFILE)
                folder = private / REGISTRATIONS
                if folder.exists() or folder.is_symlink():
                    if not _ordinary(folder, directory=True):
                        raise BoundaryError(
                            "local_model_registration", "registration_metadata_invalid"
                        )
                else:
                    folder.mkdir(mode=0o700)
                selection = "local-native-m2-" + uuid.uuid4().hex
                target = folder / selection
                target.mkdir(mode=0o700)
                config_path, manifest_path = target / "config.json", target / "manifest.json"
                try:
                    _, bound_manifest = bind_native_export(
                                       self.models.root, export, config_path, manifest_path,
                                       artifact_id=identity, manifest_id=selection,
                                       requirements=requirements, support=support,
                                       required_seams=required_seams, binding_root=private)
                    _selected_native_execution_policy(bound_manifest, execution_policy)
                    self.models._public_manifest_contract(manifest_path)
                    entry = {"id": selection, "label": "本机原生 M2 有界 Agent " + identity[:8],
                             "adapter": NATIVE_ADAPTER, "runtime_profile": NATIVE_PROFILE,
                             "manifest": manifest_path.relative_to(private).as_posix(),
                             "config": config_path.relative_to(private).as_posix()}
                    entries = self._entries()
                    _remaining(deadline)
                    atomic_json(private / REGISTRY,
                                {"schema": "stpd/local-token-policies-v1",
                                 "policies": [*entries, entry]})
                except Exception:
                    config_path.unlink(missing_ok=True)
                    manifest_path.unlink(missing_ok=True)
                    target.rmdir()
                    raise
                return _public(identity, "registered", selection_id=selection,
                               profile=NATIVE_PROFILE)
        except BoundaryError as error:
            if error.code == "already_running":
                raise BoundaryError(
                    "local_model_registration", "registration_in_progress"
                ) from error
            raise
        except OSError as error:
            raise BoundaryError("local_model_registration", "registration_write_failed") from error


    def _public_m2_runtime(self, *, deadline: float) -> tuple[Path, Path]:
        from stpd.public_m2_policy_installation import require_evidence

        require_evidence()
        node_modules, sdk = self._native_runtime(deadline=deadline)
        script = (
            "const runtime=await import(process.argv[1]);"
            "if(runtime.POLICY_PORT_V4_SCHEMA!=='sts2.policy-runtime/policy-port-4'||"
            "runtime.PUBLIC_STATEFUL_PROFILE!=='sts2.policy-runtime/public-observation-stateful-v1'||"
            "typeof runtime.NdjsonPolicyPort?.prototype.decideV4!=='function')"
            "throw Error('public_stateful_runtime_required');"
        )
        try:
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script,
                 (node_modules / RUNTIME_PACKAGE / "dist/index.js").as_uri()],
                capture_output=True, timeout=_remaining(deadline), check=False,
            )
            if result.returncode:
                raise ValueError("Port4 unavailable")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            _remaining(deadline)
            raise BoundaryError("local_model_registration",
                                "public_m2_runtime_contract_unavailable") from error
        return node_modules, sdk

    def _matching_public_m2(self, identity: str, export: Path, *,
                            requirements: dict | None = None, support: dict | None = None,
                            task: dict[str, str] | None = None,
                            ) -> tuple[str | None, bool]:
        from stpd.public_m2_policy_installation import validate

        stale = False
        for entry in reversed(self._entries()):
            if entry.get("adapter") != PUBLIC_M2_ADAPTER:
                continue
            config_path = _inside(self.models.private_root, entry["config"])
            manifest_path = _inside(self.models.private_root, entry["manifest"])
            config = _object_file(config_path)
            if (config.get("model_id") != identity
                    or config.get("export_path") != str(export.resolve())):
                continue
            # TaskNone and the summary task are distinct Agent compositions.
            # Neither rewrites nor adopts the other's existing selection.
            if config.get("task_spec") != task:
                continue
            try:
                _, manifest = validate(self.models.root, config_path, manifest_path,
                                       binding_root=self.models.private_root, verify_payloads=False)
            except BoundaryError:
                stale = True
                continue
            if (requirements is not None and manifest["requirements"] != requirements
                    or support is not None and manifest["support"] != support):
                stale = True
                continue
            return entry["id"], stale
        return None, stale

    def _status_public_m2(self, identity: str, observed: dict[str, Any],
                          environment_kind: str, *, task: dict[str, str] | None) -> dict[str, Any]:
        if environment_kind != "native":
            return _public(identity, "unavailable", profile=PUBLIC_M2_PROFILE,
                           reason_code="public_snapshot_managed_environment_not_supported")
        if (observed.get("availability") != "ready"
                or observed["operation"].get("status") != "completed"
                or observed["operation"].get("verified_receipt") is None):
            return _public(identity, "unavailable", profile=PUBLIC_M2_PROFILE,
                           reason_code="verified_export_required")
        if not local_models_available():
            return _public(identity, "unavailable", profile=PUBLIC_M2_PROFILE,
                           reason_code="local_models_extra_required")
        try:
            self._public_m2_runtime(deadline=monotonic() + REGISTRATION_SECONDS)
            export = self.config.state_dir / "model-exports" / identity
            found, stale = self._matching_public_m2(identity, export, task=task)
            return _public(identity, "registered" if found else "not_registered",
                           selection_id=found, profile=PUBLIC_M2_PROFILE,
                           reason_code="source_binding_changed" if stale and not found else None)
        except (BoundaryError, OSError, ValueError) as error:
            return _public(identity, "unavailable", profile=PUBLIC_M2_PROFILE,
                           reason_code=getattr(error, "code", "registration_metadata_invalid"))

    def _register_public_m2(self, identity: str, *, environment_kind: str,
                            task: dict[str, str] | None) -> dict[str, Any]:
        from stpd.public_m2_policy_installation import bind_public_m2_export, require_evidence

        if environment_kind != "native":
            raise BoundaryError("local_model_registration",
                                "public_snapshot_managed_environment_not_supported")
        require_evidence()
        require_local_models("local_model_registration")
        verification_deadline = monotonic() + 300.0
        export = self.export.verified_public_m2_for_registration(
            identity, deadline=verification_deadline)
        _remaining(verification_deadline)
        deadline = monotonic() + REGISTRATION_SECONDS
        _, sdk = self._public_m2_runtime(deadline=deadline)
        capabilities = self._capabilities(sdk, deadline=deadline, input_profile=PUBLIC_M2_PROFILE)
        requirements, support = _requirements(capabilities, input_profile=PUBLIC_M2_PROFILE)
        private = self.models.private_root
        if private.exists() or private.is_symlink():
            if not _ordinary(private, directory=True):
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        else:
            private.mkdir(mode=0o700)
        lock_path = private / LOCK
        if lock_path.is_symlink() or (lock_path.exists()
                                    and not _ordinary(lock_path, directory=False)):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        with instance_lock(lock_path):
            _remaining(deadline)
            found, _ = self._matching_public_m2(identity, export,
                                              requirements=requirements, support=support, task=task)
            if found is not None:
                return _public(identity, "registered", selection_id=found,
                               profile=PUBLIC_M2_PROFILE)
            folder = private / REGISTRATIONS
            if folder.exists() or folder.is_symlink():
                if not _ordinary(folder, directory=True):
                    raise BoundaryError("local_model_registration", "registration_metadata_invalid")
            else:
                folder.mkdir(mode=0o700)
            selection = "local-public-m2-" + uuid.uuid4().hex
            target = folder / selection
            target.mkdir(mode=0o700)
            config_path, manifest_path = target / "config.json", target / "manifest.json"
            try:
                bind_public_m2_export(
                    self.models.root, export, config_path, manifest_path, manifest_id=selection,
                    policy={"id": selection, "version": "1.1.0" if task else "1.0.0",
                            "provider": "stpd",
                            "architecture": "stpd.public-observation.light-action.m2.v1"},
                    requirements=requirements, support=support, binding_root=private, task=task,
                )
                self.models._public_manifest_contract(manifest_path)
                task_label = " 至结算页 " if task else " 无任务基线 "
                entry = {"id": selection, "label": "本机公开快照 M2" + task_label + identity[:8],
                         "adapter": PUBLIC_M2_ADAPTER, "runtime_profile": PUBLIC_M2_PROFILE,
                         "manifest": manifest_path.relative_to(private).as_posix(),
                         "config": config_path.relative_to(private).as_posix()}
                entries = self._entries()
                _remaining(deadline)
                atomic_json(private / REGISTRY, {"schema": "stpd/local-token-policies-v1",
                                                "policies": [*entries, entry]})
            except Exception:
                config_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                target.rmdir()
                raise
            return _public(identity, "registered", selection_id=selection,
                           profile=PUBLIC_M2_PROFILE)

    def _matching_public_m0(self, identity: str, export: Path, *,
                            requirements: dict | None = None,
                            support: dict | None = None,
                            task: dict[str, str] | None = None,
                            program: dict[str, str] | None = None) -> tuple[str | None, bool]:
        from stpd.public_m0_policy_installation import validate

        stale = False
        for entry in reversed(self._entries()):
            if entry.get("adapter") != PUBLIC_M0_ADAPTER:
                continue
            config_path = _inside(self.models.private_root, entry["config"])
            manifest_path = _inside(self.models.private_root, entry["manifest"])
            config = _object_file(config_path)
            if (config.get("model_id") != identity
                    or config.get("export_path") != str(export.resolve())):
                continue
            # A different declared task is a different Agent composition, not
            # a stale binding to rewrite or a legacy selection to adopt.
            if config.get("task_spec") != task or config.get("program_spec") != program:
                continue
            try:
                _, manifest = validate(self.models.root, config_path, manifest_path,
                                       binding_root=self.models.private_root)
            except BoundaryError:
                stale = True
                continue
            if (requirements is not None and manifest["requirements"] != requirements
                    or support is not None and manifest["support"] != support):
                stale = True
                continue
            return entry["id"], stale
        return None, stale

    def _status_public_m0(self, identity: str, observed: dict,
                          environment_kind: str, *,
                          task: dict[str, str] | None,
                          program: dict[str, str] | None = None) -> dict[str, Any]:
        if environment_kind != "native":
            return _public(identity, "unavailable", profile=PUBLIC_M0_PROFILE,
                           reason_code="public_snapshot_managed_environment_not_supported")
        operation = observed["operation"]
        if (operation.get("status") != "completed" or operation.get("verified_receipt") is None):
            return _public(identity, "unavailable", profile=PUBLIC_M0_PROFILE,
                           reason_code="verified_export_required")
        try:
            export = self.config.state_dir / "model-exports" / identity
            found, stale = self._matching_public_m0(identity, export, task=task, program=program)
            return _public(identity, "registered" if found else "not_registered",
                           selection_id=found, profile=PUBLIC_M0_PROFILE,
                           reason_code="source_binding_changed" if stale and not found else None)
        except (BoundaryError, OSError, ValueError):
            return _public(identity, "unavailable", profile=PUBLIC_M0_PROFILE,
                           reason_code="registration_metadata_invalid")

    def _register_public_m0(self, identity: str, *, environment_kind: str,
                            task: dict[str, str] | None,
                            program: dict[str, str] | None = None) -> dict[str, Any]:
        from stpd.public_m0_policy_installation import bind_public_m0_export

        if environment_kind != "native":
            raise BoundaryError("local_model_registration",
                                "public_snapshot_managed_environment_not_supported")
        require_local_models("local_model_registration")
        verify = getattr(self.export, "verified_public_m0_for_registration", None)
        if not callable(verify):
            raise BoundaryError("local_model_registration",
                                "public_m0_export_verification_required")
        verification_deadline = monotonic() + 300.0
        export = verify(identity, deadline=verification_deadline)
        _remaining(verification_deadline)
        deadline = monotonic() + REGISTRATION_SECONDS
        _, sdk = self._native_runtime(deadline=deadline)
        capabilities = self._capabilities(sdk, deadline=deadline,
                                          input_profile=PUBLIC_M0_PROFILE)
        requirements, support = _requirements(capabilities, input_profile=PUBLIC_M0_PROFILE)
        private = self.models.private_root
        if private.exists() or private.is_symlink():
            if not _ordinary(private, directory=True):
                raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        else:
            private.mkdir(mode=0o700)
        lock_path = private / LOCK
        if lock_path.is_symlink() or (lock_path.exists()
                                    and not _ordinary(lock_path, directory=False)):
            raise BoundaryError("local_model_registration", "registration_metadata_invalid")
        with instance_lock(lock_path):
            _remaining(deadline)
            found, _ = self._matching_public_m0(identity, export,
                                                requirements=requirements, support=support,
                                                task=task, program=program)
            if found is not None:
                return _public(identity, "registered", selection_id=found,
                               profile=PUBLIC_M0_PROFILE)
            folder = private / REGISTRATIONS
            if folder.exists() or folder.is_symlink():
                if not _ordinary(folder, directory=True):
                    raise BoundaryError("local_model_registration", "registration_metadata_invalid")
            else:
                folder.mkdir(mode=0o700)
            selection = "local-public-m0-" + uuid.uuid4().hex
            target = folder / selection
            target.mkdir(mode=0o700)
            config_path, manifest_path = target / "config.json", target / "manifest.json"
            try:
                policy = {"id": selection, "version": "1.2.0" if program else
                          "1.1.0" if task else "1.0.0",
                          "provider": "stpd",
                          "architecture": ("stpd.public-m0.complete-agent.card-reward-take.v1"
                                           if program else "stage1a.dsimple.light-action.m0.s.v1")}
                bind_public_m0_export(self.models.root, export, config_path, manifest_path,
                                      manifest_id=selection, policy=policy,
                                      requirements=requirements, support=support,
                                      binding_root=private, task=task, program=program)
                self.models._public_manifest_contract(manifest_path)
                entries = self._entries()
                task_label = " 至结算页 " if task else " 无任务基线 "
                program_label = " 选牌奖励程序" if program else ""
                entry = {"id": selection,
                         "label": ("本机公开快照 D-Simple M0" + program_label
                                   + task_label + identity[:8]),
                         "adapter": PUBLIC_M0_ADAPTER, "runtime_profile": PUBLIC_M0_PROFILE,
                         "manifest": manifest_path.relative_to(private).as_posix(),
                         "config": config_path.relative_to(private).as_posix()}
                _remaining(deadline)
                atomic_json(private / REGISTRY,
                            {"schema": "stpd/local-token-policies-v1",
                             "policies": [*entries, entry]})
            except Exception:
                config_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
                target.rmdir()
                raise
            return _public(identity, "registered", selection_id=selection,
                           profile=PUBLIC_M0_PROFILE)
