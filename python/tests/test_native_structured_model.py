"""Synthetic native cross-language input and numerical AgentSession conformance."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.native_structured_inputs import (
    INPUT_SPEC,
    PROFILE,
    SCOPE,
    native_catalog_digest,
    project_native_structured,
)
from stpd.fullrun.native_structured_sequences import SOURCE_SCHEMA, parse_native_sequence
from stpd.models.native_structured_scorer import NativeStructuredScorer
from stpd.models.structured_m2 import StructuredM2
from stpd.native_code_scope import PATHS
from stpd.policy.native_agent import (
    LIMIT_MAXIMA,
    MAX_STATE_BYTES,
    SESSION_SCHEMA,
    NativeStructuredAgent,
    adapter_identity,
)
from stpd.policy.native_structured_export import (
    AGENT_SPEC,
    STATE_FORMAT,
    encode_native_weights,
    export_native_package,
    load_native_package,
)
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT.parent / "components/connector/contracts/fixtures/native-logical-v1.json"
WIRE = json.loads(FIXTURES.read_text())


@pytest.fixture(autouse=True)
def two_threads():
    old = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def snapshot(number=1, *, empty=False, focus=None):
    value = copy.deepcopy(WIRE["wire_samples"]["observation"])
    value["snapshot_id"] = f"snapshot-{number}"
    value["revision"] = number
    value["owner_occurrence"].update(
        occurrence_id=f"occurrence-{number}",
        focus_referent_id=focus,
        focus_occurrence=None if focus is None else f"focus-{number}",
    )
    value["referents"] = [
        {
            "referent_id": identifier,
            "kind": "card",
            "role": "card",
            "label": "Strike",
            "properties_schema": "public-card",
            "properties": {"cost": 1, "damage": 6},
            "state": {
                "visible": False,
                "enabled": True,
                "selected": False,
                "focused": identifier == focus,
                "observation_basis": "logical-public",
            },
        }
        for identifier in ("card-A", "card-B")
    ]
    actions = (
        []
        if empty
        else [
            {
                "action_id": f"action-{number}-{identifier}",
                "kind": "native_input",
                "verb": "pick",
                "label": "Pick Strike",
                "subject_referent_id": identifier,
                "arguments": [],
                "effect_domain": "native",
            }
            for identifier in ("card-A", "card-B")
        ]
    )
    value["catalog"].update(
        snapshot_id=value["snapshot_id"],
        total_count=len(actions),
        digest=native_catalog_digest(actions),
    )
    return value, actions


def offer(observation, actions, acquisition="acquisition-1", previous=None, continuity="segment"):
    return {
        "acquisition_id": acquisition,
        "input_spec": INPUT_SPEC,
        "continuity_token": continuity,
        "previous_consumption_id": previous,
        "observation": observation,
        "catalog": actions,
    }


def ack(report, publication="1"):
    return {
        key: report[key]
        for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
    } | {
        "prefix": {
            "continuity_token": report["continuity_token"],
            "history_mode": "full_reference",
            "consumption_mode": "once_per_occurrence",
            "received_cursor": "known-cursor",
            "consumed_publication_index": publication,
            "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None},
        }
    }


def scorer():
    model = StructuredM2(seed=0)
    return NativeStructuredScorer(
        model, "1" * 64, hashlib.sha256(encode_native_weights(model)).hexdigest()
    )


def test_owned_csharp_digest_fixtures_and_invalid_cases():
    for case in WIRE["digest_cases"]:
        assert native_catalog_digest(case["actions"]) == case["sha256"]
    for case in WIRE["invalid_action_cases"]:
        with pytest.raises(BoundaryError):
            native_catalog_digest(case["actions"])


def test_csharp_complete_empty_observation_is_directly_projected():
    frame = project_native_structured(WIRE["wire_samples"]["observation"], [])
    assert frame.nodes and frame.candidates == ()
    assert frame.candidate_digest == WIRE["digest_cases"][0]["sha256"]


def test_all_authorized_public_referents_and_actual_focus_are_preserved():
    value, actions = snapshot(focus="card-A")
    frame = project_native_structured(value, actions)
    assert len(frame.ref_rows) == 2 and frame.action_ids == tuple(a["action_id"] for a in actions)
    # Both refs are logically public despite visible:false. No catalog is filtered.
    focused_rows = {target for _, target, relation in frame.edges if relation == 2}
    rows = dict(frame.ref_rows)
    assert rows["card-A"] in focused_rows
    model = StructuredM2(seed=0)
    encoded = model.encode(frame)
    assert not torch.equal(encoded[rows["card-A"]], encoded[rows["card-B"]])


def test_catalog_never_changes_observation_encoder_or_writer():
    value, actions = snapshot()
    first = project_native_structured(value, actions)
    changed = copy.deepcopy(actions)
    changed.reverse()
    changed[0]["label"] = "different candidate wording"
    value["catalog"]["digest"] = native_catalog_digest(changed)
    second = project_native_structured(value, changed)
    assert first.nodes == second.nodes and first.edges == second.edges
    assert first.state_digest == second.state_digest
    model = StructuredM2(seed=0)
    assert torch.equal(model.encode(first), model.encode(second))
    assert torch.equal(
        model.advance(model.encode(first), model.initial_memory()),
        model.advance(model.encode(second), model.initial_memory()),
    )


def test_private_future_and_program_metadata_do_not_enter_features_but_public_outcome_does():
    value, actions = snapshot()
    base = project_native_structured(value, actions)
    value["persistent"]["content"].update(
        receipt={"entity_id": "poison", "hp": 999},
        history={"future_state": {"damage": 999}},
        seed="hidden-seed",
        teacher_label="chosen",
    )
    masked = project_native_structured(value, actions)
    assert base.nodes == masked.nodes and base.state_digest == masked.state_digest
    value["persistent"]["content"]["run_outcome"] = "victory_public_summary"
    terminal = project_native_structured(value, actions)
    assert any(node.text == "victory_public_summary" for node in terminal.nodes)


def test_pending_consume_commits_only_on_exact_ack_and_empty_c_advances(monkeypatch):
    current = scorer()
    value, actions = snapshot(empty=True)
    before = current.memory.clone()
    report = current.propose_consume(offer(value, actions))
    assert report["advanced"] and report["state_version"] == 1
    assert torch.equal(before, current.memory)
    with pytest.raises(BoundaryError, match="acknowledged_basis"):
        current.scores()
    wrong = ack(report)
    wrong["state_version"] = 2
    with pytest.raises(BoundaryError, match="consume_ack_binding"):
        current.acknowledge(wrong)
    assert torch.equal(before, current.memory)
    current.acknowledge(ack(report))
    assert not torch.equal(before, current.memory)
    monkeypatch.setattr(current.model, "score", lambda *_args: pytest.fail("empty C was scored"))
    assert current.scores() == ()


def test_duplicate_pages_do_not_advance_equal_features_new_occurrence_does():
    current = scorer()
    value, actions = snapshot(empty=True)
    report = current.propose_consume(offer(value, actions))
    current.acknowledge(ack(report))
    before = current.memory.clone()
    repeated = copy.deepcopy(value)
    repeated["observed_at"] = "later-clock"
    repeated["catalog"].update(catalog_ref="new-handle", scope_id="same-fields-new-scope")
    duplicate = current.propose_consume(
        offer(repeated, actions, "acquisition-2", current.consumption_id)
    )
    assert not duplicate["advanced"] and duplicate["consumption_id"] == report["consumption_id"]
    current.acknowledge(ack(duplicate, "2"))
    assert torch.equal(before, current.memory)
    new, actions = snapshot(2, empty=True)
    advanced = current.propose_consume(offer(new, actions, "acquisition-3", current.consumption_id))
    assert advanced["advanced"] and advanced["state_version"] == 2
    current.acknowledge(ack(advanced, "3"))
    assert not torch.equal(before, current.memory)


def test_same_key_drift_regression_and_generation_without_reset_fail_before_mutation():
    current = scorer()
    value, actions = snapshot()
    report = current.propose_consume(offer(value, actions))
    current.acknowledge(ack(report))
    before = current.memory.clone()
    wrong = copy.deepcopy(value)
    wrong["persistent"]["content"]["text"] = "changed at same occurrence"
    with pytest.raises(BoundaryError, match="coherence_drift"):
        current.propose_consume(offer(wrong, actions, previous=current.consumption_id))
    new, actions = snapshot(2)
    new["catalog"]["stream_generation"] = "another-generation"
    with pytest.raises(BoundaryError, match="generation_requires_explicit_reset"):
        current.propose_consume(offer(new, actions, previous=current.consumption_id))
    assert torch.equal(before, current.memory) and current.pending is None


def test_offline_online_native_replay_has_exact_same_resets_advances_and_scores():
    one, c1 = snapshot(empty=True)
    two, c2 = snapshot(2)
    rows = [
        {
            "observation": o,
            "catalog": c,
            "continuity_token": "segment",
            "reset_before": index == 0,
            "chosen_action_id": None if not c else c[0]["action_id"],
        }
        for index, (o, c) in enumerate(((one, c1), (one, c1), (two, c2)))
    ]
    sequence = parse_native_sequence(
        json_bytes(
            {
                "schema": SOURCE_SCHEMA,
                "source_kind": "synthetic",
                "input_spec": INPUT_SPEC,
                "steps": rows,
            }
        )
    )
    assert [row.advance for row in sequence.steps] == [True, False, True]
    online = scorer()
    offline = online.model.initial_memory()
    for index, step in enumerate(sequence.steps):
        if step.reset_before:
            offline = online.model.initial_memory()
        entities = online.model.encode(step.frame)
        if step.advance:
            offline = online.model.advance(entities, offline)
        response = online.propose_consume(
            offer(
                rows[index]["observation"],
                rows[index]["catalog"],
                f"acquisition-{index}",
                online.consumption_id,
            )
        )
        online.acknowledge(ack(response, str(index + 1)))
        assert torch.equal(offline, online.memory)
        if step.frame.candidates:
            with torch.inference_mode():
                expected = tuple(
                    float(x) for x in online.model.score(step.frame, entities, offline)
                )
            assert online.scores() == expected


@pytest.fixture
def agent_files(tmp_path):
    folder = tmp_path / "package"
    origin = Producer(
        "https://example.test/source",
        "a" * 40,
        hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    )
    metadata = export_native_package(
        StructuredM2(seed=0),
        folder,
        producer=origin,
        data_sha256="d" * 64,
        training={"initialization_only": True},
    )
    manifest = {
        "schema": "sts2.policy-runtime/agent-manifest-1",
        "manifest_id": "native-fixture",
        "agent": {
            "id": AGENT_SPEC["id"],
            "version": AGENT_SPEC["version"],
            "provider": "stpd",
            "architecture": "structured-native-m2-k1d96",
        },
        "adapter": adapter_identity(),
        "artifact": {
            "id": metadata["model_id"],
            "path": str(folder / "model.json"),
            "sha256": hashlib.sha256((folder / "model.json").read_bytes()).hexdigest(),
        },
        "input": {
            "profile": PROFILE,
            "input_spec": INPUT_SPEC,
            "projection": {"id": INPUT_SPEC["id"], "version": INPUT_SPEC["version"]},
            "state_format_version": STATE_FORMAT,
            "history_mode": "full_reference",
            "consumption_mode": "once_per_occurrence",
            "gap_policy": "handoff",
            "state_recovery": {
                "mode": "opaque",
                "max_state_bytes": MAX_STATE_BYTES,
                "model_bindings": [
                    {
                        "model_id": metadata["model_id"],
                        "weights_sha256": metadata["weights"]["sha256"],
                    }
                ],
            },
            "attachment": {
                "eager_scope": list(SCOPE),
                "required_seams": [
                    {"source_seam": "bootstrap", "version": "1", "coverage": "complete_at_seam"}
                ],
                "delivery_mode": "full_reference",
            },
        },
        "requirements": {
            "connector_protocol_version": "1.0.0",
            "environment": {
                "host_kind": "test",
                "connector_version": "fixture",
                "connector_source_revision": "fixture",
                "connector_artifact_sha256": "c" * 64,
                "connector_module_version_id": "fixture",
                "modset_status": "fixture",
                "modset_fingerprint": "fixture",
                "loaded_mod_ids": [],
            },
            "required_methods": ["attach", "events", "read", "catalog", "submit", "await"],
        },
        "support": {
            "game_versions": ["synthetic"],
            "game_commits": ["synthetic"],
            "interaction_kinds": ["selector"],
            "action_verbs": ["pick"],
        },
        "limits": dict(LIMIT_MAXIMA),
        "claims": {
            "catalog_filtered": False,
            "creates_action_authority": False,
            "creates_native_operands": False,
            "human_origin": False,
            "causal_successor": False,
        },
    }
    path = tmp_path / "agent.json"
    path.write_bytes(json_bytes(manifest))
    return folder, path, metadata, manifest


def state_metadata(agent):
    scorer = agent.scorer
    observation = scorer.input["observation"]
    return {
        "agent_artifact_id": agent.manifest["artifact"]["id"],
        "agent_artifact_sha256": agent.manifest["artifact"]["sha256"],
        "adapter_code_sha256": agent.manifest["adapter"]["code_sha256"],
        "model_bindings": scorer.model_bindings,
        "input_spec": INPUT_SPEC,
        "profile": PROFILE,
        "state_format_version": STATE_FORMAT,
        "stream_generation": scorer.unit.occurrence[0],
        "continuity_token": scorer.continuity,
        "consumption_id": scorer.consumption_id,
        "state_version": scorer.state_version,
        "prefix": scorer.prefix,
        "last_acknowledged_basis": {
            "acquisition_id": scorer.acquisition_id,
            "capture_sha256": "c" * 64,
            "snapshot_id": observation["snapshot_id"],
            "owner_occurrence": observation["owner_occurrence"],
            "revision": observation["revision"],
            "included": list(SCOPE),
            "publication_index": scorer.prefix["consumed_publication_index"],
        },
    }


def test_native_package_new_identity_closed_verifier_and_weight_drift(agent_files):
    folder, _path, metadata, _manifest = agent_files
    assert load_native_package(folder)[0] == metadata
    assert metadata["input_spec"] == INPUT_SPEC and metadata["projection"]["profile"] == PROFILE
    assert metadata["qualification"] == "synthetic_engineering_only"
    (folder / "weights.tensor-tree").write_bytes(b"corrupted")
    with pytest.raises(BoundaryError, match="weights_digest"):
        load_native_package(folder)


def test_actual_weights_and_ack_bound_state_roundtrip_preserve_scores(agent_files):
    folder, path, *_ = agent_files
    agent = NativeStructuredAgent(folder, path)
    value, actions = snapshot()
    report = agent.consume(offer(value, actions))
    with pytest.raises(BoundaryError):
        agent.scorer.state()
    agent.scorer.acknowledge(ack(report))
    metadata = state_metadata(agent)
    exported = agent.export_state(metadata)
    restored = NativeStructuredAgent(folder, path)
    assert restored.restore_state(metadata, exported) == {"metadata": metadata}
    assert torch.equal(agent.scorer.memory, restored.scorer.memory)
    assert agent.scorer.scores() == restored.scorer.scores()
    next_input = {
        "continuity_token": "segment",
        "consumption_id": report["consumption_id"],
        "state_version": 1,
        "basis_acquisition_id": "acquisition-1",
        "received_cursor": "known-cursor",
    }
    assert agent.next(next_input) == restored.next(next_input)


@pytest.mark.parametrize("field", ["model_bindings", "input_spec", "stream_generation", "prefix"])
def test_state_wrong_binding_is_rejected_without_mutation(agent_files, field):
    folder, path, *_ = agent_files
    original = NativeStructuredAgent(folder, path)
    value, actions = snapshot(empty=True)
    report = original.consume(offer(value, actions))
    original.scorer.acknowledge(ack(report))
    metadata = state_metadata(original)
    exported = original.export_state(metadata)
    changed = copy.deepcopy(metadata)
    changed[field] = [] if field == "model_bindings" else "wrong-binding"
    target = NativeStructuredAgent(folder, path)
    before = target.scorer.memory.clone()
    with pytest.raises((BoundaryError, TypeError)):
        target.restore_state(changed, exported)
    assert torch.equal(before, target.scorer.memory) and target.scorer.unit is None


def test_empty_c_agent_await_is_not_a_fake_wait_candidate(agent_files):
    folder, path, *_ = agent_files
    agent = NativeStructuredAgent(folder, path)
    value, actions = snapshot(empty=True)
    report = agent.consume(offer(value, actions))
    agent.scorer.acknowledge(ack(report))
    result = agent.next(
        {
            "continuity_token": "segment",
            "consumption_id": report["consumption_id"],
            "state_version": 1,
            "basis_acquisition_id": "acquisition-1",
            "received_cursor": "known-cursor",
        }
    )
    assert result["directive"] == {
        "type": "await",
        "after_cursor": "known-cursor",
        "condition": "observation",
        "timeout_ms": 30000,
    }


def test_clean_native_import_inventory_is_complete_and_excludes_apps():
    script = f"""
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
import stpd.policy.native_agent
actual = {{Path(m.__file__).resolve().relative_to(Path({str(ROOT)!r})).as_posix()
           for name, m in sys.modules.items() if name.split(".")[0] in {{"stpd", "spireagent"}}
           and getattr(m,"__file__",None)}}
