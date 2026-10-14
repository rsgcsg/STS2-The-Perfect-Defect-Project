"""Synthetic Source3 product composition; no real Human or training-use proof."""

import json

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from source3_product_fixture import ORIGINAL, ROOT, settled, setup
from sts2_platform_evidence import verify_source_session_bundle_v3

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench import trusted_recipes
from spireagent.workbench.local_dataset import source3_capabilities
from spireagent.workbench.recipes.structured import StructuredRecipeAdapter
from stpd.fullrun.ordered_source import verify_ordered_source_partition
from stpd.native_sampled_carry_spec import INPUT_SPEC
from stpd.native_training_source_spec import (
    DIRECT_PROFILE,
    SOURCE3_PROFILE,
)
from stpd.native_training_source_spec import (
    RECIPE as COMMON_SAMPLED_RECIPE,
)
from stpd.ordered_source_spec import (
    DEFAULT_RECIPE,
    DEFAULT_VIEW,
    PRETRAIN_VIEW,
    SAMPLED_RECIPE,
    SAMPLED_VIEW,
    recipe_view,
)
from stpd.structured_profiles import source_profile


def test_product_default_and_display_project_the_owner_views(monkeypatch):
    monkeypatch.setattr(trusted_recipes, "recipe_dependencies_available", lambda _: False)
    common = trusted_recipes.describe_recipe(COMMON_SAMPLED_RECIPE)
    assert common["source_profiles"] == [DIRECT_PROFILE, SOURCE3_PROFILE]
    assert common["input_spec"] == INPUT_SPEC
    legacy = trusted_recipes.describe_recipe(SAMPLED_RECIPE)
    assert legacy["source_view"] == recipe_view(SAMPLED_RECIPE) == SAMPLED_VIEW
    support = source3_capabilities()
    assert support["default_view"] == SAMPLED_VIEW
    assert support["default_cohort"] == "declared_human"
    assert set(support["cohorts"]) == {"declared_human", "agent_native_ui", "agent_protocol"}
    assert "本人声明" in support["source_labels"]["declared_human"]
    assert "键鼠/UI" in support["source_labels"]["agent_native_ui"]
    assert "程序协议" in support["source_labels"]["agent_protocol"]
    assert "unknown" not in support["cohorts"]
    views = {row["view"]: row for row in support["views"]}
    assert {DEFAULT_VIEW, PRETRAIN_VIEW, SAMPLED_VIEW} <= views.keys()
    for name, row in views.items():
        if name == SAMPLED_VIEW:
            assert row["recommended_recipe_id"] == common["recipe_id"]
        else:
            assert recipe_view(row["recommended_recipe_id"]) == name
        assert row["label"] and row["label"] != name
    assert views[DEFAULT_VIEW]["recommended_recipe_id"] == DEFAULT_RECIPE
    assert views[PRETRAIN_VIEW]["label"] == "录制公开观察预训练"
    assert views[SAMPLED_VIEW]["recommended_recipe_id"] == COMMON_SAMPLED_RECIPE
    assert views[SAMPLED_VIEW]["history_scope"] == (
        "declared_original_input_basis_sampled_segments"
    )
    for name in (DEFAULT_VIEW, PRETRAIN_VIEW):
        assert views[name]["history_scope"] == "original_admitted_attachment_epoch_prefix"


