"""Content-addressed Input-v2 shards on the existing ArtifactStore.

A pack replaces repeated projection/encoding, never the owning snapshot verifier,
Curation admission, split, weights or target semantics. Original payload hashes
are still verified by the normal worker before consuming these derived inputs.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

from .data import digest, json_bytes
from .dataset import SnapshotReader, projection_payload_role
from .formal_training import Population
from .input import V2_INPUT_SPEC, project
from .model import MStd
from .tensor_input import TensorFrame, compile_frame
from .value_targets import from_run_facts
from .weighting import point_weights

SCHEMA = "stpd/p1-mstd-tensor-pack-1"
CODE_FILES = ("tensor_pack.py", "tensor_input.py", "model.py", "input.py", "input_order.py",
              "input_identity.py", "vocabulary.py", "public_vocabulary_v1.json", "dataset.py",
              "data.py", "value_targets.py", "weighting.py", "cohort.py", "spec.py", "execution.py")
SHARD_POINTS = 128


def compiler_identity() -> dict[str, str]:
    root = Path(__file__).parent
    return {**{name: digest((root / name).read_bytes()) for name in CODE_FILES},
            "checkpoint_codec.py": digest(
                (root.parent / "workers/checkpoint_codec.py").read_bytes()),
            "uv.lock": digest((root.parents[1] / "uv.lock").read_bytes())}


def pack_binding(reader: SnapshotReader, vocabulary: dict[str, Any],
                 weighting: dict[str, Any] | None, value_heads: bool) -> dict[str, Any]:
    return {"snapshot_id": reader.manifest.artifact_id,
            "selection_sha256": reader.manifest.payload("selection").sha256,
            "input_spec": vocabulary["input_spec"],
            "vocabulary_sha256": digest(json_bytes(vocabulary)),
            "loss_weighting_sha256": digest(json_bytes(weighting)), "value_heads": value_heads,
            "compiler": compiler_identity()}


def _populations(rows: dict[str, list[tuple[Any, ...]]], weighted: bool,
                 value_heads: bool) -> dict[str, Population]:
    return {split: Population(tuple(r[1] for r in sorted(samples)),
                             tuple(r[2] for r in sorted(samples)),
                             tuple(r[0] for r in sorted(samples)),
                             tuple(r[3] for r in sorted(samples)) if split == 'train' and weighted
                             else (), tuple(r[4] for r in sorted(samples)) if value_heads else ())
            for split, samples in rows.items()}


def population_identity(populations: dict[str, Population]) -> dict[str, Any]:
    return {split: {"N": len(p.frames), "points_sha256": digest(json_bytes(p.point_ids)),
                    "targets_sha256": digest(json_bytes(p.targets)),
                    "weights_sha256": digest(json_bytes(p.weights)),
                    "values_sha256": digest(json_bytes(p.values))}
            for split, p in populations.items()}


def publish_tensor_pack(store: ArtifactStore, reader: SnapshotReader, vocabulary: dict[str, Any],
                        producer: Producer, *, loss_weighting: dict[str, Any] | None = None,
                        value_heads: bool = False) -> tuple[Manifest, dict[str, Population]]:
    if vocabulary.get("input_spec") != V2_INPUT_SPEC:
        raise BoundaryError("p1_tensor_pack", "v2_input_required")
    from .execution import MStdExecution

    # Encoding has no learned state and must not consume the caller's RNG.
    with torch.random.fork_rng(devices=[]):
        model = MStd(vocabulary, MStdExecution.for_plan("mstd-training-v1"))
    selection = reader.selection
    weights = point_weights(loss_weighting, selection) if loss_weighting is not None else {}
    rows: dict[str, list[tuple[Any, ...]]] = {s: [] for s in ("train", "dev", "test")}
    points = reader.iter_supervised_points() if value_heads else reader.iter_points()
    payloads = []
    shards: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    def flush() -> None:
        if not pending:
            return
        role = f"shard_{len(shards):05d}"
        raw = encode_checkpoint({"schema": SCHEMA, "rows": pending})
        payload = store.put_payload(role, io.BytesIO(raw), "application/vnd.stpd.tensor-tree")
        payloads.append(payload)
        shards.append({"role": role, "sha256": payload.sha256, "N": len(pending)})
        pending.clear()

    for reference, observation in points:
        raw_frame = project(observation, input_projection=V2_INPUT_SPEC)
        frame = compile_frame(raw_frame, model)
        target = frame.choices.index(reference["chosen"])
        point = reference["point_id"]
        split = reference["split"]
        weight = weights[point] if split == "train" and weights else 1.
        values = from_run_facts(reference["auxiliary_run_facts"]) if value_heads else None
        rows[split].append((point, frame, target, weight, values))
        pending.append({"point_id": point, "split": split, "target": target, "weight": weight,
                        "values": values, "frame": frame.state()})
        if len(pending) == SHARD_POINTS:
            flush()
    flush()
    populations = _populations(rows, bool(weights), value_heads)
    binding = pack_binding(reader, vocabulary, loss_weighting, value_heads)
    sources = [{"projection_id": s["projection_id"], "payload_sha256": store.get_manifest(
        s["projection_id"]).payload(projection_payload_role(store.get_manifest(
            s["projection_id"]))).sha256} for s in selection["sources"]]
    index = {"schema": SCHEMA, "binding": binding, "compiler_producer": producer.to_dict(),
             "sources": sources, "shards": shards,
             "partitions": population_identity(populations)}
    payloads.append(store.put_payload("index", io.BytesIO(json_bytes(index)), "application/json"))
    manifest = Manifest("feature_set", producer, (Parent("source", reader.manifest.artifact_id),),
                        tuple(payloads), FrozenObject.of({"schema": SCHEMA, "binding": binding,
                                        "compiler_producer": producer.to_dict(),
                                        "partitions": index["partitions"]}))
    store.publish(manifest)
    return manifest, populations


def load_tensor_pack(store: ArtifactStore, pack_id: str, reader: SnapshotReader,
                     vocabulary: dict[str, Any], *, loss_weighting: dict[str, Any] | None = None,
                     value_heads: bool = False) -> dict[str, Population]:
    manifest = store.get_manifest(pack_id)
    params = manifest.parameters.value()
    expected = pack_binding(reader, vocabulary, loss_weighting, value_heads)
    if (manifest.kind != "feature_set" or params.get("schema") != SCHEMA
            or params.get("binding") != expected
            or params.get("compiler_producer") != manifest.producer.to_dict()
            or manifest.parents != (Parent("source", reader.manifest.artifact_id),)):
        raise BoundaryError("p1_tensor_pack", "pack_identity_mismatch")
    index_raw = b"".join(store.read_payload(manifest.payload("index")))
    index = json.loads(index_raw)
    if (index_raw != json_bytes(index) or index.get("schema") != SCHEMA
            or index.get("binding") != expected or index.get("partitions") != params["partitions"]
            or index.get("compiler_producer") != manifest.producer.to_dict()
            or {p.role for p in manifest.payloads} != {
                "index", *(s["role"] for s in index["shards"])}
            or len(index["shards"]) != len({s["role"] for s in index["shards"]})):
        raise BoundaryError("p1_tensor_pack", "pack_index_mismatch")
    sources = [{"projection_id": s["projection_id"], "payload_sha256": store.get_manifest(
        s["projection_id"]).payload(projection_payload_role(store.get_manifest(
            s["projection_id"]))).sha256} for s in reader.selection["sources"]]
    if index["sources"] != sources:
        raise BoundaryError("p1_tensor_pack", "source_payload_binding_mismatch")
    selection = reader.selection
    expected_refs = {p["point_id"]: p for p in selection["points"]}
    expected_weights = (point_weights(loss_weighting, selection)
                        if loss_weighting is not None else {})
    expected_values = ({key: from_run_facts(facts)
                        for key, facts in reader.supervision_by_projection().items()}
                       if value_heads else {})
    rows: dict[str, list[tuple[Any, ...]]] = {s: [] for s in ("train", "dev", "test")}
    for shard in index["shards"]:
        payload = manifest.payload(shard["role"])
        if payload.sha256 != shard["sha256"]:
            raise BoundaryError("p1_tensor_pack", "shard_hash_mismatch")
        state = decode_checkpoint(b"".join(store.read_payload(payload)))
        if state.get("schema") != SCHEMA or len(state["rows"]) != shard["N"]:
            raise BoundaryError("p1_tensor_pack", "shard_count_mismatch")
        for row in state["rows"]:
            frame = TensorFrame.restore(row["frame"])
            reference = expected_refs.get(row["point_id"])
            target = row["target"]
            if (reference is None or row["split"] != reference["split"]
                    or type(target) is not int or not 0 <= target < len(frame.choices)
                    or frame.choices[target] != reference["chosen"]
                    or row["weight"] != expected_weights.get(row["point_id"], 1.)
                    or row["values"] != (expected_values[reference["projection_id"]]
                                         if value_heads else None)):
                raise BoundaryError("p1_tensor_pack", "point_supervision_mismatch")
            rows[row["split"]].append((row["point_id"], frame, row["target"],
                                       row["weight"], row["values"]))
    populations = _populations(rows, loss_weighting is not None, value_heads)
    selection = reader.selection
    expected_points = {s: sorted(p["point_id"] for p in selection["points"] if p["split"] == s)
                       for s in rows}
    if (population_identity(populations) != index["partitions"] or any(
            list(populations[s].point_ids) != expected_points[s] for s in rows)):
        raise BoundaryError("p1_tensor_pack", "pack_population_mismatch")
    return populations


def main(argv: list[str] | None = None) -> int:
    """Build inputs before allocating compute; no optimizer or provider is opened."""
    import argparse

    from spireagent.source import source_identity
    from spireagent.storage.config import open_store
    from spireagent.storage.input_transfer import copy_input_artifact
    from spireagent.storage.read_session import VerifiedReadSession

    from .workload import read_payloads, verify_p1_run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="existing input Store path or s3")
    parser.add_argument("--run", required=True, help="frozen recipe; never changed or submitted")
    parser.add_argument("--destination",
                        help="existing Store for the new pack, default input Store")
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new receipt JSON file")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output receipt already exists; inspect the original result before rebuilding")
    producer = source_identity(Path(__file__).resolve().parents[2])
    source = open_store(args.store)
    destination = open_store(args.destination) if args.destination else source
    with VerifiedReadSession(source, scratch=args.scratch) as session:
        run = session.get_manifest(args.run)
        training = session.get_manifest(run.parent("training_input"))
        session.prefetch(read_payloads(session, training.parent("source"), run_id=args.run))
        workload = verify_p1_run(session, args.run, run.producer)
        if destination is not source:
            copy_input_artifact(session, destination, workload.snapshot.artifact_id, (),
                                scratch=args.scratch)
        pack, populations = publish_tensor_pack(
            destination, workload.reader, workload.vocabulary, producer,
            loss_weighting=workload.training.parameters.value().get("loss_weighting"),
            value_heads=workload.config.value_heads)
        receipt = {"schema": "stpd/p1-tensor-pack-build-1", "pack_id": pack.artifact_id,
                   "producer": producer.to_dict(), "binding": pack.parameters.value()["binding"],
                   "partitions": population_identity(populations),
                   "payload_bytes": sum(p.size for p in pack.payloads),
                   "payloads": [p.to_dict() for p in pack.payloads],
                   "optimizer_updates": 0, "provider_submitted": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
