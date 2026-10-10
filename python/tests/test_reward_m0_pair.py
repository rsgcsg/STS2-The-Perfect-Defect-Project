"""Sealed synthetic originals and actual owner receipts for the reward-M0 pair."""

from __future__ import annotations

import copy
import hashlib
import io
from dataclasses import replace

import pytest
from platform_bundle3_fixture import bundle3, load, rows, seal, stream
from test_artifact_store_v1 import PRODUCER
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_dataset import LocalDatasetService
from spireagent.workbench.reward_m0_data import require_historical_training
from stpd.fullrun.public_bc import publish_public_bc_view
from stpd.fullrun.reward_m0_source import (
    COMPLETE_DECK,
    append_deck,
    canonical_deck,
    deck_for_frame,
    project_reward_m0_pair,
)
from stpd.reward_m0_spec import RewardM0Config


@pytest.fixture
def pair_originals(tmp_path, monkeypatch, request):
    """Private synthetic metadata/BC history; no prior numerical Model claim."""
    from test_dataset_curation import build
    from test_decision_store import setup

    from spireagent.storage.store import copy_artifact
    from stpd.fullrun.decision_dataset import SelectionRules
    from stpd.fullrun.decision_spool import SpoolSelection
    from stpd.fullrun.decision_store import preview
    from stpd.fullrun.decision_training import AllocationSpec, publish_allocation

    def synthetic_bundle(path):
        root = bundle3(path, public_bindings=True)
        raw = root / "raw"
        canonical = rows(raw / "canonical-transitions.jsonl")
        trace = rows(raw / "semantic-boundary-trace.jsonl")
        for number, original in enumerate(canonical):
            ref = original["pre_state_ref"]
            frame = load(raw / ref["object_ref"])
            snapshot = frame["snapshot"]
            snapshot["interaction"]["kind"] = "card_reward_selection"
            take = snapshot["bound_actions"]["actions"][0]
            skip = {
                **take,
                "bound_action_id": "skip-" + str(number),
                "verb": "activate",
                "subject_referent_id": None,
            }
            actions = [take, skip]
            snapshot["bound_actions"].update(actions=actions, total_count=2, materialized_count=2)
            original["decision"]["surface"] = "card_reward_selection"
            chosen = take if number % 2 == 0 else skip
            original["action"] = {**chosen, "arguments": {}}
            deck = {
                "kind": "run_deck",
                "card_count": 2 + number % 2,
                "cards": [
                    card("Strike", "opaque-" + str(index)) for index in range(2 + number % 2)
                ],
            }
            deck_bytes = json_bytes(deck)
            sha = hashlib.sha256(deck_bytes).hexdigest()
            payload_ref = "blobs/sha256/" + sha[:2] + "/" + sha + ".json"
            (raw / payload_ref).parent.mkdir(parents=True, exist_ok=True)
            (raw / payload_ref).write_bytes(deck_bytes)
            read = next(value for value in frame["reads"] if value["kind"] == "run_deck")
            read.update(
                payload_ref=payload_ref,
                payload_sha256=sha,
                runtime_instance_id=snapshot["session"]["runtime_instance_id"],
                environment_fingerprint=snapshot["session"]["environment_fingerprint"],
                completeness={
                    "status": "complete",
                    "missing": [],
                    "hidden_by_policy": [],
                    "visible_information": COMPLETE_DECK,
                },
            )
            snapshot["reads"] = [
                {
                    "kind": "run_deck",
                    "read_id": read["read_id"],
                    "content_schema": read["content_schema"],
                    "snapshot_bound": True,
                    "ordering_semantics": "unordered_multiset",
                }
            ]
            frame["catalog_count"] = 2
            content = json_bytes(frame)
            frame_sha = hashlib.sha256(content).hexdigest()
            object_ref = "semantic-frames/sha256/" + frame_sha[:2] + "/" + frame_sha + ".json"
            (raw / object_ref).parent.mkdir(parents=True, exist_ok=True)
            (raw / object_ref).write_bytes(content)
            new_ref = {**ref, "object_ref": object_ref, "content_sha256": frame_sha}
            human_ref = new_ref
            if getattr(request, "param", None) == "missing" and number % 2 == 0:
                # H and execution S are distinct evidence. Retain canonical S's
                # required Read, while H has an explicitly unavailable optional
                # deck. The paired source must never backfill B from that S.
                human = copy.deepcopy(frame)
                next(value for value in human["reads"] if value["kind"] == "run_deck").update(
                    status="not_available",
                    payload_ref=None,
                    payload_sha256=None,
                    error_code="fixture_unavailable",
                )
                human_bytes = json_bytes(human)
                human_sha = hashlib.sha256(human_bytes).hexdigest()
                human_path = "semantic-frames/sha256/" + human_sha[:2] + "/" + human_sha + ".json"
                (raw / human_path).parent.mkdir(parents=True, exist_ok=True)
                (raw / human_path).write_bytes(human_bytes)
                human_ref = {**ref, "object_ref": human_path, "content_sha256": human_sha}
            original["pre_state_ref"] = new_ref
            for event in trace:
                if event["action"]["action_witness_id"] == original["action_witness_id"]:
                    event["action"]["decision"] = original["decision"]
                    event["action"]["bound_action"] = original["action"]
                    event["human_observation_ref"] = human_ref
                    if "execution_pre_ref" in event:
                        event["execution_pre_ref"] = new_ref
        stream(raw / "canonical-transitions.jsonl", canonical)
        stream(raw / "semantic-boundary-trace.jsonl", trace)
        seal(root)
        return root

    monkeypatch.setattr("test_decision_store.bundle3", synthetic_bundle)
    hub, upload, source, jobs = setup(tmp_path / "hub")
    dataset_id = build(jobs, upload)["result"]["artifact_id"]
    (tmp_path / "private").mkdir()
    store, owner = setup_store(tmp_path / "private")
    copy_artifact(hub.store, store, dataset_id)
    projection = preview(
        store,
        (store.get_manifest(source.artifact_id),),
        SelectionRules(),
        on_projection=lambda value: owner.ledger.index_source(source.artifact_id, value),
    )
    try:
        owner.ledger.claim(dataset_id, "training", projection.run_ids)
        owner.ledger.bind(dataset_id, dataset_id)
    finally:
        if isinstance(projection.records, SpoolSelection):
            projection.records.owner.close()
    operation = "a" * 32
    owner.reserve_training_datasets(store, (dataset_id,), operation)
    receipt = owner.verified_training_receipt(store, (dataset_id,), operation)
    allocation = publish_allocation(
        store, dataset_id, AllocationSpec(max_train=4, max_dev=2), PRODUCER
    )
    view = publish_public_bc_view(store, allocation.artifact_id, PRODUCER)
    training_binding = {
        "schema": "stpd/light-action-m0-training-binding-v1",
        "dataset_ids": [dataset_id],
        "model_view_id": view.artifact_id,
        "allocation_id": allocation.artifact_id,
        "training_operation_id": operation,
    }
    # A historical typed input metadata fixture, not trained weights or runtime evidence.
    old_input = Manifest(
        "training_input",
        PRODUCER,
        (Parent("model_view", view.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/stage1a-light-action-m0-public-input-v1",
                "source_renderer": view.parameters.value()["serializer"],
                "training_binding": training_binding,
            }
        ),
    )
    store.publish(old_input)
    members = load_json_payload(store, allocation.payload("members"))["members"]
    ids = [row["occurrence"] for row in members if row["split"] == "train"]
    historical = {
        "dataset_id": dataset_id,
        "cached_view_id": view.artifact_id,
        "allocation_id": allocation.artifact_id,
        "training_operation_id": operation,
        "receipt_id": receipt,
        "training_input_id": old_input.artifact_id,
    }
    config = ProjectConfig(tmp_path / "private/state", "", "", None, combination())
    return store, owner, config, historical, ids


