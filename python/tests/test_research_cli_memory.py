"""Real subprocess CLI execution of an already prepared synthetic M2 run."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import torch

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.models.dsimple_sequence_training import MemorySequenceEpisode, MemorySequenceStep
from stpd.workers.memory_ranking import MemoryConfig, MemoryTrainingInput
from stpd.workers.memory_run import prepare_memory_run

PRODUCER = Producer("synthetic-cli-source", "a" * 40, "b" * 64)
TOKENIZER = b"fixed synthetic tokenizer"

CLI_PROCESS = """
import sys
from unittest.mock import patch
from spireagent.artifact_contracts import Producer
from spireagent import research_cli
from stpd.workers.memory_ranking import MemoryRankingEngine

# The CLI still obtains producer identity from its source_identity owner. This
# process fixture pins that owner to a synthetic producer independent of Git.
research_cli.source_identity = lambda root: Producer('synthetic-cli-source', 'a'*40, 'b'*64)
fixture_flag = sys.argv[-1]
sys.argv = ['research_cli', *sys.argv[1:-1]]
if fixture_flag == '--assert-no-advance':
    with patch.object(MemoryRankingEngine, 'advance', side_effect=AssertionError('reoptimized')):
        raise SystemExit(research_cli.main())
raise SystemExit(research_cli.main())
"""


def _invoke(store_dir: Path, run_id: str, *options: str,
            assert_no_advance: bool = False) -> subprocess.CompletedProcess[str]:
    arguments = ["--store", str(store_dir), "run-memory", "--run", run_id, *options]
    # The last argument is only a fixture flag, never a CLI option.
    arguments.append("--assert-no-advance" if assert_no_advance else "--fixture-end")
    return subprocess.run(
        [sys.executable, "-c", CLI_PROCESS, *arguments], capture_output=True,
        text=True, check=False, timeout=30,
    )


def _prepared(tmp_path: Path):
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        store_dir = tmp_path / "objects"
        blobs = LocalBlobStore(store_dir)
        store = ManifestArtifactStore(blobs)
        reporter = ObjectStoreRunReporter(store, blobs)
        origin = Manifest("dataset", PRODUCER)
        store.publish(origin)
        episodes = tuple(MemorySequenceEpisode(
            f"episode-{index}", (MemorySequenceStep(
                f"episode-{index}", 0, torch.tensor([1, 2 + index]),
                ("A", "B"), (torch.tensor([3]), torch.tensor([4])), "A",
                reset_before=True,
            ),),
        ) for index in range(2))
        source = MemoryTrainingInput(origin.artifact_id,
                                     hashlib.sha256(TOKENIZER).hexdigest(), episodes)
        config = MemoryConfig(vocab_size=32, episode_count=2, cpu_threads=2,
                              max_total_input_tokens=100, max_episode_input_tokens=100)
        run = prepare_memory_run(store, source, config, PRODUCER, TOKENIZER)
        other = prepare_memory_run(store, replace(source, episodes=episodes[::-1]),
                                   config, PRODUCER, TOKENIZER)
        foreign = prepare_memory_run(store, source, config,
                                     replace(PRODUCER, source_revision="c" * 40), TOKENIZER)
        return store_dir, store, reporter, run, other, foreign, origin
    finally:
        torch.set_num_threads(previous)


def test_cli_exact_run_resume_completion_and_rejections(tmp_path: Path):
    store_dir, store, reporter, run, other, foreign, origin = _prepared(tmp_path)
    for wrong in (origin.artifact_id, foreign.artifact_id):
        rejected = _invoke(store_dir, wrong)
        assert rejected.returncode != 0
        assert "run_identity_mismatch" in rejected.stderr
    started = _invoke(store_dir, run.artifact_id, "--stop-after", "1")
    assert started.returncode == 0, started.stderr
    paused = json.loads(started.stdout)
    assert paused["state"] == "paused" and paused["checkpoint_id"]
    assert reporter.completed(run.artifact_id) is None
    attempted_retry = _invoke(store_dir, run.artifact_id)
    assert attempted_retry.returncode != 0
    assert "existing_attempt_requires_explicit_resume" in attempted_retry.stderr
    wrong_checkpoint = _invoke(store_dir, other.artifact_id, "--resume", paused["checkpoint_id"])
    assert wrong_checkpoint.returncode != 0
    assert "resume_identity_mismatch" in wrong_checkpoint.stderr
    assert reporter.completed(other.artifact_id) is None

    resumed = _invoke(store_dir, run.artifact_id, "--resume", paused["checkpoint_id"])
    assert resumed.returncode == 0, resumed.stderr
    completed = json.loads(resumed.stdout)
    assert completed["state"] == "completed" and completed["result_id"]
    before = store.manifest_ids()
    repeated = _invoke(store_dir, run.artifact_id, assert_no_advance=True)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["result_id"] == completed["result_id"]
    assert store.manifest_ids() == before
