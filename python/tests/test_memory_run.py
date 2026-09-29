"""Synthetic train-only M2 artifact lifecycle; no admission or game claim."""

from __future__ import annotations

import hashlib
import io
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
import torch
from tokenizers import Tokenizer, models, pre_tokenizers

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, decode_json, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.workers.checkpoint_codec import decode_checkpoint
from stpd.workers.memory_ranking import MemoryConfig, MemoryTrainingInput
from stpd.workers.memory_run import (
    _load_run,
    execute_memory_run,
    prepare_memory_run,
    prepare_observed_memory_run,
)

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


def _verified_human_source(tmp_path: Path):
    from test_artifact_store_v1 import PRODUCER as SOURCE_PRODUCER
    from test_memory_sequence_bridge import _declared_bundle

    from stpd.fullrun.text_menu_human_import import (
        publish_human_text_source,
        publish_verified_human_text_bundle,
    )

    directory, _, _ = _declared_bundle(
        tmp_path / "human-fixture", "begin_card_play_exact_factory_return", "begin_card_play",
    )
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "observed-store"))
    evidence = publish_verified_human_text_bundle(store, directory, SOURCE_PRODUCER)
    source = publish_human_text_source(store, (evidence.artifact_id,), SOURCE_PRODUCER)
    return store, source


def _real_fixture_tokenizer() -> bytes:
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    return tokenizer.to_str().encode("utf-8")


def test_observed_source_api_persists_verified_episode_mapping_train_only(tmp_path):
    store, source = _verified_human_source(tmp_path)
    tokenizer = _real_fixture_tokenizer()
    config = MemoryConfig(vocab_size=1, episode_count=1, max_tokens=256,
                          max_episode_input_tokens=1024, max_total_input_tokens=1024)

    run = prepare_observed_memory_run(store, source.artifact_id, config, PRODUCER, tokenizer)
    _loaded, training_input, _config, engine = _load_run(store, run.artifact_id, PRODUCER)

    assert training_input.parameters.value()["schema"] == "stpd/experimental-m2-training-input-v2"
    assert training_input.parameters.value()["source_event_count"] == 1
    assert sorted(payload.role for payload in training_input.payloads) == [
        "episodes", "source_map", "tokenizer",
    ]
    assert engine.next_episode == 0
    assert run.parameters.value()["partition"] == "train"


def test_experimental_v1_training_input_still_loads_without_rewrite(tmp_path):
    store, _reporter, _source, _config, run = setup(tmp_path)
    _loaded, training_input, _config, engine = _load_run(store, run.artifact_id, PRODUCER)
    assert training_input.parameters.value()["schema"] == "stpd/experimental-m2-training-input-v1"
    assert sorted(payload.role for payload in training_input.payloads) == ["episodes", "tokenizer"]
    assert engine.next_episode == 0


@pytest.mark.parametrize("bad_tokenizer", [b"not-json", b"\xff"])
def test_observed_source_api_requires_parseable_fixed_tokenizer(tmp_path, bad_tokenizer):
    store, source = _verified_human_source(tmp_path)
    config = MemoryConfig(vocab_size=1, episode_count=1, max_tokens=256,
                          max_episode_input_tokens=1024, max_total_input_tokens=1024)
    with pytest.raises(BoundaryError, match="tokenizer_parse_failed"):
        prepare_observed_memory_run(store, source.artifact_id, config, PRODUCER, bad_tokenizer)


def test_observed_source_api_does_not_infer_episode_count_or_vocab(tmp_path):
    store, source = _verified_human_source(tmp_path)
    tokenizer = _real_fixture_tokenizer()
    with pytest.raises(BoundaryError, match="tokenizer_vocabulary_mismatch"):
        prepare_observed_memory_run(
            store, source.artifact_id,
            MemoryConfig(vocab_size=2, episode_count=1, max_tokens=256), PRODUCER, tokenizer,
        )
    with pytest.raises(BoundaryError, match="projected_episode_count_mismatch"):
        prepare_observed_memory_run(
            store, source.artifact_id,
            MemoryConfig(vocab_size=1, episode_count=2, max_tokens=256), PRODUCER, tokenizer,
        )