def load_json_payload(store, payload):
    import json

    return json.loads(b"".join(store.read_payload(payload)))


def card(name, identifier="opaque", upgraded=False):
    return {
        "entity_id": identifier,
        "definition_id": name.upper(),
        "name": name,
        "type": "Attack",
        "cost": "X",
        "rarity": "Common",
        "is_upgraded": upgraded,
        "is_selected": False,
        "description": "Public description",
    }


def limits():
    return {"wall_seconds": 120, "scratch_bytes": 512 * 1024**2}


def project(fixture, config=None):
    store, owner, _, historical, ids = fixture
    require_historical_training(owner, store, historical)
    return project_reward_m0_pair(
        store,
        historical["cached_view_id"],
        ids,
        config or RewardM0Config(steps=2, state_vocab_size=256),
        limits(),
        historical,
    )


def test_exact_original_pair_keeps_A_C_target_and_H_deck_binding(pair_originals):
    result = project(pair_originals)
    assert result["counts"] == {
        "requested_unique_TRAIN_N": 4,
        "paired_unique_TRAIN_N": 4,
        "excluded_from_both": 0,
    }
    assert result["full_corpus_reprojection"] is False and result["heldout_or_Gold"] is False
    for row in result["rows"]:
        assert row["A"]["action_texts"] == row["B"]["action_texts"]
        assert row["A"]["action_keys"] == row["B"]["action_keys"]
        assert row["A"]["chosen_index"] == row["B"]["chosen_index"] == row["chosen_index"]
        assert row["B"]["state_text"] == append_deck(row["A"]["state_text"], row["deck"])
        assert "opaque-" not in row["B"]["state_text"]
    grouped = {}
    for row in result["rows"]:
        grouped.setdefault(row["run_id"], []).append(row)
    for rows_ in grouped.values():
        assert rows_[0]["A"]["state_text"] == rows_[1]["A"]["state_text"]
        assert rows_[0]["B"]["state_text"] != rows_[1]["B"]["state_text"]


