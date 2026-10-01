"""Trusted installation metadata for public-snapshot light-action M0 exports.

The policy consumes only the current generic Connector Snapshot and scores its
complete bound-action catalog. It never owns delivery, legality, or operands.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.policy_files import _inside, _object_file

CONFIG_SCHEMA = "stpd/public-m0-policy-config-v1"
PROFILE = "public-snapshot-m0-v1"
ADAPTER_ID = "stpd-public-m0-decision-adapter"
ADAPTER_VERSION = "1.0.0"
CODE_SCOPE = "python-owner-source-and-lock-v1"
PROTOCOL = "sts2.policy-runtime/decision-only-ndjson-1"
SNAPSHOT_SCHEMA = "sts2.player-environment/snapshot-1"
# Closed generic Snapshot surfaces declared by Connector LiveObservationReader
# and the specialized surfaces prepended by PlayerEnvironmentService.BuildSnapshot.
# This excludes native text-menu overrides and fails closed on future kinds.
GENERIC_INTERACTION_KINDS = frozenset({
    "tutorial", "deck_enchant_selection", "combat_hand_card_selection",
    "card_bundle_selection", "card_reward_selection", "reward_claim", "map_navigation",
    "combat_turn", "shop_inventory", "shop_room", "treasure_room", "game_over",
    "character_select", "main_menu", "singleplayer_menu", "event_dialogue", "event_option",
    "native_generated_card_choice", "native_boss_relic_selection",
    "native_simple_card_selection", "deck_upgrade_selection", "deck_transform_selection",
    "native_combat_pile_selection", "native_deck_card_selection", "rest_site",
})
GENERIC_ACTION_VERBS = frozenset({
    "activate", "select", "deselect", "confirm", "cancel", "play", "target", "use",
    "end_turn", "skip", "open", "close",
})
CLAIMS = {
    "full_run": False,
    "selector": False,
    "catalog_filtered": False,
    "creates_action_authority": False,
    "creates_native_operands": False,
}
_REQUIREMENT_KEYS = {
    "connector_protocol_version", "environment", "reads", "whole_decision_admission",
    "candidate_order_digest", "score_count_matches_candidate_count", "selected_index",
    "successor_required",
}
_ENVIRONMENT_KEYS = {
    "host_kind", "connector_version", "connector_source_revision",
    "connector_artifact_sha256", "connector_module_version_id", "modset_status",
    "modset_fingerprint", "loaded_mod_ids",
}
_SUPPORT_KEYS = {"game_versions", "game_commits", "interaction_kinds", "action_verbs"}


def _manifest_artifact_path(
    artifact: PurePath, manifest_directory: PurePath,
    relpath: Callable[[str, str], str] = os.path.relpath,
) -> str:
    """Use a relative pin where possible, or an absolute pin across drives."""
    if artifact.drive.casefold() != manifest_directory.drive.casefold():
        return str(artifact)
    return relpath(str(artifact), str(manifest_directory))


def code_digest(root: Path) -> str:
    """Reuse the reviewed conservative Python/lock digest for the trusted port."""
    from .token_policy_installation import code_digest as digest_source

    return digest_source(root)


def _model_details(export_path: Path, model_id: str | None = None) -> tuple[Any, Any, dict]:
    """Check the exact public-M0 artifact family and return its renderer binding."""
    from spireagent.artifact_contracts import Manifest

    from .fullrun.public_bc import LEGACY_VIEW_SCHEMA, VIEW_SCHEMA
    from .fullrun.public_inputs import COMPACT_IDENTITY, IDENTITY
    from .policy import token_decision

    envelope = _object_file(export_path / "model.json")
    if envelope.get("schema") != token_decision.PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA:
        raise BoundaryError("public_m0_policy", "unsupported_export")
    identity = envelope.get("model_id")
    if not isinstance(identity, str) or len(identity) != 64 or (
        model_id is not None and identity != model_id
    ):
        raise BoundaryError("public_m0_policy", "export_identity_mismatch")
    artifact = Manifest.from_bytes(json_bytes(envelope.get("model")), identity)
    if artifact.kind != "model" or artifact.artifact_id != identity:
        raise BoundaryError("public_m0_policy", "unsupported_export")
    config, info = token_decision.check_light_action_m0_model(artifact)
    renderer = info.get("source_renderer")
    if (
        info.get("schema") != token_decision.PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA
        or info.get("input_schema") != "stpd/stage1a-light-action-m0-public-input-v1"
        or info.get("source_view_schema") not in {LEGACY_VIEW_SCHEMA, VIEW_SCHEMA}
        or renderer not in (IDENTITY, COMPACT_IDENTITY)
    ):
        raise BoundaryError("public_m0_policy", "public_model_identity_mismatch")
    expected_export_schema = token_decision.PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
    if envelope.get("schema") != expected_export_schema:
        raise BoundaryError("public_m0_policy", "export_family_mismatch")
    return artifact, config, {**info, "renderer": renderer}


def _validate_requirements(requirements: object, support: object) -> tuple[dict, dict]:
    req = object_fields(requirements, _REQUIREMENT_KEYS, "public_m0.requirements")
    env = object_fields(req.get("environment"), _ENVIRONMENT_KEYS,
                        "public_m0.requirements.environment")
    sup = object_fields(support, _SUPPORT_KEYS, "public_m0.support")
    if (
        not isinstance(req.get("connector_protocol_version"), str)
        or not req["connector_protocol_version"]
        or req.get("reads") != []
        or req.get("whole_decision_admission") is not True
        or req.get("candidate_order_digest") != "sha256-json-bound-action-id-order"
        or req.get("score_count_matches_candidate_count") is not True
        or req.get("selected_index") is not True
        or req.get("successor_required") is not True
    ):
        raise BoundaryError("public_m0_policy", "generic_runtime_requirements_mismatch")
    if (
        not isinstance(env["host_kind"], str) or not env["host_kind"]
        or not all(isinstance(env[key], str) and env[key] for key in (
            "connector_version", "connector_source_revision", "connector_module_version_id",
            "modset_status", "modset_fingerprint",
        ))
        or not isinstance(env["connector_artifact_sha256"], str)
        or len(env["connector_artifact_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in
               env["connector_artifact_sha256"])
        or not isinstance(env["loaded_mod_ids"], list)
        or any(not isinstance(value, str) or not value for value in env["loaded_mod_ids"])
        or len(env["loaded_mod_ids"]) != len(set(env["loaded_mod_ids"]))
    ):
        raise BoundaryError("public_m0_policy", "generic_runtime_requirements_mismatch")
    for key in ("game_versions", "game_commits", "interaction_kinds", "action_verbs"):
        values = sup.get(key)
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise BoundaryError("public_m0_policy", "generic_runtime_support_mismatch")
    if (not set(sup["interaction_kinds"]) <= GENERIC_INTERACTION_KINDS
            or not set(sup["action_verbs"]) <= GENERIC_ACTION_VERBS):
        raise BoundaryError("public_m0_policy", "generic_runtime_support_mismatch")
    return req, sup


def _validate_export_payloads(export_path: Path, artifact: Any) -> None:
    from .policy.token_decision import LIGHT_ACTION_M0_FILES

    for role, filename in LIGHT_ACTION_M0_FILES.items():
        path = export_path / filename
        payload = artifact.payload(role)
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != payload.size
                or file_sha256(path) != payload.sha256):
            raise BoundaryError("public_m0_policy", "export_payload_identity_mismatch")


def _validate_qwen_snapshot(config: Any, info: dict[str, Any],
                            snapshot: str | None) -> None:
    """Require an explicit, content-pinned full-weight snapshot for PF/PL M0."""
    from .models.stage1a import recipe_for

    backbone = recipe_for(config.recipe).backbone
    if backbone == "s":
        if snapshot is not None:
            raise BoundaryError("public_m0_policy", "scratch_has_no_qwen_dependency")
        return
    if not isinstance(snapshot, str) or not snapshot:
        raise BoundaryError("public_m0_policy", "pinned_snapshot_required")
    try:
        from .qwen.l2 import inspect_l2_snapshot

        observed = inspect_l2_snapshot(Path(snapshot))
        identity = info.get("backbone", {}).get(
            "qwen" if backbone == "pf" else "qwen_base"
        )
        if not isinstance(identity, dict):
            raise ValueError("model has no pinned Qwen base identity")
        expected = {
            "model_id": observed.model_id,
            "model_revision": observed.repo_revision,
            "tokenizer_revision": observed.repo_revision,
            "weights_sha256": observed.weights_sha256,
            "config_sha256": observed.config_sha256,
            "tokenizer_sha256": observed.tokenizer_bundle_sha256,
        }
        if any(identity.get(key) != value for key, value in expected.items()):
            raise ValueError("snapshot content differs from model Qwen identity")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise BoundaryError("public_m0_policy", "qwen_snapshot_identity_mismatch") from error


def bind_public_m0_export(
    root: Path, export_path: Path, config_path: Path, manifest_path: Path, *,
    manifest_id: str, policy: dict[str, Any], requirements: dict[str, Any],
    support: dict[str, Any], qwen_snapshot: Path | None = None,
    binding_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind one verified public M0 export to exact generic Runtime capabilities."""
    root = root.resolve()
    binding_root = (binding_root or root).resolve()
    export_path = export_path.resolve()
    config_path = config_path.resolve()
    manifest_path = manifest_path.resolve()
    if (
        not config_path.is_relative_to(binding_root)
        or not manifest_path.is_relative_to(binding_root)
        or config_path == manifest_path or config_path.exists() or manifest_path.exists()
        or not isinstance(manifest_id, str) or not manifest_id
        or not all(isinstance(value, dict) for value in (policy, requirements, support))
    ):
        raise BoundaryError("public_m0_policy", "invalid_binding_destination_or_facts")
    object_fields(policy, {"id", "version", "provider", "architecture"},
                  "public_m0.policy")
    if any(not isinstance(value, str) or not value for value in policy.values()):
        raise BoundaryError("public_m0_policy", "invalid_binding_destination_or_facts")
    req, sup = _validate_requirements(requirements, support)
    artifact, config, info = _model_details(export_path)
    _validate_export_payloads(export_path, artifact)
    from .fullrun.public_inputs import COMPACT_IDENTITY, IDENTITY

    renderer = info["renderer"]
    _validate_qwen_snapshot(config, info, str(qwen_snapshot.resolve())
                            if qwen_snapshot is not None else None)
    representation = {
        "id": renderer["profile"], "version": renderer["version"],
        "input_schema": SNAPSHOT_SCHEMA,
    }
    if renderer not in (IDENTITY, COMPACT_IDENTITY):
        raise BoundaryError("public_m0_policy", "public_renderer_mismatch")
    model_identity = artifact.artifact_id
    config_value = {
        "schema": CONFIG_SCHEMA,
        "export_path": str(export_path),
        "export_manifest_sha256": file_sha256(export_path / "model.json"),
        "model_id": model_identity,
        "qwen_snapshot": str(qwen_snapshot.resolve()) if qwen_snapshot else None,
        "renderer": renderer,
    }
    adapter = {"id": ADAPTER_ID, "version": ADAPTER_VERSION,
               "protocol": PROTOCOL, "code_sha256": code_digest(root)}
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "manifest_id": manifest_id,
        "policy": policy,
        "adapter": adapter,
        "artifact": {"id": model_identity,
                     "path": _manifest_artifact_path(export_path / "model.json",
                                                     manifest_path.parent),
                     "sha256": config_value["export_manifest_sha256"]},
        "representation": representation,
        "requirements": req,
        "support": sup,
        "adapter_config": {"public_m0": {
            "code_digest_scope": CODE_SCOPE,
            "config": {"path": config_path.relative_to(binding_root).as_posix(),
                       "sha256": hashlib.sha256(json_bytes(config_value)).hexdigest(),
                       "schema": CONFIG_SCHEMA},
            "model_schema": "stpd/stage1a-light-action-m0-public-model-v1",
            "export_schema": "stpd/stage1a-light-action-m0-public-export-v1",
            "renderer": renderer,
        }},
        "claims": dict(CLAIMS),
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_bytes(json_bytes(config_value))
        manifest_path.write_bytes(json_bytes(manifest))
        validate(root, config_path, manifest_path, binding_root=binding_root)
    except Exception:
        config_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        raise
    return config_value, manifest


