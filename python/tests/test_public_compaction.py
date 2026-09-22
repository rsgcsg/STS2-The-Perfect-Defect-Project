import copy
import json

import pytest
from test_public_inputs import snapshot

from spireagent.json_boundary import BoundaryError
from stpd.canonical import canonical_json
from stpd.fullrun.public_compaction import REFERENCE, compact_public_state, expand_public_state
from stpd.fullrun.public_inputs import project_public_snapshot


def test_nested_repetitions_preserve_multiplicity_order_values_and_aliases():
    card = {"description": "gain block " * 100, "hp": 42, "flags": [True, None]}
    deck = [card, card, {**card, "hp": 43}]
    state = {"deck": deck, "another": copy.deepcopy(deck), "card": card}
    before = copy.deepcopy(state)
    compact = compact_public_state(state)
    assert state == before
    assert expand_public_state(compact) == before
    assert len(canonical_json(compact)) < len(canonical_json(before))
    assert compact_public_state(dict(reversed(list(state.items())))) == compact
    with pytest.raises(BoundaryError, match="reserved_compaction"):
        compact_public_state({"a": {REFERENCE: "f0"}})


def test_public_formats_differ_only_in_lossless_state_encoding_and_version():
    value = snapshot()
    old = project_public_snapshot(value)
    new = project_public_snapshot(value, compact=True)
    def decode(text):
        return json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert expand_public_state(decode(new.state_text)) == decode(old.state_text)
    assert tuple(map(decode, new.action_texts)) == tuple(map(decode, old.action_texts))
    assert new.actions == old.actions and new.candidate_digest == old.candidate_digest
    value["bound_actions"]["actions"].reverse()
    reordered = project_public_snapshot(value, compact=True)
    assert reordered.state_text == new.state_text
    assert reordered.action_texts == new.action_texts[::-1]
