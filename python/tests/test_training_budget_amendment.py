"""Stopped-writer authorization preserves cumulative charges and immutable inputs."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_training_service_contracts import ready

from spireagent.json_boundary import BoundaryError
from spireagent.storage.replaceable_file import write_replaceable_json
from spireagent.workbench.instance_lock import instance_lock
from spireagent.workbench.local_training import OPERATION_FILE, LocalTrainingService
from spireagent.workbench.trusted_recipes import validate_recipe_config


def stopped(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    request = replace(request, config=validate_recipe_config(request.recipe_id, request.config),
                      limits={"wall_seconds": 300, "scratch_bytes": 16 * 1024 * 1024})
    value = service._new_operation(request, owner, "a" * 32, previous={"status": "idle"})
    value.update(status="interrupted_unknown", writer_terminal=True, elapsed_seconds=300.02,
                 error_code="private_child_timeout", run_id="b" * 64,
                 input_id="c" * 64, checkpoint_id="d" * 64,
                 child_exit={"attempt_id": value["attempt_id"], "exit_code": -9, "forced": True},
                 artifact_reserved_bytes=41)
    path, lock = service._paths(owner)
    with instance_lock(lock):
        service._prepare_child_ownership(path, value)
        (path.parent/value["scratch_name"]/"retained").write_bytes(b"audit")
        write_replaceable_json(path, value)
    return service, request, owner, store, value, path


def amend(service, value, cap=600, intent="e" * 32):
    return service.amend_wall_budget(value["operation_id"], value["attempt_id"], intent, cap)


def test_amendment_is_explicit_append_only_idempotent_and_does_not_dispatch(tmp_path, monkeypatch):
    service, request, owner, _store, value, path = stopped(tmp_path, monkeypatch)
    before = service.status()["operation"]
    assert "resume" not in before["supported_actions"]
    assert "amend_wall_budget" in before["supported_actions"]
    amended = amend(service, value)["operation"]
    assert service._thread is None
    assert amended["limits"] == request.limits
    assert amended["effective_limits"] == {**request.limits, "wall_seconds": 600}
    assert amended["budget_state"]["remaining_wall_seconds"] == pytest.approx(299.98)
    assert amended["budget_state"]["retained_scratch_bytes"] == 5
    assert "resume" in amended["supported_actions"]
    saved = json.loads(path.read_bytes())
    assert {key: saved[key] for key in value if key != "updated_at"} == {
        key: value[key] for key in value if key != "updated_at"}
    assert saved["budget_amendments"][0]["charged_elapsed_seconds"] == 300.02
    first_bytes = path.read_bytes()
    assert amend(service, value)["operation"]["budget_amendments"] == saved["budget_amendments"]
    assert path.read_bytes() == first_bytes
    reloaded = LocalTrainingService(service.config)
    assert reloaded.status()["operation"]["effective_limits"] == amended["effective_limits"]
    second = amend(reloaded, value, cap=700, intent="f" * 32)["operation"]
    assert second["budget_amendments"][0] == amended["budget_amendments"][0]
    assert second["elapsed_seconds"] == 300.02 and second["artifact_reserved_bytes"] == 41
    assert second["run_id"] == value["run_id"] and second["checkpoint_id"] == value["checkpoint_id"]
    assert path.name == OPERATION_FILE and owner.path.parent == path.parent


@pytest.mark.parametrize("cap,error", [(300, "wall_budget_increase_required"),
                                       (200, "wall_budget_increase_required"),
                                       (3601, "invalid_wall_seconds"),
                                       (True, "invalid_wall_seconds")])
def test_invalid_caps_do_not_rewrite_or_replenish(tmp_path, monkeypatch, cap, error):
    service, _request, _owner, _store, value, path = stopped(tmp_path, monkeypatch)
    prior = path.read_bytes()
    with pytest.raises(BoundaryError, match=error):
        amend(service, value, cap=cap)
    assert path.read_bytes() == prior


def test_stale_live_parent_live_child_and_intent_reuse_reject(tmp_path, monkeypatch):
    service, _request, owner, _store, value, path = stopped(tmp_path, monkeypatch)
    prior = path.read_bytes()
    with pytest.raises(BoundaryError, match="stale_operation_attempt"):
        amend(service, {**value, "attempt_id": "0" * 32})
    with instance_lock(service._paths(owner)[1]), pytest.raises(
            BoundaryError, match="writer_still_running"):
        amend(service, value)
    with instance_lock(service._child_path(path, value)), pytest.raises(
            BoundaryError, match="orphan_child_still_running"):
        amend(service, value)
    with pytest.raises(BoundaryError, match="new_amendment_intent_required"):
        amend(service, value, intent=value["intent_id"])
    assert path.read_bytes() == prior
    amend(service, value)
    prior = path.read_bytes()
    with pytest.raises(BoundaryError, match="intent_payload_mismatch"):
        amend(service, value, cap=601)
    assert path.read_bytes() == prior


@pytest.mark.parametrize("exhaustion", ["wall", "artifact", "scratch"])
def test_budget_amendment_never_resets_exhausted_charges(tmp_path, monkeypatch, exhaustion):
    service, request, _owner, _store, value, path = stopped(tmp_path, monkeypatch)
    if exhaustion == "wall":
        value["elapsed_seconds"] = 600.01
    elif exhaustion == "artifact":
        value["artifact_reserved_bytes"] = request.limits["scratch_bytes"]
    else:
        with (path.parent/value["scratch_name"]/"retained").open("wb") as handle:
            handle.truncate(request.limits["scratch_bytes"])
    write_replaceable_json(path, value)
    prior = path.read_bytes()
    with pytest.raises(BoundaryError, match="cumulative_budget_exhausted"):
        amend(service, value)
    assert path.read_bytes() == prior
    assert "resume" not in service.status()["operation"]["supported_actions"]


def test_old_operation_and_tampered_amendment_chain_fail_closed(tmp_path, monkeypatch):
    service, request, owner, _store, value, path = stopped(tmp_path, monkeypatch)
    assert service._read(path, owner.identity)["request"] == request.to_dict()
    assert "budget_amendments" not in json.loads(path.read_bytes())
    amend(service, value)
    corrupted = json.loads(path.read_bytes())
    corrupted["budget_amendments"][0]["prior_wall_seconds"] = 100
    write_replaceable_json(path, corrupted)
    assert service.status()["availability"] == "recovery_required"
    with pytest.raises(BoundaryError, match="operation_recovery_required"):
        amend(service, value, cap=700, intent="f" * 32)