assert actual <= set({PATHS!r}), actual - set({PATHS!r})
blocked = ("spireagent.hub", "spireagent.workbench", "stpd.training", "stpd.qwen")
assert not any(name.startswith(blocked) for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], text=True, capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_actual_stdio_agent_session_consume_ack_act_and_empty_c_await(agent_files):
    folder, manifest_path, _metadata, manifest = agent_files
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0,{str(ROOT)!r}); "
            "from stpd.policy.native_agent import main; "
            "raise SystemExit(main())",
            "--package",
            str(folder),
            "--manifest",
            str(manifest_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin and process.stdout
    try:
        ready = json.loads(process.stdout.readline())
        assert ready == {
            "schema": SESSION_SCHEMA,
            "message_type": "ready",
            "adapter": manifest["adapter"],
        }

        def send(kind, identity, value, field="input"):
            process.stdin.write(
                json_bytes(
                    {
                        "schema": SESSION_SCHEMA,
                        "message_type": kind,
                        "session_id": "session",
                        "recovery_epoch": 0,
                        "request_id": identity,
                        field: value,
                    }
                ).decode()
            )
            process.stdin.flush()

        observation, catalog = snapshot()
        send("consume", "parent-1", offer(observation, catalog))
        consumed = json.loads(process.stdout.readline())
        assert consumed["message_type"] == "consumed" and consumed["request_id"] == "parent-1"
        report = consumed["completion"]
        send("consume_ack", "parent-1", ack(report), "completion")
        send(
            "next",
            "parent-2",
            {
                "continuity_token": "segment",
                "consumption_id": report["consumption_id"],
                "state_version": 1,
                "basis_acquisition_id": "acquisition-1",
                "received_cursor": "known-cursor",
            },
        )
        directive = json.loads(process.stdout.readline())["output"]["directive"]
        assert directive["type"] == "act"
        assert directive["selection"]["action_id"] in [a["action_id"] for a in catalog]
        assert len(directive["scores"]["values"]) == len(catalog)
        observation, catalog = snapshot(2, empty=True)
        send(
            "consume",
            "parent-3",
            offer(observation, catalog, "acquisition-2", report["consumption_id"]),
        )
        report = json.loads(process.stdout.readline())["completion"]
        assert report["advanced"] and report["state_version"] == 2
        send("consume_ack", "parent-3", ack(report, "2"), "completion")
        send(
            "next",
            "parent-4",
            {
                "continuity_token": "segment",
                "consumption_id": report["consumption_id"],
                "state_version": 2,
                "basis_acquisition_id": "acquisition-2",
                "received_cursor": "cursor-2",
            },
        )
        directive = json.loads(process.stdout.readline())["output"]["directive"]
        assert directive["type"] == "await" and directive["after_cursor"] == "cursor-2"
        process.stdin.close()
        assert process.wait(timeout=30) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def test_mutated_weights_and_nonfinite_state_fail_before_commit(agent_files):
    folder, path, *_ = agent_files
    agent = NativeStructuredAgent(folder, path)
    observation, catalog = snapshot()
    report = agent.consume(offer(observation, catalog))
    agent.scorer.acknowledge(ack(report))
    metadata = state_metadata(agent)
    exported = agent.export_state(metadata)
    payload = exported["payload"]
    import base64

    decoded = decode_checkpoint(base64.b64decode(payload["data_base64"]))
    decoded["state"]["memory"][0, 0] = float("nan")
    with pytest.raises(BoundaryError, match="non_finite_tensor"):
        encode_checkpoint(decoded)
    decoded["state"]["memory"] = torch.zeros((2, 96), dtype=torch.float32)
    raw = encode_checkpoint(decoded)
    payload.update(
        data_base64=base64.b64encode(raw).decode(),
        byte_count=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
    target = NativeStructuredAgent(folder, path)
    before = target.scorer.memory.clone()
    with pytest.raises(BoundaryError):
        target.restore_state(metadata, exported)
    assert torch.equal(before, target.scorer.memory)
    with torch.no_grad():
        next(agent.scorer.model.parameters()).add_(1.0)
    with pytest.raises(BoundaryError, match="actual_weights_changed"):
        agent.export_state(metadata)


@pytest.mark.parametrize("count", [200, 500, 10000])
def test_complete_native_catalog_projection_preserves_every_member(count):
    value, _ = snapshot()
    actions = [
        {
            "action_id": f"action-{i}",
            "kind": "native_input",
            "verb": "pick",
            "label": "Pick",
            "subject_referent_id": "card-A",
            "arguments": [],
            "effect_domain": "native",
        }
        for i in range(count)
    ]
    value["catalog"].update(total_count=count, digest=native_catalog_digest(actions))
    frame = project_native_structured(value, actions)
    assert len(frame.candidates) == count
    assert frame.action_ids == tuple(a["action_id"] for a in actions)


def test_same_feature_new_focus_and_aba_are_distinct_consumption_units():
    current = scorer()
    expected = []
    for number, owner, focus_occurrence in (
        (1, "owner-A", "focus-A"),
        (2, "owner-A", "focus-B"),
        (3, "owner-B", "focus-C"),
        (4, "owner-A", "focus-A-new"),
    ):
        observation, catalog = snapshot(number, empty=True)
        observation["owner_occurrence"].update(owner_id=owner, focus_occurrence=focus_occurrence)
        frame = project_native_structured(observation, catalog)
        expected.append(frame.state_digest)
        report = current.propose_consume(
            offer(observation, catalog, f"acq-{number}", current.consumption_id)
        )
        assert report["advanced"] and report["state_version"] == number
        current.acknowledge(ack(report, str(number)))
    assert len(set(expected)) == 1  # Source occurrence identities never enter E.


@pytest.mark.parametrize(
    "mutate",
    [
        lambda observation: observation["completeness"].update(full_reference_complete=False),
        lambda observation: observation["completeness"].update(full_reference_complete=1),
        lambda observation: observation["completeness"].update(missing=["interaction.preview"]),
        lambda observation: observation["catalog"].update(total_count=1),
        lambda observation: observation["owner_occurrence"].update(
            focus_referent_id="private-missing"
        ),
    ],
)
def test_partial_or_wrong_native_binding_never_changes_memory(mutate):
    observation, catalog = snapshot(empty=True)
    mutate(observation)
    current = scorer()
    before = current.memory.clone()
    with pytest.raises(BoundaryError):
        current.propose_consume(offer(observation, catalog))
    assert torch.equal(before, current.memory) and current.pending is None


def test_empty_catalog_cannot_acquire_fake_wait_training_label():
    observation, catalog = snapshot(empty=True)
    body = {
        "schema": SOURCE_SCHEMA,
        "source_kind": "synthetic",
        "input_spec": INPUT_SPEC,
        "steps": [
            {
                "observation": observation,
                "catalog": catalog,
                "continuity_token": "segment",
                "reset_before": True,
                "chosen_action_id": "wait",
            }
        ],
    }
    with pytest.raises(BoundaryError, match="label_catalog_binding"):
        parse_native_sequence(json_bytes(body))
    body["source_kind"] = "agent"
    body["steps"][0]["chosen_action_id"] = None
    with pytest.raises(BoundaryError, match="source_identity_or_scope"):
        parse_native_sequence(json_bytes(body))


def test_state_requires_fresh_child_and_rejects_corrupt_payload_and_gap(agent_files):
    folder, path, *_ = agent_files
    agent = NativeStructuredAgent(folder, path)
    observation, catalog = snapshot()
    report = agent.consume(offer(observation, catalog))
    agent.scorer.acknowledge(ack(report))
    metadata = state_metadata(agent)
    state = agent.export_state(metadata)
    with pytest.raises(BoundaryError, match="fresh_state_restore_required"):
        agent.restore_state(metadata, state)
    target = NativeStructuredAgent(folder, path)
    invalid = copy.deepcopy(state)
    invalid["payload"]["byte_count"] = MAX_STATE_BYTES + 1
    with pytest.raises(BoundaryError, match="metadata_or_size"):
        target.restore_state(metadata, invalid)
    invalid = copy.deepcopy(state)
    invalid["payload"]["sha256"] = "0" * 64
    with pytest.raises(BoundaryError, match="integrity"):
        target.restore_state(metadata, invalid)
    gap = ack(report)
    gap["prefix"]["omissions"]["gap"] = {"reason": "lost-source"}
    blocked = scorer()
    proposal = blocked.propose_consume(offer(observation, catalog))
    gap.update(
        {
            key: proposal[key]
            for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
        }
    )
    with pytest.raises(BoundaryError, match="qualified_ack_prefix_required"):
        blocked.acknowledge(gap)
    assert blocked.unit is None


def test_native_static_potential_imports_and_lazy_initializers_fit_reviewed_closure():
    from test_structured_code_scope import (
        test_reviewed_static_potential_imports_and_initializers_are_covered,
    )

    test_reviewed_static_potential_imports_and_initializers_are_covered("native-inference", PATHS)
