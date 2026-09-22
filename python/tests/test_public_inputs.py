"""Online-input contract tests; no native or Human qualification."""
import copy
from dataclasses import replace

import pytest
from test_policy_adapter import _snapshot

from spireagent.json_boundary import BoundaryError
from stpd.fullrun.public_inputs import match_recorded_choice, project_public_snapshot


def snapshot():
    value = _snapshot()
    value["schema"] = "sts2.player-environment/snapshot-1"
    value["information_policy"]["includes_hidden_information"] = False
    value["interaction"]["interaction_id"] = "interaction-1"
    for action in value["bound_actions"]["actions"]:
        action["interaction_id"] = "interaction-1"
    return value


def test_public_projection_preserves_order_but_keeps_binding_ids_out_of_text():
    value = snapshot()
    first = project_public_snapshot(value)
    text = first.state_text + str(first.action_texts)
    assert all(x not in text for x in ("card-runtime", "candidate-play", "interaction-1"))
    assert "Gain 5 Block" in text and "execution" not in text
    value["bound_actions"]["actions"].reverse()
    second = project_public_snapshot(value)
    assert second.state_text == first.state_text
    assert second.action_texts == first.action_texts[::-1]
    assert second.candidate_digest != first.candidate_digest
    assert match_recorded_choice(second, first.actions, first.actions[0].key) == 1
    renamed = copy.deepcopy(snapshot())
    renamed["referents"][0]["referent_id"] = "fresh-card-id"
    renamed["bound_actions"]["actions"][0]["subject_referent_id"] = "fresh-card-id"
    assert project_public_snapshot(renamed).action_texts == first.action_texts


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s.update(status="settling"), "interactive_complete"),
    (lambda s: s["bound_actions"].update(total_count=3), "complete_catalog"),
    (lambda s: s["bound_actions"]["actions"][0].update(subject_referent_id="missing"), "binding"),
    (lambda s: s["bound_actions"]["actions"][0].update(interaction_id="stale"), "binding"),
    (lambda s: s["information_policy"].update(includes_hidden_information=True), "public_snapshot"),
    (lambda s: s["interaction"]["content"].update(hidden_rng=7), "forbidden"),
    (lambda s: s["referents"].append(copy.deepcopy(s["referents"][0])), "unique"),
])
def test_invalid_public_input_cannot_become_partial_scoring(mutation, code):
    value = snapshot()
    mutation(value)
    with pytest.raises(BoundaryError, match=code):
        project_public_snapshot(value)


def test_choice_mapping_refuses_changed_catalog_native_verb_and_ambiguous_duplicates():
    value = snapshot()
    public = project_public_snapshot(value)
    with pytest.raises(BoundaryError, match="catalog_semantics"):
        match_recorded_choice(public, public.actions[:1], public.actions[0].key)
    with pytest.raises(BoundaryError, match="catalog_semantics"):
        match_recorded_choice(public, (replace(public.actions[0], kind="native_play"),
                                       public.actions[1]), public.actions[0].key)
    duplicate = copy.deepcopy(value["bound_actions"]["actions"][0])
    duplicate["bound_action_id"] = "other-binding"
    value["bound_actions"]["actions"][1] = duplicate
    ambiguous = project_public_snapshot(value)
    with pytest.raises(BoundaryError, match="ambiguous"):
        match_recorded_choice(ambiguous, ambiguous.actions, ambiguous.actions[0].key)


def test_null_control_properties_are_supported():
    value = snapshot()
    value["referents"][0]["properties"] = None
    assert len(project_public_snapshot(value).actions) == 2


def test_audit_accounts_for_every_allocated_occurrence_without_publishing(tmp_path):
    from test_decision_training import prepared

    from stpd.fullrun.decision_training import AllocationSpec, publish_allocation
    from stpd.fullrun.live_input_audit import audit_allocation

    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(owner.store, dataset, AllocationSpec(max_train=2, max_dev=1),
                                    owner.producer)
    before = owner.store.manifest_ids()
    report = audit_allocation(owner.store, allocation.artifact_id)
    assert len(report["rows"]) == sum(report["counts"].values()) == 3
    assert len({r["occurrence"] for r in report["rows"]}) == 3
    assert sum(report["by_split"]["train"].values()) == 2
    assert sum(report["by_split"]["dev"].values()) == 1
    assert owner.store.manifest_ids() == before
    assert report["native_runtime"] == "not_tested"
