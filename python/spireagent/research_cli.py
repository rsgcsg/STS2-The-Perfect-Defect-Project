"""S01 and Stage 1a engineering workflows using shared storage and worker infrastructure.

Preparation runs beside the authoritative Hub operations database. Copies of a database
are not a substitute for current Gold reservations. Later commands consume the fixed
allocation; token and pooled execution share lineage, storage, reporting and evaluation owners.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from spireagent.hub.curation_access import record_use
from spireagent.hub.database import Operations
from spireagent.json_boundary import BoundaryError, digest, json_bytes, object_fields
from spireagent.package_identity import file_sha256
from spireagent.source import source_identity
from spireagent.storage.config import open_store
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.decision_training import AllocationSpec, publish_allocation, publish_decision_view
from stpd.fullrun.features import compile_features
from stpd.fullrun.representation import FullRunSerializer
from stpd.fullrun.view_session import verified_model_views
from stpd.workers.contracts import TrainingConfig, prepare_run, prepare_training_input
from stpd.workers.worker import execute


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
    run_tokens = commands.add_parser("run-tokens", help="execute an existing exact token run")
    run_tokens.add_argument("--run", required=True)
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
    evaluate_memory.add_argument("--max-settling-events", type=int, default=0)
    evaluate_memory.add_argument("--semantic-overlap", choices=("true", "false"), required=True)
    prepare_memory = commands.add_parser(
        "prepare-workbench-memory", help="prepare a caller-admitted train-only Human M2 run")
    prepare_memory.add_argument("--source", required=True)
    prepare_memory.add_argument("--operation", required=True)
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
    store = open_store(args.store)
    runtime = source_identity(Path(__file__).resolve().parents[1])
    started = perf_counter()
    with verified_model_views(store) as views:
        result: dict
        if args.command == "compare-tokens":
            from stpd.fullrun.token_comparison import compare_token_results

            result = compare_token_results(store, args.result)
        elif args.command == "prepare":
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
        elif args.command == "run-tokens":
            import torch

            from stpd.workers.token_worker import execute_tokens

            torch.set_num_threads(2)
            run_id = digest(args.run, "token_run.id")
            result = asdict(execute_tokens(
                store, ObjectStoreRunReporter(store, store.blobs), run_id, runtime,
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
                    store, source_id, runtime, operation_id)
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
            torch.set_num_threads(MemoryConfig(**info["config"]).cpu_threads)
            reporter = ObjectStoreRunReporter(store, store.blobs)
            if args.command == "export-memory":
                completed = reporter.completed(run_id)
                if completed is None or completed.parent("model") != model_id:
                    raise BoundaryError("memory_export", "completed_model_mismatch")
                package = export_memory_package(store, reporter, run_id, args.destination)
                verify_memory_package(store, reporter, model_id, args.destination)
            else:
                package = verify_memory_package(store, reporter, model_id,
                                                args.destination)
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
            config = TrainingConfig(
                seed=1701, max_steps=args.steps, epochs=5,
                learning_rate=0.001, checkpoint_interval=16,
            )
            training = prepare_training_input(store, args.features, runtime, config)
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
