"""Explicit reviewed segment aggregation over sealed synthetic originals only."""

from __future__ import annotations

import copy
import json
import runpy

import pytest
from test_native_agent_sampled_source import (
    IMPORTER,
    PROJECTOR,
    ROOT,
    declared_owned_teacher_fixture,
)
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun import native_agent_sampled_source as source
from stpd.native_agent_sampled_source_spec import (
    AGGREGATE_TEACHER_PROJECTION_SPEC,
    AGGREGATE_TEACHER_RELATION_SPEC,
    FIXTURE_COHORT,
    MAP_READY_TEACHER_PRODUCER,
    MAP_READY_TEACHER_RELATION_SPEC,
    REWARD_READY_TEACHER_PRODUCER,
    UTF8_TEACHER_PRODUCER,
    relation_body,
)


@pytest.fixture
def original_factory(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "components/evidence/tests"))
    tools = runpy.run_path(
        str(ROOT / "components/evidence/tests/test_sampled_agent_session_run_evidence.py")
    )
    helpers = []

    def create(definition, number):
        helper = tools["OwnedStaleEvidenceFixture"]()
        helpers.append(helper)
        runtime = "aggregate-runtime-" + str(number)
        helper.f.run["run_id"] = "aggregate-run-" + str(number)
        helper.f.events[0]["payload"]["environment"]["runtime_instance_id"] = runtime
        helper.binding["runtime_instance_id"] = runtime
        for frame in helper.base.shared["frames"].values():
            frame["capture"]["session"]["runtime_instance_id"] = runtime
            frame["observation"]["session"]["runtime_instance_id"] = runtime
        declared_owned_teacher_fixture(helper, definition)
        return helper

    try:
        yield create
    finally:
        for helper in helpers:
            helper.close()


@pytest.fixture
def originals(original_factory):
    return [
        original_factory(definition, number)
        for number, definition in enumerate(
            (UTF8_TEACHER_PRODUCER, REWARD_READY_TEACHER_PRODUCER, MAP_READY_TEACHER_PRODUCER)
        )
    ]


def publish_refs(store, helpers, relation=AGGREGATE_TEACHER_RELATION_SPEC):
    refs = []
    for helper in helpers:
        raw = source.publish_native_agent_sampled_raw(
            store, helper.directory, IMPORTER, relation=relation
        )
        refs.append(
            source.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
        )
    return tuple(refs)


def test_reviewed_exact_producers_aggregate_with_W0_and_campaign_exposure(tmp_path, originals):
    store, owner = setup_store(tmp_path)
    refs = publish_refs(store, originals)
    verified = source.publish_native_agent_sampled_partition(store, refs, "train", PROJECTOR)
    assert (
        source.verify_native_agent_sampled_partition(store, verified.manifest.artifact_id)
        == verified
    )
    assert len(verified.dataset.runs) == 3
    assert (
        sum(
            step.chosen_action_id is not None for run in verified.dataset.runs for step in run.steps
        )
        == 3
    )
    campaign = relation_body(AGGREGATE_TEACHER_RELATION_SPEC, FIXTURE_COHORT)["aggregation"][
        "related_keys"
    ][0]
    for run in verified.dataset.runs:
        assert run.steps[0].reset_before and run.steps[0].reset_reason == "sample_segment_start"
        assert run.steps[0].position == 0
        assert not run.steps[1].reset_before
        assert campaign in run.identity.value()["related_keys"]
        assert run.identity.value()["native_game_continuity_id"] is None
    reservation = owner.reserve_verified_native_agent_sampled_source(
        store, verified.manifest.artifact_id
    )
    assert len(reservation["source_groups"]) == 3
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
    reverse = source.publish_native_agent_sampled_partition(
        store, tuple(reversed(refs)), "train", PROJECTOR
    )
    assert reverse == verified
    assert (
        verified.manifest.parameters.value()["projection_spec"] == AGGREGATE_TEACHER_PROJECTION_SPEC
    )


