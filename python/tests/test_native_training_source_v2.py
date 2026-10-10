"""Pure closed source/recipe metadata for one sampled native computational Model."""

from __future__ import annotations

import json

import pytest
from test_native_agent_sampled_source import (
    PROJECTOR,
    ROOT,
    publish,
)
from test_native_agent_sampled_source import (
    original as original,
)
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from stpd.fullrun import ordered_source
from stpd.native_agent_sampled_source_spec import MAP_TEACHER_PRODUCER, TEACHER_PRODUCER
from stpd.native_code_scope import is_native_model_schema
from stpd.native_graph_spec import MODEL_SCHEMA as LEGACY_GRAPH_MODEL
from stpd.native_graph_spec import TRAINING_SCOPE
from stpd.native_sampled_carry_spec import INPUT_SPEC
from stpd.native_training_source_spec import (
    DIRECT_PROFILE,
    MODEL_SCHEMA,
    RECIPE,
    SOURCE3_PROFILE,
    checked_trained_source,
)
from stpd.structured_profiles import (
    common_sampled_source,
    native_model_schema,
    native_source_schema,
    parse_dataset,
    source_profile,
    source_verification,
    trained_source,
    validate_profile,
    verify_sampled_partition,
    verify_training_partition,
)


def source3(store):
    raw = ordered_source.publish_ordered_source_raw(
        store,
        ROOT / "components/evidence/tests/fixtures/source_session_v3/bundle",
        Producer("fixture://original", "e" * 40, "a" * 64),
    )
    ref = ordered_source.publish_ordered_source_admission(
        store, raw.artifact_id, PROJECTOR, cohort="agent_protocol", view="decision_sample_carry"
    )
    return ordered_source.publish_ordered_source_partition(store, (ref,), "train", PROJECTOR)


@pytest.mark.parametrize("which", ["direct", "source3"])
def test_same_graph_scope_routes_by_exact_schema_not_actor(tmp_path, original, which):
    store, _ = setup_store(tmp_path)
    partition = publish(store, original)[2] if which == "direct" else source3(store)
    dataset = partition.dataset
    assert dataset.source_kind == "agent_protocol" and dataset.input_spec.value() == INPUT_SPEC
    assert validate_profile(dataset, TRAINING_SCOPE)
    assert parse_dataset(dataset.source_bytes, TRAINING_SCOPE) == dataset
    assert common_sampled_source(dataset, TRAINING_SCOPE)
    assert source_profile(dataset) == (DIRECT_PROFILE if which == "direct" else SOURCE3_PROFILE)
    assert (
        native_source_schema(TRAINING_SCOPE, dataset) == json.loads(dataset.source_bytes)["schema"]
    )
    assert native_model_schema(TRAINING_SCOPE, dataset) == MODEL_SCHEMA
    verify_training_partition(store, partition.manifest.artifact_id, dataset, TRAINING_SCOPE)
    assert verify_sampled_partition(store, partition.manifest.artifact_id) == partition
    data = trained_source(dataset, TRAINING_SCOPE, partition.manifest.artifact_id, "1" * 64)
    assert checked_trained_source(data, dataset.source_sha256) == data
    assert data["verification_identity"] == source_verification(dataset)


@pytest.mark.parametrize("change", ["profile", "actor", "cohort", "validation", "data"])
def test_wrong_source_union_member_fails_closed(tmp_path, original, change):
    store, _ = setup_store(tmp_path)
    partition = publish(store, original)[2]
    value = trained_source(
        partition.dataset, TRAINING_SCOPE, partition.manifest.artifact_id, "1" * 64
    )
    if change == "profile":
        value["source_profile"] = SOURCE3_PROFILE
    elif change == "actor":
        value["kind"] = "declared_human"
    elif change == "cohort":
        value["cohort"] = "declared_native_machine_teacher"
    elif change == "validation":
        value["verification_identity"]["validation"] = "invented_permission"
    else:
        value["data_sha256"] = "f" * 64
    with pytest.raises(BoundaryError):
        checked_trained_source(value, partition.dataset.source_sha256)


def test_old_1_0_2_teacher_definition_kept_and_new_1_0_4_added():
    assert (
        TEACHER_PRODUCER["artifact_sha256"]
        == "50bbe123d4c4564e21c762dd5a3616c2618e7296c93cb067b3d9ba2bebac97f0"
    )
    assert TEACHER_PRODUCER["agent_spec"]["teacher"]["version"] == "1.0.2"
    assert (
        MAP_TEACHER_PRODUCER["artifact_sha256"]
        == "e590c1ec158d636909e9a3bcddd539b46e8e2e3a72c4303d721019635cb4ecac"
    )
    assert MAP_TEACHER_PRODUCER["agent_spec"]["teacher"]["version"] == "1.0.4"
    assert MAP_TEACHER_PRODUCER["input_spec"] == TEACHER_PRODUCER["input_spec"]
    assert native_model_schema(TRAINING_SCOPE) == LEGACY_GRAPH_MODEL
    assert is_native_model_schema(MODEL_SCHEMA)


def test_new_recipe_uses_existing_graph_run_checkpoint_and_native_dependencies(monkeypatch):
    from spireagent.workbench import trusted_recipes
    from stpd.native_graph_spec import CHECKPOINT_SCHEMA, RUN_SCHEMA

    monkeypatch.setattr(trusted_recipes, "recipe_dependencies_available", lambda _: False)
    recipe = trusted_recipes.describe_recipe(RECIPE)
    assert recipe["source_profiles"] == [DIRECT_PROFILE, SOURCE3_PROFILE]
    assert recipe["code_scope"] == TRAINING_SCOPE and recipe["run_schema"] == RUN_SCHEMA
    assert recipe["checkpoint_schema"] == CHECKPOINT_SCHEMA
    assert recipe["model_schema"] == MODEL_SCHEMA
    assert recipe["model_control"]["reset"]["mode"] == "carry"
