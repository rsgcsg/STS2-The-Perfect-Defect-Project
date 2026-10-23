"""Bounded request/receipt tool for one isolated full-state CUDA qualification.

It never submits compute, selects a live switch point, edits original artifacts,
or changes a continuation/budget policy. The operator first prepares an isolated
Run through normal Application/Curation, then submits the emitted ComputeRequest
through the existing Scheduler/provider. Both calls stop inside the same epoch;
TEST is never scored. The source checkpoint must remain an approved clone.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ArtifactStore, ManifestArtifactStore
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest
from stpd.cloud_jobs.execution import preflight_training_request, validate_receipt
from stpd.workers.checkpoint_codec import encode_checkpoint
from stpd.workers.p1_run import verify_p1_checkpoint

from .data import digest
from .derivation import source_workload, state_sha256
from .spec import DERIVED_RUN_SCHEMA
from .workload import verify_p1_run

UPDATES_PER_PROCESS = 2
TOTAL_UPDATES = 4


def initial_request(
    store: ArtifactStore,
    run_id: str,
    producer: Producer,
    attempt_id: str,
) -> ComputeRequest:
    workload = verify_p1_run(store, run_id, producer)
    if workload.run.parameters.value().get("schema") != DERIVED_RUN_SCHEMA:
        raise BoundaryError("p1_derivation_QA", "explicit_derived_run_required")
    _, original = source_workload(store, workload.run.parent("original_checkpoint"))
    state = original.state
    # A full epoch can trigger early stopping and normal TEST. Stay strictly
    # inside this source epoch for both calls; data is complete, never truncated.
    if (
        state["sampler"]["cursor"] + TOTAL_UPDATES * workload.config.batch_size
        >= workload.training.parameters.value()["partition_N"]["train"]
    ):
        raise BoundaryError("p1_derivation_QA", "four_full_updates_before_epoch_end_required")
    request = ComputeRequest(
        "training", run_id, producer, attempt_id, stop_after=UPDATES_PER_PROCESS
    )
    preflight_training_request(store, request)
    return request


def _paused(store: ArtifactStore, request: ComputeRequest, receipt: ComputeReceipt) -> Any:
    validate_receipt(store, request, receipt)
    if receipt.state != "paused" or receipt.output_id is not None or receipt.checkpoint_id is None:
        raise BoundaryError("p1_derivation_QA", "paused_TRAIN_only_receipt_required")
    workload = verify_p1_run(store, request.input_id, request.producer)
    checked = verify_p1_checkpoint(store, receipt.checkpoint_id, workload)
    if checked.state["phase"] != "training" or checked.state["test_result"] is not None:
        raise BoundaryError("p1_derivation_QA", "TRAIN_only_no_TEST_required")
    return checked


def replacement_request(
    store: ArtifactStore,
    first: ComputeRequest,
    receipt: ComputeReceipt,
    attempt_id: str,
) -> ComputeRequest:
    checked = _paused(store, first, receipt)
    planned = initial_request(store, first.input_id, first.producer, first.attempt_id)
    if first != planned or attempt_id == first.attempt_id:
        raise BoundaryError("p1_derivation_QA", "exact_initial_and_new_attempt_required")
    _, source = source_workload(
        store, store.get_manifest(first.input_id).parent("original_checkpoint")
    )
    if checked.state["step"] != source.state["step"] + UPDATES_PER_PROCESS:
        raise BoundaryError("p1_derivation_QA", "first_update_count_mismatch")
    request = replace(first, attempt_id=attempt_id, resume=receipt.checkpoint_id)
    preflight_training_request(store, request)
    return request


def qualification_report(
    store: ManifestArtifactStore,
    first: ComputeRequest,
    first_receipt: ComputeReceipt,
    second: ComputeRequest,
    second_receipt: ComputeReceipt,
) -> dict[str, Any]:
    if second != replacement_request(store, first, first_receipt, second.attempt_id):
        raise BoundaryError("p1_derivation_QA", "replacement_request_mismatch")
    checked = _paused(store, second, second_receipt)
    workload = verify_p1_run(store, first.input_id, first.producer)
    _, source = source_workload(store, workload.run.parent("original_checkpoint"))
    if checked.state["step"] != source.state["step"] + TOTAL_UPDATES:
        raise BoundaryError("p1_derivation_QA", "total_update_count_mismatch")
    roots = [
        e.parameters.value()["details"]["checkpoint_id"]
        for e in ObjectStoreRunReporter(store, store.blobs).events(first.input_id)
        if e.parameters.value().get("kind") == "checkpoint"
        and e.parameters.value().get("details", {}).get("original_checkpoint")
        == source.manifest.artifact_id
    ]
    if len(roots) != 1:
        raise BoundaryError("p1_derivation_QA", "one_initial_derivation_event_required")
    root = verify_p1_checkpoint(store, roots[0], workload)
    if state_sha256(root.state) != state_sha256(source.state):
        raise BoundaryError("p1_derivation_QA", "initial_full_state_not_preserved")
    hashes = {
        name: {
            "source": digest(encode_checkpoint({"value": source.state[name]})),
            "after_four_updates": digest(encode_checkpoint({"value": checked.state[name]})),
        }
        for name in ("model", "optimizer", "rng", "sampler")
    }
    if any(
        hashes[name]["source"] == hashes[name]["after_four_updates"]
        for name in ("model", "optimizer")
    ):
        raise BoundaryError("p1_derivation_QA", "real_model_and_AdamW_updates_required")
    return {
        "schema": "stpd/p1-full-state-derivation-qualification-1",
        "run_id": first.input_id,
        "original_run": workload.run.parent("original_run"),
        "original_checkpoint": source.manifest.artifact_id,
        "initial_derived_checkpoint": root.manifest.artifact_id,
        "initial_preserved_state_sha256": state_sha256(root.state),
        "first_request_id": first.request_id,
        "second_request_id": second.request_id,
        "last_checkpoint": checked.manifest.artifact_id,
        "starting_step": source.state["step"],
        "ending_step": checked.state["step"],
        "runtime_compatibility": root.manifest.parameters.value()["runtime_compatibility"],
        "execution_policy": workload.execution.to_dict(),
        "state_hashes": hashes,
        "TRAIN_only": True,
        "TEST_scored": False,
        "future_bitwise_equivalence_required": False,
        "quality_or_live_switch_qualified": False,
    }


def fixed_dev_comparison(store: ArtifactStore, run_id: str, checkpoint_id: str) -> dict[str, Any]:
    """Same final weights, complete frozen DEV, reference vs target; descriptive only.

    Run on the explicitly provisioned qualification worker after both update calls.
    No optimizer step, TEST evaluation, selection mutation or artifact publication.
    """
    import torch

    from .execution import MStdExecution
    from .formal_training import MStdTrainer
    from .model import MStd
    from .workload import populations_from_reader

    run = store.get_manifest(run_id)
    workload = verify_p1_run(store, run_id, run.producer)
    checkpoint = verify_p1_checkpoint(store, checkpoint_id, workload)
    if checkpoint.state["phase"] != "training" or checkpoint.state["test_result"] is not None:
        raise BoundaryError("p1_derivation_QA", "unfinished_TRAIN_checkpoint_required")
    trainer = MStdTrainer(
        populations_from_reader(workload.reader),
        workload.vocabulary,
        workload.snapshot.artifact_id,
        workload.config,
        workload.execution,
    )
    trainer.restore(checkpoint.raw)
    with trainer._numerics.scope(), torch.random.fork_rng(devices=[]):
        reference = MStd(workload.vocabulary, MStdExecution()).to(trainer.device)
        reference.load_state_dict(trainer.model.state_dict(), strict=True)
        ref = trainer.evaluate(reference, trainer.populations["dev"])
        target = trainer.evaluate(trainer.model, trainer.populations["dev"])
    return {
        "checkpoint_id": checkpoint_id,
        "weights": "same_current_model_weights",
        "reference_DEV": ref,
        "target_DEV": target,
        "TEST_scored": False,
        "descriptive_only": True,
        "historical_selection_recomputed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("initial", "replacement", "report", "fixed-dev"))
    parser.add_argument("--store-binding", required=True, type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--first-request", type=Path)
    parser.add_argument("--first-receipt", type=Path)
    parser.add_argument("--second-request", type=Path)
    parser.add_argument("--second-receipt", type=Path)
    parser.add_argument("--checkpoint-id")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    from spireagent.source import source_identity
    from spireagent.storage.config import open_peer

    store = open_peer(json.loads(args.store_binding.read_text()))
    producer = source_identity(Path(__file__).resolve().parents[2])

    def read_request(path):
        return ComputeRequest.decode(json.loads(path.read_text()))

    def read_receipt(path):
        return ComputeReceipt.decode(json.loads(path.read_text()))

    if args.phase == "initial":
        value = initial_request(store, args.run_id, producer, args.attempt_id).to_dict()
    elif args.phase == "replacement":
        value = replacement_request(
            store,
            read_request(args.first_request),
            read_receipt(args.first_receipt),
            args.attempt_id,
        ).to_dict()
    elif args.phase == "fixed-dev":
        value = fixed_dev_comparison(store, args.run_id, args.checkpoint_id)
    else:
        value = qualification_report(
            store,
            read_request(args.first_request),
            read_receipt(args.first_receipt),
            read_request(args.second_request),
            read_receipt(args.second_receipt),
        )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
