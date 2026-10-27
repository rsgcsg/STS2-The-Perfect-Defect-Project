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