def test_legacy_tuple_still_rejects_cross_runtime_partition(tmp_path, original_factory):
    originals = [original_factory(MAP_READY_TEACHER_PRODUCER, number) for number in (0, 1)]
    store, _ = setup_store(tmp_path)
    refs = publish_refs(store, originals[:2], MAP_READY_TEACHER_RELATION_SPEC)
    with pytest.raises(BoundaryError, match="one_original_runtime_required"):
        source.publish_native_agent_sampled_partition(store, refs, "train", PROJECTOR)


@pytest.mark.parametrize("mutation", ["policy", "artifact", "adapter", "input"])
def test_aggregate_never_admits_unreviewed_exact_producer(tmp_path, original_factory, mutation):
    definition = copy.deepcopy(MAP_READY_TEACHER_PRODUCER)
    if mutation == "policy":
        definition["execution_policy"]["max_known_stale_rejections"] -= 1
    elif mutation == "artifact":
        definition["artifact_sha256"] = "f" * 64
    elif mutation == "adapter":
        definition["adapter"]["code_sha256"] = "f" * 64
    else:
        definition["input_spec"]["sha256"] = "f" * 64
    helper = original_factory(definition, 9)
    store, _ = setup_store(tmp_path)
    with pytest.raises(
        BoundaryError,
        match=(
            "producer_execution_policy_relation"
            if mutation == "policy"
            else "producer_input_spec_relation"
            if mutation == "input"
            else "fixed_teacher_producer_identity"
        ),
    ):
        publish_refs(store, (helper,))
    assert not store.manifest_ids()


def test_duplicate_original_alias_and_shared_capture_cannot_pad_N(tmp_path, originals):
    store, _ = setup_store(tmp_path)
    helper = originals[-1]
    first = publish_refs(store, (helper,))[0]
    raw = source.publish_native_agent_sampled_raw(
        store,
        helper.directory,
        Producer("fixture://second-importer", "f" * 40, "f" * 64),
        relation=AGGREGATE_TEACHER_RELATION_SPEC,
    )
    alias = source.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    assert first.raw_id != alias.raw_id
    with pytest.raises(BoundaryError, match="duplicate_original_evidence_content"):
        source.publish_native_agent_sampled_partition(store, (first, alias), "train", PROJECTOR)
    helper.f.run["run_id"] = "alias-run"
    for event in helper.f.events:
        if event["kind"] == "native_submission_requested":
            event["payload"]["run_id"] = "alias-run"
    helper.f.write()
    other = publish_refs(store, (helper,))[0]
    with pytest.raises(BoundaryError, match="shared_original_capture_alias"):
        source.publish_native_agent_sampled_partition(store, (first, other), "train", PROJECTOR)


@pytest.mark.parametrize("mutation", ["campaign", "reset", "producer", "raw_parent"])
def test_rehashed_aggregate_preserves_association_reset_and_exact_lineage(
    tmp_path,
    originals,
    mutation,
):
    store, _ = setup_store(tmp_path)
    partition = source.publish_native_agent_sampled_partition(
        store, publish_refs(store, originals), "train", PROJECTOR
    )
    data = json.loads(partition.dataset.source_bytes)
    run = data["runs"][1]
    if mutation == "campaign":
        run["identity"]["related_keys"] = [run["source_group"]]
    elif mutation == "reset":
        run["steps"][0]["reset_before"] = False
    elif mutation == "producer":
        run["identity"]["producer"]["agent_manifest"]["artifact"]["sha256"] = "f" * 64
    else:
        run["identity"]["raw_id"] = "f" * 64
    with pytest.raises(BoundaryError):
        source.parse_native_agent_sampled_dataset(json_bytes(data))


