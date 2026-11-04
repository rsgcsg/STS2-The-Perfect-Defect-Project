"""Read-only historical segment encodings through the real CAS and cold reader."""

from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import io
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from test_rl_loop import PRODUCER, _model_artifact

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.rl_exchange import SEGMENT_SCHEMA, Exchange, _sealed
from spireagent.storage.archives import _extract, archive_bundle
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.managed_segment_store import (
    COMPILED_SCHEMA,
    MAX_BYTES,
    SCHEMA,
    _parents,
    read_segment,
    validate_segment,
)
from spireagent.workbench.rl_segment_producer import OriginalBarrier as HotOriginalBarrier
from stpd.p1.data import digest
from stpd.p1.data import json_bytes as p1_json_bytes
from stpd.rl.segment_inputs import segment_body


# Frozen historical fixture writers live only in tests.
def historical_segment(run, trace, rows):
    """Construct one frozen old encoding to exercise the historical reader."""
    behaviors = [json.loads(raw) for raw in trace.read_bytes().splitlines()]
    for row, behavior in zip(rows, behaviors, strict=True):
        probabilities = [1 / len(behavior["scores"])] * len(behavior["scores"])
        row.update(behavior=behavior, sampling={"mode": "temperature_uniform",
            "temperature": 1.0, "uniform_mix": 0.0, "probabilities": probabilities,
            "model_probabilities": probabilities, "model_logits": behavior["scores"],
            "selected_probability": probabilities[0]})
    barrier = HotOriginalBarrier(run, trace)
    try:
        original, _ = barrier.commit(rows, trace_through_bytes=trace.stat().st_size)
    finally:
        barrier.close()
    original.pop("representation")
    original["schema"] = "stpd/segment-original-commit-1"
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        (stage / "originals").mkdir()
        for ref in original["artifacts"]:
            (stage / "originals" / ref["path"]).write_bytes((run / ref["path"]).read_bytes())
        (stage / "events-delta.jsonl").write_bytes((run / "events.jsonl").read_bytes())
        (stage / "behavior-delta.jsonl").write_bytes(trace.read_bytes())
        (stage / "commit.json").write_bytes(json_bytes(original))
        raw = archive_bundle(stage)
    original.update(archive_bytes=len(raw), archive_sha256=hashlib.sha256(raw).hexdigest())
    value = {"schema": SCHEMA, "training_split": "TRAIN", "identity": {},
        "run_id": rows[0]["run_id"], "rows": len(rows),
        "segment_id": behaviors[0]["lease"], "mu": behaviors[0]["mu"], "steps": rows,
        "terminal": None, "hp_battles": [], "original_commit": original,
        "previous_segment_artifact_id": None}
    return value, raw


def publish_historical_record(exchange, actor, sequence, record):
    raw = _sealed({**record, "schema": SEGMENT_SCHEMA, "run": exchange.run,
                   "actor": actor, "sequence": sequence})
    exchange.blobs.put_if_absent(f"{exchange.prefix}shards/{actor}/{sequence:08d}.json", raw)
    return json.loads(raw)


