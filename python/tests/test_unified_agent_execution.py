"""Common owner contracts; lightweight pure/state tests (no process, NN or game)."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.agent_composition import describe
from spireagent.workbench.agent_run_request import checked, create, original, publish
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_models import LocalModelService
from stpd.policy.native_operational_outcome_v2 import progress_aware_strict_policy
from stpd.policy.native_teacher_agent import descriptor, write_artifact
from stpd.policy.native_teacher_manifest import teacher_manifest


def caps():
    return {
        "protocol_version": "1.0.0",
        "host": {
            "host_kind": "test",
            "version": "test",
            "implementation": {
                "source_revision": "a" * 40,
                "artifact_sha256": "b" * 64,
                "module_version_id": "test-module",
            },
        },
        "game": {
            "version": "test-game",
            "commit": "test-commit",
            "modset": {"status": "exact", "fingerprint": "c" * 64, "loaded_mod_ids": []},
        },
    }


def m2():
    entry = {
        "id": "public-m2",
        "adapter": "stpd-public-m2-decision-adapter",
        "runtime_profile": "public-snapshot-m2-v1",
    }
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "manifest_id": "public-m2",
        "policy": {"id": "m2", "version": "1.1.0"},
        "adapter": {
            "id": entry["adapter"],
            "protocol": "sts2.policy-runtime/decision-only-ndjson-4",
        },
        "artifact": {"id": "a" * 64, "sha256": "b" * 64},
        "representation": {
            "id": "public_compact",
            "input_schema": "sts2.player-environment/snapshot-1",
        },
        "adapter_config": {"public_m2_installation": {"task_spec": {"id": "summary"}}},
        "requirements": {},
        "support": {},
    }
    return describe(entry, manifest, {})


def test_structural_agents_are_not_uniform_models(tmp_path):
    policy = progress_aware_strict_policy()
    artifact = write_artifact(tmp_path / "program.json", execution_policy=policy)
    declared = descriptor(execution_policy=policy)
    manifest = teacher_manifest(declared, artifact, caps(), [], execution_policy=policy)
    entry = {
        "id": manifest["manifest_id"],
        "adapter": declared["adapter"]["id"],
        "runtime_profile": "native-logical-v1",
    }
    teacher = describe(entry, manifest, {}, declared)
    assert teacher["semantic_definition"]["models"] == []
    assert teacher["semantic_definition"]["state"]["kind"] == "ACK_committed_script"
    assert (
        teacher["semantic_definition"]["task_binding"]["original_spec"]
        == declared["agent_spec"]["task_spec"]
    )
    assert teacher["semantic_definition"]["driver"]["capabilities"] == "AgentSession"
    public = m2()
    assert len(public["semantic_definition"]["models"]) == 1
    assert public["semantic_definition"]["state"]["kind"] == "qualified_public_W"
    assert public["semantic_definition"]["driver"]["capabilities"] == "Decision4"
    assert "runtime_instance_id" not in teacher["semantic_definition"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["agent_spec"].update(model_bindings=[{"model_id": "fake"}]),
        lambda d: d["agent_spec"]["teacher"].update(version="wrong"),
        lambda d: d["adapter"].update(version="wrong"),
    ],
)
def test_one_factory_rejects_non_teacher_and_wrong_policy(change, tmp_path):
    policy = progress_aware_strict_policy()
    data = descriptor(execution_policy=policy)
    change(data)
    with pytest.raises(BoundaryError):
        teacher_manifest(
            data,
            {"id": "teacher", "path": str(tmp_path / "artifact"), "sha256": "a" * 64},
            caps(),
            [],
            execution_policy=policy,
        )


def test_request_is_immutable_and_later_links_are_not_backfilled(tmp_path):
    comp = m2()
    request = create(comp, "start", "short", {"max_submissions": 10}, "native")
    before = copy.deepcopy(request)
    ref = publish(tmp_path, request, "a" * 32)
    path = tmp_path / ref["path"]
    raw = path.read_bytes()
    startup = {
        "run_id": "new-run",
        "manifest_id": "public-m2",
        "policy_manifest_sha256": comp["implementation"]["manifest_sha256"],
    }
    value = original(tmp_path, ref, startup)
    assert value["operation_id"] == "a" * 32
    assert "run_id" not in value and "evidence_content_id" not in value
    assert path.read_bytes() == raw and request == before
    assert ref["sha256"] == hashlib.sha256(raw).hexdigest()
    assert "not_NN" in request["budget"]["units"]["policy_calls"]
    wrong = {**startup, "manifest_id": "wrong"}
    with pytest.raises(BoundaryError, match="original_request_run_binding_changed"):
        original(tmp_path, ref, wrong)
    path.write_bytes(raw + b" ")
    with pytest.raises(BoundaryError, match="original_request_changed"):
        original(tmp_path, ref, startup)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(operation="shell"),
        lambda r: r.update(target="cloud"),
        lambda r: r.update(composition_id="f" * 64),
        lambda r: r["composition"]["implementation"].update(command="arbitrary"),
    ],
)
def test_unadmitted_request_rejected_before_publish(change, tmp_path):
    request = create(m2(), "prepare-and-load", "short", None, "native")
    altered = copy.deepcopy(request)
    change(altered)
    with pytest.raises(BoundaryError):
        checked(altered, request)
    assert not (tmp_path / "requests").exists()


def service(tmp_path, monkeypatch):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    instance = LocalModelService(config)
    monkeypatch.setattr(instance, "_require_model_admission", lambda: None)
    monkeypatch.setattr(instance, "_run_profile", lambda *_args: True)
    monkeypatch.setattr(instance, "composition", lambda _identity: m2())
    monkeypatch.setattr(
        instance,
        "agent_request",
        lambda identity, action, profile="short", **kwargs: create(
            m2(), action, profile, {"max_submissions": 10}, "native", **kwargs
        ),
    )
    return instance


def test_legacy_start_request_exists_before_dispatch_and_keeps_intent(tmp_path, monkeypatch):
    instance = service(tmp_path, monkeypatch)
    seen = []

    def start(*args):
        ref = instance.state["agent_run_request"]
        assert (instance.directory / ref["path"]).is_file()
        seen.append((args, ref))

    monkeypatch.setattr(instance, "_start", start)
    started = instance.start("public-m2")
    instance.thread.join(timeout=2)
    assert started["operation"]["action"] == "start"
    assert instance.intent_generation == 1
    assert len(seen) == 1 and seen[0][0] == ("public-m2", 1)
    payload = json.loads((instance.directory / seen[0][1]["path"]).read_bytes())
    assert payload["operation"] == "start" and payload["run_profile"] == "short"


def test_receipt_publication_failure_prevents_existing_dispatch(tmp_path, monkeypatch):
    from spireagent.workbench import agent_run_request

    instance = service(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(instance, "_start", lambda *_args: calls.append("spawn"))

    def fail(*_args):
        raise OSError("synthetic storage failure")

    monkeypatch.setattr(agent_run_request, "publish", fail)
    with pytest.raises(OSError):
        instance.start("public-m2")
    assert calls == [] and instance.intent_generation == 0 and instance.state["operation"] is None


def test_closed_composition_options_bind_owning_specs_without_changing_default(monkeypatch):
    from spireagent.workbench.local_model_registration import LocalModelRegistration
    from stpd.policy.public_m0_agent_program import card_reward_take_program_spec
    from stpd.policy.public_m0_task import ready_summary_task_spec as m0_task
    from stpd.policy.public_m2_task import ready_summary_task_spec as m2_task

    owner = LocalModelRegistration.__new__(LocalModelRegistration)
    family = ["public_m0"]
    monkeypatch.setattr(owner, "_export_status", lambda _: {"operation": {"model_type": family[0]}})
    calls = []
    monkeypatch.setattr(owner, "register", lambda identity, **kw: calls.append(kw) or kw)
    owner.register_composition(
        "a" * 64, task_option="ready-standard-summary", program_option="card-reward-take"
    )
    assert (
        calls[-1]["task"] == m0_task() and calls[-1]["program"] == card_reward_take_program_spec()
    )
    owner.register_composition("a" * 64, task_option="none")
    assert calls[-1]["task"] is None and calls[-1]["program"] is None
    family[0] = "public_m2"
    owner.register_composition("a" * 64, task_option="ready-standard-summary")
    assert calls[-1]["task"] == m2_task()
    before = len(calls)
    for task, program in (
        ("shell", "none"),
        ("default", "arbitrary"),
        ("default", "card-reward-take"),
    ):
        with pytest.raises(BoundaryError):
            owner.register_composition("a" * 64, task_option=task, program_option=program)
    family[0] = "native"
    with pytest.raises(BoundaryError, match="public_composition_required"):
        owner.register_composition("a" * 64)
    assert len(calls) == before


def test_failed_new_operation_cannot_relabel_same_manifest_prior_run(tmp_path, monkeypatch):
    instance = service(tmp_path, monkeypatch)
    request = create(m2(), "start", "short", {"max_submissions": 10}, "native")
    prior = publish(instance.directory, request, "a" * 32)
    startup = {
        "run_id": "prior-run",
        "manifest_id": "public-m2",
        "policy_manifest_sha256": m2()["implementation"]["manifest_sha256"],
    }
    instance.state.update(
        startup=startup,
        agent_run_request=prior,
        startup_agent_run_request={"run_id": "prior-run", "request": prior},
    )

    def readiness_failed(*_args):
        raise BoundaryError("local_model", "model_readiness_blocked")

    monkeypatch.setattr(instance, "_start", readiness_failed)
    instance.start("public-m2")
    instance.thread.join(timeout=2)
    assert instance.state["operation"]["status"] == "failed"
    assert instance.state["agent_run_request"]["operation_id"] != "a" * 32
    bound = instance._original_agent_request(startup)
    assert bound["status"] == "bound" and bound["request"]["operation_id"] == "a" * 32
    instance.state["startup_agent_run_request"]["run_id"] = "different-run"
    assert instance._original_agent_request(startup)["status"] == "unknown"
    instance.state["startup_agent_run_request"] = "malformed"
    assert instance._original_agent_request(startup)["status"] == "unknown"
    instance.state.pop("startup_agent_run_request")
    assert instance._original_agent_request(startup)["status"] == "legacy_unknown"


def test_request_detaches_caller_inputs_and_cannot_claim_native_authority(tmp_path, monkeypatch):
    composition = m2()
    budget = {"max_submissions": 10}
    request = create(composition, "start", "short", budget, "native")
    request["budget"]["limits"]["max_submissions"] = 999
    request["composition"]["semantic_definition"]["models"].clear()
    assert budget == {"max_submissions": 10}
    assert len(composition["semantic_definition"]["models"]) == 1
    instance = service(tmp_path, monkeypatch)
    forged = instance.agent_request("public-m2", "start")
    forged["admission_context"]["native_intent"] = {"request_id": "forged"}
    with pytest.raises(BoundaryError, match="effective_composition_or_request_changed"):
        instance.run_agent(forged)
    assert instance.intent_generation == 0 and not (instance.directory / "requests").exists()


@pytest.mark.parametrize("field", ["learned", "max_state_bytes", "max_submissions"])
def test_request_bool_integer_substitution_rejected_before_owner_effects(
    tmp_path, monkeypatch, field
):
    declared = descriptor()
    artifact = write_artifact(tmp_path / "teacher-program.json")
    manifest = teacher_manifest(declared, artifact, caps(), [])
    comp = describe(
        {
            "id": manifest["manifest_id"],
            "adapter": declared["adapter"]["id"],
            "runtime_profile": "native-logical-v1",
        },
        manifest,
        {},
        declared,
    )
    effective = create(comp, "start", "short", {"max_submissions": 1}, "native")
    instance = service(tmp_path, monkeypatch)
    monkeypatch.setattr(instance, "agent_request", lambda *_a, **_k: copy.deepcopy(effective))
    forged = copy.deepcopy(effective)
    if field == "learned":
        forged["program_binding"]["learned"] = 0
        forged["composition"]["semantic_definition"]["program"]["learned"] = 0
    elif field == "max_state_bytes":
        forged["composition"]["semantic_definition"]["input"]["state_recovery"][field] = False
        forged["composition"]["semantic_definition"]["state"]["declaration"]["state_recovery"][
            field
        ] = False
    else:
        forged["budget"]["limits"][field] = True
    assert forged == effective  # Exact RED cause: Python equality loses JSON type.
    with pytest.raises(BoundaryError, match="effective_composition_or_request_changed"):
        instance.run_agent(forged)
    assert not (instance.directory / "requests").exists() and instance.intent_generation == 0


def test_original_request_rejects_changed_composition_despite_rehashed_file(tmp_path):
    request = create(m2(), "start", "short", None, "native")
    request["composition"]["semantic_definition"]["state"]["forged"] = True
    ref = publish(tmp_path, request, "a" * 32)
    startup = {
        "manifest_id": "public-m2",
        "policy_manifest_sha256": m2()["implementation"]["manifest_sha256"],
    }
    with pytest.raises(BoundaryError, match="original_request_run_binding_changed"):
        original(tmp_path, ref, startup)


@pytest.mark.parametrize("field", ["selection_id", "operation", "run_profile"])
@pytest.mark.parametrize("invalid", [None, False, 1, ["not-a-string"]])
def test_request_selector_types_reject_before_resolution(tmp_path, monkeypatch, field, invalid):
    instance = service(tmp_path, monkeypatch)
    request = instance.agent_request("public-m2", "start")
    request[field] = invalid
    calls = []
    monkeypatch.setattr(instance, "agent_request", lambda *_a, **_k: calls.append("resolve"))
    with pytest.raises(BoundaryError, match="invalid_request"):
        instance.run_agent(request)
    assert calls == [] and instance.intent_generation == 0
    assert not (instance.directory / "requests").exists()