def test_unordered_multiset_preserves_duplicates_upgrades_and_public_enchantments():
    first = card("Strike", "one")
    second = card("Strike", "two", upgraded=True)
    second["existing_enchantment"] = {
        "definition_id": "PUBLIC",
        "amount": 2,
        "name": "Visible",
        "description": "Public",
        "observation_source": "player_visible",
    }
    value = canonical_deck(
        {"kind": "run_deck", "card_count": 3, "cards": [first, copy.deepcopy(first), second]}
    )
    reordered = canonical_deck(
        {
            "kind": "run_deck",
            "card_count": 3,
            "cards": [{**second, "entity_id": "changed"}, {**first, "entity_id": "other"}, first],
        }
    )
    assert reordered == value and len(value["cards"]) == 3
    assert sum(row["is_upgraded"] for row in value["cards"]) == 1
    assert value["cards"][-1]["existing_enchantment"] or value["cards"][0]["existing_enchantment"]
    with pytest.raises(BoundaryError, match="complete_deck_count"):
        canonical_deck({"kind": "run_deck", "card_count": 4, "cards": [first]})


@pytest.mark.parametrize(
    "change",
    [
        "snapshot",
        "runtime",
        "environment",
        "missing",
        "incomplete",
        "opaque_digest",
        "order",
        "not_advertised",
    ],
)
def test_missing_stale_wrong_or_incomplete_Read_never_becomes_B(pair_originals, change):
    store, _, _, _, _ = pair_originals
    result = project(pair_originals)
    row = result["rows"][0]
    read = copy.deepcopy(row["Read_evidence"])
    frame = {
        "snapshot_id": read["snapshot_id"],
        "reads": [read],
        "snapshot": {
            "snapshot_id": read["snapshot_id"],
            "session": {
                "runtime_instance_id": read["runtime_instance_id"],
                "environment_fingerprint": read["environment_fingerprint"],
            },
            "reads": [
                {
                    "kind": "run_deck",
                    "read_id": read["read_id"],
                    "snapshot_bound": True,
                    "content_schema": read["content_schema"],
                    "ordering_semantics": "unordered_multiset",
                }
            ],
        },
    }
    # The pure Read boundary independently rejects every mismatched binding;
    # no original source blob is changed in this test.
    raw = json_bytes({"kind": "run_deck", "card_count": 1, "cards": [card("Strike")]})
    sha = hashlib.sha256(raw).hexdigest()
    read.update(payload_sha256=sha, payload_ref="blobs/sha256/" + sha[:2] + "/" + sha + ".json")
    if change == "snapshot":
        read["snapshot_id"] = "stale"
    elif change == "runtime":
        read["runtime_instance_id"] = "other"
    elif change == "environment":
        read["environment_fingerprint"] = "f" * 64
    elif change == "missing":
        frame["reads"] = []
    elif change == "incomplete":
        read["completeness"]["missing"] = ["cards"]
    elif change == "opaque_digest":
        read["payload_sha256"] = "f" * 64
    elif change == "order":
        frame["snapshot"]["reads"][0]["ordering_semantics"] = "observed_order"
    else:
        frame["snapshot"]["reads"] = []
    with pytest.raises(BoundaryError):
        deck_for_frame(frame, raw)