def validate(root: Path, config_path: Path, manifest_path: Path, *,
             binding_root: Path | None = None) -> tuple[dict, dict]:
    binding_root = (binding_root or root).resolve()
    config_path = _inside(binding_root,
                          config_path.resolve().relative_to(binding_root).as_posix())
    manifest_path = _inside(binding_root,
                            manifest_path.resolve().relative_to(binding_root).as_posix())
    config, manifest = _object_file(config_path), _object_file(manifest_path)
    object_fields(config, {"schema", "export_path", "export_manifest_sha256", "model_id",
                           "qwen_snapshot", "renderer"}, "public_m0.config")
    if config.get("schema") != CONFIG_SCHEMA:
        raise BoundaryError("public_m0_policy", "unsupported_config")
    object_fields(manifest, {
        "schema", "manifest_id", "policy", "adapter", "artifact", "representation",
        "requirements", "support", "adapter_config", "claims",
    }, "public_m0.manifest")
    if not isinstance(manifest["manifest_id"], str) or not manifest["manifest_id"]:
        raise BoundaryError("public_m0_policy", "invalid_manifest_identity")
    object_fields(manifest["policy"], {"id", "version", "provider", "architecture"},
                  "public_m0.policy")
    object_fields(manifest["adapter"], {"id", "version", "protocol", "code_sha256"},
                  "public_m0.adapter")
    object_fields(manifest["artifact"], {"id", "path", "sha256"}, "public_m0.artifact")
    object_fields(manifest["representation"], {"id", "version", "input_schema"},
                  "public_m0.representation")
    adapter_config = object_fields(manifest["adapter_config"], {"public_m0"},
                                   "public_m0.adapter_config")
    pin = object_fields(adapter_config["public_m0"], {
        "code_digest_scope", "config", "model_schema", "export_schema", "renderer",
    }, "public_m0.adapter_config.public_m0")
    config_pin = object_fields(pin["config"], {"path", "sha256", "schema"},
                               "public_m0.adapter_config.public_m0.config")
    export = Path(config["export_path"])
    if not export.is_absolute():
        raise BoundaryError("public_m0_policy", "absolute_export_path_required")
    renderer = config.get("renderer")
    object_fields(renderer, {"version", "profile", "status"}, "public_m0.renderer")
    from .fullrun.public_inputs import COMPACT_IDENTITY, IDENTITY
    if renderer not in (IDENTITY, COMPACT_IDENTITY):
        raise BoundaryError("public_m0_policy", "public_renderer_mismatch")
    if config.get("qwen_snapshot") is not None and not isinstance(config["qwen_snapshot"], str):
        raise BoundaryError("public_m0_policy", "invalid_qwen_snapshot")
    expected_representation = {"id": renderer["profile"], "version": renderer["version"],
                               "input_schema": SNAPSHOT_SCHEMA}
    if (
        manifest.get("schema") != "sts2.policy-runtime/policy-manifest-1"
        or pin.get("code_digest_scope") != CODE_SCOPE
        or config_pin != {
            "path": config_path.relative_to(binding_root).as_posix(),
            "sha256": file_sha256(config_path), "schema": CONFIG_SCHEMA,
        }
        or pin.get("model_schema") != "stpd/stage1a-light-action-m0-public-model-v1"
        or pin.get("export_schema") != "stpd/stage1a-light-action-m0-public-export-v1"
        or pin.get("renderer") != renderer
        or manifest.get("adapter") != {
            "id": ADAPTER_ID, "version": ADAPTER_VERSION, "protocol": PROTOCOL,
            "code_sha256": code_digest(root),
        }
        or manifest.get("representation") != expected_representation
        or not isinstance(manifest["policy"]["id"], str)
        or not isinstance(manifest["policy"]["version"], str)
        or not isinstance(manifest["policy"]["provider"], str)
        or not isinstance(manifest["policy"]["architecture"], str)
        or manifest.get("requirements", {}).get("candidate_order_digest")
        != "sha256-json-bound-action-id-order"
        or manifest.get("requirements", {}).get("reads") != []
        or manifest.get("claims") != CLAIMS
    ):
        raise BoundaryError("public_m0_policy", "trusted_policy_identity_drift")
    req, sup = _validate_requirements(manifest.get("requirements"), manifest.get("support"))
    del req
    artifact_pin = manifest.get("artifact", {})
    if (
        artifact_pin.get("id") != config.get("model_id")
        or artifact_pin.get("sha256") != config.get("export_manifest_sha256")
        or (manifest_path.parent / artifact_pin.get("path", "")).resolve()
        != (export / "model.json").resolve()
        or file_sha256(export / "model.json") != config.get("export_manifest_sha256")
    ):
        raise BoundaryError("public_m0_policy", "export_identity_drift")
    artifact, model_config, info = _model_details(export, config.get("model_id"))
    if info["renderer"] != renderer:
        raise BoundaryError("public_m0_policy", "public_renderer_mismatch")
    _validate_qwen_snapshot(model_config, info, config.get("qwen_snapshot"))
    _validate_export_payloads(export, artifact)
    return config, manifest


