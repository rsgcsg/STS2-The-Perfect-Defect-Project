"""P1 baseline workload on the existing local private-child and Store lifecycle."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT
from spireagent.workbench.p1_training_data import require_p1_training_use
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.p1.baseline_spec import BaselineConfig

from .p1_mstd import P1MStdRecipeAdapter


class P1BaselineRecipeAdapter(P1MStdRecipeAdapter):
    def validate_terminal(self, store: Any, operation: dict[str, Any],
                          terminal: dict[str, Any]) -> None:
        from stpd.p1.baseline_worker import verify_baseline_result
        from stpd.p1.baseline_workload import verify_baseline_run

        workload = verify_baseline_run(store, operation["run_id"], source_identity(ROOT))
        if terminal["state"] != "completed":
            return
        selected = ObjectStoreRunReporter(store, store.blobs).completed(operation["run_id"])
        if selected is None:
            raise BoundaryError("p1_recipe", "completed_selected_result_required")
        result, model, last = verify_baseline_result(store, selected.artifact_id, workload)
        if (terminal["result_id"] != result.artifact_id
                or terminal["model_id"] != model.artifact_id
                or terminal["checkpoint_id"] != last.manifest.artifact_id
                or terminal["optimizer_updates"] != last.state["step"]
                or terminal["event_step"] != result.parameters.value()["event_step"]):
            raise BoundaryError("p1_recipe", "completed_terminal_artifact_mismatch")


def verify_baseline_resume_checkpoint(
    store: Any, operation: dict[str, Any], checkpoint_id: str
) -> None:
    from stpd.p1.baseline_worker import verify_baseline_checkpoint
    from stpd.p1.baseline_workload import verify_baseline_run

    workload = verify_baseline_run(store, operation["run_id"], source_identity(ROOT))
    checkpoint = verify_baseline_checkpoint(store, checkpoint_id, workload).manifest
    info = checkpoint.parameters.value()
    writers = {item["attempt_id"] for item in operation["attempts"] if item["writer_terminal"]}
    if operation["writer_terminal"]:
        writers.add(operation["attempt_id"])
    if info.get("attempt") not in writers:
        raise BoundaryError("p1_recipe", "checkpoint_writer_terminal_required")
    if not any(
        event.producer == checkpoint.producer
        and event.parameters.value().get("kind") == "checkpoint"
        and event.parameters.value().get("attempt") == info["attempt"]
        and event.parameters.value().get("details", {}).get("checkpoint_id") == checkpoint_id
        for event in ObjectStoreRunReporter(store, store.blobs).events(workload.run.artifact_id)
    ):
        raise BoundaryError("p1_recipe", "checkpoint_event_required")


def run_baseline_child(args: Any, channel: Any, store: Any, fence: Any, producer: Any) -> None:
    from spireagent.workbench.local_curation import OWNER_NAME, LocalCurationOwner
    from spireagent.workbench.recipes.structured_child import (
        ChildReporter,
        FencedSlots,
        FencedStore,
    )
    from stpd.p1.baseline_worker import execute_baseline_run
    from stpd.p1.baseline_workload import prepare_baseline_run, verify_baseline_run

    operation = fence.operation()
    request = TrainingRequest.from_dict(operation["request"])
    config = BaselineConfig.decode(request.config)
    marker = decode_json((args.store / OWNER_NAME).read_bytes())
    identity = operation["_owner"]
    owner = LocalCurationOwner(
        Path(marker["ledger_path"]), args.store, identity[0], identity[1], identity[2],
        legacy_guard=marker["schema"] == "stpd/local-curation-inplace-owner-v1",
        owner_schema=marker["schema"],
    )
    require_p1_training_use(owner, store, request.source_id, args.operation_id)
    fenced = FencedStore(store, fence)
    if operation["mode"] == "start":
        run = prepare_baseline_run(fenced, request.source_id, producer, config, args.operation_id)
        fence.bind_run(run)
        channel.emit("prepared", run_id=run.artifact_id, input_id=run.parent("training_input"))
        fence.wait_for_parent_run(run.artifact_id, run.parent("training_input"))
    else:
        workload = verify_baseline_run(store, operation["run_id"], producer)
        if workload.config != config:
            raise BoundaryError("p1_recipe", "resume_config_mismatch")
        run = workload.run
    reporter = ChildReporter(ObjectStoreRunReporter(fenced, FencedSlots(store.blobs, fence)),
                             fence, channel)
    result = execute_baseline_run(
        fenced, reporter, run.artifact_id, producer, attempt_id=args.attempt_id,
        resume=operation.get("resume_checkpoint_id") if operation["mode"] == "resume" else None,
        authority=fence, control=fence, mode=operation["mode"],
    )
    channel.emit("terminal", **asdict(result))
