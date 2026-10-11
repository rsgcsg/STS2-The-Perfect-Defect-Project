"""The product default follows the selected view without relabelling older views."""

from metadata_import_guard import no_torch_imports as no_torch_imports
from source3_product_fixture import settled, setup

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


def test_actual_source3_preview_and_publication_keep_sample_history_and_no_training(
    tmp_path, monkeypatch,
):
    importer, datasets, _, candidate, store, owner, _, tool = setup(tmp_path, monkeypatch)
    importer.start(candidate)
    saved = settled(importer)
    assert saved["status"] == "completed", saved
    selected_view = source3_capabilities()["default_view"]
    datasets.start_source3_preview([saved["artifact_id"]], "agent_protocol", selected_view)
    preview = settled(datasets)
    assert preview["status"] == "preview_ready" and preview["can_publish"], preview
    assert preview["source_view"] == SAMPLED_VIEW
    assert preview["recommended_recipe_id"] == COMMON_SAMPLED_RECIPE
    assert preview["history_scope"] == "declared_original_input_basis_sampled_segments"
    assert preview["human_origin_verified"] is False
    datasets.start_publish(preview["preview_id"])
    ready = settled(datasets)
    assert ready["status"] == "completed", ready
    assert ready["actual_training_use"] is False
    assert ready["source_view"] == SAMPLED_VIEW
    partition = verify_ordered_source_partition(store, ready["training_source_id"])
    assert source_profile(partition.dataset) == SOURCE3_PROFILE
    assert all(run.identity.value()["capture_history"] == preview["history_scope"]
               for run in partition.dataset.runs)
    assert owner.ledger.dataset(partition.manifest.artifact_id)[0] == "training"
    for recipe in (COMMON_SAMPLED_RECIPE, SAMPLED_RECIPE):
        assert StructuredRecipeAdapter(recipe).preflight(
            store, owner, partition.manifest.artifact_id) == partition.dataset
    with owner.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM curation_uses").fetchone() == (0,)
    assert len(tool.calls) == 1