def inspect(root: Path, entry: dict[str, Any], manifest: dict[str, Any],
            policy_config: dict[str, Any], *,
            binding_root: Path | None = None) -> dict[str, dict[str, str]]:
    binding_root = binding_root or root
    try:
        config, checked = validate(root, _inside(binding_root, entry["config"]),
                                   _inside(binding_root, entry["manifest"]),
                                   binding_root=binding_root)
        if checked != manifest or config != policy_config:
            raise BoundaryError("public_m0_policy", "metadata_changed")
        from .policy.token_decision import check_light_action_m0_model
        artifact, _, info = _model_details(Path(config["export_path"]), config["model_id"])
        model_config, _ = check_light_action_m0_model(artifact)
        backend = model_config.device
        if backend not in {"cpu", "mps"}:
            raise BoundaryError("public_m0_policy", "unsupported_backend")
        script = "import json,torch; print(json.dumps({'mps':torch.backends.mps.is_available()}))"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
        if result.returncode or (backend == "mps" and not json.loads(result.stdout)["mps"]):
            raise BoundaryError("public_m0_policy", "backend_unavailable")
        if artifact.artifact_id != config["model_id"] or info["renderer"] != config["renderer"]:
            raise BoundaryError("public_m0_policy", "export_identity_drift")
        return {"policy_identity": {"status": "pass"},
                "backend": {"status": "pass", "code": backend}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError) as error:
        return {"policy_identity": {
            "status": "blocked",
            "code": getattr(error, "code", "metadata_or_backend_unavailable"),
        }}


def arguments(entry: dict[str, Any]) -> list[str]:
    return ["-m", "stpd.policy.public_m0_port", "--config", entry["config"],
            "--manifest", entry["manifest"]]
