"""Application parent admission to the common recorded-source recipe stays pure."""

from __future__ import annotations

import pytest
from test_native_agent_sampled_source import PROJECTOR, publish
from test_native_agent_sampled_source import original as original
from test_native_training_source_v2 import source3
from test_protocol_source import setup_store

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.recipes.structured import StructuredRecipeAdapter
from stpd.fullrun.ordered_source import OrderedSourceRef, publish_ordered_source_partition
from stpd.native_training_source_spec import RECIPE


@pytest.mark.parametrize("which", ["direct", "source3"])
def test_parent_requires_exact_prior_train_reservation(tmp_path, original, which):
    store, owner = setup_store(tmp_path)
    partition = publish(store, original)[2] if which == "direct" else source3(store)
    adapter = StructuredRecipeAdapter(RECIPE)
    with pytest.raises(BoundaryError, match="source_not_reserved_for_training"):
        adapter.preflight(store, owner, partition.manifest.artifact_id)
    reserve = (
        owner.reserve_verified_native_agent_sampled_source if which == "direct"
        else owner.reserve_verified_ordered_source
    )
    reserve(store, partition.manifest.artifact_id)
    assert adapter.preflight(store, owner, partition.manifest.artifact_id) == partition.dataset
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)


def test_common_parent_does_not_admit_Source3_heldout_partition(tmp_path):
    store, owner = setup_store(tmp_path)
    train = source3(store)
    refs = tuple(OrderedSourceRef(**ref) for ref in train.manifest.parameters.value()["raw_refs"])
    heldout = publish_ordered_source_partition(store, refs, "dev", PROJECTOR)
    with pytest.raises(BoundaryError, match="sampled_train_partition_required"):
        StructuredRecipeAdapter(RECIPE).preflight(store, owner, heldout.manifest.artifact_id)
