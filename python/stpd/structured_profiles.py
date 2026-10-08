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
from .ordered_source_spec import COHORTS, checked_view, validation_identity, view_qualification
from .ordered_source_spec import INPUT_SCHEMA as ORDERED_INPUT_SCHEMA
from .ordered_source_spec import MODEL_SCHEMA as ORDERED_MODEL_SCHEMA
from .ordered_source_spec import REPORT_SCHEMA as ORDERED_REPORT_SCHEMA
from .ordered_source_spec import RUN_SCHEMA as ORDERED_RUN_SCHEMA
from .ordered_source_spec import SCOPE as ORDERED_SCOPE
from .ordered_source_spec import SOURCE_SCHEMA as ORDERED_SOURCE_SCHEMA
from .structured_code_scope import LEGACY_SCOPE, TRAINING_SCOPE, code_identity

if TYPE_CHECKING:
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


def validate_profile(dataset: StructuredDataset, scope: str) -> bool:
    if dataset.input_spec is None:
        if scope not in {LEGACY_SCOPE, TRAINING_SCOPE}:
            raise BoundaryError("structured_profile", "input_spec_scope_mismatch")
        return False
    from .fullrun.native_structured_inputs import INPUT_SPEC

    if not isinstance(dataset.input_spec, FrozenObject):
        raise BoundaryError("structured_profile", "frozen_input_spec_required")
    if (
        scope not in NATIVE_SCOPES
        or dataset.input_spec.value() != INPUT_SPEC
        or (
            dataset.source_kind not in COHORTS
            if scope == ORDERED_SCOPE
            else dataset.source_kind != "synthetic"
        )
    ):
        raise BoundaryError("structured_profile", "native_input_spec_or_source_scope")
    return True


def parse_dataset(raw: bytes, scope: str) -> StructuredDataset:
    if scope == ORDERED_SCOPE:
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
    if dataset.source_kind in COHORTS and dataset.input_spec is not None:
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
    if dataset.input_spec is not None and dataset.source_kind in COHORTS:
        source = decode_json(dataset.source_bytes)
        return view_qualification(checked_view(source["projection_spec"], source["target_spec"]))
    return "synthetic_engineering_only" if dataset.input_spec is not None else "engineering_only"


def native_source_schema(scope: str) -> str:
    if scope not in NATIVE_SCOPES:
        raise BoundaryError("structured_profile", "unsupported_scope")
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


def native_model_schema(scope: str) -> str:
    native_source_schema(scope)
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