@pytest.mark.parametrize("fixture,cohort", [
    pytest.param("source_session_v3", "agent_protocol", id="agent_protocol"),
    pytest.param("source_session_v3_ordered", "declared_human", id="declared_human"),
])
def test_actual_source3_preview_and_publication_keep_sample_history_and_no_training(
    tmp_path, monkeypatch, fixture, cohort,
):
    golden = ROOT / "components/evidence/tests/fixtures" / fixture / "bundle"
    original = verify_source_session_bundle_v3(golden).require_value()
    assert original.human_origin_verified is False
    importer, datasets, _, candidate, store, owner, _, tool = setup(
        tmp_path, monkeypatch, golden=golden,
    )
    importer.start(candidate)
    saved = settled(importer)
    assert saved["status"] == "completed", saved
    assert saved["human_origin_verified"] is False
    raw = store.get_manifest(saved["artifact_id"])
    assert raw.producer == ORIGINAL
    assert raw.parameters.value()["human_origin_verified"] is False
    assert "human_origin_attested" not in raw.parameters.value()
    selected_view = source3_capabilities()["default_view"]
    datasets.start_source3_preview([saved["artifact_id"]], cohort, selected_view)
    preview = settled(datasets)
    assert preview["status"] == "preview_ready" and preview["can_publish"], preview
    assert preview["source_kind"] == cohort and preview["accepted_labels"] == 1
    assert preview["source_view"] == SAMPLED_VIEW
    assert preview["recommended_recipe_id"] == COMMON_SAMPLED_RECIPE
    assert preview["history_scope"] == "declared_original_input_basis_sampled_segments"
    assert preview["human_origin_verified"] is False
    datasets.start_publish(preview["preview_id"])
    ready = settled(datasets)
    assert ready["status"] == "completed", ready
    assert ready["actual_training_use"] is False
    assert ready["human_origin_verified"] is False
    assert ready["source_view"] == SAMPLED_VIEW
    partition = verify_ordered_source_partition(store, ready["training_source_id"])
    assert source_profile(partition.dataset) == SOURCE3_PROFILE
    assert partition.dataset.source_kind == cohort
    assert partition.dataset.input_spec.value() == INPUT_SPEC
    source = json.loads(partition.dataset.source_bytes)
    steps = [step for run in source["runs"] for step in run["steps"]]
    original_inputs = {row["input_id"]: row for row in original.inputs}
    assert [step["evidence"]["input_prefix_ordinal"] for step in steps] == [
        row["input_prefix_ordinal"]
        for row in sorted(original.inputs, key=lambda row: int(row["input_prefix_ordinal"]))
    ]
    declarations = {row["segment_id"]: row["declaration"] for row in original.segments}
    labels = []
    for step in steps:
        evidence = step["evidence"]
        original_input = original_inputs[evidence["input_id"]]
        assert json_bytes(evidence["source_declaration"]) == json_bytes(
            declarations[original_input["segment_id"]]
        )
        assert json_bytes(evidence["catalog"]) == json_bytes(original_input["catalog"])
        assert json_bytes(evidence["capture"]) == json_bytes(original_input["pre_capture"])
        original_catalog = json.loads(
            (golden / "raw" / original_input["catalog"]["payload_ref"]).read_bytes()
        )
        assert step["catalog"] == original_catalog
        if step["chosen_action_id"] is not None:
            outcome = original_input["outcome"]
            assert evidence["source_declaration"]["source_kind"] == cohort
            assert evidence["source_declaration"]["machine_verifiable"] is False
            assert outcome["mapping_status"] == "exact" and outcome["match_count"] == 1
            assert outcome["delivery"] == "delivered"
            assert original_input["basis_order"] == {
                "status": "native_prefix_frozen", "reason_code": None,
            }
            selected = json.loads(json_bytes(outcome["selected_action"]))
            assert original_catalog.count(selected) == 1
            assert step["chosen_action_id"] == selected["action_id"]
            labels.append(step)
    assert len(labels) == preview["accepted_labels"] == 1
    assert all(run.identity.value()["capture_history"] == preview["history_scope"]
               for run in partition.dataset.runs)
    assert owner.ledger.dataset(partition.manifest.artifact_id) == (
        "training", set(partition.runs),
    )
    for recipe in (COMMON_SAMPLED_RECIPE, SAMPLED_RECIPE):
        assert StructuredRecipeAdapter(recipe).preflight(
            store, owner, partition.manifest.artifact_id) == partition.dataset
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM curation_source_uses").fetchone() == (0,)
    assert len(tool.calls) == 1


def test_sampled_human_cohort_cannot_reclassify_machine_input_or_publish_zero_N(
    tmp_path, monkeypatch,
):
    importer, datasets, _, candidate, store, owner, _, _ = setup(tmp_path, monkeypatch)
    importer.start(candidate)
    saved = settled(importer)
    assert saved["status"] == "completed" and saved["human_origin_verified"] is False
    datasets.start_source3_preview([saved["artifact_id"]], "declared_human", SAMPLED_VIEW)
    preview = settled(datasets)
    assert preview["status"] == "preview_ready", preview
    assert preview["source_kind"] == "declared_human" and preview["source_view"] == SAMPLED_VIEW
    assert preview["accepted_labels"] == 0 and preview["can_publish"] is False
    assert preview["N_coverage"]["denominator"] == 0
    assert preview["human_origin_verified"] is False
    with pytest.raises(BoundaryError, match="preview_cannot_publish"):
        datasets.start_publish(preview["preview_id"])
    assert all(store.get_manifest(identity).kind != "dataset" for identity in store.manifest_ids())
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_claims").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM curation_source_uses").fetchone() == (0,)
