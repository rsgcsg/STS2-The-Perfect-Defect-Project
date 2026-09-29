"""Explicit local text-menu policy registration from a verified offline export.

Registration records a reviewed engineering scope. Runtime readiness and loading
remain separate, and the current Connector remains the environment authority.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
from spireagent.workbench.local_model_export import LocalModelExport, _ordinary
from spireagent.workbench.local_models import LocalModelService, _loopback
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    RESET_K1_RECIPE,
    V2_M2_K1_RECIPE,
    V2_MEMORY_RECIPES,
    V2_RESET_K1_RECIPE,
)
from spireagent.workbench.runtime_install import (
    CONNECTOR_PACKAGE,
    RUNTIME_PACKAGE,
    validate_runtime_install,
)
from stpd.memory_policy_installation import (
    bind_memory_export,
)
from stpd.memory_policy_installation import (
    code_digest as memory_code_digest,
)
from stpd.memory_policy_installation import (
    validate as validate_memory,
)
from stpd.token_policy_installation import bind_text_menu_export, code_digest, validate

SCHEMA = "stpd/local-model-registration-v1"
PROFILE = "text-menu-v1"
M2_PROFILE = "text-menu-m2-v1"
V2_M2_PROFILE = "text-menu-m2-v2"
RECIPE_LABELS = {"stage1a.b.s.v2": "B", "stage1a.dsimple.s.v1": "D-Simple"}
MEMORY_RECIPE_LABELS = {M2_K1_RECIPE: "M2-K1 训练版",
                        RESET_K1_RECIPE: "Reset-K1 独立训练对照版",
                        V2_M2_K1_RECIPE: "M2-K1 v2 工程训练版",
                        V2_RESET_K1_RECIPE: "Reset-K1 v2 工程对照版"}


def _export_memory_profile(export: Path) -> str:
    """Read a closed package projection for display; POST rechecks full lineage."""
    from stpd.fullrun.memory_sequence_bridge import (
        parse_episode_projection_config,
        projection_input_profile,
    )

    value = _object_file(export / "model.json")
    try:
        profile = projection_input_profile(parse_episode_projection_config(
            value.get("projection_config")))
    except (TypeError, ValueError) as error:
        raise BoundaryError("local_model_registration", "export_profile_invalid") from error
    return V2_M2_PROFILE if profile == "text-menu-v2" else M2_PROFILE


def _v2_sdk_available(sdk: Path) -> bool:
    """Inspect the exact installed SDK without opening a game or mutating state."""
    node = shutil.which("node")
    if node is None or not sdk.is_file() or sdk.is_symlink():
        return False
    script = ("const {PlayerEnvironmentRestClient}=await import(process.argv[1]);"
              "if(typeof PlayerEnvironmentRestClient.prototype.textMenuV2Capabilities"
              "!=='function'||typeof PlayerEnvironmentRestClient.prototype."
              "observeTextMenuV2Context!=='function')process.exit(1);")
    environment = {key: value for key, value in os.environ.items() if key in
                   {"PATH", "SYSTEMROOT", "SystemRoot", "TMPDIR", "TEMP", "TMP"}}
    try:
        return subprocess.run(
            [node, "--input-type=module", "-e", script, sdk.as_uri()],
            capture_output=True, check=False, timeout=5, env=environment,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
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
const {PlayerEnvironmentRestClient} = await import(process.argv[1]);
const client = new PlayerEnvironmentRestClient(process.argv[2], 5000);
const result = await (process.argv[3] === 'text-menu-v2'
  ? client.textMenuV2Capabilities() : client.textMenuCapabilities());
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
                  profile: str = PROFILE) -> tuple[str | None, bool]:
        stale = False
        current_code = (memory_code_digest(self.models.root) if profile in
                        {M2_PROFILE, V2_M2_PROFILE}
                        else code_digest(self.models.root))
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
                if manifest.get("adapter", {}).get("code_sha256") != current_code:
                    stale = True
                    continue
                validator = (validate_memory if profile in {M2_PROFILE, V2_M2_PROFILE}
                             else validate)
                validator(self.models.root,
                          _inside(self.models.private_root, entry["config"]),
                          _inside(self.models.private_root, entry["manifest"]),
                          binding_root=self.models.private_root)
                if (requirements is not None and manifest.get("requirements") != requirements
                        or support is not None and manifest.get("support") != support):
                    continue
                return entry["id"], stale
            except (AttributeError, BoundaryError, KeyError, TypeError, ValueError) as error:
                raise BoundaryError("local_model_registration",
                                    "registration_metadata_invalid") from error
        return None, stale

    def status(self, model_id: object) -> dict[str, Any]:
        identity = digest(model_id, "local_model_registration.model_id")
        observed = self.export.status()
        operation = observed["operation"]
        memory = (operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        profile = M2_PROFILE if memory else PROFILE
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
            found, stale = self._matching(identity, export, profile=profile)
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
                    if not _v2_sdk_available(sdk):
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

    def register(self, model_id: object) -> dict[str, Any]:
        from stpd.policy.token_decision import check_model

        deadline = monotonic() + REGISTRATION_SECONDS
        identity = digest(model_id, "local_model_registration.model_id")
        # Weight/scorer verification and current-store binding are explicit POST work.
        observed = self.export.status()
        operation = observed["operation"]
        memory = (observed.get("schema") == "stpd/local-model-export-operation-v2"
                  and operation.get("model_id") == identity
                  and operation.get("model_type") == "memory")
        export = (self.export.verified_memory_for_registration(identity, deadline=deadline)
                  if memory else self.export.verified_for_registration(identity))
        _remaining(deadline)
        if memory:
            # The isolated verification child has checked exact model/run lineage
            # and the parent has rebound the response to unchanged package bytes.
            recipe = self.export.verified_memory_recipe_for_registration(
                identity, deadline=deadline)
            if recipe not in MEMORY_RECIPE_LABELS:
                raise BoundaryError("local_model_registration", "unsupported_model_recipe")
            profile = V2_M2_PROFILE if recipe in V2_MEMORY_RECIPES else M2_PROFILE
        else:
            profile = PROFILE
            envelope = _object_file(export / "model.json")
            if envelope.get("model_id") != identity:
                raise BoundaryError("local_model_registration", "export_identity_mismatch")
            artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
            recipe = check_model(artifact)[0].recipe
            if recipe not in RECIPE_LABELS:
                raise BoundaryError("local_model_registration", "unsupported_model_recipe")
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
        if profile == V2_M2_PROFILE and not _v2_sdk_available(sdk):
            raise BoundaryError("local_model_registration", "v2_runtime_contract_unavailable")
        _remaining(deadline)
        input_profile = "text-menu-v2" if profile == V2_M2_PROFILE else PROFILE
        capabilities = (self._capabilities(sdk, deadline=deadline,
                                           input_profile=input_profile)
                        if profile == V2_M2_PROFILE else
                        self._capabilities(sdk, deadline=deadline))
        requirements, support = _requirements(capabilities,
                                              input_profile=input_profile)
        if memory:
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
                                           profile=profile)
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
                selection = ("local-text-m2-" if memory else "local-text-b-") + uuid.uuid4().hex
                target = folder / selection
                target.mkdir(mode=0o700)
                config_path, manifest_path = target / "config.json", target / "manifest.json"
                try:
                    binder = bind_memory_export if memory else bind_text_menu_export
                    binding: dict[str, Any] = {"manifest_id": selection,
                               "policy": {"id": selection, "version": "1.0.0",
                                          "provider": "stpd", "architecture": recipe},
                               "requirements": requirements, "support": support,
                               "binding_root": self.models.private_root}
                    if profile == V2_M2_PROFILE:
                        binding["input_profile"] = "text-menu-v2"
                    binder(self.models.root, export, config_path, manifest_path,
                           **binding)
                    if memory:
                        self._m2_runtime_manifest_compatible(node_modules, manifest_path,
                                                             deadline=deadline)
                    entries = self._entries()
                    label = (MEMORY_RECIPE_LABELS[recipe] if memory
                             else RECIPE_LABELS[recipe])
                    entry = {"id": selection,
                             "label": "本机文字菜单 " + label + " " + identity[:8],
                             "adapter": "stpd-m2-decision-adapter" if memory else "token-v1",
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
