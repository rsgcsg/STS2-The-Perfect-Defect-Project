"""Synthetic Managed v2 source through the existing M2 train/export/scorer owners."""

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from test_artifact_store_v1 import PRODUCER, store
from test_managed_text_menu_import import _archive
from tokenizers import Tokenizer

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.package_identity import file_sha256
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from stpd.fullrun.managed_text_menu_import import import_managed_text_menu_report
from stpd.fullrun.memory_sequence_bridge import v2_episode_projection_config
from stpd.fullrun.memory_token_inputs import encode_memory_texts, project_memory_v2_snapshot
from stpd.fullrun.memory_training_prepare import fit_observed_memory_tokenizer
from stpd.fullrun.observed_input_sequence import load_observed_input_view
from stpd.policy.memory_export import (
    MANIFEST_NAME,
    RENDERER,
    V2_RENDERER,
    export_memory_package,
    validate_memory_package,
    verify_memory_package,
)
from stpd.policy.memory_port import MemoryPolicyAdapter
from stpd.policy.memory_scorer import OnlineM2Scorer
from stpd.workers.memory_ranking import MemoryConfig, load_memory_export
from stpd.workers.memory_run import (
    _load_run,
    execute_memory_run,
    prepare_observed_memory_run,
)


@pytest.fixture(autouse=True)
def two_cpu_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


@pytest.fixture
def package(tmp_path: Path):
    archive, report_id, expected = _archive(tmp_path)
    research = store(tmp_path / "research")
    source = import_managed_text_menu_report(
        archive, research, report_id, PRODUCER, expected=expected)
    view = load_observed_input_view(research, source.manifest.artifact_id)
    tokenizer_bytes, episode_count = fit_observed_memory_tokenizer(
        view, max_settling_events=0, input_profile="text-menu-v2")
    tokenizer = Tokenizer.from_str(tokenizer_bytes.decode("utf-8"))
    config = MemoryConfig(
        vocab_size=tokenizer.get_vocab_size(), episode_count=episode_count,
        max_tokens=4096, max_episode_observations=8, max_episode_input_tokens=65536,
        max_total_input_tokens=65536, max_chunk_steps=2,
        max_chunk_input_tokens=65536, cpu_threads=2,
    )
    run = prepare_observed_memory_run(
        research, source.manifest.artifact_id, config, PRODUCER, tokenizer_bytes,
        max_settling_events=0, input_profile="text-menu-v2", reject_diagnostics=True)
    _, input_manifest, _, engine = _load_run(research, run.artifact_id, PRODUCER)
    assert engine.next_episode == 0
    assert input_manifest.parameters.value()["projection_config"] == asdict(
        v2_episode_projection_config())
    reporter = ObjectStoreRunReporter(research, research.blobs)
    outcome = execute_memory_run(research, reporter, run.artifact_id, PRODUCER)
    assert outcome.state == "completed" and outcome.result_id
    destination = tmp_path / "package"
    exported = export_memory_package(research, reporter, run.artifact_id, destination)
    return destination, exported, research, reporter, run.artifact_id, view


def test_v2_run_replays_and_detached_scorer_binds_complete_order(package):
    directory, manifest, store_, reporter, run_id, view = package
    assert manifest["renderer"] == V2_RENDERER
    assert manifest["ids"]["source"] == view.source_id
    checked, weights, tokenizer_bytes, config = validate_memory_package(
        directory, input_profile="text-menu-v2")
    assert checked == manifest
    assert verify_memory_package(
        store_, reporter, manifest["ids"]["model"], directory,
        input_profile="text-menu-v2") == manifest
    scorer = OnlineM2Scorer.from_package(directory, input_profile="text-menu-v2")
    first = view.inputs[0].snapshot
    assert first is not None
    first_public = project_memory_v2_snapshot(first)
    row = encode_memory_texts(
        Tokenizer.from_str(tokenizer_bytes.decode("utf-8")),
        first_public.state_text, first_public.action_texts,
        max_tokens=config.max_tokens, slots=config.slots)
    offline = load_memory_export(weights, config, manifest["tokenizer"]["sha256"]).eval()
    with torch.inference_mode():
        expected, _ = offline.step(
            torch.tensor(row.state), tuple(torch.tensor(action) for action in row.actions),
            offline.initial_memory())
    for item in view.inputs:
        assert item.snapshot is not None
        public = project_memory_v2_snapshot(item.snapshot)
        scored = scorer.observe_and_score(
            continuity_token="managed-fixture", snapshot_bytes=json_bytes(item.snapshot),
            expected_candidate_digest=public.candidate_digest,
            expected_candidate_count=len(public.action_ids))
        assert scored.action_ids == public.action_ids
        assert scored.candidate_digest == public.candidate_digest
        assert len(scored.scores) == len(public.action_ids)
        if item is view.inputs[0]:
            assert scored.scores == tuple(float(value) for value in expected.tolist())
    assert scorer.observe_and_score(
        continuity_token="managed-fixture", snapshot_bytes=json_bytes(view.inputs[-1].snapshot),
        expected_candidate_digest=public.candidate_digest,
        expected_candidate_count=len(public.action_ids)) == scored
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory)
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        OnlineM2Scorer.from_package(directory)


