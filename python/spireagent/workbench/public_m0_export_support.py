"""Historical Public M0 export admission at the existing application boundary.

No projection, training or data schema is added. These are the original public
M0 manifest edges; current training-use authorization remains the curation owner.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.package_identity import file_sha256
from spireagent.storage.run_reporter import ObjectStoreRunReporter

MODEL_SCHEMA = "stpd/stage1a-light-action-m0-public-model-v1"
PROFILE = "public-snapshot-m0-v1"
RECEIPT_SCHEMA = "stpd/local-public-m0-export-verification-v1"


def lineage(store: Any, owner: Any, model: Manifest, *, verify_payloads: bool = False) -> dict:
    """Check immutable completion edges before bytes, then ask current use owner.

    First export verifies the bounded ancestor graph's payloads. Rebinding uses
    the original immutable receipt and repeats all manifest/current-use checks.
    """
    from stpd.policy.token_decision import check_light_action_m0_model
    from stpd.workers.report_schemas import TOKEN_EVALUATION_SCHEMA

    try:
        config, info = check_light_action_m0_model(model)
        if info["schema"] != MODEL_SCHEMA:
            raise ValueError
        binding = info["training_binding"]
        run = store.get_manifest(model.parent("run"))
        source = store.get_manifest(model.parent("training_input"))
        view = store.get_manifest(model.parent("model_view"))
        allocation = store.get_manifest(binding["allocation_id"])
        checkpoint = store.get_manifest(model.parent("checkpoint"))
        result = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
        if result is None:
            raise ValueError
        report = store.get_manifest(result.parent("offline_evaluation"))
        source_info = source.parameters.value()
        run_info = run.parameters.value()
        checkpoint_info = checkpoint.parameters.value()
        if (
            run.kind != "run"
            or run.producer != model.producer
            or run_info.get("schema") != "stpd/stage1a-run-v1"
            or sorted(p.role for p in run.parents) != ["experiment", "training_input"]
            or run.parent("training_input") != source.artifact_id
            or run_info.get("config") != info["config"]
            or run_info.get("training_binding") != binding
            or source.kind != "training_input"
            or [p.role for p in source.parents] != ["model_view"]
            or source.parent("model_view") != view.artifact_id
            or sorted(p.role for p in source.payloads)
            != ["action_codec", "rows", "state_tokenizer"]
            or source_info.get("schema") != info["input_schema"]
            or source_info.get("format") != info["input_format"]
            or source_info.get("source_schema") != info["source_view_schema"]
            or source_info.get("source_renderer") != info["source_renderer"]
            or source_info.get("training_binding") != binding
            or source_info.get("state_codec") != info["state_codec"]
            or source_info.get("action_codec") != info["action_codec"]
            or view.kind != "model_view"
            or view.parameters.value().get("schema") != info["source_view_schema"]
            or view.parameters.value().get("serializer") != info["source_renderer"]
            or sorted(p.role for p in view.parents) != ["allocation", "dataset"]
            or binding["dataset_ids"] != [view.parent("dataset")]
            or binding["model_view_id"] != view.artifact_id
            or view.parent("allocation") != allocation.artifact_id
            or allocation.kind != "protocol"
            or allocation.parameters.value().get("schema") != "stpd/decision-allocation-v1"
            or [p.role for p in allocation.parents] != ["dataset"]
            or allocation.parent("dataset") != view.parent("dataset")
            or checkpoint.kind != "checkpoint"
            or checkpoint.producer != model.producer
            or sorted(p.role for p in checkpoint.parents) != ["run", "training_input"]
            or checkpoint.parent("run") != run.artifact_id
            or checkpoint.parent("training_input") != source.artifact_id
            or checkpoint_info.get("schema") != "stpd/stage1a-light-action-m0-checkpoint-v1"
            or checkpoint_info.get("step") != config.steps
            or result.producer != model.producer
            or result.parameters.value().get("state") != "completed"
            or result.parameters.value().get("steps") != config.steps
            or sorted(p.role for p in result.parents)
            != ["model", "offline_evaluation", "run", "training_input"]
            or result.parent("model") != model.artifact_id
            or result.parent("training_input") != source.artifact_id
            or report.kind != "offline_evaluation"
            or report.producer != model.producer
            or report.parameters.value().get("schema") != TOKEN_EVALUATION_SCHEMA
            or report.parent("model") != model.artifact_id
        ):
            raise ValueError
        for key in (
            "recipe",
            "graph",
            "action_codec",
            "backbone",
            "adapter_config",
            "adapter_tensor_names",
            "input_schema",
            "input_format",
            "source_view_schema",
            "source_renderer",
            "training_binding",
        ):
            if checkpoint_info.get(key) != info.get(key):
                raise ValueError
        for role in ("state_tokenizer", "action_codec"):
            if source.payload(role) != model.payload(role):
                raise ValueError
        experiment = store.get_manifest(run.parent("experiment"))
        if (
            experiment.kind != "experiment"
            or experiment.producer != model.producer
            or experiment.parameters.value().get("schema") != "stpd/experiment-v1"
            or experiment.parent("training_input") != source.artifact_id
            or experiment.parameters.value().get("config") != info["config"]
            or experiment.parameters.value().get("training_binding") != binding
        ):
            raise ValueError
        expected_eval = {
            "evaluation_scope": "within_training_purpose_allocation",
            "historical_external_exposure": "unknown",
            "physical_game_independence": "unresolved",
            "clean_held_out_claim": False,
        }
        if any(report.parameters.value().get(key) != value for key, value in expected_eval.items()):
            raise ValueError
        metrics_payload = report.payload("metrics")
        if metrics_payload.size > 64 * 1024 * 1024:
            raise ValueError
        owner.verified_training_receipt(
            store, tuple(binding["dataset_ids"]), binding["training_operation_id"]
        )
        if verify_payloads:
            from spireagent.json_boundary import decode_json

            metrics = decode_json(b"".join(store.read_payload(metrics_payload)))
            summaries = [metrics.get("summary", {}).get("bootstrap")]
            summaries.extend(
                item.get("bootstrap") for item in metrics.get("baselines", {}).values()
            )
            if metrics.get("admission") != expected_eval or any(
                not isinstance(item, dict)
                or item.get("status") != "unknown"
                or item.get("unit") != "session_scoped_run_group"
                for item in summaries
            ):
                raise ValueError
        if verify_payloads:
            pending = [result.artifact_id]
            seen: set[str] = set()
            while pending:
                identity = pending.pop()
                if identity in seen:
                    continue
                if len(seen) >= 512:
                    raise ValueError
                seen.add(identity)
                item = store.get_manifest(identity)
                for payload in item.payloads:
                    for _ in store.read_payload(payload):
                        pass
                pending.extend(p.artifact_id for p in item.parents)
        return {
            "run_id": run.artifact_id,
            "result_id": result.artifact_id,
            "checkpoint_id": checkpoint.artifact_id,
            "input_id": source.artifact_id,
            **binding,
        }
    except (BoundaryError, OSError, ValueError, KeyError, TypeError) as error:
        raise BoundaryError("local_model_export", "public_m0_lineage_invalid") from error


def package_receipt(
    model: Manifest, directory: Path, binding: dict, *, verify_inference: bool = False
) -> dict:
    """Exact closed package byte verification, without constructing a model."""
    from spireagent.workbench.local_model_export import _ordinary
    from stpd.public_m0_policy_installation import _model_details, _validate_export_payloads

    names = {"model.json", "weights.safetensors", "state-tokenizer.json", "action-codec.json"}
    if (
        not _ordinary(directory, directory=True)
        or {p.name for p in directory.iterdir()} != names
        or any(not _ordinary(directory / name, directory=False) for name in names)
    ):
        raise BoundaryError("local_model_export", "unsafe_export_destination")
    artifact, _, _ = _model_details(directory, model.artifact_id)
    if artifact != model:
        raise BoundaryError("local_model_export", "export_identity_mismatch")
    _validate_export_payloads(directory, model)
    if verify_inference:
        from stpd.policy.token_decision import LightActionM0DecisionScorer

        scorer = LightActionM0DecisionScorer(directory, runtime_device="cpu")
        if scorer.artifact != model:
            raise BoundaryError("local_model_export", "export_identity_mismatch")
    return {
        "schema": RECEIPT_SCHEMA,
        "model_id": model.artifact_id,
        "lineage_sha256": hashlib.sha256(json_bytes(binding)).hexdigest(),
        "package_sha256": file_sha256(directory / "model.json"),
        "payload_bytes": sum(p.size for p in model.payloads),
    }
