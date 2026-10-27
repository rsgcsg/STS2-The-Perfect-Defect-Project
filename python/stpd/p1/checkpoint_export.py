"""Diagnostic CPU inference Model from an immutable P1 checkpoint.

Normal Run/Snapshot verification reads original projection and label bytes only
for integrity. No populations, TEST evaluation, optimizer or RunReporter are used.
The exporting producer remains distinct from the original training producer.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore
from stpd.workers.p1_run import verify_p1_checkpoint

from .data import digest
from .export import export_model
from .formal_training import FORMAL_CODE_FILES
from .model import MStd, architecture
from .spec import MODEL_SCHEMA
from .workload import verify_p1_run

SCHEMA = "stpd/p1-checkpoint-model-export-1"
ROLE = "diagnostic_checkpoint_inference_not_final_result"


def publish_checkpoint_model(
    store: ArtifactStore, checkpoint_id: str, producer: Producer, *, selection: str = "best",
) -> dict[str, Any]:
    if not isinstance(selection, str) or selection not in {"best", "current"}:
        raise BoundaryError("p1_checkpoint_export", "best_or_current_selection_required")
    checkpoint = store.get_manifest(checkpoint_id)
    run_id = checkpoint.parent("run")
    run = store.get_manifest(run_id)
    workload = verify_p1_run(store, run_id, run.producer)
    checked = verify_p1_checkpoint(store, checkpoint_id, workload)
    state, identity = checked.state, checked.state["identity"]
    root = Path(__file__).parent
    code = {name: digest((root / name).read_bytes()) for name in FORMAL_CODE_FILES}
    if (identity.get("code") != code
            or identity.get("architecture") != architecture(workload.config.value_heads,
                                                              workload.config.model_size)
            or identity.get("codec_sha256")
            != digest((root.parent / "workers/checkpoint_codec.py").read_bytes())
            or identity.get("planned_steps")
            != workload.training.parameters.value()["planned_steps"]):
        raise BoundaryError("p1_checkpoint_export", "checkpoint_code_or_architecture_mismatch")
    if state["step"] < 1:
        raise BoundaryError("p1_checkpoint_export", "trained_checkpoint_required")
    selected_step = state.get("best_step") if selection == "best" else state["step"]
    weights = state.get("best_model") if selection == "best" else state["model"]
    if (not isinstance(weights, dict) or type(selected_step) is not int
            or not 1 <= selected_step <= state["step"]):
        raise BoundaryError("p1_checkpoint_export", "selected_checkpoint_weights_unavailable")
    # CPU initialization must not change the caller's RNG or require the training GPU.
    with torch.random.fork_rng(devices=[]), torch.device("cpu"):
        model = MStd(workload.vocabulary, workload.execution,
                     value_heads=workload.config.value_heads, model_size=workload.config.model_size)
        reference = model.state_dict()
        for name, original in reference.items():
            value = weights.get(name)
            if (not isinstance(value, torch.Tensor) or value.shape != original.shape
                    or value.dtype != original.dtype or value.layout != torch.strided
                    or not bool(torch.isfinite(value).all())):
                raise BoundaryError("p1_checkpoint_export", "checkpoint_tensor_binding_mismatch")
        if weights.keys() != reference.keys():
            raise BoundaryError("p1_checkpoint_export", "checkpoint_tensor_inventory_mismatch")
        model.load_state_dict(weights, strict=True)
        model.eval()
        with tempfile.TemporaryDirectory(prefix="p1-checkpoint-export-") as temporary:
            directory = Path(temporary)
            checkpoint_path = directory / "checkpoint.tensor-tree"
            checkpoint_path.write_bytes(checked.raw)
            package = directory / "model"
            descriptor = export_model(model, checkpoint_path, package, identity)
            payloads = []
            for role, name, media in (
                ("weights", "model.tensor-tree", "application/vnd.stpd.tensor-tree"),
                ("package_manifest", "model.json", "application/json"),
            ):
                with (package / name).open("rb") as source:
                    payloads.append(store.put_payload(role, source, media))
    model_artifact = Manifest("model", producer,
        (Parent("run", run_id), Parent("training_input", workload.training.artifact_id),
         Parent("checkpoint", checkpoint_id)), tuple(payloads), FrozenObject.of({
            "schema": MODEL_SCHEMA, "package_sha256": descriptor["package_sha256"],
            "input_spec": descriptor["input_spec"], "selected_step": selected_step,
            "training_device": workload.config.device, "export_device": "cpu",
            "selected_state": "best_model" if selection == "best" else "model",
            "selection": selection, "export_role": ROLE,
            "training_producer": run.producer.to_dict(), "research_admission": "not_evaluated",
        }))
    store.publish(model_artifact)
    return {"schema": SCHEMA, "model_id": model_artifact.artifact_id,
            "run_id": run_id, "checkpoint_id": checkpoint_id, "selection": selection,
            "selected_step": selected_step, "export_role": ROLE,
            "export_producer": producer.to_dict(), "training_producer": run.producer.to_dict(),
            "test_access": "integrity_verification_only_no_population_or_evaluation"}
