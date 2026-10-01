"""S01 and Stage 1a engineering workflows using shared storage and worker infrastructure.

Preparation runs beside the authoritative Hub operations database. Copies of a database
are not a substitute for current Gold reservations. Later commands consume the fixed
allocation; token and pooled execution share lineage, storage, reporting and evaluation owners.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, cast

from spireagent.artifact_contracts import Manifest
from spireagent.hub.curation_access import record_use
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.features import compile_features
from stpd.fullrun.representation import FullRunSerializer
from stpd.fullrun.view_session import verified_model_views
from stpd.workers.contracts import TrainingConfig, prepare_run, prepare_training_input
from stpd.workers.worker import execute

if TYPE_CHECKING:
    from spireagent.workbench.local_curation import LocalCurationOwner


def _configured_m0_owner(
    project_config: Path, store_location: str,
) -> LocalCurationOwner:
    """Resolve the existing configured owner before opening a caller-supplied store."""
    from spireagent.workbench.developer import ProjectConfig
    from spireagent.workbench.inplace_curation import configured_owner

    config = ProjectConfig.load(project_config)
    if config.research_workspace is None or store_location == "s3":
        raise BoundaryError("light_action_m0", "configured_local_workspace_required")
    owner = configured_owner(config)
    if Path(store_location).expanduser().resolve() != owner.store_dir.resolve():
        raise BoundaryError("light_action_m0", "store_identity_mismatch")
    return owner


def _check_m0_store(store: ManifestArtifactStore, owner: LocalCurationOwner) -> None:
    if not isinstance(store.blobs, LocalBlobStore) or store.blobs.root != owner.store_dir.resolve():
        raise BoundaryError("light_action_m0", "store_identity_mismatch")


def _admit_m0_binding(
    owner: LocalCurationOwner, store: ManifestArtifactStore,
    binding: dict[str, Any], operation_id: str,
) -> None:
    from stpd.fullrun.light_action_inputs import TRAINING_BINDING_SCHEMA

    operation = digest(operation_id, "light_action_m0.operation_id", length=32)
    if (binding.get("schema") != TRAINING_BINDING_SCHEMA
            or binding.get("training_operation_id") != operation
            or not isinstance(binding.get("dataset_ids"), list)):
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    owner.require_training_datasets(
        store, tuple(binding["dataset_ids"]), operation,
    )


def _admit_m0_input(
    owner: LocalCurationOwner, store: ManifestArtifactStore,
    input_id: str, operation_id: str,
) -> dict[str, Any]:
    binding = _m0_input_binding_manifest_only(store, input_id)
    _admit_m0_binding(owner, store, binding, operation_id)
    return binding


def _m0_input_binding_manifest_only(
    store: ManifestArtifactStore, input_id: str,
) -> dict[str, Any]:
    from stpd.fullrun.light_action_inputs import (
        PUBLIC_SCHEMA,
        canonical_training_binding,
        public_training_binding,
    )

    input_manifest = store.get_manifest(input_id)
    schema = input_manifest.parameters.value().get("schema")
    if schema == PUBLIC_SCHEMA:
        return public_training_binding(store, input_id)
    return canonical_training_binding(store, input_id)


def _admit_m0_run(
    owner: LocalCurationOwner, store: ManifestArtifactStore,
    run_id: str, operation_id: str,
) -> dict[str, Any]:
    run = store.get_manifest(digest(run_id, "light_action_m0.run_id"))
    if (run.kind != "run"
            or [parent.role for parent in run.parents] != ["experiment", "training_input"]):
        raise BoundaryError("light_action_m0", "run_identity_mismatch")
    input_id = run.parent("training_input")
    binding = _m0_input_binding_manifest_only(store, input_id)
    if run.parameters.value().get("training_binding") != binding:
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    experiment = store.get_manifest(run.parent("experiment"))
    run_info = run.parameters.value()
    experiment_info = experiment.parameters.value()
    if (experiment.kind != "experiment"
            or experiment.parameters.value().get("schema") != "stpd/experiment-v1"
            or experiment.parent("training_input") != input_id
            or experiment_info.get("config") != run_info.get("config")
            or experiment_info.get("training_binding") != binding):
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    _admit_m0_binding(owner, store, binding, operation_id)
    return binding


def _admit_m0_model(
    owner: LocalCurationOwner, store: ManifestArtifactStore,
    model_id: str, operation_id: str,
) -> dict[str, Any]:
    from stpd.policy.token_decision import (
        CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA,
        PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
    )

    model = store.get_manifest(digest(model_id, "light_action_m0.model_id"))
    if (model.kind != "model" or model.parameters.value().get("schema") not in {
            CANONICAL_LIGHT_ACTION_M0_MODEL_SCHEMA, PUBLIC_LIGHT_ACTION_M0_MODEL_SCHEMA,
    }):
        raise BoundaryError("light_action_m0", "canonical_model_required")
    binding = _m0_input_binding_manifest_only(store, model.parent("training_input"))
    if model.parameters.value().get("training_binding") != binding:
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    run = store.get_manifest(model.parent("run"))
    if run.parameters.value().get("training_binding") != binding:
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    _admit_m0_binding(owner, store, binding, operation_id)
    return binding


def _reserve_m0_dev(
    owner: LocalCurationOwner, store: ManifestArtifactStore,
    binding: dict[str, Any], operation_id: str,
    run_id: str, input_id: str, model: Manifest, view: Manifest,
) -> dict[str, Any]:
    if (model.parent("run") != run_id
            or model.parent("training_input") != input_id
            or model.parent("model_view") != binding.get("model_view_id")
            or view.artifact_id != binding.get("model_view_id")
            or model.parameters.value().get("training_binding") != binding):
        raise BoundaryError("light_action_m0", "training_binding_mismatch")
    evaluation_operation = hashlib.sha256(
        f"stage1a-m0-dev-v1:{model.parent('run')}:{model.artifact_id}".encode("ascii")
    ).hexdigest()[:32]
    return cast(dict[str, Any], owner.reserve_allocation_dev(
        store, model.artifact_id, binding["allocation_id"], operation_id,
        evaluation_operation,
    ))


def _verify_m0_completion(store: ManifestArtifactStore, model: Manifest) -> None:
    from spireagent.storage.run_reporter import ObjectStoreRunReporter
    from stpd.workers.token_worker import _verify_completed

    run = store.get_manifest(model.parent("run"))
    completed = ObjectStoreRunReporter(store, store.blobs).completed(run.artifact_id)
    if completed is None or completed.parent("model") != model.artifact_id:
        raise BoundaryError("light_action_m0", "completed_model_required")
    _verify_completed(store, completed, run)


def add_token_recipe_arguments(parser: argparse.ArgumentParser) -> None:
    recipe = parser.add_mutually_exclusive_group(required=True)
    recipe.add_argument("--recipe")
    recipe.add_argument(
        "--text-menu-small-b", action="store_true",
        help="use the explicit zero-dropout scratch B v2 engineering config",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--dataset", required=True)
    prepare.add_argument("--operations", required=True, type=Path)
    prepare.add_argument("--isolation", choices=("run", "decision"), default="run")
    prepare.add_argument("--train-limit", type=int, default=100)
    prepare.add_argument("--dev-limit", type=int, default=32)
    prepare.add_argument("--profile", choices=("lite", "standard", "full"), default="standard")
    encode = commands.add_parser("encode")
    encode.add_argument("--view", required=True)
    encode.add_argument("--snapshot", type=Path, required=True)
    encode.add_argument("--backend", choices=("cpu", "mps"), required=True)
    tokenize = commands.add_parser("tokenize", help="prepare fixed Stage 1a B/D token inputs")
    tokenize.add_argument("--view", required=True)
    tokenize.add_argument("--backbone", choices=("s", "pf"), required=True)
    tokenize.add_argument("--snapshot", type=Path)
    tokenize.add_argument("--max-tokens", type=int, default=16384)
    m0_prepare = commands.add_parser(
        "prepare-light-action-m0", help="reserve and prepare exact M0 inputs")
    m0_prepare.add_argument("--project-config", required=True, type=Path)
    m0_prepare.add_argument("--dataset", required=True)
    m0_prepare.add_argument("--operation", required=True)
    m0_prepare.add_argument("--backbone", choices=("s", "pf", "pl"), required=True)
    m0_prepare.add_argument("--profile", choices=("lite", "standard", "full"),
                            default="standard")
    m0_prepare.add_argument("--input-profile",
                            choices=("canonical", "public_lite", "public_compact"),
                            default="canonical")
    m0_prepare.add_argument("--snapshot", type=Path)
    m0_prepare.add_argument("--train-limit", type=int, default=100)
    m0_prepare.add_argument("--dev-limit", type=int, default=32)
    m0_prepare.add_argument("--max-state-tokens", type=int, default=8192)
    m0_prepare.add_argument("--max-action-bytes", type=int, default=8192)
    public_view = commands.add_parser("public-view", help="exact Human public-observation BC view")
    public_view.add_argument("--allocation", required=True)
    compare = commands.add_parser("compare-tokens", help="paired completed token-run dev reports")
    compare.add_argument("--result", action="append", required=True)
    train = commands.add_parser("train")
    train.add_argument("--features", required=True)
    train.add_argument("--steps", type=int, default=100)
    train.add_argument("--replicate", default="s01")
    train.add_argument("--stop-after", type=int)
    train.add_argument("--resume")
    token_train = commands.add_parser("train-tokens", help="bounded local Stage 1a token training")
    token_train.add_argument("--inputs", required=True)
    add_token_recipe_arguments(token_train)
    token_train.add_argument("--steps", type=int, default=10)
    token_train.add_argument("--backend", choices=("cpu", "mps"), required=True)
    token_train.add_argument("--snapshot", type=Path)
    token_train.add_argument("--replicate", default="stage1a")
    token_train.add_argument("--resume")
    token_train.add_argument("--stop-after", type=int)
    token_train.add_argument("--max-tokens", type=int, default=16384)
    m0_train = commands.add_parser(
        "train-light-action-m0", help="train one canonical, owner-admitted M0 run")
    m0_train.add_argument("--project-config", required=True, type=Path)
    m0_train.add_argument("--inputs", required=True)
    m0_train.add_argument("--operation", required=True)
    m0_train.add_argument("--recipe", choices=(
        "stage1a.dsimple.light-action.m0.s.v1",
        "stage1a.dsimple.light-action.m0.pf.v1",
        "stage1a.dsimple.light-action.m0.pl.v1",
    ), required=True)
    m0_train.add_argument("--steps", type=int, default=10)
    m0_train.add_argument("--backend", choices=("cpu", "mps"), default="cpu")
    m0_train.add_argument("--snapshot", type=Path)
    m0_train.add_argument("--max-state-tokens", type=int, default=8192)
    m0_train.add_argument("--max-action-bytes", type=int, default=8192)
    m0_train.add_argument("--replicate", default="light-action-m0")
    m0_train.add_argument("--stop-after", type=int)
    run_m0 = commands.add_parser(
        "run-light-action-m0", help="resume/continue one exact canonical M0 run")
    run_m0.add_argument("--project-config", required=True, type=Path)
    run_m0.add_argument("--run", required=True)
    run_m0.add_argument("--operation", required=True)
    run_m0.add_argument("--resume", help="exact checkpoint ID from this run")
    run_m0.add_argument("--stop-after", type=int)
    run_m0.add_argument("--snapshot", type=Path)
    run_tokens = commands.add_parser("run-tokens", help="execute an existing exact token run")
    run_tokens.add_argument("--run", required=True)
    run_tokens.add_argument("--project-config", type=Path)
    run_tokens.add_argument("--operation", help="canonical M0 training operation ID")
    run_tokens.add_argument("--resume", help="exact checkpoint ID from this run")
    run_tokens.add_argument("--stop-after", type=int)
    run_tokens.add_argument("--snapshot", type=Path)
    run_memory = commands.add_parser("run-memory", help="execute an existing exact M2 episode run")
    run_memory.add_argument("--run", required=True)
    run_memory.add_argument("--resume", help="exact prior episode checkpoint ID")
    run_memory.add_argument("--stop-after", type=int, help="pause after this many whole episodes")
    verify_memory = commands.add_parser("verify-memory", help="verify an exact completed M2 run")
    verify_memory.add_argument("--run", required=True)
    evaluate_memory = commands.add_parser(
        "evaluate-memory", help="internal, owner-admitted M2 dev evaluation worker")
    evaluate_memory.add_argument("--model", required=True)
    evaluate_memory.add_argument("--source", required=True)
    evaluate_memory.add_argument("--operation", required=True)
    evaluate_memory.add_argument("--max-settling-events", type=int)
    evaluate_memory.add_argument("--semantic-overlap", choices=("true", "false"), required=True)
    prepare_memory = commands.add_parser(
        "prepare-workbench-memory", help="prepare a caller-admitted train-only Human M2 run")
    prepare_memory.add_argument("--source", required=True)
    prepare_memory.add_argument("--operation", required=True)
    from spireagent.workbench.memory_recipe import MEMORY_RECIPES
    prepare_memory.add_argument("--recipe", choices=sorted(MEMORY_RECIPES), required=True)
    memory_export = commands.add_parser(
        "export-memory", help="export an exact completed train-only M2 run")
    memory_export.add_argument("--run", required=True)
    memory_export.add_argument("--model", required=True)
    memory_export.add_argument("--destination", required=True, type=Path)
    memory_verify_export = commands.add_parser(
        "verify-memory-export", help="reconcile a portable M2 package with its store")
    memory_verify_export.add_argument("--run", required=True)
    memory_verify_export.add_argument("--model", required=True)
    memory_verify_export.add_argument("--destination", required=True, type=Path)
    export = commands.add_parser("export")
    export.add_argument("--model", required=True)
    export.add_argument("--destination", type=Path, required=True)
    token_export = commands.add_parser("export-tokens")
    token_export.add_argument("--model", required=True)
    token_export.add_argument("--destination", type=Path, required=True)
    m0_export = commands.add_parser(
        "export-light-action-m0", help="export a completed, owner-admitted M0 model")
    m0_export.add_argument("--project-config", type=Path)
    m0_export.add_argument("--operation", help="canonical M0 training operation ID")
    m0_export.add_argument("--model", required=True)
    m0_export.add_argument("--destination", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--artifact", required=True)
    score = commands.add_parser("score")
    score.add_argument("--model-directory", type=Path, required=True)
    score.add_argument("--input", type=Path, required=True)
    score.add_argument("--snapshot", type=Path, required=True)
    score.add_argument("--backend", choices=("cpu", "mps"), required=True)
    token_score = commands.add_parser("score-tokens")
    token_score.add_argument("--model-directory", type=Path, required=True)
    token_score.add_argument("--input", type=Path, required=True)
    token_score.add_argument("--snapshot", type=Path)
    public_score = commands.add_parser("score-snapshot", help="standalone public snapshot scoring")
    public_score.add_argument("--model-directory", type=Path, required=True)
    public_score.add_argument("--input", type=Path, required=True)
    public_score.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.command == "score-snapshot":
        from stpd.policy.token_decision import TokenDecisionScorer

        if args.input.stat().st_size > 16 * 1024**2:
            raise BoundaryError("stage1", "input_size_limit")
        observation = json.loads(args.input.read_bytes())
        scorer_public = TokenDecisionScorer(args.model_directory, snapshot=args.snapshot)
        print(json_bytes({"model_id": scorer_public.artifact.artifact_id,
                          "scores": scorer_public.score_snapshot(observation)}).decode())
        return 0
    if args.command in {"score", "score-tokens"}:
        from stpd.fullrun.contracts import SemanticAction, SemanticState
        from stpd.policy.decision import DecisionScorer
        from stpd.qwen.portable_backend import PortableQwenBackend

        if args.input.stat().st_size > 16 * 1024**2:
            raise BoundaryError("stage1", "input_size_limit")
        value = object_fields(json.loads(args.input.read_bytes()), {"state", "actions"}, "input")
        state = SemanticState.decode(value["state"])
        if not isinstance(value["actions"], list):
            raise BoundaryError("stage1", "actions_array_required")
        actions = tuple(SemanticAction.decode(a) for a in value["actions"])
        load_started = perf_counter()
        if args.command == "score-tokens":
            from stpd.policy.token_decision import TokenDecisionScorer

            token_scorer = TokenDecisionScorer(args.model_directory, snapshot=args.snapshot)
            score_function = token_scorer.score
            model_id = token_scorer.artifact.artifact_id
        else:
            backend = PortableQwenBackend(args.snapshot, device=args.backend)
            scorer = DecisionScorer(args.model_directory, backend)
            score_function = scorer.score
            model_id = scorer.model.artifact_id
        load_seconds = perf_counter() - load_started
        started = perf_counter()
        scores = score_function(state, actions)
        print(
            json_bytes(
                {
                    "model_id": model_id,
                    "scores": scores,
                    "inference_seconds": perf_counter() - started,
                    "load_seconds": load_seconds,
                }
            ).decode()
        )
        return 0
    if args.store is None:
        parser.error("--store is required except for standalone score")
    project_path = getattr(args, "project_config", None)
    owner_required = args.command in {
        "prepare-light-action-m0", "train-light-action-m0", "run-light-action-m0",
    }
    if owner_required and project_path is None:
        raise BoundaryError("light_action_m0", "configured_local_workspace_required")
    project_owner = (_configured_m0_owner(project_path, args.store)
                     if project_path is not None else None)
    store = open_store(args.store)
    if project_owner is not None:
        _check_m0_store(store, project_owner)
    runtime = source_identity(Path(__file__).resolve().parents[1])
    started = perf_counter()
    with verified_model_views(store) as views:
        result: dict
        if args.command == "compare-tokens":
            from stpd.fullrun.token_comparison import compare_token_results

            result = compare_token_results(store, args.result)
        elif args.command == "prepare-light-action-m0":
            from stpd.fullrun.decision_training import AllocationSpec, publish_allocation
            from stpd.fullrun.light_action_inputs import (
                TRAINING_BINDING_SCHEMA,
                load_light_action_inputs,
                publish_light_action_inputs,
            )

            dataset_id = digest(args.dataset, "light_action_m0.dataset_id")
            operation_id = digest(args.operation, "light_action_m0.operation_id", length=32)
            if project_owner is None:
                raise BoundaryError("light_action_m0", "configured_local_workspace_required")
            # The reservation precedes allocation, view serialization, and tokenizer fitting.
            admission = project_owner.reserve_training_datasets(
                store, (dataset_id,), operation_id,
            )
            allocation = publish_allocation(
                store, dataset_id,
                AllocationSpec(isolation="run", max_train=args.train_limit,
                               max_dev=args.dev_limit), runtime,
            )
            if args.input_profile == "canonical":
                from stpd.fullrun.decision_training import publish_decision_view

                view = publish_decision_view(
                    store, allocation.artifact_id, FullRunSerializer(args.profile), runtime,
                )
            else:
                from stpd.fullrun.public_bc import publish_public_bc_view

                view = publish_public_bc_view(
                    store, allocation.artifact_id, runtime,
                    compact=args.input_profile == "public_compact",
                )
            binding = {
                "schema": TRAINING_BINDING_SCHEMA,
                "dataset_ids": [dataset_id],
                "training_operation_id": operation_id,
                "allocation_id": allocation.artifact_id,
                "model_view_id": view.artifact_id,
            }
            if args.input_profile == "canonical" and (
                    args.max_state_tokens > 8192 or args.max_action_bytes > 8192):
                raise BoundaryError("light_action_m0", "canonical_length_limit_exceeded")
            manifest = publish_light_action_inputs(
                store, view.artifact_id, "s" if args.backbone == "s" else "qwen3",
                runtime, snapshot=args.snapshot,
                max_state_tokens=args.max_state_tokens,
                max_action_bytes=args.max_action_bytes,
                training_binding=binding,
            )
            load_light_action_inputs(store, manifest.artifact_id)
            # Independent read-only admission after durable preparation. No new use rows.
            project_owner.require_training_datasets(store, (dataset_id,), operation_id)
            result = {
                "allocation_id": allocation.artifact_id,
                "model_view_id": view.artifact_id,
                "training_input_id": manifest.artifact_id,
                "input_schema": manifest.parameters.value()["schema"],
                "backbone": args.backbone,
                "admission": admission,
                "counts": allocation.parameters.value()["counts"],
            }
        elif args.command == "prepare":
            from stpd.fullrun.decision_training import (
                AllocationSpec,
                publish_allocation,
                publish_decision_view,
            )

            if not args.operations.is_file():
                raise BoundaryError("stage1", "existing_authoritative_operations_required")
            operations = Operations(args.operations)
            dataset = store.get_manifest(args.dataset)
            # Record before preparing/exporting training inputs. A later failure keeps
            # a conservative use reservation, rather than allowing this data into Gold.
            record_use(operations, store, dataset, "training")
            allocation = publish_allocation(
                store,
                args.dataset,
                AllocationSpec(
                    isolation=args.isolation, max_train=args.train_limit, max_dev=args.dev_limit
                ),
                runtime,
            )
            view = publish_decision_view(
                store, allocation.artifact_id, FullRunSerializer(args.profile), runtime
            )
            result = {
                "allocation_id": allocation.artifact_id,
                "model_view_id": view.artifact_id,
                "counts": allocation.parameters.value()["counts"],
            }
        elif args.command == "public-view":
            from stpd.fullrun.public_bc import publish_public_bc_view

            view = publish_public_bc_view(store, args.allocation, runtime)
            result = {"view": view.artifact_id, **view.parameters.value()}
        elif args.command == "tokenize":
            from stpd.fullrun.token_inputs import publish_token_inputs

            item = publish_token_inputs(
                store, args.view, args.backbone, runtime, snapshot=args.snapshot,
                max_tokens=args.max_tokens,
            )
            result = {"training_input_id": item.artifact_id, **item.parameters.value()}
        elif args.command == "encode":
            from stpd.qwen.portable_backend import PortableQwenBackend

            backend = PortableQwenBackend(args.snapshot, device=args.backend)
            features = compile_features(store, args.view, backend, runtime, batch_size=1)
            result = {
                "feature_id": features.artifact_id,
                "runtime": backend.runtime_summary(),
                "rows": features.parameters.value()["rows"],
            }
        elif args.command == "train-tokens":
            import torch

            from stpd.fullrun.token_inputs import load_token_inputs
            from stpd.workers.token_ranking import TokenConfig
            from stpd.workers.token_worker import execute_tokens, prepare_token_run

            torch.set_num_threads(2)
            input_manifest = store.get_manifest(digest(args.inputs, "token_input.id"))
            if input_manifest.parameters.value().get("schema") == \
                    "stpd/stage1a-light-action-m0-canonical-input-v1":
                raise BoundaryError("token_run", "use_owner_admitted_light_action_m0_command")
            inputs = load_token_inputs(store, args.inputs)
            token_config = (
                TokenConfig.text_menu_small_b(steps=args.steps, device=args.backend,
                                              max_tokens=args.max_tokens)
                if args.text_menu_small_b else
                TokenConfig(recipe=args.recipe, steps=args.steps, device=args.backend,
                            max_tokens=args.max_tokens)
            )
            run = prepare_token_run(store, inputs, token_config, runtime, replicate=args.replicate)
            result = asdict(execute_tokens(
                store, ObjectStoreRunReporter(store, store.blobs), run.artifact_id, runtime,
                snapshot=args.snapshot, resume=args.resume, stop_after=args.stop_after,
            ))
        elif args.command == "train-light-action-m0":
            import torch

            from stpd.fullrun.light_action_inputs import load_light_action_inputs
            from stpd.workers.token_ranking import LightActionM0Config
            from stpd.workers.token_worker import execute_tokens, prepare_token_run

            input_id = digest(args.inputs, "light_action_m0.input_id")
            operation_id = digest(args.operation, "light_action_m0.operation_id", length=32)
            if project_owner is None:
                raise BoundaryError("light_action_m0", "configured_local_workspace_required")
            binding = _admit_m0_input(project_owner, store, input_id, operation_id)
            input_manifest = store.get_manifest(input_id)
            input_info = input_manifest.parameters.value()
            public_profile = None
            if input_info.get("schema") == "stpd/stage1a-light-action-m0-public-input-v1":
                view = store.get_manifest(input_manifest.parent("model_view"))
                public_profile = view.parameters.value().get("serializer", {}).get("profile")
            m0_inputs = load_light_action_inputs(store, input_id)
            torch.set_num_threads(2)
            m0_config = LightActionM0Config(
                recipe=args.recipe, steps=args.steps, device=args.backend,
                max_state_tokens=args.max_state_tokens,
                max_action_bytes=args.max_action_bytes,
                public_profile=public_profile,
            )
            run = prepare_token_run(store, m0_inputs, m0_config, runtime,
                                    replicate=args.replicate)
            result = asdict(execute_tokens(
                store, ObjectStoreRunReporter(store, store.blobs), run.artifact_id, runtime,
                snapshot=args.snapshot, stop_after=args.stop_after,
                dev_admitter=lambda model, view: _reserve_m0_dev(
                    project_owner, store, binding, operation_id, run.artifact_id,
                    input_id, model, view),
            ))
            result["training_input_id"] = input_id
            result["training_binding"] = m0_inputs.manifest.parameters.value()["training_binding"]
        elif args.command == "run-tokens":
            import torch

            from stpd.workers.token_worker import execute_tokens, preflight_token_run

            torch.set_num_threads(2)
            run_id = digest(args.run, "token_run.id")
            run_manifest = store.get_manifest(run_id)
            input_manifest = store.get_manifest(run_manifest.parent("training_input"))
            dev_admitter = None
            if input_manifest.parameters.value().get("schema") in {
                    "stpd/stage1a-light-action-m0-canonical-input-v1",
                    "stpd/stage1a-light-action-m0-public-input-v1",
            }:
                resume_id = digest(args.resume, "token_run.resume") if args.resume else None
                preflight_token_run(store, run_id, runtime, resume=resume_id)
                if project_owner is None or args.operation is None:
                    raise BoundaryError(
                        "light_action_m0", "configured_owner_and_operation_required")
                binding = _admit_m0_run(project_owner, store, run_id, args.operation)
                def reserve_dev(model: Manifest, view: Manifest) -> dict[str, Any]:
                    return _reserve_m0_dev(
                        project_owner, store, binding, args.operation, run_id,
                        run_manifest.parent("training_input"), model, view)

                dev_admitter = reserve_dev
            result = asdict(execute_tokens(
                store, ObjectStoreRunReporter(store, store.blobs), run_id, runtime,
                snapshot=args.snapshot,
                resume=(digest(args.resume, "token_run.resume") if args.resume else None),
                stop_after=args.stop_after,
                dev_admitter=dev_admitter,
            ))
        elif args.command == "run-light-action-m0":
            import torch

            from stpd.workers.token_worker import execute_tokens, preflight_token_run

            torch.set_num_threads(2)
            run_id = digest(args.run, "light_action_m0.run_id")
            run_manifest = store.get_manifest(run_id)
            resume_id = (digest(args.resume, "light_action_m0.resume")
                         if args.resume else None)
            preflight_token_run(store, run_id, runtime, resume=resume_id)
            if project_owner is None:
                raise BoundaryError("light_action_m0", "configured_local_workspace_required")
            binding = _admit_m0_run(project_owner, store, run_id, args.operation)
            result = asdict(execute_tokens(
                store, ObjectStoreRunReporter(store, store.blobs), run_id, runtime,
                snapshot=args.snapshot,
                resume=resume_id,
                stop_after=args.stop_after,
                dev_admitter=lambda model, view: _reserve_m0_dev(
                    project_owner, store, binding, args.operation, run_id,
                    run_manifest.parent("training_input"), model, view),
            ))
        elif args.command == "run-memory":
            import torch

            from stpd.workers.memory_ranking import MemoryConfig
            from stpd.workers.memory_run import RUN_SCHEMA, execute_memory_run

            run_id = digest(args.run, "memory_run.id")
            run = store.get_manifest(run_id)
            info = run.parameters.value()
            if (run.kind != "run" or run.producer != runtime
                    or info.get("schema") != RUN_SCHEMA
                    or not isinstance(info.get("config"), dict)):
                raise BoundaryError("memory_run", "run_identity_mismatch")
            try:
                memory_config = MemoryConfig(**info["config"])
            except (TypeError, ValueError) as error:
                raise BoundaryError("memory_run", "config_format_mismatch") from error
            torch.set_num_threads(memory_config.cpu_threads)
            result = asdict(execute_memory_run(
                store, ObjectStoreRunReporter(store, store.blobs), run_id, runtime,
                resume=(digest(args.resume, "memory_run.resume") if args.resume else None),
                stop_after=args.stop_after,
            ))
        elif args.command == "prepare-workbench-memory":
            from spireagent.workbench.memory_training import prepare_workbench_memory

            source_id = digest(args.source, "memory_run.source")
            operation_id = digest(args.operation, "memory_run.operation", length=32)
            try:
                run_id, input_id = prepare_workbench_memory(
                    store, source_id, runtime, operation_id, args.recipe)
            except BoundaryError as error:
                print(json_bytes({"error_code": error.code}).decode())
                return 2
            result = {"run_id": run_id, "input_id": input_id}
        elif args.command == "verify-memory":
            import torch

            from stpd.workers.memory_ranking import MemoryConfig
            from stpd.workers.memory_run import RUN_SCHEMA, _load_run, _verify_completed

            run_id = digest(args.run, "memory_run.id")
            run = store.get_manifest(run_id)
            info = run.parameters.value()
            if (run.kind != "run" or run.producer != runtime
                    or info.get("schema") != RUN_SCHEMA
                    or not isinstance(info.get("config"), dict)):
                raise BoundaryError("memory_run", "run_identity_mismatch")
            verify_config = MemoryConfig(**info["config"])
            torch.set_num_threads(verify_config.cpu_threads)
            loaded = _load_run(store, run_id, runtime)
            completed = ObjectStoreRunReporter(store, store.blobs).completed(run_id)
            if completed is None:
                raise BoundaryError("memory_run", "completion_marker_missing")
            _verify_completed(store, completed, *loaded)
            result = {"run_id": run_id, "result_id": completed.artifact_id,
                      "model_id": completed.parent("model"),
                      "checkpoint_id": completed.parent("checkpoint")}
        elif args.command == "evaluate-memory":
            import torch

            from stpd.workers.memory_evaluation import _model_lineage
            from stpd.workers.memory_evaluation import evaluate_memory as evaluate_m2

            model_id = digest(args.model, "memory_evaluation.model")
            source_id = digest(args.source, "memory_evaluation.source")
            operation_id = digest(args.operation, "memory_evaluation.operation", length=32)
            _, _, memory_config, _, _ = _model_lineage(store, model_id)
            torch.set_num_threads(memory_config.cpu_threads)
            evaluation = evaluate_m2(
                store, model_id, source_id, runtime,
                max_settling_events=args.max_settling_events,
                operation_id=operation_id, semantic_overlap=args.semantic_overlap == "true",
            )
            result = {"evaluation_id": evaluation.artifact_id,
                      "evaluation_input_id": evaluation.parent("evaluation_input")}
        elif args.command in {"export-memory", "verify-memory-export"}:
            import torch

            from stpd.fullrun.memory_sequence_bridge import (
                parse_episode_projection_config,
                projection_input_profile,
            )
            from stpd.policy.memory_export import (
                export_memory_package,
                verify_memory_package,
            )
            from stpd.workers.memory_ranking import MemoryConfig

            run_id = digest(args.run, "memory_export.run")
            model_id = digest(args.model, "memory_export.model")
            selected = store.get_manifest(run_id)
            info = selected.parameters.value()
            if (selected.kind != "run" or not isinstance(info.get("config"), dict)):
                raise BoundaryError("memory_export", "run_identity_mismatch")
            training_input = store.get_manifest(selected.parent("training_input"))
            try:
                input_profile = projection_input_profile(parse_episode_projection_config(
                    training_input.parameters.value().get("projection_config")))
            except (TypeError, ValueError) as error:
                raise BoundaryError("memory_export", "projection_config_invalid") from error
            torch.set_num_threads(MemoryConfig(**info["config"]).cpu_threads)
            reporter = ObjectStoreRunReporter(store, store.blobs)
            if args.command == "export-memory":
                completed = reporter.completed(run_id)
                if completed is None or completed.parent("model") != model_id:
                    raise BoundaryError("memory_export", "completed_model_mismatch")
                package = export_memory_package(store, reporter, run_id, args.destination)
                verify_memory_package(store, reporter, model_id, args.destination,
                                      input_profile=input_profile)
            else:
                package = verify_memory_package(store, reporter, model_id,
                                                args.destination,
                                                input_profile=input_profile)
                if package["ids"]["run"] != run_id:
                    raise BoundaryError("memory_export", "run_identity_mismatch")
            result = {"model_id": model_id, "run_id": run_id,
                      "package_schema": package["schema"],
                      "result_id": package["ids"]["result"],
                      "checkpoint_id": package["ids"]["checkpoint"],
                      "payload_bytes": package["weights"]["size"]
                      + package["tokenizer"]["size"],
                      "package_sha256": file_sha256(args.destination / "model.json"),
                      "package_size": (args.destination / "model.json").stat().st_size,
                      "weights_sha256": package["weights"]["sha256"],
                      "weights_size": package["weights"]["size"],
                      "tokenizer_sha256": package["tokenizer"]["sha256"],
                      "tokenizer_size": package["tokenizer"]["size"]}
        elif args.command == "train":
            training_config = TrainingConfig(
                seed=1701, max_steps=args.steps, epochs=5,
                learning_rate=0.001, checkpoint_interval=16,
            )
            training = prepare_training_input(store, args.features, runtime, training_config)
            _, run = prepare_run(store, training.artifact_id, runtime, replicate=args.replicate)
            result = asdict(
                execute(
                    store,
                    ObjectStoreRunReporter(store, store.blobs),
                    run.artifact_id,
                    runtime,
                    resume=args.resume,
                    stop_after=args.stop_after,
                )
            )
        elif args.command == "export-tokens":
            from stpd.policy.token_decision import export_token_model

            result = export_token_model(store, args.model, args.destination)
        elif args.command == "export-light-action-m0":
            from stpd.policy.token_decision import export_light_action_m0_model

            model = store.get_manifest(digest(args.model, "light_action_m0.model_id"))
            if model.parameters.value().get("schema") in {
                    "stpd/stage1a-light-action-m0-canonical-model-v1",
                    "stpd/stage1a-light-action-m0-public-model-v1",
            }:
                if project_owner is None or args.operation is None:
                    raise BoundaryError(
                        "light_action_m0", "configured_owner_and_operation_required")
                _verify_m0_completion(store, model)
                _admit_m0_model(project_owner, store, model.artifact_id, args.operation)
            result = export_light_action_m0_model(store, model.artifact_id, args.destination)
        elif args.command == "export":
            from stpd.policy.decision import export_model

            result = export_model(store, args.model, args.destination)
        else:
            item = store.get_manifest(args.artifact)
            result = {
                "artifact_id": item.artifact_id,
                "kind": item.kind,
                "parents": [p.to_dict() for p in item.parents],
                "parameters": item.parameters.value(),
            }
    result["verification"] = {
        "semantic_view_loads": views.misses, "checked_view_reuses": views.hits
    }
    print(json_bytes({**result, "elapsed_seconds": perf_counter() - started}).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