def test_common_dataset_preview_publish_reserves_without_new_training_use(
    pair_originals, monkeypatch
):
    store, owner, config, historical, ids = pair_originals
    monkeypatch.setattr(
        "spireagent.workbench.local_dataset.source_identity", lambda _root: PRODUCER
    )
    service = LocalDatasetService(config)
    with owner.transaction() as db:
        uses = db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0]
    service.start_reward_m0_preview(
        historical["cached_view_id"],
        ids,
        historical,
        RewardM0Config(steps=2, state_vocab_size=256).to_dict(),
        limits(),
    )
    service.thread.join(timeout=10)
    preview = service.status()["operation"]
    assert preview["status"] == "preview_ready" and preview["accepted_labels"] == 4
    assert preview["historical_external_exposure"] == "unknown"
    service.start_publish(preview["preview_id"])
    service.thread.join(timeout=10)
    published = service.status()["operation"]
    assert published["status"] == "completed" and published["split_status"] == "reserved"
    assert published["actual_training_use"] is False
    from spireagent.workbench.reward_m0_data import require_reward_m0_source

    source = require_reward_m0_source(owner, store, published["training_source_id"])
    assert owner.ledger.dataset(source.manifest.artifact_id) == ("training", set(source.runs))
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone()[0] == uses
    reopened = LocalDatasetService(config)
    assert reopened.status()["operation"]["training_source_id"] == source.manifest.artifact_id


@pytest.mark.parametrize(
    "change", ["receipt", "historical_use", "source_roster", "Gold", "membership", "cache_metadata"]
)
def test_changed_authority_or_cache_lineage_cannot_enter_preparation(
    pair_originals,
    monkeypatch,
    change,
):
    store, owner, config, historical, ids = pair_originals
    monkeypatch.setattr(
        "spireagent.workbench.local_dataset.source_identity", lambda _root: PRODUCER
    )
    before = store.manifest_ids()
    request = copy.deepcopy(historical)
    if change == "receipt":
        request["receipt_id"] = "f" * 64
    elif change == "historical_use":
        with owner.transaction() as db:
            db.execute(
                "DELETE FROM curation_source_uses WHERE reference=?",
                (historical["training_operation_id"],),
            )
    elif change == "source_roster":
        with owner.transaction() as db:
            db.execute("DELETE FROM curation_occurrences WHERE id=?", (ids[0],))
    elif change == "Gold":
        with owner.transaction() as db:
            run = db.execute(
                "SELECT run FROM curation_occurrences WHERE id=?", (ids[0],)
            ).fetchone()[0]
            # Public ledger already refuses Gold after training; simulate an
            # externally changed current authority to regress the denial guard.
            db.execute("INSERT INTO curation_claims VALUES('fixture-Gold','gold',NULL,0)")
            db.execute("INSERT INTO curation_claim_runs VALUES('fixture-Gold',?)", (run,))
    elif change == "membership":
        view = store.get_manifest(historical["cached_view_id"])
        report = load_json_payload(store, view.payload("dispositions"))
        next(row for row in report["rows"] if row["occurrence"] == ids[0])["split"] = "dev"
        payload = store.put_payload(
            "dispositions", io.BytesIO(json_bytes(report)), "application/json"
        )
        altered = replace(view, payloads=(payload, view.payload("samples")))
        store.publish(altered)
        request["cached_view_id"] = altered.artifact_id
    else:
        view = store.get_manifest(historical["cached_view_id"])
        altered = replace(
            view,
            parameters=FrozenObject.of(
                {
                    **view.parameters.value(),
                    "serializer": {
                        "profile": "public_compact",
                        "version": "other",
                        "status": "provisional",
                    },
                }
            ),
        )
        store.publish(altered)
        request["cached_view_id"] = altered.artifact_id
    service = LocalDatasetService(config)
    try:
        service.start_reward_m0_preview(
            request["cached_view_id"],
            ids,
            request,
            RewardM0Config(steps=2, state_vocab_size=256).to_dict(),
            limits(),
        )
    except BoundaryError:
        assert service.thread is None
    else:
        service.thread.join(timeout=10)
        assert service.status()["operation"]["status"] == "failed"
    after = store.manifest_ids()
    assert not any(
        store.get_manifest(identity).parameters.value().get("schema")
        == ("stpd/reward-m0-paired-training-source-v1")
        for identity in set(after) - set(before)
    )


