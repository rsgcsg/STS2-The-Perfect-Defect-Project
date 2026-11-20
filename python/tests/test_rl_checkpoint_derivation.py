"""Derived evaluation CPs: a CP's stored weight EMA, or the mean of CPs (`derive_checkpoint`)."""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from test_rl41_w1 import SOURCE_PRODUCER, assert_tree, configuration
from test_rl_segment_learner import read_cp

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import FrozenObject
from stpd.evaluation.rl_checkpoint import derive_checkpoint, export_checkpoint
from stpd.rl.checkpoint_state import SCHEMA, decode_state, encode_state, text_digest
from stpd.rl.segment_learner import SegmentLearner


DERIVED_BY = {"repository": "rsgcsg/STS2-The-Perfect-Defect-Project",
              "source_revision": "f" * 40, "lock_sha256": "e" * 64}


def shifted(weights, by):
    return {k: v + by if v.is_floating_point() else v.clone() for k, v in weights.items()}


def publish(store, producer, cp, *, weights=None, ema=None, updates=None, run=None):
    weights = cp.weights if weights is None else weights
    changed = dataclasses.replace(
        cp, weights=weights, ema=cp.ema if ema is None else ema,
        identity=cp.identity if run is None else dataclasses.replace(cp.identity, run=run),
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
    yield SimpleNamespace(store=owner.exchange.store, producer=owner.producer, cp=cp,
                          owner=owner, config=config, io=io)
    owner.close()


def test_ema_export_puts_the_stored_EMA_in_the_weights_under_its_own_identity(source, tmp_path):
    store, producer, cp = source.store, source.producer, source.cp
    ema = shifted(cp.weights, 0.25)  # distinct from theta, as after real updates
    original = publish(store, producer, cp, ema=ema)
    result = derive_checkpoint(store, [original], "ema", derived_by=DERIVED_BY)
    assert result["checkpoint_artifact_id"] != original
    manifest, derived = read(store, result["checkpoint_artifact_id"])
    assert_tree(derived.weights, ema)
    assert [p.artifact_id for p in manifest.parents] == [original]
    derivation = manifest.parameters.value()["weights_derivation"]
    assert derivation["method"] == "ema" and derivation["ema_decay"] == 0.999
    assert derivation["derived_by"] == DERIVED_BY
    assert [s["checkpoint_artifact_id"] for s in derivation["sources"]] == [original]
    binding = export_checkpoint(store, result["checkpoint_artifact_id"], tmp_path / "eval")
    assert binding["weights"] == "ema" and binding["weights_derivation"] == derivation
    assert binding["checkpoint_artifact_id"] == result["checkpoint_artifact_id"]
    with pytest.raises(ValueError, match="derived_checkpoint_is_not_a_training_source"):
        derive_checkpoint(store, [result["checkpoint_artifact_id"]], "ema",
                          derived_by=DERIVED_BY)


def test_mean_of_last_n_is_the_per_parameter_mean(source):
    store, producer, cp = source.store, source.producer, source.cp
    ids = [publish(store, producer, cp, weights=shifted(cp.weights, by), updates=n)
           for n, by in ((1, 1.0), (2, 3.0))]
    result = derive_checkpoint(store, ids, "mean_of_last_n", derived_by=DERIVED_BY)
    manifest, derived = read(store, result["checkpoint_artifact_id"])
    for name, value in derived.weights.items():
        expected = cp.weights[name] + 2.0 if value.is_floating_point() else cp.weights[name]
        assert torch.allclose(value, expected), name
    assert derived.counters.updates == 2
    assert [s["updates"] for s in manifest.parameters.value()["weights_derivation"]["sources"]] \
        == [1, 2]
    with pytest.raises(ValueError, match="oldest_first"):
        derive_checkpoint(store, ids[::-1], "mean_of_last_n", derived_by=DERIVED_BY)


def test_a_CP_without_EMA_is_refused_never_replaced_by_theta(source):
    store, producer, cp = source.store, source.producer, source.cp
    missing = publish(store, producer, cp, ema={})
    with pytest.raises(ValueError, match="checkpoint_has_no_weight_ema"):
        derive_checkpoint(store, [missing], "ema", derived_by=DERIVED_BY)
    partial = publish(store, producer, cp, ema=dict(list(shifted(cp.weights, 0.5).items())[1:]))
    with pytest.raises(ValueError, match="checkpoint_ema_does_not_cover_weights"):
        derive_checkpoint(store, [partial], "ema", derived_by=DERIVED_BY)


def test_source_identity_count_and_run_are_checked_before_anything_is_published(source):
    store, producer, cp = source.store, source.producer, source.cp
    first = publish(store, producer, cp, weights=shifted(cp.weights, 1.0), updates=1)
    second = publish(store, producer, cp, weights=shifted(cp.weights, 2.0), updates=2)
    with pytest.raises(TypeError):
        derive_checkpoint(store, [first], "ema")  # derived_by is required
    with pytest.raises(ValueError, match="derivation_source_identity_required"):
        derive_checkpoint(store, [first], "ema", derived_by={})
    with pytest.raises(ValueError, match="ema_derivation_requires_exactly_one_checkpoint"):
        derive_checkpoint(store, [first, second], "ema", derived_by=DERIVED_BY)
    with pytest.raises(ValueError, match="mean_of_last_n_requires_at_least_two_checkpoints"):
        derive_checkpoint(store, [first], "mean_of_last_n", derived_by=DERIVED_BY)
    other = publish(store, producer, cp, weights=shifted(cp.weights, 3.0), updates=3,
                    run="another-run")
    with pytest.raises(ValueError, match="sources_differ_in_run_or_construction"):
        derive_checkpoint(store, [first, other], "mean_of_last_n", derived_by=DERIVED_BY)


def test_a_derived_CP_is_refused_as_a_resume_point(source, tmp_path):
    original = publish(source.store, source.producer, source.cp,
                       ema=shifted(source.cp.weights, 0.25))
    derived = derive_checkpoint(source.store, [original], "ema",
                                derived_by=DERIVED_BY)["checkpoint_artifact_id"]
    request = dataclasses.replace(
        source.config, start_package=None, resume_checkpoint=derived,
        segments=dataclasses.replace(source.config.segments, training_generation="g-resume"))
    with pytest.raises(ValueError, match="self_contained_checkpoint_has_no_restore_parents"):
        SegmentLearner(request, tmp_path / "resume", source.io)


def friday_evaluate():
    friday = Path(__file__).resolve().parents[2] / "tools/friday"

    def load(name, file):
        spec = importlib.util.spec_from_file_location(name, friday / file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    with patch.dict(sys.modules, {"batch": load("derive_batch", "batch.py")}):
        with patch.dict(sys.modules, {"submit": load("derive_submit", "submit.py")}):
            return load("derive_evaluate", "evaluate.py")


def test_last_selects_the_newest_records_of_one_generation_oldest_first(source):
    pytest.importorskip("fcntl", reason="Friday evaluation targets POSIX Slurm")
    evaluate = friday_evaluate()
    store, producer, cp, owner = source.store, source.producer, source.cp, source.owner
    ids = [publish(store, producer, cp, weights=shifted(cp.weights, by), updates=n)
           for n, by in ((1, 1.0), (2, 2.0))]
    for sequence, artifact_id in enumerate(ids, start=2):
        owner.exchange.publish_checkpoint(sequence, artifact_id, sequence=sequence,
                                          training_generation=owner.generation)
    peer, run = source.config.peer, source.config.run
    assert evaluate.last_checkpoints(peer, run, 2, owner.generation) == ids
    with pytest.raises(ValueError, match="last_checkpoint_count_must_be_2_to_available"):
        evaluate.last_checkpoints(peer, run, 1, owner.generation)
    with pytest.raises(ValueError, match="last_checkpoint_count_must_be_2_to_available"):
        evaluate.last_checkpoints(peer, run, 2, "g-none")
