"""Synthetic text-menu projection and scoring; no native/Human qualification."""

import copy
import hashlib

import pytest

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from stpd.fullrun.text_menu_inputs import (
    IDENTITY,
    SNAPSHOT_SCHEMA,
    classify_text_menu_result,
    project_text_menu_snapshot,
)
from stpd.policy.token_decision import TokenDecisionScorer
from stpd.policy.token_port import TokenPolicyAdapter


def snapshot():
    return {
        "protocol_version": "1.0.0", "schema": SNAPSHOT_SCHEMA,
        "input_profile": "text-menu-v1", "snapshot_id": "opaque-snapshot-1",
        "sequence": 1, "observed_at": "2026-09-26T00:00:00Z", "status": "interactive",
        "persistent": {"content": {"run": {"floor": 3}, "player": {"hp": 42}}},
        "interaction": {"kind": "combat_turn", "interaction_id": "opaque-interaction-1",
                        "content": {"phase": "player", "cards": [
                            {"entity_id": "opaque-card-1", "name": "Defend",
                             "description": "Gain 5 Block."},
                            {"entity_id": "opaque-card-2", "name": "Defend",
                             "description": "Gain 5 Block."}]}},
        "referents": [
            {"referent_id": "opaque-card-1", "role": "hand_card", "kind": "entity",
             "label": "Defend", "state": {"visible": True},
             "properties": {"name": "Defend", "description": "Gain 5 Block."}},
            {"referent_id": "opaque-card-2", "role": "hand_card", "kind": "entity",
             "label": "Defend", "state": {"visible": True},
             "properties": {"name": "Defend", "description": "Gain 5 Block."}},
        ],
        "completeness": {"status": "complete"},
        "information_policy": {"includes_hidden_information": False},
        "session": {},
        "menu": {"cursor": "root", "revision": 1,
                 "native_snapshot_id": "opaque-native-snapshot-1"},
        "menu_actions": {"status": "complete", "materialized_count": 3,
                         "total_count": 3, "ordering_semantics": "native_order",
                         "actions": [
            {"action_id": "opaque-nav", "kind": "system_navigation",
             "verb": "open_information", "label": "Information",
             "subject_referent_id": None, "arguments": [], "effect_domain": "text_menu"},
            {"action_id": "opaque-play-1", "kind": "native_input", "verb": "play",
             "label": "Play Defend", "subject_referent_id": "opaque-card-1",
             "arguments": [], "effect_domain": "native_input"},
            {"action_id": "opaque-play-2", "kind": "native_input", "verb": "play",
             "label": "Play Defend", "subject_referent_id": "opaque-card-2",
             "arguments": [], "effect_domain": "native_input"},
        ]},
    }


def test_current_page_full_text_and_exact_binding_order():
    source = snapshot()
    first = project_text_menu_snapshot(source)
    assert first.action_ids == ("opaque-nav", "opaque-play-1", "opaque-play-2")
    assert first.action_kinds == ("system_navigation", "native_input", "native_input")
    assert first.state_text.count("Gain 5 Block.") >= 2
    assert '"CURRENT_MENU":{"cursor":"root"}' in first.state_text
    assert "open_information" in first.action_texts[0]
    assert "opaque-" not in first.state_text + "".join(first.action_texts)
    assert first.action_texts[1] != first.action_texts[2]  # Native occurrence ordinal.
    reordered = copy.deepcopy(source)
    reordered["menu_actions"]["actions"].reverse()
    second = project_text_menu_snapshot(reordered)
    assert second.action_ids == first.action_ids[::-1]
    assert second.action_texts != first.action_texts
    assert second.candidate_digest != first.candidate_digest
    assert second.scores_in_catalog_order((3.0, 2.0, 1.0)) == (3.0, 2.0, 1.0)


@pytest.mark.parametrize("change", [
    lambda s: s["menu_actions"].update(total_count=4),
    lambda s: s["menu_actions"]["actions"].pop(),
    lambda s: s["menu_actions"]["actions"][0].update(effect_domain="native_input"),
    lambda s: s["menu_actions"]["actions"][1].update(subject_referent_id="missing"),
    lambda s: s["menu_actions"]["actions"][1].update(action_id="opaque-nav"),
    lambda s: s["information_policy"].update(includes_hidden_information=True),
    lambda s: s.update(bound_actions={}),
    lambda s: s["menu"].update(cursor="hidden_future"),
    lambda s: s["interaction"]["content"].update(hidden_rng=7),
])
def test_incomplete_or_cross_bound_menu_fails_as_whole(change):
    source = snapshot()
    change(source)
    with pytest.raises(BoundaryError):
        project_text_menu_snapshot(source)


class _Artifact:
    @property
    def parameters(self):
        return self

    def value(self):
        return {"serializer": IDENTITY}