def test_duplicate_ids_nonTRAIN_membership_and_changed_receipt_fingerprint_reject(
    pair_originals, monkeypatch
):
    store, owner, _, historical, ids = pair_originals
    with pytest.raises(BoundaryError, match="unique_TRAIN_occurrences"):
        project_reward_m0_pair(
            store,
            historical["cached_view_id"],
            ids + [ids[0]],
            RewardM0Config(),
            limits(),
            historical,
        )
    allocation = store.get_manifest(historical["allocation_id"])
    dev = next(
        row["occurrence"]
        for row in load_json_payload(store, allocation.payload("members"))["members"]
        if row["split"] == "dev"
    )
    with pytest.raises(BoundaryError, match="original_TRAIN_membership"):
        project_reward_m0_pair(
            store, historical["cached_view_id"], [dev], RewardM0Config(), limits(), historical
        )
    monkeypatch.setattr(
        "spireagent.workbench.local_curation.training_projection_compatibility", lambda: "f" * 64
    )
    with pytest.raises(BoundaryError, match="verified_training_receipt_required"):
        require_historical_training(owner, store, historical)


def test_both_arms_are_fully_preflighted_without_truncating_B(pair_originals):
    from stpd.fullrun.light_action_inputs import fit_state_bpe
    from stpd.fullrun.reward_m0_inputs import _compile, samples_for
    from stpd.fullrun.reward_m0_source import VerifiedRewardM0Source, reward_m0_source_manifest

    value = project(pair_originals)
    source = VerifiedRewardM0Source(reward_m0_source_manifest(value, PRODUCER), value)
    config = RewardM0Config(**{**value["paired_config"], "arm": "A"})
    tokenizer = fit_state_bpe(samples_for(source, "A") + samples_for(source, "B"), vocab_size=256)
    _, compiled = _compile(source, tokenizer, config)
    lengths = compiled["length_stats"]
    assert lengths["B"]["max_state_tokens"] > lengths["A"]["max_state_tokens"]
    capped = RewardM0Config(
        **{**config.to_dict(), "max_state_tokens": lengths["A"]["max_state_tokens"]}
    )
    changed = copy.deepcopy(value)
    changed["paired_config"] = capped.paired_schedule()
    constrained = VerifiedRewardM0Source(reward_m0_source_manifest(changed, PRODUCER), changed)
    with pytest.raises(BoundaryError, match="complete_arm_state_limit_no_truncation"):
        _compile(constrained, tokenizer, capped)


@pytest.mark.parametrize("pair_originals", ["missing"], indirect=True)
def test_original_missing_B_Read_excludes_same_occurrences_from_both_arms(pair_originals):
    result = project(pair_originals)
    assert result["counts"] == {
        "requested_unique_TRAIN_N": 4,
        "paired_unique_TRAIN_N": 2,
        "excluded_from_both": 2,
    }
    assert all(
        row["excluded_arms"] == ["A", "B"]
        and row["reason"] == "matching_materialized_run_deck_missing"
        for row in result["exclusions"]
    )
    assert {row["occurrence_id"] for row in result["rows"]}.isdisjoint(
        row["occurrence_id"] for row in result["exclusions"]
    )
