"""Closed source-neutral sampled native package/recipe definitions; no numerical imports."""

from __future__ import annotations

from typing import Any

from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields

from .native_agent_sampled_source_spec import PROFILE as DIRECT_PROFILE
from .native_agent_sampled_source_spec import SOURCE_SCHEMA as DIRECT_SOURCE_SCHEMA
from .native_agent_sampled_source_spec import qualification as direct_qualification
from .native_agent_sampled_source_spec import validation_identity as direct_validation
from .ordered_source_spec import COHORTS, SAMPLED_VIEW, checked_view, view_qualification
from .ordered_source_spec import SOURCE_SCHEMA as ORDERED_SOURCE_SCHEMA
from .ordered_source_spec import validation_identity as ordered_validation

PACKAGE_SCHEMA = "stpd/native-trained-m2-package-v2"
MODEL_SCHEMA = "stpd/native-trained-m2-model-v2"
RECIPE = "native-m2-k1d96-carry-N-sampled-v2"
SOURCE3_PROFILE = "source3_sampled_basis_v1"
SOURCE_PROFILES = (DIRECT_PROFILE, SOURCE3_PROFILE)


def source_profile(source: object) -> str | None:
    if not isinstance(source, dict):
        raise BoundaryError("native_training_source", "source_object_required")
    if source.get("schema") == DIRECT_SOURCE_SCHEMA:
        if source.get("source_profile") != DIRECT_PROFILE:
            raise BoundaryError("native_training_source", "direct_source_profile_mismatch")
        return DIRECT_PROFILE
    if (source.get("schema") == ORDERED_SOURCE_SCHEMA
            and checked_view(source.get("projection_spec"), source.get("target_spec"))
            == SAMPLED_VIEW):
        return SOURCE3_PROFILE
    return None


def source_validation(source: dict[str, Any], data_sha256: str) -> dict[str, Any]:
    profile = source_profile(source)
    if profile == DIRECT_PROFILE:
        return direct_validation(
            data_sha256,
            source["cohort"],
            source["producer_student_relation"],
            source["raw_refs"],
            projection_spec=source["projection_spec"],
            target_spec=source["target_spec"],
        )
    if profile == SOURCE3_PROFILE:
        return ordered_validation(
            data_sha256,
            source["source_kind"],
            source["raw_refs"],
            projection_spec=source["projection_spec"],
            target_spec=source["target_spec"],
        )
    raise BoundaryError("native_training_source", "unsupported_recorded_sampled_source")


def checked_trained_source(source: object, data_sha256: str) -> dict[str, Any]:
    value = object_fields(
        source,
        {
            "source_profile",
            "kind",
            "cohort",
            "data_sha256",
            "source_artifact_id",
            "training_input_id",
            "verification_identity",
        },
        "native_training_source.source",
    )
    digest(data_sha256, "native_training_source.data_sha256")
    digest(value["source_artifact_id"], "native_training_source.source_artifact_id")
    digest(value["training_input_id"], "native_training_source.training_input_id")
    if value["data_sha256"] != data_sha256:
        raise BoundaryError("native_training_source", "source_data_identity")
    verification = value["verification_identity"]
    if not isinstance(verification, dict):
        raise BoundaryError("native_training_source", "verification_required")
    if value["source_profile"] == DIRECT_PROFILE:
        if value["kind"] != "agent_protocol" or value["cohort"] != verification.get("cohort"):
            raise BoundaryError("native_training_source", "direct_actor_cohort_mismatch")
        expected = direct_validation(
            data_sha256,
            value["cohort"],
            verification.get("producer_student_relation"),
            verification.get("raw_refs"),
            projection_spec=verification.get("projection_spec"),
            target_spec=verification.get("target_spec"),
        )
    elif value["source_profile"] == SOURCE3_PROFILE:
        if value["kind"] not in COHORTS or value["cohort"] != value["kind"]:
            raise BoundaryError("native_training_source", "source3_actor_cohort_mismatch")
        if (
            checked_view(verification.get("projection_spec"), verification.get("target_spec"))
            != SAMPLED_VIEW
        ):
            raise BoundaryError("native_training_source", "sampled_source3_view_required")
        expected = ordered_validation(
            data_sha256,
            value["kind"],
            verification.get("raw_refs"),
            projection_spec=verification.get("projection_spec"),
            target_spec=verification.get("target_spec"),
        )
    else:
        raise BoundaryError("native_training_source", "unsupported_source_profile")
    if json_bytes(verification) != json_bytes(expected):
        raise BoundaryError("native_training_source", "source_validation_identity")
    return value


def source_qualification(source: dict[str, Any]) -> str:
    value = checked_trained_source(source, source["data_sha256"])
    if value["source_profile"] == DIRECT_PROFILE:
        identity = value["verification_identity"]
        return direct_qualification(identity["producer_student_relation"], value["cohort"])
    return view_qualification(SAMPLED_VIEW)
