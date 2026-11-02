"""Durable segment production through the real CAS, exchange and cold reader."""

from __future__ import annotations

import copy
import hashlib

import pytest
from test_rl_loop import PRODUCER, _model_artifact

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.rl_exchange import Exchange
from spireagent.workbench.managed_segment_store import publish_segment, read_segment
from spireagent.workbench.rl_segment_producer import OriginalBarrier, SegmentAssembler
from stpd.p1.data import digest
from stpd.p1.data import json_bytes as p1_json_bytes


def fixture(tmp_path, count, *, terminal=False):
    peer = {"kind": "local", "directory": str(tmp_path / "peer")}
    exchange = Exchange(peer, "segments")
    model, sha = _model_artifact(exchange.store)
    head = Manifest(
        "checkpoint",
        PRODUCER,
        parents=(Parent("policy", model.artifact_id),),
        payloads=(exchange.store.put_bytes("checkpoint", b"synthetic heads fixture"),),
    )
    exchange.store.publish(head)
    mu = {
        "snapshot_sha256": "a" * 64,
        "model_artifact_id": model.artifact_id,
        "model_sha256": sha,
        "heads_artifact_id": head.artifact_id,
        "heads_sha256": head.payload("checkpoint").sha256,
        "version": 0,
        "precision": "fp32",
    }
    run = tmp_path / "run"
    run.mkdir()
    for name in ("manifest.json", "agent-manifest.json"):
        (run / name).write_bytes(b"{}\n")
    (run / "global-source.json").write_bytes(b"original source bytes")
    (run / "events.jsonl").write_bytes(
        b"".join(json_bytes({"sequence": i}) for i in range(1, count + 2))
    )
    catalog = [{"choice_id": "first"}, {"choice_id": "second"}]
    inputs = [
        {
            "acquisition_id": f"a-{i}",
            "input_spec": {},
            "catalog": catalog,
            "observation": {"catalog": catalog, "status": "decision", "value": i},
        }
        for i in range(count + 1)
    ]
    if terminal:
        inputs[-1]["observation"]["status"] = "terminal"
    trace = tmp_path / "behavior.jsonl"
    rows = []
    with trace.open("wb") as stream:
        for i in range(count):
            stream.write(
                json_bytes(
                    {
                        "mu": mu,
                        "lease": "actor/0",
                        "scores": [0.0, 0.0],
                        "acquisition_id": inputs[i]["acquisition_id"],
                        "input_sha256": digest(p1_json_bytes(inputs[i])),
                        "catalog_sha256": digest(p1_json_bytes(catalog)),
                    }
                )
            )
            rows.append(
                {
                    "schema": "sts2.policy-runtime/verified-global-transition-1",
                    "run_id": "native-run",
                    "step_index": i,
                    "result_sequence": i + 1,
                    "verified_through_sequence": i + 2,
                    "generated_at": "original-time",
                    "input": inputs[i],
                    "successor": inputs[i + 1],
                    "directive": {
                        "directive": {
                            "selection": {"action_id": "first"},
                            "scores": {"values": [0.0, 0.0]},
                        }
                    },
                    "result": {"status": "completed"},
                    "original_paths": ["global-source.json"],
                }
            )
    return exchange, mu, run, trace, rows


@pytest.mark.parametrize("count", [1, 64])
def test_whole_verified_segment_survives_store_exchange_cold_read(tmp_path, count):
    exchange, mu, run, trace, rows = fixture(tmp_path, count)
    delivered = []

    def commit(segment):
        manifest = publish_segment(exchange.store, segment, PRODUCER)
        record = {key: segment[key] for key in ("segment_id", "run_id", "rows", "mu")}
        record["segment_artifact_id"] = manifest.artifact_id
        exchange.publish_segment("actor", len(delivered), record)
        delivered.append(segment)

    assembler = SegmentAssembler(
        {"behavior_policy": {"temperature": 1.0, "uniform_mix": 0.0}},
        OriginalBarrier(run, trace),
        commit,
    )
    for row in rows:
        assembler.transition(row)
    assembler.finish(None)
    assembler.close()
    cold = Exchange(exchange.binding, "segments")
    record = cold.segment_record(cold.shard_keys()[0])
    restored = read_segment(cold.store, record["segment_artifact_id"], scratch=tmp_path)
    assert restored == delivered[0] and restored["rows"] == count and restored["mu"] == mu
    original = restored["original_commit"]
    assert (
        original["journal_prefix_sha256"]
        == hashlib.sha256((run / "events.jsonl").read_bytes()).hexdigest()
    )
    assert original["verified_through_sequence"] == count + 1
    assert original["behavior_trace_bytes"] == trace.stat().st_size
    assert original["behavior_trace_prefix_sha256"] == (
        hashlib.sha256(trace.read_bytes()).hexdigest())
    bad = copy.deepcopy(restored)
    bad["steps"][-1]["behavior"]["mu"]["snapshot_sha256"] = "b" * 64
    with pytest.raises(BoundaryError, match="transition_behavior_binding_changed"):
        publish_segment(cold.store, bad, PRODUCER)
    with pytest.raises(BoundaryError, match="immutable_key_collision"):
        cold.publish_segment("actor", 0, {**record, "published_at": 1})


def test_terminal_tail_waits_for_normal_typed_label_and_unknown_is_not_a_loss(tmp_path):
    _, _, run, trace, rows = fixture(tmp_path, 1, terminal=True)
    delivered = []
    assembler = SegmentAssembler(
        {"behavior_policy": {"temperature": 1.0, "uniform_mix": 0.0}},
        OriginalBarrier(run, trace),
        delivered.append,
    )
    assembler.transition(rows[0])
    assembler.finish(None)
    assert not delivered
    label = {"class": 4, "reward": 0.5 * (4 / 48) ** 2, "typed_original": "original-id"}
    assembler.finish(label)
    assert delivered[0]["terminal"] == label and delivered[0]["rows"] == 1
    assembler.close()


def test_lost_segment_admission_receipt_does_not_repeat_the_original_offer(tmp_path):
    _, _, run, trace, rows = fixture(tmp_path, 64)
    calls = []

    def commit(segment):
        calls.append(segment["segment_id"])
        raise TimeoutError("accepted remotely, reply unavailable")

    assembler = SegmentAssembler(
        {"behavior_policy": {"temperature": 1.0, "uniform_mix": 0.0}},
        OriginalBarrier(run, trace),
        commit,
    )
    with pytest.raises(TimeoutError):
        for row in rows:
            assembler.transition(row)
    with pytest.raises(BoundaryError, match="requires_reconciliation"):
        assembler.finish(None)
    assert calls == ["actor/0"]
    assert len(assembler.steps) == 64
    assembler.close()
