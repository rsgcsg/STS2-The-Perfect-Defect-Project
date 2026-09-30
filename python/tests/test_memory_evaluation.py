"""Synthetic held-out M2 evaluation: lineage, leakage and frozen inference."""

from __future__ import annotations

import hashlib
import io
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import torch
from test_memory_sequence_bridge import observed
from tokenizers import Tokenizer

from spireagent import research_cli
from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_evaluation import summary
from stpd.fullrun.confirmed_interaction import (
    HISTORY_INPUT_PROFILE,
    V2_HISTORY_INPUT_PROFILE,
    confirmed_action_text,
)
from stpd.fullrun.memory_projection_config import (
    RENDERER_IDENTITY,
    history_episode_projection_config,
    v2_episode_projection_config,
)
from stpd.fullrun.memory_sequence_bridge import (
    MemoryEpisodeProjectionConfig,
    project_memory_episodes,
)
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import ObservedInputView
from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.token_core import ScratchShape, ScratchTokenCore
from stpd.workers.memory_evaluation import _evaluation_projection_config, evaluate_memory
from stpd.workers.memory_ranking import MemoryConfig, MemoryTrainingInput
from stpd.workers.memory_run import execute_memory_run, prepare_memory_run

PRODUCER = Producer("synthetic-memory-evaluation", "a" * 40, "b" * 64)


@pytest.fixture(autouse=True)
def threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def _view(source_id: str, session: str, *events: str) -> ObservedInputView:
    items = tuple(replace(
        observed(event, index + 1, reset=index == 0,
                 stream=f"human:{session}:timeline:run"),
        event_id=f"{session}:{event}",
    ) for index, event in enumerate(events))
    return ObservedInputView(source_id, "partial_human_input_stream", False, items)


def _history_view(source_id: str, session: str) -> ObservedInputView:
    stream = f"human:{session}:timeline:run"
    rows = (
        replace(observed("a", 1, stream=stream, reset=True, action="opaque-play"),
                event_id=f"{session}:a", capture_ordinal=1, physical_sequence=4,
                completed_append_watermark=0, confirmed_at_sequence=4,
                confirmed_effect_domain="native_input"),
        replace(observed("b", 2, stream=stream, action="opaque-nav"),
                event_id=f"{session}:b", capture_ordinal=2, physical_sequence=3,
                completed_append_watermark=0, confirmed_at_sequence=3,
                confirmed_effect_domain="text_menu"),
        replace(observed("c", 3, stream=stream, action="opaque-play"),
                event_id=f"{session}:c", capture_ordinal=3, physical_sequence=5,
                completed_append_watermark=3),
    )
    return ObservedInputView(source_id, "partial_human_input_stream", False, rows)


def prepared(tmp_path: Path, monkeypatch, *, reset_each_step: bool = False,
             train_events: tuple[str, ...] = ("train-cue", "train-choice"),
             dev_events: tuple[str, ...] = ("dev-cue", "dev-choice"),
             operation_id: str | None = None, history: bool = False):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "objects"))
    evidence_a = Manifest("evidence", PRODUCER)
    evidence_b = Manifest("evidence", replace(PRODUCER, source_revision="c" * 40))
    store.publish(evidence_a)
    store.publish(evidence_b)
    train_source = Manifest("dataset", PRODUCER, (Parent("evidence", evidence_a.artifact_id),))
    dev_source = Manifest("dataset", PRODUCER, (Parent("evidence", evidence_b.artifact_id),))
    store.publish(train_source)
    store.publish(dev_source)
    train_view = (_history_view(train_source.artifact_id, "train-session") if history else
                  _view(train_source.artifact_id, "train-session", *train_events))
    dev_view = (_history_view(dev_source.artifact_id, "dev-session") if history else
                _view(dev_source.artifact_id, "dev-session", *dev_events))
    views = {train_source.artifact_id: train_view, dev_source.artifact_id: dev_view}
    monkeypatch.setattr("stpd.workers.memory_evaluation.load_observed_input_view",
                        lambda _store, source_id: views[source_id])
    monkeypatch.setattr("stpd.fullrun.observed_input_sequence.load_observed_input_view",
                        lambda _store, source_id: views[source_id])
    settling = 64 if history else 0
    projection_config = (history_episode_projection_config(HISTORY_INPUT_PROFILE) if history
                         else MemoryEpisodeProjectionConfig(
                             "stpd/memory-episode-projection-config-v1", 0))
    tokens, count = fit_observed_memory_tokenizer(
        train_view, max_settling_events=settling,
        input_profile=HISTORY_INPUT_PROFILE if history else "text-menu-v1")
    fitted = Tokenizer.from_str(tokens.decode())
    config = MemoryConfig(vocab_size=fitted.get_vocab_size(), episode_count=count,
                          reset_each_step=reset_each_step,
                          max_episode_input_tokens=2048, max_total_input_tokens=2048,
                          max_chunk_steps=2)
    network = ExperimentalDSimpleM2(
        ScratchTokenCore(ScratchShape(config.vocab_size, 8, 1, 2, 16, 0.0, 256)),
        reset_each_step=reset_each_step,
    )
    projection = project_memory_episodes(
        train_view, fitted, network, max_observations=16, max_input_tokens=2048,
        max_settling_events=settling, projection_config=projection_config)
    assert not projection.diagnostics
    run = prepare_memory_run(
        store, MemoryTrainingInput(train_source.artifact_id, hashlib.sha256(tokens).hexdigest(),
                                   projection.episodes), config, PRODUCER, tokens,
        source_mapping=projection.event_mapping,
        projection_config=projection_config,
        operation_id=operation_id,
    )
    reporter = ObjectStoreRunReporter(store, store.blobs)
    result = execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    model_id = store.get_manifest(result.result_id).parent("model")
    return store, model_id, train_source, dev_source, views, config, run


