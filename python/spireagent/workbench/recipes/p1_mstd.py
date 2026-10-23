"""P1 N workload on the existing local private-child and Store lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, decode_json
from spireagent.source import source_identity
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT, ProjectConfig
from spireagent.workbench.p1_training_data import (
    record_p1_training_use,
    require_p1_snapshot,
    require_p1_training_use,
)
from spireagent.workbench.recipe_contracts import TrainingRequest
from stpd.p1.spec import MStdConfig

from .structured import StructuredRecipeAdapter


@dataclass(frozen=True)
class _RunGroup:
    run_id: str


@dataclass(frozen=True)
class P1Preflight:
    runs: tuple[_RunGroup, ...]


class P1MStdRecipeAdapter(StructuredRecipeAdapter):
    def export_checkpoint(self, config: ProjectConfig, checkpoint_id: str, *,
                          selection: str = "best") -> dict[str, Any]:
        """Recipe-owned publication; the Model supervisor only observes its operation."""
        from spireagent.storage.config import open_peer
        from spireagent.storage.local import LocalBlobStore
        from spireagent.storage.store import ArtifactStore, ManifestArtifactStore
        from spireagent.workbench.local_model_export import LocalModelExport
        from stpd.p1.checkpoint_export import publish_checkpoint_model

        producer = source_identity(ROOT)
        store: ArtifactStore
        if config.remote_compute is not None:
            store = open_peer(config.remote_compute.compute_store)
        else:
            reader = LocalModelExport(config)._workspace().store
            if not isinstance(reader, ManifestArtifactStore) or not isinstance(
                reader.blobs, LocalBlobStore
            ):
                raise BoundaryError("local_model", "configured_workspace_store_required")
            store = ManifestArtifactStore(LocalBlobStore(reader.blobs.root, create=False))
        return publish_checkpoint_model(store, checkpoint_id, producer, selection=selection)

    def preflight(self, store: Any, owner: Any, source_id: str) -> P1Preflight:
        _, scopes = require_p1_snapshot(owner, store, source_id)
        return P1Preflight(tuple(_RunGroup(run) for run in sorted(scopes["train"]["roots"])))

    def record_training_use(self, owner: Any, store: Any, operation: dict[str, Any],
                            source_id: str, identity: str, runs: set[str]) -> None:
        if operation["mode"] == "start":
            record_p1_training_use(owner, store, source_id, identity)
        require_p1_training_use(owner, store, source_id, identity)

    def optimizer_update_limit(self, store: Any, operation: dict[str, Any]) -> int:
        return int(store.get_manifest(operation["input_id"]).parameters.value()["planned_steps"])

    def validate_terminal(self, store: Any, operation: dict[str, Any],
                          terminal: dict[str, Any]) -> None:
        from stpd.p1.workload import verify_p1_run
        from stpd.workers.p1_run import verify_p1_result

        workload = verify_p1_run(store, operation["run_id"], source_identity(ROOT))
        if terminal["state"] != "completed":
            return
        selected = ObjectStoreRunReporter(store, store.blobs).completed(operation["run_id"])
        if selected is None:
            raise BoundaryError("p1_recipe", "completed_selected_result_required")
        result, model, last = verify_p1_result(store, selected.artifact_id, workload)
        if (terminal["result_id"] != result.artifact_id
                or terminal["model_id"] != model.artifact_id
                or terminal["checkpoint_id"] != last.manifest.artifact_id
                or terminal["optimizer_updates"] != last.state["step"]
                or terminal["event_step"] != result.parameters.value()["event_step"]):
            raise BoundaryError("p1_recipe", "completed_terminal_artifact_mismatch")


def verify_p1_resume_checkpoint(store: Any, operation: dict[str, Any], checkpoint_id: str) -> None:
    from stpd.p1.workload import verify_p1_run
    from stpd.workers.p1_run import verify_p1_checkpoint

    workload = verify_p1_run(store, operation["run_id"], source_identity(ROOT))
    checkpoint = verify_p1_checkpoint(store, checkpoint_id, workload).manifest
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


def run_p1_child(args: Any, channel: Any, store: Any, fence: Any, producer: Any) -> None:
    from spireagent.workbench.local_curation import OWNER_NAME, LocalCurationOwner
    from spireagent.workbench.recipes.structured_child import (
        ChildReporter,
        FencedSlots,
        FencedStore,
    )
    from stpd.p1.workload import prepare_p1_run, verify_p1_run
    from stpd.workers.p1_run import execute_p1_run

    operation = fence.operation()
    request = TrainingRequest.from_dict(operation["request"])
    config = MStdConfig.decode(request.config)
    if config.device != "cpu":
        raise BoundaryError("p1_recipe", "local_P1_recipe_requires_cpu")
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
        run = prepare_p1_run(fenced, request.source_id, producer, config, args.operation_id,
                             **({"execution_policy": request.execution_policy}
                                if request.execution_policy is not None else {}))
        fence.bind_run(run)
        channel.emit("prepared", run_id=run.artifact_id, input_id=run.parent("training_input"))
        fence.wait_for_parent_run(run.artifact_id, run.parent("training_input"))
    else:
        workload = verify_p1_run(store, operation["run_id"], producer)
        if (workload.config != config
                or workload.run.parameters.value().get("execution_policy")
                != request.execution_policy):
            raise BoundaryError("p1_recipe", "resume_config_mismatch")
        run = workload.run
    reporter = ChildReporter(ObjectStoreRunReporter(fenced, FencedSlots(store.blobs, fence)),
                             fence, channel)
    result = execute_p1_run(
        fenced, reporter, run.artifact_id, producer, attempt_id=args.attempt_id,
        resume=operation.get("resume_checkpoint_id") if operation["mode"] == "resume" else None,
        authority=fence, control=fence, mode=operation["mode"],
    )
    channel.emit("terminal", **asdict(result))
