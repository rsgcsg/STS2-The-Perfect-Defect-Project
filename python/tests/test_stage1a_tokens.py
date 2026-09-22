from dataclasses import replace

import pytest
from test_decision_training import prepared
from tokenizers import Tokenizer

from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation, publish_decision_view
from stpd.fullrun.features import ModelSample
from stpd.fullrun.representation import FullRunSerializer
from stpd.fullrun.token_inputs import (
    encode_texts,
    fit_scratch,
    input_texts,
    load_token_inputs,
    publish_token_inputs,
)


def sample(split, text):
    return ModelSample("transition", "run", split, "combat", "play", text, ("attack", "end"),
                       ("key1", "key2"), 0)


def test_train_only_tokenizer_and_unicode_fallback():
    train = sample("train", "enemy hp 12 action")
    raw = fit_scratch((train, sample("dev", "unique never seen phrase")))
    assert raw == fit_scratch((train, sample("dev", "completely different test text")))
    assert raw == fit_scratch((replace(train, chosen_index=1),))
    tokenizer = Tokenizer.from_str(raw.decode())
    text = "未知卡牌 🐉 αβ[UNK]\\\"\n"
    ids = tokenizer.encode(text, add_special_tokens=False).ids
    assert tokenizer.token_to_id("[UNK]") not in ids
    assert tokenizer.decode(ids) == text
    state, actions = input_texts(text, (text,))
    row = encode_texts(tokenizer, text, (text,))
    assert tokenizer.decode(list(row.state)) == state
    assert tokenizer.decode(list(row.actions[0])) == actions[0]
    with pytest.raises(BoundaryError, match="empty_train"):
        fit_scratch((sample("dev", text),))


def test_joint_limits_preserve_catalog_instead_of_truncating():
    tokenizer = Tokenizer.from_str(fit_scratch((sample("train", "x"),)).decode())
    with pytest.raises(BoundaryError, match="no_truncation"):
        encode_texts(tokenizer, "x", ("short", "\U0001f409" * 9000))
    # The old 8192 setting is a resource budget, not a data/model invariant.
    actions = ("short", "x " * 9000)
    row = encode_texts(tokenizer, "x", actions, max_tokens=32768)
    assert len(row.state) + len(row.actions[1]) + 1 > 8192
    assert len(row.actions) == 2
    with pytest.raises(BoundaryError, match="invalid_token_budget"):
        encode_texts(tokenizer, "x", actions, max_tokens=0)


def test_token_artifact_roundtrip_revalidates_membership_and_ids(tmp_path):
    owner, dataset = prepared(tmp_path)
    allocation = publish_allocation(
        owner.store, dataset, AllocationSpec(max_train=2, max_dev=1), owner.producer,
    )
    view = publish_decision_view(
        owner.store, allocation.artifact_id, FullRunSerializer("lite"), owner.producer,
    )
    item = publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer)
    loaded = load_token_inputs(owner.store, item.artifact_id)
    assert item.parent("model_view") == view.artifact_id
    assert len(loaded.rows) == len(loaded.samples) == 3
    assert {s.split for s in loaded.samples} == {"train", "dev"}
    assert all(len(r.actions) == len(s.action_keys)
               for r, s in zip(loaded.rows, loaded.samples, strict=True))
    assert publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer) == item
    forged = replace(item, parameters=FrozenObject.of({**item.parameters.value(), "samples": 99}))
    owner.store.publish(forged)
    with pytest.raises(BoundaryError, match="projection_mismatch"):
        load_token_inputs(owner.store, forged.artifact_id)
    with pytest.raises(BoundaryError, match="snapshot_only_for_pf"):
        publish_token_inputs(owner.store, view.artifact_id, "s", owner.producer, snapshot=tmp_path)
    tokenizer_text = loaded.tokenizer.to_str()
    tokenizer_path = tmp_path / "tokenizer.json"
    tokenizer_path.write_text(tokenizer_text, encoding="utf-8")
    assert tokenizer_path.read_bytes() == tokenizer_text.encode("utf-8")
    with pytest.raises(BoundaryError, match="pin_mismatch"):
        publish_token_inputs(owner.store, view.artifact_id, "pf", owner.producer, snapshot=tmp_path)
