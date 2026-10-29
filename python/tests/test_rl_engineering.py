"""RL3 seams: classified storage faults, exact warm start and successful-use accounting."""
from __future__ import annotations

import copy
import json
import time
from dataclasses import replace

import pytest
import torch
from botocore.exceptions import ClientError, EndpointConnectionError
from test_p1_t0 import observation

from spireagent.artifact_contracts import Producer
from spireagent.storage.s3 import S3BlobStore, S3Config
from spireagent.workbench.rl_learner import IO
from stpd.p1.export import export_model
from stpd.p1.input import project
from stpd.p1.model import MStd
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.learner import Learner, LearnerConfig, Timer


def configured(tmp_path, monkeypatch, **options):
    from stpd.rl import learner as module

    torch.set_num_threads(2)
    monkeypatch.setattr(module, "source_identity", lambda _: Producer("test", "a"*40, "b"*64))
    torch.manual_seed(37)
    model = MStd(public_vocabulary())
    checkpoint = tmp_path / "origin"
    checkpoint.write_bytes(b"synthetic")
    package = tmp_path / "package"
    export_model(model, checkpoint, package, {"scope": "synthetic"})
    config = LearnerConfig(run="engineering", peer={"kind": "local",
        "directory": str(tmp_path / "peer")}, start_package=str(package), tensorboard_steps=0,
        batch_size=1, min_episodes_before_training=1, anchor_batch=0, **options)
    learner = Learner(config, tmp_path / "work", IO)
    return learner, model


def incoming(learner, run="original-run"):
    episode = {"run_id": run, "original_content_id": "c"*64,
        "identity": {"input_spec": learner.input_spec, "ascension": 0},
        "observations": [{"observation": observation()}],
        "steps": [{"index": 0, "observation_index": 0, "action_id": "c7", "action_index": 0,
            "generated_at": time.time() - 10,
            "behavior": {"mode": "temperature_uniform", "selected_probability": .5,
                         "model_probabilities": [.5, .5], "probabilities": [.5, .5]}}],
        "outcome": {"status": "finished", "unknown": False,
            "terminal": {"status": "observed", "result": "loss"},
            "max_observed_floor": 12, "boss_clearance": {"count": None}}}
    return {"key": "original-shard", "record": {"model_version": 0,
        "finished_at": time.time() - 1, "shard_artifact_id": "d"*64},
        "episode": episode, "samples": learner._project(episode)}


def test_failed_attempt_never_counts_use_duplicate_never_counts_twice(tmp_path, monkeypatch):
    from stpd.rl import learner as module

    learner, _ = configured(tmp_path, monkeypatch)
    row = incoming(learner)
    learner._admit([row, copy.deepcopy(row)])
    assert len(learner.buffer) == 1 and learner.counts["duplicate_episodes"] == 1
    real_update = module.rl_update
    monkeypatch.setattr(module, "rl_update", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("injected_before_step")))
    with pytest.raises(RuntimeError, match="injected_before_step"):
        learner.train_once()
    assert learner.updates == learner.buffer[0].uses == 0
    assert learner.ledger.counts["sample_uses"] == 0
    monkeypatch.setattr(module, "rl_update", real_update)
    result = learner.train_once()
    assert result["update"] == 1 and learner.buffer[0].uses == 1
    assert learner.ledger.counts["first_used_decisions"] == 1
    assert learner.ledger.counts["sample_uses"] == 1
    assert 0 <= result["policy_normalized_entropy"] <= 1.000001
    learner.version = 10
    assert learner.usable() == []
    assert learner.ledger.counts["retired_stale_uses1_decisions"] == 1
    records = [json.loads(line) for line in learner.ledger.path.read_text().splitlines()]
    first = next(row for row in records if row["kind"] == "trained")["first"][0]
    assert first["generated_to_first_seconds"] >= 10
    assert first["latency_interval_seconds"] is None  # clocks not calibrated
    learner.exchange.request_stop("test_completed")
    learner.run()


def test_reference_is_separate_and_new_run_resets_state(tmp_path, monkeypatch):
    reference = MStd(public_vocabulary())
    with torch.no_grad():
        reference.head[-1].weight.add_(.1)
    checkpoint = tmp_path / "reference-origin"
    checkpoint.write_bytes(b"synthetic reference")
    package = tmp_path / "reference"
    export_model(reference, checkpoint, package, {"scope": "synthetic"})
    learner, start = configured(tmp_path, monkeypatch, reference_package=str(package))
    for key, value in learner.model.state_dict().items():
        assert torch.equal(value, start.state_dict()[key])
    for key, value in learner.reference.state_dict().items():
        assert torch.equal(value, reference.state_dict()[key])
    assert learner.reference_sha256 != learner.start_sha256
    assert not learner.optimizer.state and not learner.buffer and not learner.seen
    assert learner.updates == 0 and not learner.counts
    learner.exchange.request_stop("test_completed")
    learner.run()


