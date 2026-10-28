"""Preservation intent cannot promote diagnostic/trajectory runs to scores."""
import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.managed_retention import (
    pipeline_configuration,
    preservation_route,
    retention_configuration,
)


def row(status="finished", result="loss"):
    return {"seed": "fresh", "selection_id": "m", "manifest_sha256": "a" * 64,
            "execution_status": status, "cleanup_accepted": True,
            "agent_task_outcome": {"terminal_observation": {"result": result}}}


@pytest.mark.parametrize("tier", ["formal", "diagnostic", "trajectory"])
def test_unknown_and_unclosed_promote_but_never_become_score_eligible(tier):
    retention = retention_configuration({"retention": {"tier": tier, "audit_every": 100}})
    assert "full_peer" in preservation_route(retention, row("unknown"))
    unclosed = row()
    unclosed["cleanup_accepted"] = False
    assert "full_peer" in preservation_route(retention, unclosed)
    assert retention["score_eligible"] is (tier == "formal")


def test_diagnostic_loss_full_win_local_and_audit_is_reproducible():
    retention = retention_configuration({"retention": {"tier": "diagnostic", "audit_every": 100}})
    assert preservation_route(retention, row()) == "full_peer"
    assert preservation_route(retention, row(result="win")) == "full_local"
    retention = retention_configuration({"retention": {"tier": "trajectory", "audit_every": 1}})
    assert preservation_route(retention, row()) == "trajectory_and_full_peer"
    retention["audit_every"] = 100
    assert preservation_route(retention, row()) == preservation_route(retention, row())


@pytest.mark.parametrize("value", [None, {}, {"tier": "other", "audit_every": 100},
    {"tier": "formal", "audit_every": True}, {"tier": "trajectory", "audit_every": 0}])
def test_invalid_intent_rejected(value):
    with pytest.raises(BoundaryError, match="invalid_retention"):
        retention_configuration({"retention": value})


@pytest.mark.parametrize("value", [None, {}, {"save_workers": 2, "pending_limit": 1},
    {"save_workers": True, "pending_limit": 4}, {"save_workers": 5, "pending_limit": 8}])
def test_invalid_pipeline_rejected(value):
    with pytest.raises(BoundaryError, match="invalid_pipeline"):
        pipeline_configuration({"pipeline": value})


@pytest.mark.parametrize("count, expected", [(1, 1), (8, 1), (50, 1), (51, 2), (1000, 20)])
def test_evaluation_audits_freeze_exact_minimum_before_outcomes(count, expected):
    from spireagent.workbench.managed_retention import select_formal_audits

    slots = [{"slot_id": str(i), "status": "not_started"} for i in range(count)]
    plan = select_formal_audits(slots, salt="frozen-config-and-progress", audit_every=50)
    assert plan["count"] == expected and len(set(plan["selected_slot_ids"])) == expected
    for slot in slots:
        slot["status"] = "unknown"
    assert select_formal_audits(list(reversed(slots)), salt=plan["salt"], audit_every=50) == plan


def test_trajectory_evaluation_intent_is_explicit_and_legacy_stays_unscored():
    legacy = retention_configuration({"retention": {"tier": "trajectory", "audit_every": 50}})
    assert legacy["score_eligible"] is False
    current = retention_configuration({"retention": {
        "tier": "trajectory", "audit_every": 50, "purpose": "evaluation"}})
    assert current["score_eligible"] is True and current["schema"].endswith("v2")
    value = row()
    value["formal_audit_selected"] = True
    assert preservation_route(current, value) == "trajectory_and_full_peer"
    value["formal_audit_selected"] = False
    assert preservation_route(current, value) == "trajectory"
    value["execution_status"] = "unknown"
    assert preservation_route(current, value) == "trajectory_and_full_peer"