def test_evaluate_memory_cli_selects_exported_cpu_threads(tmp_path, monkeypatch, capsys):
    store, model_id, _, dev, _, config, _ = prepared(tmp_path, monkeypatch)
    assert config.cpu_threads == 2
    monkeypatch.setattr(research_cli, "open_store", lambda _path: store)
    monkeypatch.setattr(research_cli, "source_identity", lambda _path: PRODUCER)
    seen = []

    def fake_evaluate(_store, _model, _source, _producer, **_options):
        seen.append(torch.get_num_threads())
        return SimpleNamespace(artifact_id="a" * 64,
                               parent=lambda _role: "b" * 64)

    monkeypatch.setattr("stpd.workers.memory_evaluation.evaluate_memory", fake_evaluate)
    monkeypatch.setattr(sys, "argv", ["research_cli", "--store", str(tmp_path),
                                      "evaluate-memory", "--model", model_id,
                                      "--source", dev.artifact_id,
                                      "--operation", "c" * 32,
                                      "--semantic-overlap", "false"])
    torch.set_num_threads(1)
    assert research_cli.main() == 0
    assert seen == [config.cpu_threads]
    assert '"evaluation_id"' in capsys.readouterr().out


def test_dev_report_uses_separate_source_and_preserves_frozen_weights(tmp_path, monkeypatch):
    store, model_id, _, dev, _, _, run = prepared(tmp_path, monkeypatch)
    before = store.get_manifest(model_id).payload("weights").sha256
    with patch("torch.optim.AdamW", side_effect=AssertionError("optimizer created")):
        report = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert store.get_manifest(model_id).payload("weights").sha256 == before
    assert store.get_manifest(run.artifact_id).parameters.value()["partition"] == "train"
    payload = decode_json(b"".join(store.read_payload(report.payload("metrics"))))
    assert len(payload["rows"]) == 2
    assert all(row["candidate_count"] == 2 and row["split"] == "dev"
               for row in payload["rows"])
    assert payload["summary"]["bootstrap"]["status"] == "unknown"
    legacy_input = store.get_manifest(report.parent("evaluation_input"))
    legacy_projection = decode_json(b"".join(store.read_payload(
        legacy_input.payload("projection"))))["projection"]
    assert legacy_projection == {"renderer": RENDERER_IDENTITY,
                                 "max_settling_events": 0}
    public = summary(store, report.artifact_id)
    assert public["decision_count"] == 2
    assert public["native_run_independence"] is False
    assert public["strict_deduplicated_benchmark"] is False
    assert public["protocol"] == "independent-source-retrospective-v1"


