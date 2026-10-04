"""Synthetic-only paused-checkpoint diagnostic path; no private rows or Gold data."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import sys
from dataclasses import replace

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore

EVALUATION_PRODUCER = Producer("local/checkpoint-diagnostic-test", "f" * 40, "e" * 64)


def _enable_public_fixture(monkeypatch) -> None:
    import test_decision_store
    from platform_bundle3_fixture import bundle3, load, seal, stream

    from spireagent.json_boundary import json_bytes

    def bundle_with_multi_candidate_public_catalog(path):
        bundle = bundle3(path, public_bindings=True)
        raw = bundle / "raw"
        trace_path = raw / "semantic-boundary-trace.jsonl"
        canonical_path = raw / "canonical-transitions.jsonl"
        trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
        canonical = [json.loads(line) for line in canonical_path.read_text().splitlines()]
        references = {}

        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get("object_ref"), str) and isinstance(
                    value.get("content_sha256"), str,
                ):
                    references[(value["object_ref"], value["content_sha256"])] = value
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(trace)
        collect(canonical)
        replacements = {}
        for identity, reference in references.items():
            frame = load(raw / reference["object_ref"])
            actions = frame["snapshot"]["bound_actions"]["actions"]
            if len(actions) == 1:
                alternative = copy.deepcopy(actions[0])
                alternative["bound_action_id"] += "-diagnostic-alternative"
                alternative["verb"] = "cancel"
                actions.append(alternative)
                frame["snapshot"]["bound_actions"].update(
                    total_count=2, materialized_count=2,
                )
                frame["catalog_count"] = 2
            content = json_bytes(frame)
            sha = hashlib.sha256(content).hexdigest()
            object_ref = f"semantic-frames/sha256/{sha[:2]}/{sha}.json"
            (raw / object_ref).parent.mkdir(parents=True, exist_ok=True)
            (raw / object_ref).write_bytes(content)
            replacements[identity] = {
                **reference, "object_ref": object_ref, "content_sha256": sha,
            }

        def replace_references(value):
            if isinstance(value, dict):
                identity = (value.get("object_ref"), value.get("content_sha256"))
                replacement = replacements.get(identity)
                if replacement is not None:
                    value.update(replacement)
                for child in value.values():
                    replace_references(child)
            elif isinstance(value, list):
                for child in value:
                    replace_references(child)

        replace_references(trace)
        replace_references(canonical)
        stream(trace_path, trace)
        stream(canonical_path, canonical)
        seal(bundle)
        return bundle

    monkeypatch.setattr(test_decision_store, "bundle3", bundle_with_multi_candidate_public_catalog)


def _cli_with_producer(monkeypatch, producer: Producer, *arguments: str) -> dict:
    from spireagent.research_cli import main

    monkeypatch.setattr("spireagent.research_cli.source_identity", lambda _root: producer)
    monkeypatch.setattr(sys, "argv", ["research-cli", *arguments])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = main()
    assert status == 0
    return json.loads(output.getvalue().strip().splitlines()[-1])


def _prepared_compact_input(
    monkeypatch, common: tuple[str, ...], config_path, dataset_id: str, operation: str,
    *, train_limit: int,
) -> dict:
    from test_light_action_m0_canonical_cli import _cli

    return _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s",
        "--input-profile", "public_compact",
        "--train-limit", str(train_limit), "--dev-limit", "4",
        "--max-state-tokens", "8193", "--max-action-bytes", "8193",
    )


def test_paused_compact_checkpoint_diagnostic_is_private_and_separately_bound(
    tmp_path, monkeypatch,
):
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_artifact_store_v1 import PRODUCER as TRAINING_PRODUCER
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    _enable_public_fixture(monkeypatch)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
        common = ("--store", str(store_dir))
        operation = "a" * 32
        training = _prepared_compact_input(
            monkeypatch, common, config_path, dataset_id, operation, train_limit=8,
        )
        fixed = _prepared_compact_input(
            monkeypatch, common, config_path, dataset_id, operation, train_limit=5,
        )
        paused = _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path),
            "--inputs", training["training_input_id"], "--operation", operation,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "2",
            "--stop-after", "1", "--max-state-tokens", "8193",
            "--max-action-bytes", "8193",
        )
        assert paused["state"] == "paused"
        store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
        reporter = ObjectStoreRunReporter(store, store.blobs)
        events_before = {item.artifact_id for item in reporter.events(paused["run_id"])}
        manifest_ids_before = set(store.manifest_ids())

        from stpd.fullrun import light_action_inputs

        fit_calls: list[tuple[str, ...]] = []
        original_fit = light_action_inputs.fit_state_bpe

        def record_fit(samples):
            fit_calls.append(tuple(sample.transition_id for sample in samples))
            return original_fit(samples)

        monkeypatch.setattr(light_action_inputs, "fit_state_bpe", record_fit)
        torch_rng_before = torch.get_rng_state().clone()
        same_view = _cli_with_producer(
            monkeypatch, EVALUATION_PRODUCER,
            *common, "diagnose-checkpoint",
            "--project-config", str(config_path),
            "--checkpoint", paused["checkpoint_id"],
            "--evaluation-input", training["training_input_id"],
            "--operation", operation,
        )
        fixed_view = _cli_with_producer(
            monkeypatch, EVALUATION_PRODUCER,
            *common, "diagnose-checkpoint",
            "--project-config", str(config_path),
            "--checkpoint", paused["checkpoint_id"],
            "--evaluation-input", fixed["training_input_id"],
            "--operation", operation,
        )
        assert torch.equal(torch_rng_before, torch.get_rng_state())
        assert len(fit_calls) == 2
        assert all(calls == fit_calls[0] for calls in fit_calls)
        assert same_view["comparison_mode"] == "training_input_dev"
        assert fixed_view["comparison_mode"] == "fixed_input_regression"
        assert same_view["candidate_count_gt1"] > 0
        assert fixed_view["candidate_count_gt1"] > 0
        assert set(fixed_view["baselines"]) == {"uniform_legal", "action_only"}
        assert same_view["native_run_independence"] is False
        assert same_view["clean_held_out_claim"] is False

        new_ids = set(store.manifest_ids()) - manifest_ids_before
        assert {store.get_manifest(identity).kind for identity in new_ids} == {"analysis"}
        assert reporter.completed(paused["run_id"]) is None
        assert {item.artifact_id for item in reporter.events(paused["run_id"])} == events_before

        for receipt in (same_view, fixed_view):
            report = store.get_manifest(receipt["analysis_id"])
            assert report.kind == "analysis"
            assert report.parameters.value()["diagnostic_type"] == "private_checkpoint_diagnostic"
            assert report.parameters.value()["checkpoint_step"] == 1
            assert report.parameters.value()["clean_held_out_claim"] is False
            assert report.parameters.value()["native_run_independence"] is False
            assert report.producer == EVALUATION_PRODUCER
            assert report.parameters.value()["training_producer"] == TRAINING_PRODUCER.to_dict()
            assert report.parent("run") == paused["run_id"]
            assert report.parent("checkpoint") == paused["checkpoint_id"]
            payload = report.payload("diagnostic")
            raw = b"".join(store.read_payload(payload))
            assert b"state_text" not in raw and b"action_texts" not in raw
            value = json.loads(raw)
            assert value["codec"]["codec_refit_on_evaluation_view"] is False
            assert value["dev_input_count"] > 0
            assert len(value["rows"]) == value["dev_input_count"]
            assert {row["split"] for row in value["rows"]} == {"dev"}
            assert value["summary"]["overall"]["count"] == value["dev_input_count"]
            assert all(
                baseline["overall"]["count"] == value["dev_input_count"]
                for baseline in value["baselines"].values()
            )
            assert len(value["dev_input_commitment_sha256"]) == 64
            assert value["summary"]["candidate_count_gt1"]["overall"]["count"] > 0
            assert value["summary"]["bootstrap"]["status"] == "unknown"

        external_report = store.get_manifest(fixed_view["analysis_id"])
        assert external_report.parent("training_input") == training["training_input_id"]
        assert external_report.parent("evaluation_input") == fixed["training_input_id"]
        assert external_report.parent("model_view") != training["model_view_id"]
        assert external_report.parameters.value()["evaluation_scope"] == (
            "cross_training_purpose_allocation_engineering_regression"
        )
    finally:
        torch.set_num_threads(previous_threads)


@pytest.mark.parametrize(
    ("failure", "reason"),
    (("malformed_payload", "unsupported_format_or_size"),
     ("outer_inner_step_mismatch", "checkpoint_step_mismatch")),
)
def test_invalid_checkpoint_does_not_reserve_evaluation_use(
    tmp_path, monkeypatch, failure, reason,
):
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_light_action_m0_canonical_cli import _cli, _synthetic_workspace

    from spireagent.artifact_contracts import Manifest, Parent
    from spireagent.json_boundary import FrozenObject
    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
    from stpd.workers.token_diagnostic import diagnose_checkpoint
    from stpd.workers.token_ranking import TokenRankingEngine

    _enable_public_fixture(monkeypatch)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
        common = ("--store", str(store_dir))
        operation = "c" * 32
        prepared = _prepared_compact_input(
            monkeypatch, common, config_path, dataset_id, operation, train_limit=8,
        )
        paused = _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path),
            "--inputs", prepared["training_input_id"], "--operation", operation,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "2",
            "--stop-after", "1", "--max-state-tokens", "8193",
            "--max-action-bytes", "8193",
        )
        store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
        checkpoint = store.get_manifest(paused["checkpoint_id"])
        run = store.get_manifest(paused["run_id"])
        synthetic_run = replace(
            run, parameters=FrozenObject.of({
                **run.parameters.value(), "synthetic_case": failure,
            }),
        )
        store.publish(synthetic_run)
        reporter = ObjectStoreRunReporter(store, store.blobs)
        if failure == "malformed_payload":
            raw = b"not-a-tensor-tree"
        else:
            state = decode_checkpoint(
                b"".join(store.read_payload(checkpoint.payload("checkpoint")))
            )
            state["step"] = 0
            state["optimizer"]["state"] = {}
            raw = encode_checkpoint(state)
        forged = replace(
            checkpoint,
            parents=(Parent("run", synthetic_run.artifact_id),
                     Parent("training_input", prepared["training_input_id"])),
            payloads=(store.put_payload(
                "checkpoint", io.BytesIO(raw), "application/vnd.stpd.tensor-tree",
            ),),
        )
        store.publish(forged)
        for kind in ("checkpoint", "paused"):
            reporter.emit(Manifest(
                "run_event", checkpoint.producer,
                (Parent("run", synthetic_run.artifact_id),),
                parameters=FrozenObject.of({
                    "schema": "stpd/run-event-v1", "attempt": "e" * 32,
                    "kind": kind, "step": 1,
                    "details": {"checkpoint_id": forged.artifact_id},
                }),
            ))
        before_manifests = set(store.manifest_ids())
        with owner.transaction() as db:
            before_runs = db.execute(
                "SELECT run,kind,reference FROM curation_uses "
                "WHERE kind='evaluation' ORDER BY run,reference"
            ).fetchall()
            before_sources = db.execute(
                "SELECT source,kind,reference FROM curation_source_uses "
                "WHERE kind='evaluation' ORDER BY source,reference"
            ).fetchall()

        def must_not_score(*_args, **_kwargs):
            pytest.fail("invalid checkpoints must fail before scoring")

        monkeypatch.setattr(TokenRankingEngine, "scores_for_row", must_not_score)
        with pytest.raises(BoundaryError, match=reason):
            diagnose_checkpoint(
                store, owner, forged.artifact_id, prepared["training_input_id"],
                operation, EVALUATION_PRODUCER,
            )

        with owner.transaction() as db:
            after_runs = db.execute(
                "SELECT run,kind,reference FROM curation_uses "
                "WHERE kind='evaluation' ORDER BY run,reference"
            ).fetchall()
            after_sources = db.execute(
                "SELECT source,kind,reference FROM curation_source_uses "
                "WHERE kind='evaluation' ORDER BY source,reference"
            ).fetchall()
        assert after_runs == before_runs
        assert after_sources == before_sources
        assert set(store.manifest_ids()) == before_manifests
    finally:
        torch.set_num_threads(previous_threads)


def test_synthetic_cuda_target_checkpoint_has_cpu_readonly_restore_path(tmp_path, monkeypatch):
    """A CPU-authored tensor tree with CUDA target identity; no CUDA device is used."""
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_light_action_m0_canonical_cli import _synthetic_workspace

    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from stpd.fullrun.light_action_inputs import load_light_action_inputs
    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
    from stpd.workers.token_ranking import (
        CUDA_STEP_RNG_PROTOCOL,
        LightActionM0Config,
        TokenRankingEngine,
        TokenTargetRuntime,
        config_payload,
    )

    _enable_public_fixture(monkeypatch)
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
        prepared = _prepared_compact_input(
            monkeypatch, ("--store", str(store_dir)), config_path, dataset_id,
            "b" * 32, train_limit=8,
        )
        store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
        inputs = load_light_action_inputs(store, prepared["training_input_id"])
        cpu_config = LightActionM0Config(
            steps=2, max_state_tokens=8193, max_action_bytes=8193,
            public_profile="public_compact",
        )
        source_engine = TokenRankingEngine(inputs, cpu_config)
        source_engine.advance()
        checkpoint = decode_checkpoint(source_engine.checkpoint())

        cuda_config = replace(cpu_config, device="cuda")
        diagnostic_engine = TokenRankingEngine(
            inputs, cuda_config, score_device="cpu",
        )
        checkpoint["config"] = config_payload(cuda_config)
        checkpoint["data_identity"] = diagnostic_engine.data_identity
        checkpoint["rng_protocol"] = CUDA_STEP_RNG_PROTOCOL
        checkpoint["torch_version"] = str(torch.__version__)
        checkpoint["cpu_threads"] = torch.get_num_threads()
        encoded = encode_checkpoint(checkpoint)
        optimizer_before = diagnostic_engine.optimizer.state_dict()
        rng_before = torch.get_rng_state().clone()
        target = TokenTargetRuntime.current()
        assert diagnostic_engine.restore_for_diagnostic(encoded, target) == 1
        scores = diagnostic_engine.scores_for_row(inputs.rows[0])
        assert len(scores) == len(inputs.samples[0].action_keys)
        assert diagnostic_engine.step == 0
        assert diagnostic_engine.optimizer.state_dict() == optimizer_before
        assert torch.equal(rng_before, torch.get_rng_state())
        with pytest.raises(BoundaryError, match="diagnostic_engine_is_read_only"):
            diagnostic_engine.advance()
        with pytest.raises(BoundaryError, match="diagnostic_engine_is_read_only"):
            diagnostic_engine.restore(encoded)
    finally:
        torch.set_num_threads(previous_threads)
