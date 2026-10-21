"""P1 manifest-contract fixtures; no Human/recording qualification is claimed."""

from __future__ import annotations

import copy
import io
from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_t0 import observation

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.p1.data import digest, json_bytes
from stpd.p1.dataset import (
    SEGMENTS_SCHEMA,
    SOURCE_SCHEMA,
    TARGETS_SCHEMA,
    inspect_projections,
    iter_snapshot_points,
    load_snapshot,
    publish_snapshot,
)


def store_at(path):
    return ManifestArtifactStore(LocalBlobStore(path))


def projection_fixture(
    store,
    name,
    *,
    seed=None,
    ancestors=(),
    root=None,
    value=None,
    projection_id="native-selector-finalized-d-1",
    original=None,
):
    branch = {
        "session_id": "session-" + name,
        "timeline_id": "timeline-" + name,
        "run_id": "run-0001",
    }
    content_id = digest(name.encode())
    if original is None:
        original = Manifest(
            "evidence",
            PRODUCER,
            payloads=(
                store.put_payload("archive", io.BytesIO(b"synthetic-original-" + name.encode())),
            ),
            parameters=FrozenObject.of(
                {
                    "schema": SOURCE_SCHEMA,
                    "content_id": content_id,
                    "declared_origin": "engineering_fixture",
                    "research_admission": "not_evaluated",
                    "lineage": {
                        "root_run_id": root or name,
                        "seed": seed or "seed-" + name,
                        "ancestor_root_ids": list(ancestors),
                        "character": "Defect",
                        "ascension": 0,
                        "mode": "standard",
                        "language": "en",
                    },
                }
            ),
        )
        store.publish(original)
    else:
        content_id = original.parameters.value()["content_id"]
    if value is None:
        point = {
            "index": 0,
            "observation": observation(),
            "chosen": "c9",
            "source_event_id": "event-" + name,
            "source_decision_id": "decision-" + name,
            "mask": {"N": True, "next": False, "end": False},
        }
        value = {
            "schema": SEGMENTS_SCHEMA,
            "profile": "p1-global-r1",
            "revision": "r1.4",
            "branch": branch,
            "source_content_id": content_id,
            "segments": [{"segment_id": "segment", "branch": branch, "points": [point]}],
        }
    manifest = Manifest(
        "analysis",
        PRODUCER,
        (Parent("source", original.artifact_id),),
        (store.put_payload("targets" if value["schema"] == TARGETS_SCHEMA else "segments",
                           io.BytesIO(json_bytes(value)), "application/json"),),
        FrozenObject.of(
            {
                "schema": value["schema"],
                "projection_id": projection_id,
                "tool_release_id": "tool-release-fixture",
                "profile": "p1-global-r1",
                "revision": "r1.4",
                "run_id": value["branch"]["run_id"],
            }
        ),
    )
    store.publish(manifest)
    return manifest, original, value


def explicit_snapshot(store, projections):
    ids = [p.artifact_id for p in projections]
    inspected = inspect_projections(store, ids)
    groups = [g["group_id"] for g in inspected["groups"] if g["N"]]
    splits = {g: ("train", "dev", "test")[i % 3] for i, g in enumerate(groups)}
    return publish_snapshot(
        store, ids, PRODUCER, split_salt="engineering-test", split_by_group=splits
    )


def test_snapshot_by_store_id_reloads_only_complete_N_and_never_timeline_context(tmp_path):
    store = store_at(tmp_path)
    base, original, value = projection_fixture(store, "first")
    point = copy.deepcopy(value["segments"][0]["points"][0])
    point["observation"]["scene"]["content"]["entities"][0]["description"] = "Complete input."
    context = {
        "observation": {"partial": True, "description": "NEVER_MODEL_INPUT"},
        "chosen": None,
        "mask": {"N": False, "next": False, "end": False},
    }
    targets = {k: v for k, v in value.items() if k != "segments"}
    targets.update(
        schema=TARGETS_SCHEMA,
        points=[point, context],
        timeline=[
            {
                "observation": {"secret_context_marker": "NEVER_MODEL_INPUT"},
                "effect": "stop_relic_refresh",
                "mask": {"N": True},
            }
        ],
    )
    target, _, _ = projection_fixture(
        store,
        "targets",
        original=original,
        value=targets,
        projection_id="native-timeline-targets-1",
    )
    snap = publish_snapshot(store, [target.artifact_id], PRODUCER, split_salt="explicit")
    _, selection = load_snapshot(store, snap.artifact_id)
    assert selection["unique_N"] == 1
    assert selection["exclusions"] == {"N_false_not_a_model_input": 1}
    assert selection["partition_N"] == {"unassigned": 1}
    read = list(iter_snapshot_points(store, snap.artifact_id))
    assert len(read) == 1 and read[0][1] == point["observation"]
    assert "NEVER_MODEL_INPUT" not in str(read)
    assert snap.parameters.value()["research_admission"] == "not_evaluated"
    # The selection artifact stores references, not another copy of raw/observations.
    assert snap.payload("selection").size < base.payload("segments").size + 8000


def test_same_native_point_new_projection_is_alias_but_conflict_requires_choice(tmp_path):
    store = store_at(tmp_path)
    first, original, value = projection_fixture(store, "one")
    changed = copy.deepcopy(value)
    changed["segments"][0]["points"][0]["observation"]["envelope"]["occurrence"] = 999
    second, _, _ = projection_fixture(store, "copy", original=original, value=changed)
    read = inspect_projections(store, [second.artifact_id, first.artifact_id])
    assert read["unique_N"] == read["exact_duplicate_N"] == 1
    assert len(read["points"][0]["aliases"]) == 1
    changed["segments"][0]["points"][0]["chosen"] = "c7"
    conflict, _, _ = projection_fixture(store, "conflict", original=original, value=changed)
    with pytest.raises(BoundaryError, match="overlapping_projection_N_conflict"):
        inspect_projections(store, [first.artifact_id, conflict.artifact_id])
    chosen = inspect_projections(
        store,
        [first.artifact_id, conflict.artifact_id],
        selected_decisions={conflict.artifact_id: []},
    )
    assert chosen["unique_N"] == 1 and chosen["exact_duplicate_N"] == 0


