"""Managed v2 dev admission over verified report archives and tiny M2 exports."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import torch
from test_local_curation import create
from test_managed_text_menu_import import _archive
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService
from stpd.fullrun.confirmed_interaction import V2_HISTORY_INPUT_PROFILE
from stpd.fullrun.managed_text_menu_import import import_managed_text_menu_report
from stpd.fullrun.memory_projection_config import history_episode_projection_config
from stpd.fullrun.memory_sequence_bridge import project_memory_episodes
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import load_observed_input_view
from stpd.models.dsimple_memory import ExperimentalDSimpleM2
from stpd.models.token_core import ScratchShape, ScratchTokenCore
from stpd.workers.memory_evaluation import evaluate_memory
from stpd.workers.memory_ranking import MemoryConfig, MemoryTrainingInput
from stpd.workers.memory_run import execute_memory_run, prepare_memory_run

PRODUCER = Producer("synthetic-managed-memory-dev", "a" * 40, "b" * 64)
TRAIN_OPERATION = "a" * 32
DEV_OPERATION = "b" * 32


@pytest.fixture(autouse=True)
def cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def _source(path: Path, store: ManifestArtifactStore, owner, *, seed: str, session: str):
    archive, report_id, expected = _archive(
        path, seed=seed, session=session, scenario="same-public-pages")
    source = import_managed_text_menu_report(
        archive, store, report_id, PRODUCER, expected=expected)
    owner.ledger.reserve_managed_observed_source(
        store, source.manifest.artifact_id, "training", expected=expected)
    return source


def _model(store: ManifestArtifactStore, source) -> str:
    view = load_observed_input_view(store, source.manifest.artifact_id)
    tokens, count = fit_observed_memory_tokenizer(
        view, max_settling_events=0, input_profile=V2_HISTORY_INPUT_PROFILE)
    tokenizer = Tokenizer.from_str(tokens.decode())
    config = MemoryConfig(
        vocab_size=tokenizer.get_vocab_size(), episode_count=count,
        max_episode_input_tokens=65536, max_total_input_tokens=65536,
        max_chunk_steps=2, max_tokens=2048,
    )
    network = ExperimentalDSimpleM2(
        ScratchTokenCore(ScratchShape(config.vocab_size, 8, 1, 2, 16, 0.0, 2048)))
    projection = project_memory_episodes(
        view, tokenizer, network, max_observations=16, max_input_tokens=65536,
        max_settling_events=0,
        projection_config=history_episode_projection_config(V2_HISTORY_INPUT_PROFILE),
    )
    assert not projection.diagnostics and len(projection.episodes) == 1
    steps = projection.episodes[0].steps
    assert steps[0].previous_actual_action is None
    assert all(step.previous_actual_action is not None for step in steps[1:])
    assert all(step.public_feedback is None for step in steps)
    run = prepare_memory_run(
        store, MemoryTrainingInput(source.manifest.artifact_id,
                                   hashlib.sha256(tokens).hexdigest(), projection.episodes),
        config, PRODUCER, tokens, source_mapping=projection.event_mapping,
        projection_config=history_episode_projection_config(V2_HISTORY_INPUT_PROFILE),
        operation_id=TRAIN_OPERATION,
    )
    result = execute_memory_run(
        store, ObjectStoreRunReporter(store, store.blobs), run.artifact_id, PRODUCER)
    return store.get_manifest(result.result_id).parent("model")


def test_verified_managed_history_dev_requires_new_split_run(tmp_path: Path):
    _, directory, owner = create(tmp_path)
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    train = _source(tmp_path / "train", store, owner, seed="seed-a", session="session-a")
    repeat = _source(tmp_path / "repeat", store, owner, seed="seed-a", session="session-b")
    dev = _source(tmp_path / "dev", store, owner, seed="seed-b", session="session-c")
    shared_events = _source(
        tmp_path / "shared-events", store, owner, seed="seed-c", session="session-a")
    assert train.split_run_id == repeat.split_run_id != dev.split_run_id
    assert shared_events.split_run_id not in {train.split_run_id, dev.split_run_id}
    model_id = _model(store, train)
    assert LocalMemoryEvaluationService._training_source(store, model_id) == (
        TRAIN_OPERATION, train.manifest.artifact_id, 0)
    owner.ledger.use_source(train.manifest.artifact_id, "training", TRAIN_OPERATION)
    owner.ledger.use({train.split_run_id}, "training", TRAIN_OPERATION)
    with pytest.raises(BoundaryError, match="train_dev_exact_origin_overlap"):
        owner.reserve_memory_dev(store, train.manifest.artifact_id,
                                 repeat.manifest.artifact_id, TRAIN_OPERATION, DEV_OPERATION)
    with pytest.raises(BoundaryError, match="train_dev_session_or_stream_overlap"):
        evaluate_memory(store, model_id, repeat.manifest.artifact_id, PRODUCER)
    with pytest.raises(BoundaryError, match="train_dev_exact_origin_overlap"):
        owner.reserve_memory_dev(store, train.manifest.artifact_id,
                                 shared_events.manifest.artifact_id,
                                 TRAIN_OPERATION, DEV_OPERATION)
    with pytest.raises(BoundaryError, match="train_dev_source_or_evidence_overlap"):
        evaluate_memory(store, model_id, shared_events.manifest.artifact_id, PRODUCER)
    with pytest.raises(BoundaryError, match="train_dev_source_or_evidence_overlap"):
        evaluate_memory(store, model_id, train.manifest.artifact_id, PRODUCER)
    alias = Manifest("dataset", Producer("synthetic-alias", "c" * 40, "d" * 64),
                     train.manifest.parents, parameters=train.manifest.parameters)
    store.publish(alias)
    with pytest.raises(BoundaryError, match="train_dev_source_or_evidence_overlap"):
        evaluate_memory(store, model_id, alias.artifact_id, PRODUCER)
    assert owner.reserve_memory_dev(store, train.manifest.artifact_id,
                                    dev.manifest.artifact_id,
                                    TRAIN_OPERATION, DEV_OPERATION) is False
    report = evaluate_memory(store, model_id, dev.manifest.artifact_id, PRODUCER,
                             max_settling_events=0, operation_id=DEV_OPERATION,
                             semantic_overlap=False)
    assert report.parameters.value()["native_run_independence"] is False
    assert report.parameters.value()["qualification"] == "engineering_only"
    # Identical public pages across different seeds are measured, not merged into one run.
    assert report.parameters.value()["train_dev_rendered_overlap_count"] > 0
    forged = Manifest("dataset", dev.manifest.producer, dev.manifest.parents,
                      parameters=FrozenObject.of({
                          **dev.manifest.parameters.value(), "split_run_id": train.split_run_id,
                      }))
    store.publish(forged)
    with pytest.raises(BoundaryError, match="source_identity_mismatch"):
        evaluate_memory(store, model_id, forged.artifact_id, PRODUCER)


def test_managed_dev_requires_model_training_use(tmp_path: Path):
    _, directory, owner = create(tmp_path)
    store = ManifestArtifactStore(LocalBlobStore(directory / "store", create=False))
    train = _source(tmp_path / "train", store, owner, seed="seed-a", session="session-a")
    dev = _source(tmp_path / "dev", store, owner, seed="seed-b", session="session-b")
    with pytest.raises(BoundaryError, match="model_training_use_unproven"):
        owner.reserve_memory_dev(store, train.manifest.artifact_id,
                                 dev.manifest.artifact_id, TRAIN_OPERATION, DEV_OPERATION)
