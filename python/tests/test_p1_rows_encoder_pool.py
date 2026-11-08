"""The encoder pool returns the in-process encoder's rows and fails loudly."""
from __future__ import annotations

import json
import os
import signal

import pytest
from test_p1_input_r18 import trial
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.rows_encoder import RowsEncoder
from stpd.p1.rows_encoder_pool import EncoderPool
from stpd.p1.vocabulary import public_vocabulary


def test_pool_rows_equal_in_process_encoder_and_lanes_stick():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    local = RowsEncoder(vocabulary)
    observations = []
    for hp in range(6):
        observation = public_state()
        observation["status"] = {"hp": hp, "cards": [{"id": hp, "x": [1]}]}
        observations.append(observation)
    with EncoderPool(vocabulary, workers=2) as pool:
        assert pool.identity == json.loads(json.dumps(local.identity))
        futures = [pool.submit(index % 3, json.dumps(observation).encode())
                   for index, observation in enumerate(observations * 2)]
        for future, observation in zip(futures, observations * 2, strict=True):
            assert future.result(30) == local.encode(json.dumps(observation).encode())
        stats = pool.stats()
        # Three lanes over two workers: 2 + 1, and every lane stayed on one worker.
        assert sorted(s["hits"] > 0 for s in stats) == [True, True]
        pool.release(0)
        pool.release(1)
        pool.release(2)
        assert sum(worker.lanes for worker in pool._workers) == 0


def test_pool_reports_typed_row_errors_and_keeps_serving():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    with EncoderPool(vocabulary, workers=1) as pool:
        with pytest.raises(BoundaryError, match="invalid_public_json"):
            pool.submit("a", b"{nope").result(30)
        wrong = trial()  # another InputSpec revision
        with pytest.raises(ValueError, match="differs from bound InputSpec"):
            pool.submit("a", json.dumps(wrong).encode()).result(30)
        good = pool.submit("a", json.dumps(public_state()).encode()).result(30)
        assert good.catalog_len == len(public_state()["catalog"])


def test_dead_worker_breaks_the_pool_without_retry():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    pool = EncoderPool(vocabulary, workers=1)
    try:
        os.kill(pool._workers[0].process.pid, signal.SIGKILL)
        pool._workers[0].process.join(10)
        with pytest.raises(BoundaryError, match="encoder_worker_died"):
            pool.submit("a", json.dumps(public_state()).encode()).result(30)
        assert pool.broken
        with pytest.raises(BoundaryError, match="encoder_worker_died"):
            pool.submit("a", json.dumps(public_state()).encode())
    finally:
        pool.close()


def test_pool_rejects_fork_and_mismatched_cpus():
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    with pytest.raises(ValueError, match="encoder_pool_start_method"):
        EncoderPool(vocabulary, workers=1, start_method="fork")
    with pytest.raises(ValueError, match="encoder_pool_cpus_per_worker"):
        EncoderPool(vocabulary, workers=2, cpus=[0])