def test_root_seed_restore_and_shared_prefix_use_existing_component_allocation(tmp_path):
    store = store_at(tmp_path)
    a, _, av = projection_fixture(store, "a", seed="same")
    b, _, _ = projection_fixture(store, "b", seed="same")
    c, _, _ = projection_fixture(store, "c", ancestors=("b",))
    d, original, dv = projection_fixture(store, "d")
    # A shared immutable native decision is prefix lineage, even in another export.
    dv["segments"][0]["points"][0] = copy.deepcopy(av["segments"][0]["points"][0])
    d, _, _ = projection_fixture(store, "d-copy", original=original, value=dv)
    inspected = inspect_projections(store, [x.artifact_id for x in (a, b, c, d)])
    assert inspected["independent_group_count"] == 1
    assert inspected["groups"][0]["root_run_ids"] == ["a", "b", "c", "d"]
    assert inspected["unique_N"] == 3
    independent = [projection_fixture(store, str(i))[0] for i in range(20)]
    snap = publish_snapshot(
        store, [m.artifact_id for m in independent], PRODUCER, split_salt="frozen-20-components"
    )
    _, value = load_snapshot(store, snap.artifact_id)
    assert value["partition_N"] == {"train": 16, "dev": 2, "test": 2}
    assert value["independent_group_count"] == 20
    assert value["use_scope"] == "engineering_only_not_research_admission"


@pytest.mark.parametrize(
    "defect",
    [
        "partial",
        "null_envelope",
        "null_chosen",
        "duplicate",
        "null_choice",
        "mask_type",
        "unknown_projection",
    ],
)
def test_bad_N_and_unknown_projection_cannot_silently_become_training(tmp_path, defect):
    store = store_at(tmp_path)
    _, original, value = projection_fixture(store, "invalid")
    point = value["segments"][0]["points"][0]
    projection_id = "native-selector-finalized-d-1"
    if defect == "partial":
        point["observation"]["envelope"]["required_information_complete"] = False
    elif defect == "null_envelope":
        point["observation"]["envelope"] = None
    elif defect == "null_chosen":
        point["chosen"] = None
    elif defect == "duplicate":
        point["observation"]["catalog"].append(point["observation"]["catalog"][0])
    elif defect == "null_choice":
        point["observation"]["catalog"] = [None]
    elif defect == "mask_type":
        point["mask"]["N"] = 1
    else:
        projection_id = "native-timeline-targets-future"
    manifest, _, _ = projection_fixture(
        store, "invalid-copy", original=original, value=value, projection_id=projection_id
    )
    with pytest.raises(BoundaryError):
        inspect_projections(store, [manifest.artifact_id])


def test_snapshot_load_reproduces_parent_selection_not_just_hash(tmp_path):
    store = store_at(tmp_path)
    projections = [projection_fixture(store, str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    _, value = load_snapshot(store, snapshot.artifact_id)
    original_bytes = json_bytes(value)
    # A coherent new checksum cannot turn an unrelated group into the point's lineage.
    value["points"][0]["group_id"] = value["points"][1]["group_id"]
    payload = store.put_payload("selection", io.BytesIO(json_bytes(value)), "application/json")
    forged = replace(
        snapshot,
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                **snapshot.parameters.value(),
                "selection_sha256": payload.sha256,
            }
        ),
    )
    store.publish(forged)
    with pytest.raises(BoundaryError, match="parent_reprojection"):
        load_snapshot(store, forged.artifact_id)
    assert json_bytes(load_snapshot(store, snapshot.artifact_id)[1]) == original_bytes


def test_unknown_lineage_never_gets_an_automatic_heldout_split(tmp_path):
    from stpd.p1.workload import populations_from_snapshot

    store = store_at(tmp_path)
    projections = []
    for index in range(3):
        _, source, value = projection_fixture(store, "unknown-" + str(index))
        source = replace(source, parameters=FrozenObject.of({
            **source.parameters.value(), "lineage": {},
        }))
        store.publish(source)
        projection, _, _ = projection_fixture(
            store, "unknown-copy-" + str(index), original=source, value=value,
        )
        projections.append(projection.artifact_id)
    snapshot = publish_snapshot(store, projections, PRODUCER, split_salt="unknown")
    _, selection = load_snapshot(store, snapshot.artifact_id)
    assert selection["observed_group_count"] == 3
    assert selection["unknown_independence_groups"] == 3
    assert selection["partition_N"] == {"unassigned": 3}
    assert selection["split_method"] == "unknown_lineage_unassigned"
    with pytest.raises(BoundaryError, match="explicit_nonempty_train_dev_required"):
        populations_from_snapshot(store, snapshot.artifact_id)
    # An explicit engineering allocation is still supported and remains marked
    # as such; it does not establish statistical independence or admission.
    explicit = publish_snapshot(
        store, projections, PRODUCER, split_salt="explicit-engineering",
        split_by_group={g["group_id"]: ("train", "dev", "test")[i]
                        for i, g in enumerate(selection["groups"])},
    )
    _, chosen = load_snapshot(store, explicit.artifact_id)
    assert chosen["partition_N"] == {"train": 1, "dev": 1, "test": 1}
    assert chosen["unknown_independence_groups"] == 3
    assert chosen["split_method"] == "explicit_engineering_groups_not_statistical_qualification"