def test_v2_unknown_profile_and_saved_projection_tamper_fail_closed(package):
    directory, _manifest, store_, _reporter, run_id, view = package
    _, _, tokenizer_bytes, config = validate_memory_package(
        directory, input_profile="text-menu-v2")
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        validate_memory_package(directory, input_profile="text-menu-v3")
    with pytest.raises(BoundaryError, match="unsupported_projection_profile"):
        prepare_observed_memory_run(
            store_, view.source_id, config, PRODUCER, tokenizer_bytes,
            input_profile="text-menu-v3")
    run = store_.get_manifest(run_id)
    input_manifest = store_.get_manifest(run.parent("training_input"))
    changed = input_manifest.parameters.value()
    changed["projection_config"]["renderer_id"] = "unverified"
    altered = Manifest(
        "training_input", PRODUCER, input_manifest.parents, input_manifest.payloads,
        FrozenObject.of(changed))
    store_.publish(altered)
    altered_run = Manifest(
        "run", PRODUCER,
        (Parent("training_input", altered.artifact_id),
         Parent("experiment", run.parent("experiment"))),
        parameters=run.parameters)
    store_.publish(altered_run)
    with pytest.raises(BoundaryError, match="projection_config_mismatch"):
        _load_run(store_, altered_run.artifact_id, PRODUCER)
    with pytest.raises(BoundaryError, match="candidate_binding_mismatch"):
        scorer = OnlineM2Scorer.from_package(directory, input_profile="text-menu-v2")
        assert view.inputs[0].snapshot is not None
        scorer.observe_and_score(
            continuity_token="managed-fixture",
            snapshot_bytes=json_bytes(view.inputs[0].snapshot),
            expected_candidate_digest="0" * 64, expected_candidate_count=1)


def test_v2_detached_manifest_rejects_mixed_renderer_and_profile(package):
    directory, manifest, *_ = package
    path = directory / MANIFEST_NAME
    original = path.read_bytes()
    try:
        for change in (lambda value: value.update(renderer=RENDERER),
                       lambda value: value["projection_config"].update(
                           input_profile="text-menu-v1"),
                       lambda value: value["projection_config"].update(
                           renderer_wrapper="unverified")):
            altered = deepcopy(manifest)
            change(altered)
            path.write_bytes(json_bytes(altered))
            with pytest.raises(BoundaryError):
                validate_memory_package(directory, input_profile="text-menu-v2")
    finally:
        path.write_bytes(original)
    assert validate_memory_package(directory, input_profile="text-menu-v2")[0] == manifest


def test_existing_v1_binder_port_and_cli_reject_v2_without_constructing_host(
    package, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    directory, manifest, *_ = package
    from test_memory_policy_port import _adapter

    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        _adapter((directory, manifest), tmp_path, monkeypatch)

    monkeypatch.setattr("stpd.policy.memory_port.validate", lambda *_args, **_kwargs: (
        {"schema": "stpd/m2-policy-config-v1", "export_path": str(directory),
         "export_manifest_sha256": file_sha256(directory / MANIFEST_NAME),
         "model_id": manifest["ids"]["model"]}, {},
    ))
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        MemoryPolicyAdapter(tmp_path / "config.json", tmp_path / "manifest.json")

    script = Path(__file__).resolve().parents[1] / "tools" / "managed_memory_smoke.py"
    spec = importlib.util.spec_from_file_location("managed_memory_smoke_v2_rejection", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    constructed = []

    class FakeHost:
        def __init__(self, *_args, **_kwargs):
            constructed.append(True)

        def observe_text_menu(self):
            raise AssertionError("must not observe")

        def submit_text_menu(self):
            raise AssertionError("must not submit")

    monkeypatch.setitem(sys.modules, "sts2_headless",
                        SimpleNamespace(ManagedPlayerEnvironment=FakeHost))
    monkeypatch.setattr(module, "activate_host_runtime_client", lambda *_args: None)
    monkeypatch.setattr(module, "load_host_runtime_pin", lambda *_args: {})
    monkeypatch.setattr(sys, "argv", ["managed_memory_smoke.py", "--candidate",
                                  str(tmp_path / "candidate"), "--model-export",
                                  str(directory), "--seed", "seed-a"])
    assert module.main() == 2
    assert constructed == []
