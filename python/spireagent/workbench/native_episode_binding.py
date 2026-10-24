"""Closed native resource/request values; lifecycle remains Environment-owned."""

from __future__ import annotations

import re
from typing import Any, cast

from spireagent.json_boundary import BoundaryError, decode_json, digest, json_bytes, object_fields

ADMISSION_SCHEMA = "spireagent/native-episode-admission-v1"
BINDING_SCHEMA = "spireagent/native-episode-binding-v1"
PROFILE_SCHEMA = "spireagent/native-host-profile-v1"
PROFILE_FILE = "native-host-profile-v1.json"
RESOURCE_SCHEMA = "spireagent/native-episode-resource-v1"
RESOURCE_FILE = "native-episode-resource-v1.json"
OPERATION_DIRECTORY = "native-episode-operations-v1"
OPERATION_STORE_SCHEMA = "spireagent/native-episode-operation-store-v1"
REQUEST_SCHEMA = "spireagent/native-episode-request-v1"
RESERVED_PHASES = frozenset(
    {"preparing", "ready", "actor-associated", "closing", "unknown", "recovery_required"}
)
MODES = frozenset({"normal", "fast", "instant"})
DEFAULT_NATIVE_SPEED = "instant"
PRESENTATIONS = frozenset({"headless", "native-ui"})
CHARACTERS = frozenset({"Defect", "Ironclad", "Silent", "Regent", "Necrobinder"})
TUPLE_FIELDS = {
    "game_version",
    "game_commit",
    "host_artifact_sha256",
    "host_module_version_id",
    "host_source_revision",
    "modset_fingerprint",
}


def detached(value: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], decode_json(json_bytes(value)))


def identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value) is None
        or value in {".", ".."}
    ):
        raise BoundaryError("native_episode", "invalid_identifier")
    return value