@pytest.mark.parametrize("permission", [False, True])
def test_storage_keeps_safe_cause_and_only_transient_reads_retry(tmp_path, permission):
    class Client:
        attempts = 0

        def list_objects_v2(self, **_):
            self.attempts += 1
            if permission:
                raise ClientError({"Error": {"Code": "AccessDenied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403}}, "ListObjectsV2")
            if self.attempts < 3:
                raise EndpointConnectionError(endpoint_url="https://secret-token.invalid")
            return {"Contents": []}

    client = Client()
    blobs = S3BlobStore(S3Config("test"), client)
    learner = Learner.__new__(Learner)
    learner.work = tmp_path
    learner.timer = Timer()
    learner.config = replace(LearnerConfig(run="test", peer={}), read_retry_seconds=0)
    def call():
        return blobs.keys("exchange/rl/test/shards/")
    if permission:
        with pytest.raises(ValueError, match="s3_list_failed"):
            learner._read_retry("list_shards", call)
    else:
        assert learner._read_retry("list_shards", call) == ()
    assert client.attempts == (1 if permission else 3)
    text = (tmp_path / "io-errors.jsonl").read_text()
    assert "secret-token" not in text
    if permission:
        assert "403" in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert all(row["prefix_or_key"] == "exchange/rl/test/shards/" for row in rows)
    assert rows[0]["retry"] is (not permission)


def test_same_frozen_admission_cut_preserves_batch_order(tmp_path, monkeypatch):
    from concurrent.futures import Future

    histories = []
    final_weights = []
    for background in (False, True):
        directory = tmp_path / str(background)
        directory.mkdir()
        learner, _ = configured(directory, monkeypatch, background_io=background, read_ahead=3)
        rows = {f"shard-{i}": incoming(learner, run=f"original-{i}") for i in range(3)}
        keys = list(rows)
        class Reader:
            def submit(self, _, key, rows=rows):
                future = Future()
                future.set_result([rows[key]])
                return future

            def shutdown(self, **_):
                pass

        learner.reader.shutdown(wait=True)
        learner.reader = Reader()
        monkeypatch.setattr(learner.exchange, "shard_keys", lambda keys=keys: keys)
        if background:
            learner.discovery_future = Future()
            learner.discovery_future.set_result(keys)
        learner.poll()
        assert [item.sample.key for item in learner.buffer] == [f"original-{i}:0" for i in range(3)]
        torch.manual_seed(17)
        for _ in range(5):
            learner.train_once()
        records = [json.loads(line) for line in learner.ledger.path.read_text().splitlines()]
        histories.append([row["ids"] for row in records if row["kind"] == "trained"])
        final_weights.append({key: value.clone()
                              for key, value in learner.model.state_dict().items()})
        (learner.work / "STOP").touch()
        learner.run()
    assert histories[0] == histories[1]
    assert len(histories[0]) == 5
    for key, value in final_weights[0].items():
        assert torch.equal(value, final_weights[1][key]), key


def test_background_stop_read_has_owner_watchdog(tmp_path):
    from concurrent.futures import Future

    learner = Learner.__new__(Learner)
    learner.config = LearnerConfig(run="watchdog", peer={}, background_io=True,
                                   remote_stop_read_timeout_seconds=1)
    learner.stop_future = Future()
    learner.stop_read_started_at = time.monotonic() - 2
    with pytest.raises(ValueError, match="remote_stop_read_timeout"):
        learner._stop_request()


@pytest.mark.parametrize("corrupt", [False, True])
def test_first_update_eval_gate_checks_complete_original_probabilities(
        tmp_path, monkeypatch, corrupt):
    learner, _ = configured(tmp_path, monkeypatch, initial_policy_check=True)
    row = incoming(learner)
    frame = project(row['episode']['observations'][0]['observation'],
                    input_projection=learner.projection)
    with torch.no_grad():
        learner.model.eval()
        scores = learner.model.forward_batch([frame])[0]
        if corrupt:
            scores = scores + .5  # Same probabilities, wrong original full logits.
        ids = [choice['choice_id'] for choice in frame.choices]
        source = row['episode']['observations'][0]['observation']['catalog']
        ordering = [ids.index(choice['choice_id']) for choice in source]
        probabilities = scores.softmax(-1).tolist()
        behavior = row['episode']['steps'][0]['behavior']
        behavior['model_probabilities'] = [probabilities[i] for i in ordering]
        behavior['model_logits'] = [float(scores[i]) for i in ordering]
        behavior['probabilities'] = [.97 * p + .03 / len(ids)
                                    for p in behavior['model_probabilities']]
        behavior['selected_probability'] = behavior['probabilities'][0]
    row['samples'] = learner._project(row['episode'])
    learner._admit([row])
    if corrupt:
        with pytest.raises(ValueError, match='initial_eval_probability_mismatch'):
            learner.train_once()
        assert learner.updates == learner.buffer[0].uses == 0
    else:
        learner.train_once()
        assert learner.updates == learner.buffer[0].uses == 1
    report = json.loads((learner.work / 'initial-policy-check.json').read_bytes())
    assert report['passed'] is not corrupt
    assert report['max_abs_log_ratio'] < 1e-4
    assert abs(report['reference_kl'][0]) < 1e-5
    (learner.work / 'STOP').touch()
    learner.run()


def test_checkpoint_resume_preserves_saved_buffer_order_and_rng(tmp_path, monkeypatch):
    first, _ = configured(tmp_path, monkeypatch)
    rows = {}
    for key in ('z-shard', 'a-shard'):
        row = incoming(first, run=key + '-run')
        row['key'] = key
        rows[key] = row
        first._admit([row])
    first.train_once()
    first.maybe_version(force=True)
    first._collect_version(wait=True)
    first._take_checkpoint(wait=True)
    checkpoint = first.exchange.latest_checkpoint()['checkpoint_artifact_id']
    monkeypatch.setattr(Learner, '_read_once', lambda self, key, **_: [rows[key]])
    second = Learner(replace(first.config, resume_checkpoint=checkpoint),
                     tmp_path / 'second', first.io)
    assert [item.sample.key for item in first.buffer] == [item.sample.key for item in second.buffer]
    assert [item.uses for item in first.buffer] == [item.uses for item in second.buffer]
    assert first.random.getstate() == second.random.getstate()
    assert first.ledger.counts == second.ledger.counts
    assert first.ledger.live == second.ledger.live
    first.exchange.request_stop('test_completed')
    first.run()
    second.run()


def test_ledger_offline_reader_preserves_decisions_uses_and_right_censoring(tmp_path, monkeypatch):
    from stpd.rl.measurement import decision_ledger

    learner, _ = configured(tmp_path, monkeypatch)
    learner._admit([incoming(learner, 'one'), incoming(learner, 'two')])
    learner.train_once()
    summary = decision_ledger([learner.ledger.path], start_at=learner.ledger.started_at,
                              end_at=time.time())
    assert summary['counts']['admitted_decisions'] == 2
    assert summary['counts']['first_used_decisions'] == 1
    assert summary['cohort_first_use_rate'] == .5
    assert summary['uses_distribution'] == {'0': 1, '1': 1}
    assert summary['right_censored_admitted_rows'] == 2
    assert summary['cohort_upload_conservation'] is True
    assert summary['generated_to_first_seconds']['unknown_clock_bounds'] == 1
    frozen = learner.ledger.freeze_segments()
    assert len(frozen) == 1
    assert frozen[0][1] == learner.ledger.path.read_bytes()
    (learner.work / 'STOP').touch()
    learner.run()


def test_bounded_prefetch_is_default_off_because_admission_cut_can_differ(tmp_path, monkeypatch):
    from concurrent.futures import Future

    sizes = []
    assert LearnerConfig(run='default', peer={}).background_io is False
    for background in (False, True):
        directory = tmp_path / str(background)
        directory.mkdir()
        learner, _ = configured(directory, monkeypatch, background_io=background, read_ahead=2)
        rows = {f'shard-{i}': incoming(learner, f'original-{i}') for i in range(3)}

        class ReadyReader:
            def submit(self, _, key, rows=rows):
                future = Future()
                future.set_result([rows[key]])
                return future

            def shutdown(self, **_):
                pass

        learner.reader.shutdown(wait=True)
        learner.reader = ReadyReader()
        keys = list(rows)
        monkeypatch.setattr(learner.exchange, 'shard_keys', lambda keys=keys: keys)
        if background:
            learner.discovery_future = Future()
            learner.discovery_future.set_result(keys)
        learner.poll()
        sizes.append(len(learner.buffer))
        (learner.work / 'STOP').touch()
        learner.run()
    assert sizes == [3, 2]  # FIFO is preserved; the next update's candidate pool is not.


def test_learner_CLI_defers_usr1_to_local_stop_and_restores_handlers(tmp_path, monkeypatch):
    import signal
    import sys

    from spireagent.workbench import rl_learner as application

    config = tmp_path / 'learner.json'
    config.write_text(json.dumps({'schema': 'stpd/rl-learner-config-1', 'run': 'signal',
                                 'peer': {'kind': 'local', 'directory': str(tmp_path / 'peer')}}))
    work = tmp_path / 'work'
    original = signal.getsignal(signal.SIGUSR1)

    class NoGameLearner:
        def __init__(self, *_):
            pass

        def run(self):
            signal.raise_signal(signal.SIGUSR1)
            assert (work / 'STOP').exists()
            return {'updates': 0, 'versions': [], 'counts': {}}

    monkeypatch.setattr(application, 'Learner', NoGameLearner)
    monkeypatch.setattr(sys, 'argv', ['rl_learner', '--config', str(config), '--work', str(work)])
    application.main()
    assert signal.getsignal(signal.SIGUSR1) == original
