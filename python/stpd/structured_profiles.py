"""Two reviewed input profiles for the same numerical workload; no executable registry.

Native imports occur only after exact descriptor/scope selection. Artifact content
cannot name a module, callable, optimizer, projection, or alternate trainer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json

from .fullrun.structured_inputs import INPUT_ID, PROJECTION_VERSION
from .native_graph_spec import MODEL_SCHEMA as GRAPH_MODEL_SCHEMA
from .native_graph_spec import REPORT_SCHEMA as GRAPH_REPORT_SCHEMA
from .native_graph_spec import RUN_SCHEMA as GRAPH_RUN_SCHEMA
from .native_graph_spec import TRAINING_SCOPE as NATIVE_GRAPH_SCOPE
from .ordered_source_spec import (
    COHORTS,
    checked_view,
    validation_identity,
    view_input_spec,
    view_qualification,
)
from .ordered_source_spec import INPUT_SCHEMA as ORDERED_INPUT_SCHEMA
from .ordered_source_spec import MODEL_SCHEMA as ORDERED_MODEL_SCHEMA
from .ordered_source_spec import REPORT_SCHEMA as ORDERED_REPORT_SCHEMA
from .ordered_source_spec import RUN_SCHEMA as ORDERED_RUN_SCHEMA
from .ordered_source_spec import SCOPE as ORDERED_SCOPE
from .ordered_source_spec import SOURCE_SCHEMA as ORDERED_SOURCE_SCHEMA
from .structured_code_scope import LEGACY_SCOPE, TRAINING_SCOPE, code_identity

if TYPE_CHECKING:
    from spireagent.storage.store import ArtifactStore

    from .fullrun.structured_sequences import StructuredDataset

NATIVE_SCOPE = "native-structured-numerical-training-code-closure-v1"
NATIVE_SCOPES = frozenset({NATIVE_SCOPE, NATIVE_GRAPH_SCOPE, ORDERED_SCOPE})
CONTROL_SCOPES = frozenset({NATIVE_GRAPH_SCOPE, ORDERED_SCOPE})
NATIVE_SOURCE_SCHEMA = "stpd/native-structured-training-source-v1"
NATIVE_INPUT_SCHEMA = "stpd/native-structured-training-input-v1"
NATIVE_RUN_SCHEMA = "stpd/native-structured-m2-run-v1"
NATIVE_CHECKPOINT_SCHEMA = "stpd/native-structured-m2-training-checkpoint-v1"
NATIVE_MODEL_SCHEMA = "stpd/native-structured-m2-model-v1"
NATIVE_REPORT_SCHEMA = "stpd/native-structured-m2-training-report-v1"


def source_profile(dataset: StructuredDataset) -> str | None:
    from .native_training_source_spec import source_profile as checked_profile

    return checked_profile(decode_json(dataset.source_bytes))


def common_sampled_source(dataset: StructuredDataset, scope: str) -> bool:
    return scope == NATIVE_GRAPH_SCOPE and source_profile(dataset) is not None


def trained_source(dataset: StructuredDataset, scope: str, source_id: str,
                   training_input_id: str) -> dict[str, Any]:
    source: dict[str, Any] = {"kind": dataset.source_kind, "data_sha256": dataset.source_sha256,
              "source_artifact_id": source_id, "training_input_id": training_input_id,
              "verification_identity": source_verification(dataset)}
    if common_sampled_source(dataset, scope):
        value = decode_json(dataset.source_bytes)
        source.update(source_profile=source_profile(dataset),
                      cohort=value.get("cohort", dataset.source_kind))
    return source


def verify_training_partition(store: ArtifactStore, source_id: str | None,
                              dataset: StructuredDataset, scope: str) -> None:
    """Static dispatch to existing immutable source owners, never a new permission owner."""
    from .native_training_source_spec import DIRECT_PROFILE

    if scope != ORDERED_SCOPE and not common_sampled_source(dataset, scope):
        return
    if source_id is None:
        raise BoundaryError("structured_profile", "typed_recorded_partition_required")
    verified: Any
    if source_profile(dataset) == DIRECT_PROFILE:
        from .fullrun.native_agent_sampled_source import verify_native_agent_sampled_partition

        verified = verify_native_agent_sampled_partition(store, source_id)
    else:
        from .fullrun.ordered_source import verify_ordered_source_partition

        verified = verify_ordered_source_partition(store, source_id)
    if verified.dataset != dataset or verified.split != "train":
        raise BoundaryError("structured_profile", "recorded_partition_dataset_binding")


def verify_sampled_partition(store: Any, source_id: str) -> Any:
    """Closed dispatch to the already owning direct/Source3 typed replay APIs."""
    from .native_agent_sampled_source_spec import PARTITION_SCHEMA as DIRECT_PARTITION_SCHEMA
    from .ordered_source_spec import PARTITION_SCHEMA as ORDERED_PARTITION_SCHEMA

    schema = store.get_manifest(source_id).parameters.value().get("partition_schema")
    verified: Any
    if schema == DIRECT_PARTITION_SCHEMA:
        from .fullrun.native_agent_sampled_source import verify_native_agent_sampled_partition

        verified = verify_native_agent_sampled_partition(store, source_id)
    elif schema == ORDERED_PARTITION_SCHEMA:
        from .fullrun.ordered_source import verify_ordered_source_partition

        verified = verify_ordered_source_partition(store, source_id)
    else:
        raise BoundaryError("native_training_source", "typed_sampled_partition_required")
    from spireagent.json_boundary import decode_json

    from .native_sampled_carry_spec import INPUT_SPEC
    from .native_training_source_spec import SOURCE_PROFILES
    from .native_training_source_spec import source_profile as checked_profile

    body = decode_json(verified.dataset.source_bytes)
    if (verified.split != "train" or checked_profile(body) not in SOURCE_PROFILES
            or verified.dataset.input_spec is None
            or verified.dataset.input_spec.value() != INPUT_SPEC):
        raise BoundaryError("native_training_source", "sampled_train_partition_required")
    return verified


def validate_profile(dataset: StructuredDataset, scope: str) -> bool:
    if dataset.input_spec is None:
        if scope not in {LEGACY_SCOPE, TRAINING_SCOPE}:
            raise BoundaryError("structured_profile", "input_spec_scope_mismatch")
        return False
    from .fullrun.native_structured_inputs import INPUT_SPEC

    if not isinstance(dataset.input_spec, FrozenObject):
        raise BoundaryError("structured_profile", "frozen_input_spec_required")
    expected_input: dict[str, Any] = INPUT_SPEC
    if scope == ORDERED_SCOPE:
        source = decode_json(dataset.source_bytes)
        expected_input = view_input_spec(
            checked_view(source["projection_spec"], source["target_spec"])
        )
    elif common_sampled_source(dataset, scope):
        from .native_sampled_carry_spec import INPUT_SPEC as SAMPLED_INPUT_SPEC

        expected_input = SAMPLED_INPUT_SPEC
    if (
        scope not in NATIVE_SCOPES
        or dataset.input_spec.value() != expected_input
        or (
            dataset.source_kind not in COHORTS
            if scope == ORDERED_SCOPE or common_sampled_source(dataset, scope)
            else dataset.source_kind != "synthetic"
        )
    ):
        raise BoundaryError("structured_profile", "native_input_spec_or_source_scope")
    return True


def parse_dataset(raw: bytes, scope: str) -> StructuredDataset:
    if scope == ORDERED_SCOPE:
        from .fullrun.ordered_source import parse_ordered_training_dataset

        return parse_ordered_training_dataset(raw)
    if scope == NATIVE_GRAPH_SCOPE:
        from .native_training_source_spec import DIRECT_PROFILE
        from .native_training_source_spec import source_profile as checked_profile

        profile = checked_profile(decode_json(raw))
        if profile == DIRECT_PROFILE:
            from .fullrun.native_agent_sampled_source import parse_native_agent_sampled_dataset

            return parse_native_agent_sampled_dataset(raw)
        if profile is not None:
            from .fullrun.ordered_source import parse_ordered_training_dataset

            return parse_ordered_training_dataset(raw)
    if scope in NATIVE_SCOPES:
        from .fullrun.native_training_sequences import parse_native_training_dataset

        return parse_native_training_dataset(raw)
    if scope not in {LEGACY_SCOPE, TRAINING_SCOPE}:
        raise BoundaryError("structured_profile", "unsupported_scope")
    from .fullrun.structured_sequences import parse_structured_dataset

    return parse_structured_dataset(raw)


def profile_projection(dataset: StructuredDataset, scope: str) -> dict[str, Any]:
    if validate_profile(dataset, scope):
        from .fullrun.native_structured_inputs import PROJECTION

        return dict(PROJECTION)
    return {"id": INPUT_ID, "version": PROJECTION_VERSION, "I": False, "F": False}


def numerical_code_identity(scope: str) -> dict[str, str]:
    if scope in NATIVE_SCOPES:
        from .native_code_scope import native_training_code_identity

        return native_training_code_identity(
            graph=scope in {NATIVE_GRAPH_SCOPE, ORDERED_SCOPE}, ordered=scope == ORDERED_SCOPE
        )
    return code_identity(scope)


def source_verification(dataset: StructuredDataset) -> dict[str, Any]:
    from .native_agent_sampled_source_spec import SOURCE_SCHEMA as DIRECT_SOURCE_SCHEMA

    source = decode_json(dataset.source_bytes)
    if source.get("schema") == DIRECT_SOURCE_SCHEMA:
        from .native_training_source_spec import source_validation

        validate_profile(dataset, NATIVE_GRAPH_SCOPE)
        return source_validation(source, dataset.source_sha256)
    if source.get("schema") == ORDERED_SOURCE_SCHEMA and dataset.input_spec is not None:
        validate_profile(dataset, ORDERED_SCOPE)
        source = decode_json(dataset.source_bytes)
        return validation_identity(
            dataset.source_sha256,
            dataset.source_kind,
            source["raw_refs"],
            projection_spec=source["projection_spec"],
            target_spec=source["target_spec"],
        )
    validate_profile(dataset, NATIVE_SCOPE)
    assert dataset.input_spec is not None
    return {
        "schema": "stpd/native-synthetic-source-validation-v1",
        "source_sha256": dataset.source_sha256,
        "input_spec_sha256": dataset.input_spec.value()["sha256"],
        "validation": "synthetic_conformance_only",
    }


def qualification(dataset: StructuredDataset) -> str:
    from .native_agent_sampled_source_spec import SOURCE_SCHEMA as DIRECT_SOURCE_SCHEMA
    from .native_agent_sampled_source_spec import qualification as direct_qualification

    source = decode_json(dataset.source_bytes)
    if source.get("schema") == DIRECT_SOURCE_SCHEMA:
        return direct_qualification(source["producer_student_relation"], source["cohort"])
    if dataset.input_spec is not None and source.get("schema") == ORDERED_SOURCE_SCHEMA:
        source = decode_json(dataset.source_bytes)
        return view_qualification(checked_view(source["projection_spec"], source["target_spec"]))
    return "synthetic_engineering_only" if dataset.input_spec is not None else "engineering_only"


def native_source_schema(scope: str, dataset: StructuredDataset | None = None) -> str:
    if scope not in NATIVE_SCOPES:
        raise BoundaryError("structured_profile", "unsupported_scope")
    if dataset is not None and common_sampled_source(dataset, scope):
        validate_profile(dataset, scope)
        return str(decode_json(dataset.source_bytes)["schema"])
    return ORDERED_SOURCE_SCHEMA if scope == ORDERED_SCOPE else NATIVE_SOURCE_SCHEMA


def native_input_schema(scope: str) -> str:
    if scope not in NATIVE_SCOPES:
        raise BoundaryError("structured_profile", "unsupported_scope")
    return ORDERED_INPUT_SCHEMA if scope == ORDERED_SCOPE else NATIVE_INPUT_SCHEMA


def native_run_schema(scope: str) -> str:
    native_source_schema(scope)
    return (
        ORDERED_RUN_SCHEMA
        if scope == ORDERED_SCOPE
        else GRAPH_RUN_SCHEMA
        if scope == NATIVE_GRAPH_SCOPE
        else NATIVE_RUN_SCHEMA
    )


def native_model_schema(scope: str, dataset: StructuredDataset | None = None) -> str:
    native_source_schema(scope)
    if dataset is not None and common_sampled_source(dataset, scope):
        from .native_training_source_spec import MODEL_SCHEMA

        return MODEL_SCHEMA
    return (
        ORDERED_MODEL_SCHEMA
        if scope == ORDERED_SCOPE
        else GRAPH_MODEL_SCHEMA
        if scope == NATIVE_GRAPH_SCOPE
        else NATIVE_MODEL_SCHEMA
    )


def native_report_schema(scope: str) -> str:
    native_source_schema(scope)
    return (
        ORDERED_REPORT_SCHEMA
        if scope == ORDERED_SCOPE
        else GRAPH_REPORT_SCHEMA
        if scope == NATIVE_GRAPH_SCOPE
        else NATIVE_REPORT_SCHEMA
    )


def native_experiment(scope: str) -> dict[str, str]:
    native_source_schema(scope)
    return (
        {
            "schema": "stpd/source3-ordered-native-experiment-v1",
            "purpose": "declared_cohort_choice_N_reexpression_pretraining",
        }
        if scope == ORDERED_SCOPE
        else {
            "schema": "stpd/native-structured-experiment-v1",
            "purpose": "native_synthetic_teacher_imitation",
        }
    )