def test_human_history_dev_replays_exact_train_profile_without_future_label(
    tmp_path, monkeypatch,
):
    store, model_id, _, dev, views, _, _ = prepared(tmp_path, monkeypatch, history=True)
    before = store.get_manifest(model_id).payload("weights").sha256
    previous = []
    original = ExperimentalDSimpleM2.advance

    def watched(self, page, memory, **kwargs):
        previous.append(kwargs["previous_actual_action"])
        assert kwargs["feedback"] is None
        return original(self, page, memory, **kwargs)

    with patch.object(ExperimentalDSimpleM2, "advance", watched), patch(
        "torch.optim.AdamW", side_effect=AssertionError("optimizer created")
    ):
        report = evaluate_memory(
            store, model_id, dev.artifact_id, PRODUCER, max_settling_events=64)
    assert store.get_manifest(model_id).payload("weights").sha256 == before
    assert len(previous) == 3 and previous[0] is None and previous[1] is None
    assert previous[2] is not None  # B completed before C; A completed later.
    prior = views[dev.artifact_id].inputs[1]
    tokenizer_bytes = b"".join(store.read_payload(
        store.get_manifest(model_id).payload("tokenizer")))
    tokenizer = Tokenizer.from_str(tokenizer_bytes.decode())
    expected = confirmed_action_text(
        prior.snapshot, prior.selected_action_id,
        effect_domain="text_menu", basis="last_known_human_input_witness",
        profile=HISTORY_INPUT_PROFILE)
    assert previous[2].tolist() == tokenizer.encode(expected).ids
    evaluation_input = store.get_manifest(report.parent("evaluation_input"))
    projection = decode_json(b"".join(store.read_payload(
        evaluation_input.payload("projection"))))
    assert projection["projection"]["renderer"]["id"] == \
        "stpd/m2-confirmed-interaction-v1"
    assert projection["projection"]["max_settling_events"] == 64
    assert projection["projection"]["input_profile"] == HISTORY_INPUT_PROFILE


def test_human_history_overlap_includes_prior_action_but_not_current_label(
    tmp_path, monkeypatch,
):
    store, model_id, _, dev, views, _, _ = prepared(tmp_path, monkeypatch, history=True)
    original = views[dev.artifact_id]
    rows = list(original.inputs)
    rows[1] = replace(rows[1], selected_action_id="opaque-play",
                      confirmed_effect_domain="native_input")
    views[dev.artifact_id] = replace(original, inputs=tuple(rows))
    report = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert report.parameters.value()["train_dev_rendered_overlap_count"] == 2
    assert store.get_manifest(report.parent("evaluation_input")).parameters.value()[
        "train_dev_rendered_overlap_count"] == 2


def test_human_history_dev_rejects_explicit_settling_mismatch(tmp_path, monkeypatch):
    store, model_id, _, dev, _, _, _ = prepared(tmp_path, monkeypatch, history=True)
    with pytest.raises(BoundaryError, match="dev_projection_config_mismatch"):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER,
                        max_settling_events=0)


@pytest.mark.parametrize("projection", [
    v2_episode_projection_config(),
    history_episode_projection_config(V2_HISTORY_INPUT_PROFILE),
])
def test_dev_evaluator_rejects_managed_or_agent_train_profile(
    tmp_path, monkeypatch, projection,
):
    store, model_id, _, _, _, _, _ = prepared(tmp_path, monkeypatch)
    model = store.get_manifest(model_id)
    training = store.get_manifest(model.parent("training_input"))
    parameters = {**training.parameters.value(), "projection_config": asdict(projection)}
    altered = Manifest(training.kind, training.producer, training.parents,
                       training.payloads, FrozenObject.of(parameters))
    with pytest.raises(BoundaryError, match="human_train_projection_required"):
        _evaluation_projection_config(altered)


@pytest.mark.parametrize(("scope", "kind", "stream"), [
    ("verified_agent_observed_inputs", "agent_decision_inputs", "agent:content:run"),
    ("managed_engineering_control_inputs", "managed_control_input_stream", "managed:run"),
])
def test_human_history_dev_rejects_other_source_kinds(
    tmp_path, monkeypatch, scope, kind, stream,
):
    store, model_id, _, dev, views, _, _ = prepared(tmp_path, monkeypatch, history=True)
    original = views[dev.artifact_id]
    views[dev.artifact_id] = replace(
        original, stream_scope=scope,
        inputs=tuple(replace(item, source_kind=kind, stream_id=stream)
                     for item in original.inputs))
    with pytest.raises(BoundaryError, match="human_dev_source_required"):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)


def test_same_source_evidence_session_rejected_but_natural_page_repeat_counted(
    tmp_path, monkeypatch,
):
    store, model_id, train, dev, views, _, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(BoundaryError, match="train_dev_source_or_evidence_overlap"):
        evaluate_memory(store, model_id, train.artifact_id, PRODUCER)
    shared = Manifest(
        "dataset", PRODUCER, (Parent("evidence", train.parent("evidence")),),
        parameters=FrozenObject.of({"purpose": "bc_input_observation"}),
    )
    # Distinct source manifest over the same evidence remains exposed to training.
    assert shared.artifact_id != train.artifact_id
    store.publish(shared)
    with pytest.raises(BoundaryError, match="train_dev_source_or_evidence_overlap"):
        evaluate_memory(store, model_id, shared.artifact_id, PRODUCER)
    views[dev.artifact_id] = _view(dev.artifact_id, "train-session", "different")
    with pytest.raises(BoundaryError, match="train_dev_session_or_stream_overlap"):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    views[dev.artifact_id] = _view(dev.artifact_id, "dev-session", "train-cue")
    report = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert report.parameters.value()["train_dev_rendered_overlap_count"] == 1


