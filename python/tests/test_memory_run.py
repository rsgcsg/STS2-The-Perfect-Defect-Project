"""Synthetic train-only M2 artifact lifecycle; no admission or game claim."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import torch

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.memory_ranking import MemoryConfig, MemoryTrainingInput
from stpd.workers.memory_run import execute_memory_run, prepare_memory_run

PRODUCER = Producer("synthetic-memory-test", "a" * 40, "b" * 64)
TOKENIZER = b"synthetic fixed tokenizer bytes"


@pytest.fixture(autouse=True)
def two_cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def setup(tmp_path: Path, *, count: int = 2):
    blobs = LocalBlobStore(tmp_path / "objects")
    store = ManifestArtifactStore(blobs)
    reporter = ObjectStoreRunReporter(store, blobs)
    origin = Manifest("dataset", PRODUCER)
    store.publish(origin)
    episodes = tuple(MemorySequenceEpisode(
        f"episode-{index}", (
            MemorySequenceStep(
                f"episode-{index}", 0, torch.tensor([1, 2 + index]),
                ("left", "right"), (torch.tensor([3]), torch.tensor([4])),
                "left", reset_before=True,
            ),
            MemorySequenceStep(
                f"episode-{index}", 1, torch.tensor([2, 3 + index]),
                ("right", "left"), (torch.tensor([4]), torch.tensor([3])),
                "right", previous_actual_action=torch.tensor([3]),
            ),
        ),
    ) for index in range(count))
    source = MemoryTrainingInput(origin.artifact_id, hashlib.sha256(TOKENIZER).hexdigest(),
                                 episodes)
    config = MemoryConfig(vocab_size=32, episode_count=count, max_chunk_steps=2,
                          max_episode_input_tokens=100, max_total_input_tokens=200)
    run = prepare_memory_run(store, source, config, PRODUCER, TOKENIZER)
    return store, reporter, source, config, run


def test_new_process_resume_completes_train_only_and_repeated_request_is_read_only(tmp_path):
    store, reporter, _, config, run = setup(tmp_path)
    paused = execute_memory_run(store, reporter, run.artifact_id, PRODUCER, stop_after=1)
    assert paused.state == "paused" and paused.checkpoint_id
    saved = store.get_manifest(paused.checkpoint_id)
    assert saved.parameters.value()["next_episode"] == 1
    assert saved.parent("run") == run.artifact_id
    assert reporter.completed(run.artifact_id) is None

    script = """
import sys
import torch
from pathlib import Path
from spireagent.artifact_contracts import Producer
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.workers.memory_run import execute_memory_run
torch.set_num_threads(2)
blobs = LocalBlobStore(Path(sys.argv[1]))
store = ManifestArtifactStore(blobs)
reporter = ObjectStoreRunReporter(store, blobs)
producer = Producer('synthetic-memory-test', 'a' * 40, 'b' * 64)
result = execute_memory_run(store, reporter, sys.argv[2], producer, resume=sys.argv[3])
assert result.state == 'completed' and result.result_id
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "objects"), run.artifact_id,
         paused.checkpoint_id], capture_output=True, text=True, timeout=30, check=False,
    )
    assert child.returncode == 0, child.stderr
    result = reporter.completed(run.artifact_id)
    assert result is not None and result.parameters.value()["partition"] == "train"
    assert result.parent("checkpoint") != paused.checkpoint_id
    model = store.get_manifest(result.parent("model"))
    export = decode_checkpoint(b"".join(store.read_payload(model.payload("weights"))))
    assert export["schema"] == "stpd/experimental-m2-weights-v1"
    assert "optimizer" not in export and "source_id" not in export
    assert model.payload("tokenizer").sha256 == hashlib.sha256(TOKENIZER).hexdigest()
    assert not any(store.get_manifest(identity).kind == "offline_evaluation"
                   for identity in store.manifest_ids())
    before = store.manifest_ids()
    with patch("stpd.workers.memory_run.MemoryRankingEngine.advance", side_effect=AssertionError):
        again = execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    assert again.result_id == result.artifact_id
    assert store.manifest_ids() == before
    assert config.episode_count == result.parameters.value()["episodes"]


def test_attempt_requires_explicit_same_run_checkpoint_and_bound_tokenizer(tmp_path):
    store, reporter, source, config, run = setup(tmp_path)
    with pytest.raises(BoundaryError, match="tokenizer_digest_mismatch"):
        prepare_memory_run(store, source, config, PRODUCER, b"different")
    paused = execute_memory_run(store, reporter, run.artifact_id, PRODUCER, stop_after=1)
    with pytest.raises(BoundaryError, match="existing_attempt_requires_explicit_resume"):
        execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    other = prepare_memory_run(store, replace(source, episodes=source.episodes[::-1]),
                               config, PRODUCER, TOKENIZER)
    with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
        execute_memory_run(store, reporter, other.artifact_id, PRODUCER,
                           resume=paused.checkpoint_id)
    assert reporter.completed(other.artifact_id) is None


def test_failed_attempt_cannot_implicitly_retry(tmp_path):
    store, reporter, _, _, run = setup(tmp_path, count=1)
    with (patch("stpd.workers.memory_run.MemoryRankingEngine.advance",
                side_effect=RuntimeError), pytest.raises(RuntimeError)):
        execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    assert any(event.parameters.value()["kind"] == "failed"
               for event in reporter.events(run.artifact_id))
    with pytest.raises(BoundaryError, match="existing_attempt_requires_explicit_resume"):
        execute_memory_run(store, reporter, run.artifact_id, PRODUCER)


def test_failed_checkpoint_write_resumes_only_from_last_durable_episode(tmp_path):
    store, reporter, _, _, run = setup(tmp_path)
    original_put = store.put_payload
    writes = 0

    def fail_second_checkpoint(role, stream, media_type="application/octet-stream"):
        nonlocal writes
        if role == "checkpoint":
            writes += 1
            if writes == 2:
                raise RuntimeError("synthetic checkpoint storage failure")
        return original_put(role, stream, media_type)

    with (patch.object(store, "put_payload", side_effect=fail_second_checkpoint),
          pytest.raises(RuntimeError, match="checkpoint storage failure")):
        execute_memory_run(store, reporter, run.artifact_id, PRODUCER)
    events = reporter.events(run.artifact_id)
    good = [event.parameters.value()["details"]["checkpoint_id"] for event in events
            if event.parameters.value()["kind"] == "checkpoint"]
    assert len(good) == 1
    failed = [event for event in events if event.parameters.value()["kind"] == "failed"]
    assert len(failed) == 1
    assert failed[0].parameters.value()["details"]["last_checkpoint"] == good[0]
    assert reporter.completed(run.artifact_id) is None
    with pytest.raises(BoundaryError, match="existing_attempt_requires_explicit_resume"):
        execute_memory_run(store, reporter, run.artifact_id, PRODUCER)

    resumed = execute_memory_run(store, reporter, run.artifact_id, PRODUCER, resume=good[0])
    assert resumed.state == "completed" and resumed.result_id
    assert store.get_manifest(resumed.result_id).parent("checkpoint") != good[0]
    assert any(event.parameters.value()["kind"] == "failed"
               for event in reporter.events(run.artifact_id))