def test_scoring_uses_same_four_recipe_text_route_and_port_catalog_order():
    from stpd.models.stage1a import recipe_for

    assert {recipe_for(f"stage1a.{family}.{backbone}.v1").family
            for family in ("b", "dsimple") for backbone in ("s", "pf")} == {"b", "dsimple"}
    scorer = object.__new__(TokenDecisionScorer)
    scorer.serializer, scorer.artifact = None, _Artifact()
    seen = []

    def score(state, actions):
        seen.append((state, actions))
        return (0.1, 0.8, 0.3)

    scorer.score_texts = score
    source = snapshot()
    assert scorer.score_snapshot(source) == {
        "opaque-nav": 0.1, "opaque-play-1": 0.8, "opaque-play-2": 0.3,
    }
    assert seen[0] == (project_text_menu_snapshot(source).state_text,
                       project_text_menu_snapshot(source).action_texts)
    adapter = object.__new__(TokenPolicyAdapter)
    adapter.scorer, adapter.closed = scorer, False
    adapter.manifest = {"representation": {"input_schema": SNAPSHOT_SCHEMA},
                        "support": {"interaction_kinds": ["combat_turn"],
                                    "action_verbs": ["open_information", "play"]}}
    keys = [a["action_id"] for a in source["menu_actions"]["actions"]]
    request = {"run_id": "test-run", "manifest": adapter.manifest,
               "bundle": {"observation": source, "reads": []},
               "candidate_count": len(keys),
               "candidate_digest": hashlib.sha256(canonical_json(keys).encode()).hexdigest()}
    assert adapter.decide(request) == {
        "candidate_digest": request["candidate_digest"],
        "scores": [0.1, 0.8, 0.3], "selected_index": 1,
    }
    source["menu_actions"]["actions"].reverse()
    with pytest.raises(BoundaryError, match="candidate_binding_mismatch"):
        adapter.decide(request)


def test_small_b_shared_observation_scores_every_text_menu_action_in_order():
    import torch

    from stpd.models.stage1a import build_scorer
    from stpd.models.token_core import ScratchShape, ScratchTokenCore

    current = project_text_menu_snapshot(snapshot())
    shape = ScratchShape(vocab_size=256, width=16, layers=1, heads=2,
                         feedforward=32, dropout=0.0, max_tokens=4096)
    model = build_scorer("stage1a.b.s.v2", ScratchTokenCore(shape)).eval()
    state = torch.tensor(list(current.state_text.encode("utf-8")), dtype=torch.long)
    actions = tuple(torch.tensor(list(text.encode("utf-8")), dtype=torch.long)
                    for text in current.action_texts)
    with torch.no_grad():
        values = model(state, actions)
        reversed_values = model(state, actions[::-1])
    assert values.shape == (3,) and torch.isfinite(values).all()
    torch.testing.assert_close(values, reversed_values.flip(0))
    assert current.scores_in_catalog_order(values.tolist()) == tuple(values.tolist())


def test_navigation_is_never_counted_as_native_delivery():
    base = {"schema": "sts2.player-environment/text-menu-action-result-1",
            "status": "applied", "effect_domain": "text_menu", "native_delivery": None,
            "action": snapshot()["menu_actions"]["actions"][0], "retry": "never"}
    assert classify_text_menu_result(base) == "system_navigation"
    assert classify_text_menu_result({**base, "effect_domain": "native_input",
                                      "native_delivery": "delivered",
                                      "action": snapshot()["menu_actions"]["actions"][1]}) == (
                                          "native_input_delivered_unsettled")
    assert classify_text_menu_result({**base, "status": "unknown", "effect_domain": "native_input",
                                      "native_delivery": "unknown",
                                      "action": snapshot()["menu_actions"]["actions"][1]}) == (
                                          "unknown_native_delivery")
    assert classify_text_menu_result({**base, "status": "not_applied",
                                      "successor": snapshot()}) == "not_applied"
    for mutation in ({"status": "unknown"}, {"status": "unknown", "effect_domain": None},
                     {"status": "unknown", "effect_domain": "native_input",
                      "native_delivery": "unknown", "retry": "reobserve"}):
        with pytest.raises(BoundaryError, match="result_domain_mismatch"):
            classify_text_menu_result({**base, **mutation})
    with pytest.raises(BoundaryError, match="result_domain_mismatch"):
        classify_text_menu_result({**base, "native_delivery": "delivered"})


def test_full_menu_is_rejected_if_shared_token_budget_cannot_hold_it():
    from stpd.fullrun.token_inputs import encode_texts

    class _Encoding:
        def __init__(self, text):
            self.ids = list(text.encode("utf-8"))

    class _Tokenizer:
        def encode(self, text, add_special_tokens=False):
            assert not add_special_tokens
            return _Encoding(text)

    current = project_text_menu_snapshot(snapshot())
    with pytest.raises(BoundaryError, match="joint_limit_exceeded_no_truncation"):
        encode_texts(_Tokenizer(), current.state_text, current.action_texts, max_tokens=20)


def test_null_persistent_and_visible_referents_and_public_label_collision():
    source = snapshot()
    source["persistent"] = None
    source["referents"][1]["state"]["visible"] = False
    source["menu_actions"]["actions"].pop()
    source["menu_actions"].update(total_count=2, materialized_count=2)
    source["menu_actions"]["actions"][0]["label"] = "opaque-nav"
    current = project_text_menu_snapshot(source)
    assert '"CURRENT_PERSISTENT":null' in current.state_text
    assert current.state_text.count('"role":"hand_card"') == 1
    assert '"display_text":"opaque-nav"' in current.action_texts[0]
    source["menu_actions"]["actions"][1]["subject_referent_id"] = "opaque-card-2"
    with pytest.raises(BoundaryError, match="subject_binding_mismatch"):
        project_text_menu_snapshot(source)


@pytest.mark.parametrize("revision", [-1, "1", True])
def test_menu_revision_is_nonnegative_integer(revision):
    source = snapshot()
    source["menu"]["revision"] = revision
    with pytest.raises(BoundaryError, match="current_cursor_required"):
        project_text_menu_snapshot(source)