def test_aggregate_unknown_tail_never_bridges_to_another_run_or_heldout(tmp_path, originals):
    helper = originals[0]
    terminal = next(
        event for event in reversed(helper.f.events) if event["kind"] == "native_result"
    )
    terminal["payload"]["result"]["delivery"] = "unknown"
    helper.f.run["tainted"] = True
    helper.f.write()
    store, _ = setup_store(tmp_path)
    refs = publish_refs(store, originals)
    with pytest.raises(BoundaryError, match="train_only_partition_required"):
        source.publish_native_agent_sampled_partition(store, refs, "test", PROJECTOR)
    partition = source.publish_native_agent_sampled_partition(store, refs, "train", PROJECTOR)
    assert len(partition.dataset.runs) == 3
    unknown = next(
        run
        for run in partition.dataset.runs
        if run.identity.value()["agent_run_id"] == "aggregate-run-0"
    )
    assert unknown.identity.value()["censored_tail"] is not None
    assert all(step.chosen_action_id is None for step in unknown.steps)
    assert all(run.steps[0].reset_before for run in partition.dataset.runs)


def test_frozen_aggregate_definitions_bind_compatible_public_semantics_and_exact_bytes():
    from test_native_agent_sampled_source import sha

    from stpd.native_agent_sampled_source_spec import TARGET_SPEC

    body = relation_body(AGGREGATE_TEACHER_RELATION_SPEC, FIXTURE_COHORT)
    assert len(body["producer_definitions"]) == 3
    assert body["aggregation"]["independent_games"] is False
    projection, target = source.relation_specs(AGGREGATE_TEACHER_RELATION_SPEC, FIXTURE_COHORT)
    assert projection == AGGREGATE_TEACHER_PROJECTION_SPEC and target == TARGET_SPEC
    for definition in body["producer_definitions"]:
        artifact = {
            "schema": definition["artifact_schema"],
            **{
                key: definition[key]
                for key in (
                    "agent_spec",
                    "input_spec_body",
                    "input_spec",
                    "adapter",
                    "code_files",
                    "runtime_provenance",
                )
            },
        }
        assert sha(json_bytes(artifact)) == definition["artifact_sha256"]
        assert definition["input_spec"] == body["producer_input_spec"]
        assert (
            sha(json_bytes(definition["input_spec_body"]))
            == (body["producer_input_spec_body_sha256"])
        )
        assert definition["state_format_version"] == body["producer_state_format"]
        assert definition["execution_policy"] == definition["agent_spec"]["execution_policy"]
        assert definition["history_mode"] == "sampled_current"
        assert definition["consumption_mode"] == "once_per_occurrence"
        assert definition["input_spec_body"]["I"] is False
        assert definition["input_spec_body"]["F"] is False


def test_common_application_import_preview_publish_reserves_without_actual_use(tmp_path, originals):
    from test_native_agent_product_data import settled, setup

    from spireagent.workbench.local_recording_import import native_agent_import_choices
    from stpd.native_agent_sampled_source_spec import OWNED_STALE_TEACHER_RELATION_SPEC

    importer, datasets, store, owner = setup(tmp_path / "product")
    choices = native_agent_import_choices()
    assert choices["default_relation_id"] == OWNED_STALE_TEACHER_RELATION_SPEC["id"]
    ids = []
    for helper in originals:
        importer.start_native_agent_run(
            str(helper.directory), FIXTURE_COHORT, AGGREGATE_TEACHER_RELATION_SPEC["id"]
        )
        imported = settled(importer)
        assert imported["status"] == "completed", imported
        ids.append(imported["artifact_id"])
    datasets.start_native_agent_preview(ids)
    preview = settled(datasets)
    assert preview["status"] == "preview_ready" and preview["accepted_labels"] == 3
    assert preview["producer_student_relation"] == AGGREGATE_TEACHER_RELATION_SPEC
    datasets.start_publish(preview["preview_id"])
    published = settled(datasets)
    assert published["status"] == "completed" and published["split_status"] == "reserved"
    assert published["actual_training_use"] is False
    verified = source.verify_native_agent_sampled_partition(store, published["training_source_id"])
    assert len(verified.dataset.runs) == 3
    assert owner.ledger.dataset(verified.manifest.artifact_id) == ("training", set(verified.runs))
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM local_source_pending").fetchone() == (0,)
