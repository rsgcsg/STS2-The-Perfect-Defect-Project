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


def test_a_send_racing_the_break_never_leaves_a_pending_future():
    """#429 review S6: submit passed its broken check, then the reader broke the
    pool and drained before this send appended: the future must fail at once."""
    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    pool = EncoderPool(vocabulary, workers=1)
    try:
        worker = pool._workers[0]
        pool._break(RuntimeError("encoder_reply_out_of_order"))  # drained: nothing pending
        future = pool._send(worker, 99, b"")  # the send that was inside the window
        assert future.done()
        with pytest.raises(BoundaryError, match="encoder_worker_died"):
            future.result(0)
        assert not worker.pending
    finally:
        pool.close()


def test_release_handler_drops_a_link2_lane_after_its_submitted_rows():
    from test_p1_link_frames import _game, _views

    vocabulary = public_vocabulary(V2_INPUT_SPEC)
    lines, full = _game(vocabulary, "g1", _views(4))
    with EncoderPool(vocabulary, workers=1, handler="stpd.p1.fast_aggregator:encode_link_decision",
                     release_handler="stpd.p1.fast_aggregator:release_link_lane") as pool:
        first = [pool.submit("g1", line) for line in lines[:2]]
        pool.release("g1")  # sent after both rows: they still see the lane
        for future, expected in zip(first, full[:2], strict=True):
            meta, row = future.result(30)
            assert meta["frame"] is None and row == expected.row
        meta, row = pool.submit("g1", lines[2]).result(30)
        assert meta["frame"] == "frame_base" and row == b""