def preparation_body(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoundaryError("native_episode", "invalid_preparation")
    owned = value.get("ownership") == "owned"
    fields = (
        {"ownership", "intent_id", "seed", "display_mode", "speed"}
        if owned
        else {"ownership", "intent_id"}
    )
    if owned and ("character" in value or "ascension" in value):
        fields |= {"character", "ascension"}
    body = object_fields(value, fields, "native_episode")
    if body["ownership"] not in {"owned", "attached"}:
        raise BoundaryError("native_episode", "invalid_ownership")
    digest(body["intent_id"], "native_episode.intent_id", length=32)
    if owned and (
        not isinstance(body["display_mode"], str)
        or not isinstance(body["speed"], str)
        or body["display_mode"] not in PRESENTATIONS
        or body["speed"] not in MODES | {"default"}
        or not isinstance(body["seed"], str)
        or not 1 <= len(body["seed"]) <= 64
    ):
        raise BoundaryError("native_episode", "unsupported_run_configuration")
    if owned:
        scenario(body)
    return detached(body)


def scenario(value: dict[str, Any]) -> dict[str, Any]:
    character, ascension = value.get("character", "Defect"), value.get("ascension", 0)
    if (not isinstance(character, str) or character not in CHARACTERS
            or type(ascension) is not int or not 0 <= ascension <= 10):
        raise BoundaryError("native_episode", "unsupported_character_or_ascension")
    return {"character": character, "ascension": ascension}


def installed_tuple(
    caps: dict[str, Any], game_assembly_sha256: str | None = None
) -> dict[str, Any]:
    try:
        host, game = caps["host"], caps["game"]
        implementation, modset = host["implementation"], game["modset"]
        if (
            caps["protocol_version"] != "1.0.0"
            or caps["execution_available"] is not True
            or (
                host["host_kind"] not in {"headless", "live_ui"}
                or modset["status"]
                not in {"exact_platform_modset", "exact_player_environment_only"}
            )
        ):
            raise ValueError
        value = {
            "game_version": identifier(game["version"]),
            "game_commit": identifier(game["commit"]),
            "host_artifact_sha256": digest(
                implementation["artifact_sha256"], "native_episode.artifact"
            ),
            "host_module_version_id": identifier(implementation["module_version_id"]),
            "host_source_revision": digest(
                implementation["source_revision"], "native_episode.source", length=40
            ),
            "modset_fingerprint": digest(modset["fingerprint"], "native_episode.modset"),
        }
        if game_assembly_sha256 is not None:
            value["game_assembly_sha256"] = digest(
                game_assembly_sha256, "native_episode.game_assembly"
            )
        return value
    except (KeyError, TypeError, ValueError) as error:
        raise BoundaryError("native_episode", "installed_tuple_unavailable") from error


def checked_context(value: object) -> dict[str, Any]:
    context = object_fields(value, {"schema", "environment_ref", "binding"}, "native_episode")
    if context["schema"] != ADMISSION_SCHEMA:
        raise BoundaryError("native_episode", "unsupported_admission_schema")
    digest(context["environment_ref"], "native_episode.environment_ref", length=32)
    binding = context["binding"]
    if not isinstance(binding, dict):
        raise BoundaryError("native_episode", "invalid_binding")
    owned = binding.get("ownership") == "owned"
    fields = {
        "schema",
        "ownership",
        "application_instance_id",
        "configuration_id",
        "generation_id",
        "runtime_instance_id",
        "endpoint_binding_sha256",
        "installed_tuple",
        "initialization",
    }
    if owned:
        fields |= {"host_package_pin", "profile", "presentation", "speed", "handoff"}
        if "scenario" in binding:
            fields.add("scenario")
    body = object_fields(binding, fields, "native_episode.binding")
    if body["schema"] != BINDING_SCHEMA or body["ownership"] not in {"owned", "attached"}:
        raise BoundaryError("native_episode", "unsupported_binding")
    for name in ("application_instance_id", "generation_id"):
        digest(body[name], "native_episode." + name, length=32)
    for name in ("configuration_id", "endpoint_binding_sha256"):
        digest(body[name], "native_episode." + name)
    identifier(body["runtime_instance_id"])
    initialization = ("fresh_seeded_reference_scenario" if "scenario" in body
                      else "fresh_seeded_defect_a0") if owned else "cold_current_segment"
    if body["initialization"] != initialization:
        raise BoundaryError("native_episode", "unsupported_initialization")
    if owned and "scenario" in body:
        object_fields(body["scenario"], {"character", "ascension"}, "native_episode.scenario")
        scenario(body["scenario"])
    tuple_fields = TUPLE_FIELDS | ({"game_assembly_sha256"} if owned else set())
    tuple_value = object_fields(
        body["installed_tuple"], tuple_fields, "native_episode.installed_tuple"
    )
    for name in tuple_fields:
        if name.endswith("sha256") or name == "modset_fingerprint":
            digest(tuple_value[name], "native_episode." + name)
        elif name == "host_source_revision":
            digest(tuple_value[name], "native_episode.source", length=40)
        else:
            identifier(tuple_value[name])
    if owned:
        profile = object_fields(
            body["profile"],
            {"template_id", "template_payload_sha256", "generation_id", "isolation"},
            "native_episode.profile",
        )
        identifier(profile["generation_id"])
        digest(profile["template_payload_sha256"], "native_episode.template")
        if (
            profile["template_id"] != "defect-a0-s0"
            or profile["isolation"] != "isolated_local_profile"
        ):
            raise BoundaryError("native_episode", "profile_unproven")
        presentation = object_fields(
            body["presentation"], {"requested", "observed_host_kind"}, "native_episode.presentation"
        )
        if presentation["requested"] not in PRESENTATIONS or presentation["observed_host_kind"] != (
            "headless" if presentation["requested"] == "headless" else "live_ui"
        ):
            raise BoundaryError("native_episode", "presentation_unproven")
        speed = object_fields(
            body["speed"],
            {"requested", "effective", "before", "basis", "configured_default"},
            "native_episode.speed",
        )
        if (
            speed["requested"] not in MODES
            or speed["effective"] != speed["requested"]
            or speed["before"] not in MODES
        ):
            raise BoundaryError("native_episode", "speed_unproven")
        if speed["basis"] != "native_typed_readback":
            raise BoundaryError("native_episode", "speed_unproven")
        default = speed["configured_default"]
        if default is not None:
            default = object_fields(default, {"mode", "basis"}, "native_episode.speed.default")
            if (
                default["mode"] != DEFAULT_NATIVE_SPEED
                or default["mode"] != speed["requested"]
                or default["basis"] != "project_default"
            ):
                raise BoundaryError("native_episode", "project_default_unproven")
        handoff = object_fields(
            body["handoff"],
            {"schema", "runtime_instance_id", "observed_at", "controller", "basis"},
            "native_episode.handoff",
        )
        if (
            handoff["schema"] != "sts2.host-runtime/reference-controller-handoff-1"
            or handoff["runtime_instance_id"] != body["runtime_instance_id"]
            or handoff["controller"] is not None
            or handoff["basis"] != "fresh_control_observation_after_close"
            or not isinstance(handoff["observed_at"], str)
        ):
            raise BoundaryError("native_episode", "handoff_unproven")
        from .local_environment import _validate_private_pin

        _validate_private_pin(body["host_package_pin"])
    if len(json_bytes(context)) > 16384:
        raise BoundaryError("native_episode", "binding_over_limit")
    return detached(context)


def native_exit_disposition(value: object, expected_runtime: str | None) -> dict[str, Any]:
    """Classify the owning Host's original exit; driver0 is a separate axis."""
    result = {"native_exit_disposition": "unknown", "native_shutdown_ack": "unconfirmed"}
    if not isinstance(value, dict) or not {"code", "signal", "forced", "host_shutdown"} <= set(
        value
    ):
        return result
    if (
        (value["code"] is not None and type(value["code"]) is not int)
        or (value["signal"] is not None and not isinstance(value["signal"], str))
        or type(value["forced"]) is not bool
        or (value["code"] is None and value["signal"] is None)
    ):
        return result
    shutdown = value["host_shutdown"]
    response = shutdown.get("response") if isinstance(shutdown, dict) else None
    acknowledged = (
        isinstance(shutdown, dict)
        and shutdown.get("status") == "requested"
        and isinstance(response, dict)
        and (
            response.get("status") == "shutdown_requested"
            and expected_runtime is not None
            and response.get("runtime_instance_id") == expected_runtime
        )
    )
    if acknowledged:
        result["native_shutdown_ack"] = "runtime_bound_acknowledged"
    result["native_exit_disposition"] = (
        "forced"
        if value["forced"]
        else "failed"
        if value["code"] != 0 or value["signal"] is not None
        else ("graceful" if acknowledged else "exited_shutdown_unconfirmed")
    )
    return result