def test_no_labeled_dev_and_exact_reset_config(tmp_path, monkeypatch):
    store, model_id, _, dev, views, _, _ = prepared(tmp_path, monkeypatch,
                                                     reset_each_step=True)
    report = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert report.parameters.value()["partition"] == "dev"
    views[dev.artifact_id] = ObservedInputView(
        dev.artifact_id, "partial_human_input_stream", False,
        (replace(observed("new-unlabeled", 1, reset=True,
                          stream="human:dev-session:timeline:run"),
                 selected_action_id=None, choice_mask=False),),
    )
    with pytest.raises(BoundaryError, match="no_learn_span_label"):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)


def test_second_dev_episode_starts_from_empty_memory_with_different_count(
    tmp_path, monkeypatch,
):
    store, model_id, _, dev, views, config, _ = prepared(tmp_path, monkeypatch)
    first = _view(dev.artifact_id, "dev-session-a", "dev-a")
    second = _view(dev.artifact_id, "dev-session-b", "dev-b")
    views[dev.artifact_id] = replace(first, inputs=first.inputs + second.inputs)
    initial = []
    original = ExperimentalDSimpleM2.advance

    def watched(self, page, memory, **kwargs):
        if kwargs["reset_before"]:
            initial.append(memory.detach().clone())
        return original(self, page, memory, **kwargs)

    with patch.object(ExperimentalDSimpleM2, "advance", watched):
        report = evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert config.episode_count == 1
    assert store.get_manifest(report.parent("evaluation_input")).parameters.value()[
        "episode_count"] == 2
    assert len(initial) == 2 and all(torch.count_nonzero(item) == 0 for item in initial)


def test_changed_tokenizer_or_reset_config_is_not_a_new_trained_control(
    tmp_path, monkeypatch,
):
    store, model_id, _, dev, _, config, _ = prepared(tmp_path, monkeypatch)
    model = store.get_manifest(model_id)
    altered = Manifest(
        "model", PRODUCER, model.parents,
        (model.payload("weights"), store.put_payload("tokenizer", io.BytesIO(b"x"))),
        model.parameters,
    )
    store.publish(altered)
    with pytest.raises(BoundaryError, match="model_lineage_mismatch"):
        evaluate_memory(store, altered.artifact_id, dev.artifact_id, PRODUCER)
    changed = asdict(config)
    changed["reset_each_step"] = True
    relabeled = Manifest("model", PRODUCER, model.parents, model.payloads,
                         FrozenObject.of({**model.parameters.value(), "config": changed}))
    store.publish(relabeled)
    with pytest.raises(BoundaryError, match="model_lineage_mismatch"):
        evaluate_memory(store, relabeled.artifact_id, dev.artifact_id, PRODUCER)


def test_tokenizer_fit_is_replayed_only_on_train_source(tmp_path, monkeypatch):
    store, model_id, train, dev, _, _, _ = prepared(tmp_path, monkeypatch)
    called = []
    original = fit_observed_memory_tokenizer

    def watched(view, **kwargs):
        called.append(view.source_id)
        return original(view, **kwargs)

    monkeypatch.setattr("stpd.workers.memory_evaluation.fit_observed_memory_tokenizer", watched)
    evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
    assert called == [train.artifact_id]
    monkeypatch.setattr(
        "stpd.workers.memory_evaluation.fit_observed_memory_tokenizer",
        lambda view, **kwargs: (b"different-fit", 1),
    )
    with pytest.raises(BoundaryError, match="train_tokenizer_fit_mismatch"):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)


@pytest.mark.parametrize("scores", [torch.tensor([float("nan"), 0.0]), torch.tensor([0.0])])
def test_nonfinite_or_incomplete_candidate_scores_fail_closed(tmp_path, monkeypatch, scores):
    store, model_id, _, dev, _, _, _ = prepared(tmp_path, monkeypatch)
    with (patch.object(ExperimentalDSimpleM2, "score", return_value=scores),
          pytest.raises(BoundaryError, match="candidate_alignment_or_nonfinite")):
        evaluate_memory(store, model_id, dev.artifact_id, PRODUCER)
