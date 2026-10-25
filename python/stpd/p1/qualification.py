"""Bounded TRAIN qualification on the existing calibration/Store path.

This writes performance evidence, never a formal Run, TEST result or installed
model. The application records the original Curation use before remote entry.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.store import ArtifactStore

from .calibration import INPUT_SCHEMA, measure, read_input
from .data import digest, json_bytes
from .execution import MStdExecution, microbatches
from .formal_training import MStdTrainer, Population
from .input import Entity, Frame
from .model import MStd
from .numerics import TrainingNumerics
from .spec import MStdConfig
from .workload import populations_from_reader, verify_p1_run

SCHEMA = "stpd/p1-execution-qualification-1"
SCORE_TOLERANCE = {"rtol": 3e-4, "atol": 2e-5}
GRADIENT_TOLERANCE = {"rtol": 3e-4, "atol": 2e-6}


def safe_error(error: BaseException) -> dict[str, str]:
    """Keep the original failure/stack while removing URL/environment secrets."""
    message = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    for key, value in os.environ.items():
        if len(value) >= 8 and re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|ACCESS_KEY", key):
            message = message.replace(value, "[redacted]")
    message = re.sub(r"https?://\S+", "[redacted-url]", message)
    return {"type": type(error).__name__, "code": str(getattr(error, "code", "")),
            "traceback": message[-12000:]}


def require_binding(store: ArtifactStore, input_id: str, binding: dict) -> Manifest:
    capsule = store.get_manifest(input_id)
    if (capsule.parameters.value().get("schema") != INPUT_SCHEMA
            or binding.get("calibration_input_id") != input_id
            or binding.get("source_run_id") != capsule.parent("run")
            or binding.get("checkpoint_id") != capsule.parent("checkpoint")
            or binding.get("snapshot_id") != capsule.parent("snapshot")
            or not re.fullmatch("[0-9a-f]{32}", str(binding.get("operation_id", "")))
            or binding.get("training_use_recorded") is not False
            or binding.get("original_training_use_verified") is not True
            or binding.get("training_use_reference")
            != capsule.parameters.value().get("operation_id")):
        raise BoundaryError("p1_qualification", "original_Curation_use_binding_required")
    return capsule


def shape_report(population: Population, model: MStd) -> tuple[dict, list[int]]:
    rows = []
    for frame in population.frames:
        texts = {text for entity in frame.entities for _, text in entity.text}
        lengths = [len(model.tokenize(text)[0]) for text in texts]
        rows.append({"entities": len(frame.entities), "choices": len(frame.choices),
                     "unique_texts": len(texts), "max_text_tokens": max(lengths, default=0),
                     "text_terms": sum(len(entity.text) for entity in frame.entities),
                     "numeric_terms": sum(len(entity.numeric) for entity in frame.entities),
                     "reference_terms": sum(len(entity.refs) for entity in frame.entities),
                     "scene_attention_area": len(frame.entities) ** 2,
                     "text_attention_area": len(texts) * max(lengths, default=0) ** 2})
    selected: set[int] = set()
    distributions = {}
    for key in rows[0]:
        ordered = sorted(range(len(rows)), key=lambda index: (rows[index][key], index))
        quantiles = {name: ordered[min(len(rows) - 1, int((len(rows) - 1) * fraction))]
                     for name, fraction in (("min", 0), ("p50", .5), ("p95", .95),
                                            ("p99", .99), ("max", 1))}
        selected.update((ordered[0], ordered[-1]))
        if key in {"entities", "max_text_tokens", "choices"}:
            selected.update(quantiles.values())
        distributions[key] = {name: {"value": rows[index][key],
                                     "point_id": population.point_ids[index]}
                              for name, index in quantiles.items()}
    groups = [group for offset in range(0, len(rows), 64)
              for group in microbatches(population.frames,
                                        list(range(offset, min(len(rows), offset + 64))), model)]
    return {"TRAIN_N": len(rows), "distributions": distributions,
            "representative_indices": sorted(selected),
            "representative_point_ids": [population.point_ids[i] for i in sorted(selected)],
            "reference_microbatch_size_counts": {str(size): sum(len(g) == size for g in groups)
                                                    for size in sorted({len(g) for g in groups})},
            "all_TRAIN_shapes_scanned": True}, sorted(selected)


def parity(population: Population, indices: list[int], vocabulary: dict, state: dict,
           execution: MStdExecution, device: str) -> dict:
    import torch
    from torch.nn import functional as F

    reference = MStd(vocabulary).to(device)
    candidate = MStd(vocabulary, execution).to(device)
    reference.load_state_dict(state["model"], strict=True)
    candidate.load_state_dict(state["model"], strict=True)
    rows = []
    for index in indices:
        try:
            frame, target = population.frames[index], population.targets[index]
            outputs, gradients, rngs, losses = [], [], [], []
            for model in (reference, candidate):
                model.train()
                model.zero_grad(set_to_none=True)
                numerics = TrainingNumerics(1, 2, torch.device(device))
                numerics.rng = copy.deepcopy(state["rng"])
                if torch.device(device).type == "cpu":
                    numerics.rng["torch_cuda"] = None
                with numerics.scope():
                    score = model.forward_batch([frame])[0]
                    loss = F.cross_entropy(score[None], torch.tensor([target], device=device))
                    loss.backward()
                outputs.append(score.detach().cpu())
                losses.append(loss.detach().cpu())
                gradients.append({name: (None if parameter.grad is None
                                          else parameter.grad.cpu().clone())
                                  for name, parameter in model.named_parameters()})
                rngs.append(numerics.rng)
            torch.testing.assert_close(outputs[1], outputs[0], rtol=SCORE_TOLERANCE["rtol"],
                                       atol=SCORE_TOLERANCE["atol"])
            torch.testing.assert_close(losses[1], losses[0], rtol=SCORE_TOLERANCE["rtol"],
                                       atol=SCORE_TOLERANCE["atol"])
            maximum = 0.0
            for name, wanted in gradients[0].items():
                actual = gradients[1][name]
                if (wanted is None) != (actual is None):
                    raise BoundaryError("p1_qualification", "gradient_presence_changed:" + name)
                if wanted is not None:
                    assert actual is not None
                    torch.testing.assert_close(actual, wanted, rtol=GRADIENT_TOLERANCE["rtol"],
                                               atol=GRADIENT_TOLERANCE["atol"])
                    maximum = max(maximum, float((actual - wanted).abs().max()))
            for key in ("torch_cpu", "torch_cuda"):
                wanted_rng, actual_rng = rngs[0][key], rngs[1][key]
                if (wanted_rng is None) != (actual_rng is None):
                    raise BoundaryError("p1_qualification", "RNG_inventory_changed")
                if wanted_rng is not None:
                    assert actual_rng is not None
                    if not torch.equal(wanted_rng, actual_rng):
                        raise BoundaryError("p1_qualification", "RNG_draws_changed")
            if rngs[0]["python"] != rngs[1]["python"]:
                raise BoundaryError("p1_qualification", "python_RNG_changed")
            rows.append({"point_id": population.point_ids[index], "entities": len(frame.entities),
                         "choices": len(frame.choices),
                         "loss_abs": float(abs(losses[1] - losses[0])),
                         "scores_max_abs": float((outputs[1] - outputs[0]).abs().max()),
                         "gradients_max_abs": maximum, "gradient_presence_equal": True,
                         "RNG_equal": True})
        except Exception as error:
            error.add_note("TRAIN point_id=" + population.point_ids[index]
                           + "; entities=" + str(len(population.frames[index].entities)))
            raise

    del reference, candidate
    if torch.device(device).type == "cuda":
        torch.cuda.empty_cache()
    return {"score_loss_tolerance": SCORE_TOLERANCE, "gradient_tolerance": GRADIENT_TOLERANCE,
            "training_dropout": True, "source_weights": True, "rows": rows, "passed": True}


def _cache_populations(path: Path, populations: dict[str, Population], vocabulary: dict) -> str:
    value = {"vocabulary": vocabulary, "populations": {
        split: {"frames": [asdict(frame) for frame in population.frames],
                "targets": population.targets, "point_ids": population.point_ids}
        for split, population in populations.items()}}
    path.write_bytes(json_bytes(value))
    return digest(path.read_bytes())


def _read_cache(path: Path, expected: str) -> tuple[dict[str, Population], dict]:
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise BoundaryError("p1_qualification", "projection_cache_checksum_mismatch")
    value = json.loads(raw)
    populations = {}
    for split, row in value["populations"].items():
        frames = tuple(Frame([
            Entity(entity["kind"], entity["position"],
                   [tuple(term) for term in entity["identities"]],
                   [tuple(term) for term in entity["text"]],
                   [tuple(term) for term in entity["numeric"]], entity["refs"])
            for entity in frame["entities"]], frame["choices"],
            [tuple(role) for role in frame["roles"]]) for frame in row["frames"])
        populations[split] = Population(frames, tuple(row["targets"]), tuple(row["point_ids"]))
    return populations, value["vocabulary"]


def _publish_checkpoint(store: ArtifactStore, raw: bytes, input_id: str,
                        producer: Producer) -> Manifest:
    payload = store.put_payload("checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree")
    manifest = Manifest("analysis", producer, (Parent("calibration_input", input_id),), (payload,),
                        FrozenObject.of({"schema": SCHEMA + "-checkpoint", "formal_run": False}))
    store.publish(manifest)
    return manifest


def child(job: dict, mode: str) -> dict:
    """A replacement OS process reads the whole CP via a fresh Store, then advances."""
    import torch

    from stpd.workers.checkpoint_codec import decode_checkpoint

    from .export import export_model, load_model

    populations, vocabulary = _read_cache(Path(job["cache"]), job["cache_sha256"])
    config = MStdConfig.decode(job["config"])
    trainer = MStdTrainer(populations, vocabulary, job["snapshot_id"], config,
                          MStdExecution.decode(job["execution"]))
    store = open_store(job["store"])
    producer = Producer.decode(job["producer"])
    checkpoint_path = Path(job["directory"]) / "saved.tensor-tree"
    started = time.perf_counter()
    if mode == "train":
        timings = []
        for _ in range(job["steps"]):
            row = trainer.advance()
            timings.append(row["timing"])
        raw = trainer.checkpoint()
        saved = _publish_checkpoint(store, raw, job["input_id"], producer)
        checkpoint_path.write_bytes(raw)
        selected = trainer.selected_model() if trainer.best_weights is not None else None
        trainer.advance()
        expected = trainer.checkpoint()
        result = {"steps": job["steps"], "checkpoint_id": saved.artifact_id,
                  "checkpoint_sha256": digest(raw), "checkpoint_bytes": len(raw),
                  "expected_next_state_sha256": digest(expected), "updates": timings,
                  "DEV_checks": len(trainer.dev_history) + len(trainer.checkpoint_dev_history),
                  "TEST_scored": False, "pid": os.getpid()}
        if selected is not None:
            package = Path(job["directory"]) / "export"
            descriptor = export_model(selected, checkpoint_path, package, trainer.identity)
            loaded, _ = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
            frame = populations["train"].frames[0]
            with torch.inference_mode():
                torch.testing.assert_close(loaded(frame), selected(frame), rtol=0, atol=0)
            payload = store.put_payload("model_package", io.BytesIO(
                (package / "model.tensor-tree").read_bytes()), "application/vnd.stpd.tensor-tree")
            result["export"] = {"package_sha256": descriptor["package_sha256"],
                                 "payload": payload.to_dict(), "reader_equal": True,
                                 "installed": False, "registered": False}
    else:
        previous = json.loads((Path(job["directory"]) / "train-result.json").read_bytes())
        saved = store.get_manifest(previous["checkpoint_id"])
        cold_directory = Path(job["directory"]) / "cold-readback"
        cold_directory.mkdir(exist_ok=False)
        raw = b"".join(store.read_payload(saved.payload("checkpoint")))
        (cold_directory / "checkpoint.tensor-tree").write_bytes(raw)
        if digest(raw) != previous["checkpoint_sha256"]:
            raise BoundaryError("p1_qualification", "cold_checkpoint_readback_mismatch")
        trainer.restore(raw)
        if digest(trainer.checkpoint()) != previous["checkpoint_sha256"]:
            raise BoundaryError("p1_qualification", "full_restore_state_mismatch")
        trainer.advance()
        actual = digest(trainer.checkpoint())
        if actual != previous["expected_next_state_sha256"]:
            raise BoundaryError("p1_qualification", "replacement_next_update_state_mismatch")
        state = decode_checkpoint(raw)
        result = {"pid": os.getpid(), "original_pid": previous["pid"],
                  "fresh_Store_full_readback": True, "empty_directory_readback": True,
                  "readback_bytes": len(raw), "restored_step": state["step"],
                  "continued_step": trainer.step, "complete_state_equal": True,
                  "expected_next_state_sha256": actual, "TEST_scored": False}
    result["wall_seconds"] = time.perf_counter() - started
    result["peak_allocated_bytes"] = (torch.cuda.max_memory_allocated()
                                        if trainer.device.type == "cuda" else None)
    return result


def qualify(store: ArtifactStore, input_id: str, producer: Producer, plan: dict,
            binding: dict, directory: Path, *, steps: int = 256, device: str = "cuda:0",
            store_location: str = "s3") -> Manifest:
    import torch

    if type(steps) is not int or not 200 <= steps <= 320:
        raise BoundaryError("p1_qualification", "bounded_200_to_320_updates_required")
    directory.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"schema": SCHEMA, "state": "running", "phase": "binding",
                              "binding": binding, "steps": steps, "TEST_scored": False,
                              "formal_run_created": False, "device": device,
                              "producer": producer.to_dict(), "phase_seconds": {}}
    began = previous = time.perf_counter()

    def phase(name: str) -> None:
        nonlocal previous
        now = time.perf_counter()
        report["phase_seconds"][report["phase"]] = now - previous
        report["phase"] = name
        previous = now
        partial = Manifest("performance", producer, (Parent("calibration_input", input_id),),
                           parameters=FrozenObject.of(report))
        store.publish(partial)
        progress = directory / "progress.json"
        temporary = directory / ".progress.json"
        temporary.write_bytes(json_bytes({"state": report["state"], "phase": name,
                                          "performance_id": partial.artifact_id}))
        temporary.replace(progress)
        print(json.dumps({"phase": name, "performance_id": partial.artifact_id}),
              file=sys.stderr, flush=True)

    try:
        capsule = require_binding(store, input_id, binding)
        phase("verify_full_snapshot")
        workload = verify_p1_run(store, capsule.parent("run"),
                                  store.get_manifest(capsule.parent("run")).producer)
        phase("project_full_populations")
        populations = populations_from_reader(workload.reader)
        report["partition_N"] = {key: len(value.frames) for key, value in populations.items()}
        del workload
        phase("source_checkpoint")
        _, _, _, vocabulary, source_state = read_input(store, input_id)
        config = MStdConfig.decode(capsule.parameters.value()["source_config"])
        config = MStdConfig.decode({**config.to_dict(), "device": device})
        execution = MStdExecution.for_plan("mstd-training-v1")
        report["selected_execution"] = execution.to_dict()
        report["execution_plan"] = "mstd-training-v1"
        report["config"] = config.to_dict()
        phase("TRAIN_shapes")
        model = MStd(vocabulary)
        report["shapes"], indices = shape_report(populations["train"], model)
        del model
        phase("loss_gradient_parity")
        report["parity"] = parity(populations["train"], indices, vocabulary, source_state,
                                   execution, device)
        del source_state
        phase("ABBA_throughput")
        throughput = measure(store, input_id, producer, plan, device=device)
        report["throughput_id"] = throughput.artifact_id
        report["throughput"] = throughput.parameters.value()
        phase("projection_cache")
        cache = directory / "populations.json"
        cache_sha = _cache_populations(cache, populations, vocabulary)
        del populations, vocabulary
        if torch.device(device).type == "cuda":
            torch.cuda.empty_cache()
        job = {"cache": str(cache), "cache_sha256": cache_sha,
               "config": config.to_dict(), "snapshot_id": capsule.parent("snapshot"),
               "execution": execution.to_dict(), "store": store_location,
               "producer": producer.to_dict(), "directory": str(directory),
               "input_id": input_id, "steps": steps}
        job_path = directory / "job.json"
        job_path.write_bytes(json_bytes(job))
        for mode in ("train", "resume"):
            phase("short_" + mode)
            result_path = directory / (mode + "-result.json")
            completed = subprocess.run([sys.executable, "-m", "stpd.p1.qualification",
                                        "--child", mode, "--job", str(job_path),
                                        "--result", str(result_path)],
                                       capture_output=True, text=True, check=False)
            if not result_path.is_file():
                report["child_returncode"] = completed.returncode
                report["child_diagnostic"] = safe_error(RuntimeError(completed.stderr[-8000:]))
                raise BoundaryError("p1_qualification", "child_result_missing:" + mode)
            result = json.loads(result_path.read_bytes())
            report[mode] = result
            if completed.returncode or result.get("state") == "failed":
                raise BoundaryError("p1_qualification", "child_failed:" + mode)
        if report["train"]["pid"] == report["resume"]["pid"]:
            raise BoundaryError("p1_qualification", "replacement_process_not_observed")
        report["state"] = "completed"
    except Exception as error:
        report.update(state="failed", error=safe_error(error))
    finally:
        failed_phase = report["phase"] if report["state"] == "failed" else None
        phase("publication")
        if failed_phase is not None:
            report["failed_phase"] = failed_phase
        report["total_wall_seconds"] = time.perf_counter() - began
    from spireagent.artifact_contracts import Payload

    parents = [Parent("calibration_input", input_id)]
    payloads = []
    if "checkpoint_id" in report.get("train", {}):
        parents.append(Parent("qualification_checkpoint", report["train"]["checkpoint_id"]))
    if "export" in report.get("train", {}):
        payloads.append(Payload.decode(report["train"]["export"]["payload"]))
    artifact = Manifest("performance", producer, tuple(parents), tuple(payloads),
                        parameters=FrozenObject.of(report))
    store.publish(artifact)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="s3")
    parser.add_argument("--input")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument("--expected-source")
    parser.add_argument("--expected-lock")
    parser.add_argument("--child", choices=("train", "resume"))
    parser.add_argument("--job", type=Path)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args(argv)
    if args.child:
        try:
            value = child(json.loads(args.job.read_bytes()), args.child)
        except Exception as error:
            value = {"state": "failed", "error": safe_error(error)}
        args.result.write_bytes(json_bytes(value))
        return 1 if value.get("state") == "failed" else 0
    producer = source_identity(Path(__file__).resolve().parents[2])
    if (producer.source_revision != args.expected_source
            or producer.uv_lock_sha256 != args.expected_lock):
        raise BoundaryError("p1_qualification", "exact_worker_source_and_lock_required")
    artifact = qualify(open_store(args.store), args.input, producer,
                       json.loads(args.plan.read_bytes()), json.loads(args.binding.read_bytes()),
                       args.directory, steps=args.steps, device=args.device,
                       store_location=args.store)
    value = artifact.parameters.value()
    print(json.dumps({"state": value["state"], "performance_id": artifact.artifact_id,
                      "phase": value.get("failed_phase", value["phase"])}))
    return 0 if value["state"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