def publish_segment(
    store: ManifestArtifactStore, segment: dict[str, Any], producer: Producer, *, originals: bytes
) -> Manifest:
    validate_segment(segment)
    trajectory = segment
    compiled_payload = None
    if segment["schema"] == COMPILED_SCHEMA:
        compiled = json_bytes(segment_body(segment))
        if len(compiled) > MAX_BYTES:
            raise BoundaryError("trajectory_segment", "compiled_segment_byte_limit")
        compiled_payload = store.put_bytes("compiled_inputs", gzip.compress(
            compiled, compresslevel=1, mtime=0), "application/gzip")
        trajectory = copy.deepcopy(segment)
        for step in trajectory["steps"]:
            step["behavior"].pop("compiled_input")
        trajectory.pop("compiled_boundary")
    raw = json_bytes(trajectory)
    if len(raw) > MAX_BYTES:
        raise BoundaryError("trajectory_segment", "segment_byte_limit")
    parents = _parents(store, segment)
    expected = segment["original_commit"]
    if (
        len(originals) != expected["archive_bytes"]
        or hashlib.sha256(originals).hexdigest() != expected["archive_sha256"]
    ):
        raise BoundaryError("trajectory_segment", "original_block_integrity_failure")
    original_payload = store.put_payload("originals", io.BytesIO(originals), "application/gzip")
    # The existing observer may continue to trail the game while this bounded
    # upload/readback completes. Its lease stays charged, providing backpressure.
    observed = hashlib.sha256()
    for chunk in store.read_payload(original_payload):
        observed.update(chunk)
    if observed.hexdigest() != expected["archive_sha256"]:
        raise BoundaryError("trajectory_segment", "original_block_peer_readback_failure")
    payload = store.put_payload(
        "trajectory", io.BytesIO(gzip.compress(raw, compresslevel=1, mtime=0)), "application/gzip"
    )
    manifest = Manifest(
        "analysis",
        producer,
        parents=parents,
        payloads=tuple(sorted((payload, original_payload) + (
            (compiled_payload,) if compiled_payload is not None else ()))),
        parameters=FrozenObject.of(
            {
                "schema": segment["schema"],
                "run_id": segment["run_id"],
                "segment_id": segment["segment_id"],
                "rows": len(segment["steps"]),
                "start_step": segment["steps"][0]["step_index"],
                "end_step": segment["steps"][-1]["step_index"],
                "decoded_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mu": segment["mu"],
            }
        ),
    )
    store.publish(manifest)
    return manifest




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
        "updates": 0,
        "precision": "fp32",
    }
    run = tmp_path / "run"
    run.mkdir()
    for name in ("manifest.json", "agent-manifest.json", "adapter-attestation.json"):
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
                    "manifest_original_base64": base64.b64encode(b"{}\n").decode(),
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
    segment, originals = historical_segment(run, trace, rows)
    manifest = publish_segment(exchange.store, segment, PRODUCER, originals=originals)
    record = {key: segment[key] for key in ("segment_id", "run_id", "rows", "mu")}
    record["segment_artifact_id"] = manifest.artifact_id
    publish_historical_record(exchange, "actor", 0, record)
    delivered = [segment]
    record = exchange.segment_record(exchange.shard_keys()[0])
    cold = Exchange({"kind": "local", "directory": str(tmp_path/"empty-peer")}, "segments")
    copied_id = copy_artifact(exchange.store, cold.store, record["segment_artifact_id"],
                              scratch=tmp_path)
    manifest = cold.store.get_manifest(copied_id)
    publish_historical_record(cold, "actor", 0, {key: record[key] for key in (
        "segment_id", "run_id", "rows", "mu", "segment_artifact_id")})
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
        hashlib.sha256(trace.read_bytes()).hexdigest()
    )
    raw = b"".join(cold.store.read_payload(manifest.payload("originals")))
    _extract(raw, tmp_path/"recovered-originals")
    assert (tmp_path/"recovered-originals/originals/global-source.json").read_bytes() == (
        run/"global-source.json").read_bytes()
    assert (tmp_path/"recovered-originals/events-delta.jsonl").read_bytes() == (
        run/"events.jsonl").read_bytes()
    assert (tmp_path/"recovered-originals/behavior-delta.jsonl").read_bytes() == trace.read_bytes()
    with pytest.raises(BoundaryError, match="original_block_integrity_failure"):
        publish_segment(cold.store, restored, PRODUCER, originals=b"wrong original block")
    bad = copy.deepcopy(restored)
    bad["steps"][-1]["behavior"]["mu"]["snapshot_sha256"] = "b" * 64
    with pytest.raises(BoundaryError, match="transition_behavior_binding_changed"):
        publish_segment(cold.store, bad, PRODUCER, originals=b"invalid; not reached")
    with pytest.raises(BoundaryError, match="immutable_key_collision"):
        publish_historical_record(cold, "actor", 0, {**record, "published_at": 1})


def test_terminal_battle_hp_uses_public_terminal_even_if_combat_span_remains_open():
    from spireagent.workbench.rl_segment_producer import completed_auxiliary

    observations = [{"observation": {"status": "decision"}} for _ in range(2)]
    observations.append(
        {
            "observation": {
                "status": "terminal",
                "scene": {"native_kind": "game_over"},
                "player": {"content": {"hp": 0}},
            }
        }
    )
    episode = {
        "run_id": "native",
        "observations": observations,
        "combat_spans": [{"start_observation_index": 0, "end_observation_index": None}],
        "steps": [{"index": i, "observation_index": i} for i in range(2)],
    }
    segments = [{"start_step": 0, "rows": 2, "segment_id": "actor/0",
                 "training_generation": "g-unit"}]
    result = completed_auxiliary(episode, segments, {"class": 3, "reward": 0.01})
    assert [row["hp"] for row in result["hp_battles"][0]["states"]] == [0.0, 0.0]
    observations[-1]["observation"]["player"]["content"]["hp"] = None
    assert completed_auxiliary(episode, segments, {"class": 3, "reward": 0.01})["hp_battles"] == []