def test_v2_run_loader_rejects_changed_event_mapping(tmp_path):
    store, source = _verified_human_source(tmp_path)
    tokenizer = _real_fixture_tokenizer()
    config = MemoryConfig(vocab_size=1, episode_count=1, max_tokens=256,
                          max_episode_input_tokens=1024, max_total_input_tokens=1024)
    run = prepare_observed_memory_run(store, source.artifact_id, config, PRODUCER, tokenizer)
    _loaded, training_input, _config, _engine = _load_run(store, run.artifact_id, PRODUCER)

    raw_map = b"".join(store.read_payload(training_input.payload("source_map")))
    mapping = decode_json(raw_map)
    mapping["events"][0]["stream_id"] += ":tampered"
    map_payload = store.put_payload("source_map", io.BytesIO(json_bytes(mapping)),
                                    "application/json")
    altered_input = Manifest(
        "training_input", training_input.producer, training_input.parents,
        (training_input.payload("episodes"), training_input.payload("tokenizer"), map_payload),
        training_input.parameters,
    )
    store.publish(altered_input)
    experiment = store.get_manifest(run.parent("experiment"))
    altered_experiment = Manifest(
        "experiment", experiment.producer,
        (Parent("training_input", altered_input.artifact_id),), parameters=experiment.parameters,
    )
    store.publish(altered_experiment)
    altered_run = Manifest(
        "run", run.producer,
        (Parent("training_input", altered_input.artifact_id),
         Parent("experiment", altered_experiment.artifact_id)), parameters=run.parameters,
    )
    store.publish(altered_run)

    with pytest.raises(BoundaryError, match="source_map_observed_event_coverage_mismatch"):
        _load_run(store, altered_run.artifact_id, PRODUCER)


def test_v2_run_replays_per_episode_settling_limit_without_inference(tmp_path, monkeypatch):
    from test_memory_sequence_bridge import session_observed

    import stpd.fullrun.observed_input_sequence as observed_module
    from stpd.fullrun.observed_input_sequence import ObservedInputView

    store, source = _verified_human_source(tmp_path)
    def item(event: str, sequence: int, stream: str, *, settling: bool = False,
             reset: bool = False):
        return replace(
            session_observed(event, sequence, status="settling" if settling else "interactive",
                             reset=reset), stream_id=stream,
        )

    view = ObservedInputView(source.artifact_id, "partial_human_input_stream", False, (
        item("a-ready-1", 1, "human:session:a", reset=True),
        item("a-settle", 2, "human:session:a", settling=True),
        item("a-ready-2", 3, "human:session:a"),
        item("b-ready-1", 1, "human:session:b", reset=True),
        item("b-settle", 2, "human:session:b", settling=True),
        item("b-ready-2", 3, "human:session:b"),
        item("c-ready-1", 1, "human:session:c", reset=True),
        item("c-settle-1", 2, "human:session:c", settling=True),
        item("c-settle-2", 3, "human:session:c", settling=True),
        item("c-ready-2", 4, "human:session:c"),
    ))
    monkeypatch.setattr(observed_module, "load_observed_input_view",
                        lambda _store, _source_id: view)
    tokenizer = _real_fixture_tokenizer()
    config = MemoryConfig(vocab_size=1, episode_count=2, max_tokens=256,
                          max_episode_input_tokens=1024, max_total_input_tokens=4096)

    run = prepare_observed_memory_run(
        store, source.artifact_id, config, PRODUCER, tokenizer, max_settling_events=1,
    )
    _loaded, training_input, _config, engine = _load_run(store, run.artifact_id, PRODUCER)

    assert training_input.parameters.value()["source_event_count"] == len(view.inputs)
    assert training_input.parameters.value()["projection_config"]["max_settling_events"] == 1
    raw_map = b"".join(store.read_payload(training_input.payload("source_map")))
    mapping = decode_json(raw_map)["events"]
    omitted = next(item for item in mapping if item["event_id"] == "c-settle-2")
    assert omitted["disposition"] == "excluded"
    assert omitted["reason"] == "episode_settling_limit"
    assert engine.next_episode == 0


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
