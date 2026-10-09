"""Existing curation transactions for conservative direct machine exposure families."""

from __future__ import annotations

import copy

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_native_agent_sampled_source import (
    IMPORTER,
    PROJECTOR,
    ROOT,
    SHARED,
    publish,
    replace_original_sample,
)
from test_native_agent_sampled_source import (
    original as original,
)
from test_native_agent_sampled_source import (
    owned_stale_original as owned_stale_original,
)
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from stpd.fullrun import native_agent_sampled_source as direct
from stpd.fullrun import ordered_source as ordered

SOURCE3 = ROOT / "components/evidence/tests/fixtures/source_session_v3/bundle"
RECORDER = Producer("fixture://original-Source3-production-path", "e" * 40, "a" * 64)


@pytest.mark.parametrize("duplicate_delivery", [None, "not_started", "delivered"])
def test_owned_stale_synthetic_prefix_replays_and_reserves_every_original_offer(
    tmp_path, owned_stale_original, duplicate_delivery
):
    """Source-local owning Evidence + private synthetic tuple, never production admission."""
    helper, relation, _, _ = owned_stale_original
    helper.terminal(helper.submission(), defer=True)
    helper.readiness()
    helper.sample()
    helper.terminal(helper.submission(), delivery="delivered")
    helper.stop()
    if duplicate_delivery is not None:
        position = next(i for i, event in enumerate(helper.f.events)
                        if event["kind"] == "native_result"
                        and event["payload"]["result"]["delivery"] == duplicate_delivery)
        helper.f.events.insert(position + 1, copy.deepcopy(helper.f.events[position]))
        helper.f.write()
    original_bytes = {path.name: path.read_bytes() for path in helper.directory.iterdir()}
    store, owner = setup_store(tmp_path)
    raw = direct.publish_native_agent_sampled_raw(
        store, helper.directory, IMPORTER, relation=relation)
    ref = direct.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    partition = direct.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)
    verified = direct.verify_native_agent_sampled_partition(store, partition.manifest.artifact_id)
    assert verified.source_ids == (raw.artifact_id,) and len(verified.index) == 3
    assert [row["admitted"] for row in verified.index] == [True, False, True]
    assert [row["N_eligible"] for row in verified.index] == [False, False, True]
    assert verified.index[0]["target_eligibility"] == "original_delivery_or_execution_not_N"
    assert {row["source_group"] for row in verified.index} == {"protocol-runtime:runtime-fixture"}
    reservation = owner.reserve_verified_native_agent_sampled_source(
        store, partition.manifest.artifact_id)
    assert reservation["source_groups"] == ["protocol-runtime:runtime-fixture"]
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_decisions WHERE source=?",
                          (raw.artifact_id,)).fetchone() == (3,)
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
    with pytest.raises(BoundaryError, match="protocol_training_use_missing"):
        owner.require_verified_native_agent_sampled_training_use(
            store, partition.manifest.artifact_id, "1" * 32)
    assert original_bytes == {path.name: path.read_bytes() for path in helper.directory.iterdir()}


def source3(store, split="train"):
    raw = ordered.publish_ordered_source_raw(store, SOURCE3, RECORDER)
    ref = ordered.publish_ordered_source_admission(
        store, raw.artifact_id, PROJECTOR, cohort="agent_protocol"
    )
    return ordered.publish_ordered_source_partition(store, (ref,), split, PROJECTOR)


def test_reservation_indexes_all_offers_and_actual_use_is_separate(tmp_path, original):
    store, owner = setup_store(tmp_path)
    raw, _, partition = publish(store, original)
    reservation = owner.reserve_verified_native_agent_sampled_source(
        store, partition.manifest.artifact_id
    )
    assert reservation["source_groups"] == ["protocol-runtime:runtime-fixture"]
    with pytest.raises(BoundaryError, match="protocol_training_use_missing"):
        owner.require_verified_native_agent_sampled_training_use(
            store, partition.manifest.artifact_id, "1" * 32
        )
    with owner.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_source_decisions WHERE source=?", (raw.artifact_id,)
        ).fetchone() == (7,)
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM curation_fingerprints WHERE fingerprint=?",
            ("protocol-runtime:runtime-fixture",),
        ).fetchone() == (1,)
    receipt = owner.record_verified_native_agent_sampled_training_use(
        store, partition.manifest.artifact_id, "1" * 32
    )
    assert receipt["qualified_run_ids"] == sorted(partition.runs)
    assert (
        owner.require_verified_native_agent_sampled_training_use(
            store, partition.manifest.artifact_id, "1" * 32
        )
        == receipt
    )
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (1,)
        db.execute(
            "DELETE FROM curation_source_decisions WHERE source=? AND rowid="
            "(SELECT min(rowid) FROM curation_source_decisions WHERE source=?)",
            (raw.artifact_id, raw.artifact_id),
        )
    with pytest.raises(BoundaryError, match="source_index_incomplete"):
        owner.require_verified_native_agent_sampled_training_use(
            store, partition.manifest.artifact_id, "1" * 32
        )


@pytest.mark.parametrize("other_split", ["train", "dev", "test"])
def test_optional_Source3_same_runtime_cannot_create_independent_purpose(
    tmp_path, original, other_split
):
    store, owner = setup_store(tmp_path)
    other = source3(store, other_split)
    assert "protocol-runtime:runtime-fixture" in ordered._plain(other.index[0]["related_keys"])
    owner.reserve_verified_ordered_source(store, other.manifest.artifact_id)
    _, _, partition = publish(store, original)
    with pytest.raises(BoundaryError, match="protocol_split_purpose_overlap"):
        owner.reserve_verified_native_agent_sampled_source(store, partition.manifest.artifact_id)
    with owner.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_claims WHERE artifact=?",
            (partition.manifest.artifact_id,),
        ).fetchone() == (0,)


