"""Derived evaluation CPs: a CP's stored weight EMA, or the mean of CPs (`derive_checkpoint`)."""
from __future__ import annotations

import dataclasses

import pytest
import torch
from test_rl41_w1 import SOURCE_PRODUCER, assert_tree, configuration
from test_rl_segment_learner import read_cp

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import FrozenObject
from stpd.evaluation.rl_checkpoint import derive_checkpoint, export_checkpoint
from stpd.rl.checkpoint_state import SCHEMA, decode_state, encode_state, text_digest
from stpd.rl.segment_learner import SegmentLearner


def shifted(weights, by):
    return {k: v + by if v.is_floating_point() else v.clone() for k, v in weights.items()}


def publish(store, producer, cp, *, weights=None, ema=None, updates=None):
    weights = cp.weights if weights is None else weights
    changed = dataclasses.replace(
        cp, weights=weights, ema=cp.ema if ema is None else ema,
        construction=dataclasses.replace(cp.construction, text_digest=text_digest(weights)),
        counters=dataclasses.replace(cp.counters, updates=cp.counters.updates
                                     if updates is None else updates))
    manifest = Manifest("checkpoint", producer, payloads=(store.put_bytes(
        "checkpoint", encode_state(changed), "application/vnd.stpd.tensor-tree"),),
        parameters=FrozenObject.of({"schema": SCHEMA}))
    store.publish(manifest)
    return manifest.artifact_id


def read(store, artifact_id):
    manifest = store.get_manifest(artifact_id)
    payload = manifest.payload("checkpoint")
    return manifest, decode_state(store.bytes(payload, maximum=payload.size))


@pytest.fixture
def source(tmp_path, monkeypatch):
    config, io = configuration(tmp_path, monkeypatch, producer=SOURCE_PRODUCER)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.run(initialize_only=True)
    _, cp = read_cp(owner)
    yield owner.exchange.store, owner.producer, cp
    owner.close()


def test_ema_export_puts_the_stored_EMA_in_the_weights_under_its_own_identity(source, tmp_path):
    store, producer, cp = source
    ema = shifted(cp.weights, 0.25)  # distinct from theta, as after real updates
    original = publish(store, producer, cp, ema=ema)
    result = derive_checkpoint(store, [original], "ema")
    assert result["checkpoint_artifact_id"] != original
    manifest, derived = read(store, result["checkpoint_artifact_id"])
    assert_tree(derived.weights, ema)
    assert [p.artifact_id for p in manifest.parents] == [original]
    derivation = manifest.parameters.value()["weights_derivation"]
    assert derivation["method"] == "ema" and derivation["ema_decay"] == 0.999
    assert [s["checkpoint_artifact_id"] for s in derivation["sources"]] == [original]
    binding = export_checkpoint(store, result["checkpoint_artifact_id"], tmp_path / "eval")
    assert binding["weights"] == "ema" and binding["weights_derivation"] == derivation
    assert binding["checkpoint_artifact_id"] == result["checkpoint_artifact_id"]
    with pytest.raises(ValueError, match="derived_checkpoint_is_not_a_training_source"):
        derive_checkpoint(store, [result["checkpoint_artifact_id"]], "ema")


def test_mean_of_last_n_is_the_per_parameter_mean(source):
    store, producer, cp = source
    ids = [publish(store, producer, cp, weights=shifted(cp.weights, by), updates=n)
           for n, by in ((1, 1.0), (2, 3.0))]
    result = derive_checkpoint(store, ids, "mean_of_last_n")
    manifest, derived = read(store, result["checkpoint_artifact_id"])
    for name, value in derived.weights.items():
        expected = cp.weights[name] + 2.0 if value.is_floating_point() else cp.weights[name]
        assert torch.allclose(value, expected), name
    assert derived.counters.updates == 2
    assert [s["updates"] for s in manifest.parameters.value()["weights_derivation"]["sources"]] \
        == [1, 2]
    with pytest.raises(ValueError, match="oldest_first"):
        derive_checkpoint(store, ids[::-1], "mean_of_last_n")


def test_a_CP_without_EMA_is_refused_never_replaced_by_theta(source):
    store, producer, cp = source
    missing = publish(store, producer, cp, ema={})
    with pytest.raises(ValueError, match="checkpoint_has_no_weight_ema"):
        derive_checkpoint(store, [missing], "ema")
    partial = publish(store, producer, cp, ema=dict(list(shifted(cp.weights, 0.5).items())[1:]))
    with pytest.raises(ValueError, match="checkpoint_ema_does_not_cover_weights"):
        derive_checkpoint(store, [partial], "ema")