def test_direct_training_family_blocks_later_Source3_heldout_and_incompatible_train(
    tmp_path, original
):
    store, owner = setup_store(tmp_path)
    _, _, partition = publish(store, original)
    owner.reserve_verified_native_agent_sampled_source(store, partition.manifest.artifact_id)
    owner.record_verified_native_agent_sampled_training_use(
        store, partition.manifest.artifact_id, "1" * 32
    )
    for split in ("train", "test"):
        other = source3(store, split)
        with pytest.raises(BoundaryError, match="protocol_split_purpose_overlap"):
            owner.reserve_verified_ordered_source(store, other.manifest.artifact_id)


def test_Gold_reservation_is_not_bypassed_by_direct_reexpression(tmp_path, original):
    store, owner = setup_store(tmp_path)
    verified = direct._verified(original.directory)
    runs, _ = direct._projection(verified, "a" * 64, "synthetic_conformance")
    owner.ledger.claim("gold-original-machine-fixture", "gold", {run["run_id"] for run in runs})
    _, _, partition = publish(store, original)
    with pytest.raises(BoundaryError, match="gold_reserved_data"):
        owner.reserve_verified_native_agent_sampled_source(store, partition.manifest.artifact_id)


def test_restarted_Agent_session_same_runtime_is_one_related_family(tmp_path, original):
    store, owner = setup_store(tmp_path)
    _, _, first = publish(store, original)
    owner.reserve_verified_native_agent_sampled_source(store, first.manifest.artifact_id)
    original.f.run["run_id"] = "different-Agent-run-same-runtime"
    for event in original.f.events:
        if event["kind"] == "native_submission_requested":
            event["payload"]["run_id"] = original.f.run["run_id"]
    original.f.write()
    _, _, second = publish(store, original)
    owner.reserve_verified_native_agent_sampled_source(store, second.manifest.artifact_id)
    owner.record_verified_native_agent_sampled_training_use(
        store, second.manifest.artifact_id, "1" * 32
    )
    assert first.runs != second.runs and first.source_groups == second.source_groups
    with owner.transaction() as db:
        grouped = owner.ledger._groups(db, second.runs)
        assert grouped == set(first.runs) | set(second.runs)
        assert db.execute(
            "SELECT count(*) FROM curation_uses WHERE kind='training'"
        ).fetchone() == (2,)


def test_duplicate_content_and_cross_store_are_rejected(tmp_path, original):
    store, owner = setup_store(tmp_path)
    raw, ref, _ = publish(store, original)
    copied = direct.publish_native_agent_sampled_raw(
        store, original.directory, Producer("fixture://copy", "f" * 40, "e" * 64)
    )
    copied_ref = direct.publish_native_agent_sampled_admission(store, copied.artifact_id, PROJECTOR)
    with pytest.raises(BoundaryError, match="duplicate_original_evidence_content"):
        direct.publish_native_agent_sampled_partition(store, (ref, copied_ref), "train", PROJECTOR)
    assert raw.artifact_id != copied.artifact_id
    other = tmp_path / "other"
    other.mkdir()
    other_store, other_owner = setup_store(other)
    _, _, partition = publish(other_store, original)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        owner.reserve_verified_native_agent_sampled_source(
            other_store, partition.manifest.artifact_id
        )
    assert other_owner.ledger.dataset(partition.manifest.artifact_id) is None


def test_censored_only_other_runtime_is_not_hidden_by_empty_projected_runs(tmp_path, original):
    store, _ = setup_store(tmp_path)
    _, accepted_ref, accepted = publish(store, original)
    first_ack = original.f.event("agent_sample_consume_ack_offered")["sequence"]
    original.f.events = original.f.events[:first_ack]
    original.last = original.f.event("agent_consumed")["payload"]["report"]
    retained = set()
    for event in original.f.events:
        if event["kind"] == "agent_sample_input_stored":
            payload = event["payload"]
            retained.update(
                {
                    payload["metadata_path"],
                    payload["metadata"]["observation"]["path"],
                    payload["metadata"]["catalog"]["path"],
                }
            )
    for path in original.directory.glob("agent-sample-*"):
        if path.name not in retained:
            path.unlink()
    frame = copy.deepcopy(SHARED["frames"]["map_a"])
    frame["capture"]["session"]["runtime_instance_id"] = "censored-runtime-B"
    frame["observation"]["session"]["runtime_instance_id"] = "censored-runtime-B"
    from test_native_agent_sampled_source import canonical

    frame["observation_utf8"] = canonical(frame["observation"]).decode("utf-8")
    original.f.events[0]["payload"]["environment"]["runtime_instance_id"] = "censored-runtime-B"
    replace_original_sample(original, 1, frame)
    original.end(uncertain=True)
    verified = direct._verified(original.directory)
    runs, report = direct._projection(verified, "a" * 64, "synthetic_conformance")
    assert not runs and len(report["index"]) == 1
    assert report["runtime_instance_id"] == "censored-runtime-B"
    assert report["index"][0]["source_group"] == "protocol-runtime:censored-runtime-B"
    assert report["censored_tail"]["reason"] == "ACK_without_known_completedNext_directive_tail"
    raw = direct.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    censored_ref = direct.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="one_original_runtime_required"):
        direct.publish_native_agent_sampled_partition(
            store, (accepted_ref, censored_ref), "train", PROJECTOR
        )
    assert store.manifest_ids() == before
    assert len(accepted.dataset.runs[0].steps) == 4
