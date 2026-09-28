"""Explicit per-run sharing, session isolation and uncertain transport recovery."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from agent_evaluation_fixture import evidence
from test_hub_member_api import api as api
from test_hub_member_api import campaign as campaign
from test_hub_member_api import signed as signed

from spireagent.hub.live_evaluations import LiveEvaluations
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.evaluation_sharing import EvaluationSharing
from spireagent.workbench.local_models import LocalModelService


@pytest.fixture
def sharing(api, tmp_path):
    router, service, _, member, _ = api
    owner = LiveEvaluations(service, router.identity.membership)
    config = ProjectConfig(tmp_path / "workbench", "", "", None, combination())
    models = LocalModelService(config)
    _, expected = evidence(models.directory / "agent-runs")
    models.state.update(startup=expected, selection_id="s1-human-combat-v4")
    models._evaluation_handoff()
    session = {"token": "personal-synthetic", "lost": False,
               "request_entered": threading.Event(), "publish_finished": threading.Event(),
               "worker_finished": threading.Event(), "receipts": []}
    calls = []

    def request(route, *, body, token):
        calls.append((route, body, token))
        assert route == "/v1/identity/member/live-evaluations"
        assert token == "personal-synthetic"
        session["request_entered"].set()
        try:
            receipt = owner.publish(member, body)
        finally:
            session["publish_finished"].set()
        session["receipts"].append(receipt)
        if session["lost"]:
            raise BoundaryError("identity", "request_unknown")
        return receipt

    account = SimpleNamespace(device=lambda: {"device_id": "one"}, request=request)
    members = SimpleNamespace(account=account, token=lambda: session["token"])
    result = EvaluationSharing(models, members)
    real_share = result._share

    def observed_share(*args):
        try:
            real_share(*args)
        finally:
            session["worker_finished"].set()

    result._share = observed_share
    return result, models.evaluations()[0]["evaluation_id"], session, calls, service


def finish(sharer):
    assert sharer.thread is not None
    sharer.thread.join(timeout=5)
    assert not sharer.thread.is_alive()
    return sharer.status()


def finish_after_real_publish(sharer, session):
    """Observe real verifier/store phases within one bounded attempt."""
    deadline = time.monotonic() + 30
    assert session["request_entered"].wait(timeout=30), "share never reached Hub publish"
    remaining = max(0, deadline - time.monotonic())
    assert session["publish_finished"].wait(timeout=remaining), "Hub publish did not finish"
    remaining = max(0, deadline - time.monotonic())
    assert session["worker_finished"].wait(timeout=remaining), "share worker did not finish"
    assert sharer.thread is not None
    sharer.thread.join(timeout=1)
    assert not sharer.thread.is_alive()
    return sharer.status()


def test_share_is_explicit_and_receipt_is_durable_and_idempotent(sharing):
    sharer, identity, _, calls, _ = sharing
    assert sharer.status() == {"status": "idle"} and calls == []
    with pytest.raises(BoundaryError, match="explicit_evaluation_sharing_required"):
        sharer.share(identity, False)
    sharer.share(identity, True)
    receipt = finish(sharer)["receipt"]
    assert len(list((sharer.models.directory / "evaluation-shares").glob("*/*.json"))) == 1
    sharer.share(identity, True)
    assert finish(sharer)["receipt"] == receipt
    assert len(calls) == 2


def test_lost_response_is_unknown_no_auto_retry_manual_retry_same_manifest(sharing):
    sharer, identity, session, calls, service = sharing
    try:
        session["lost"] = True
        sharer.share(identity, True)
        assert finish_after_real_publish(sharer, session)["status"] == "unknown"
        assert len(calls) == 1
        with service.operations.transaction() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM events WHERE operation='live_evaluation_shared'"
            ).fetchone()[0] == 1
        session["request_entered"].clear()
        session["publish_finished"].clear()
        session["worker_finished"].clear()
        session["lost"] = False
        sharer.share(identity, True)
        assert finish_after_real_publish(sharer, session)["status"] == "shared"
        assert len(calls) == 2, "the second upload requires an explicit manual share"
        assert session["receipts"][0]["artifact_id"] == session["receipts"][1]["artifact_id"]
        with service.operations.transaction() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM events WHERE operation='live_evaluation_shared'"
            ).fetchone()[0] == 1
    finally:
        sharer.close()
        if sharer.thread is not None:
            sharer.thread.join(timeout=5)


def test_real_hub_publish_must_return_before_share_can_finish(sharing, monkeypatch):
    sharer, identity, session, calls, service = sharing
    real_publish = LiveEvaluations.publish
    committed, release = threading.Event(), threading.Event()

    def held_return(owner, principal, body):
        receipt = real_publish(owner, principal, body)
        committed.set()
        assert release.wait(timeout=10)
        return receipt

    monkeypatch.setattr(LiveEvaluations, "publish", held_return)
    try:
        sharer.share(identity, True)
        assert committed.wait(timeout=30), "real Hub verification/publication did not finish"
        assert session["request_entered"].is_set()
        assert not session["publish_finished"].is_set()
        assert not session["worker_finished"].is_set()
        assert sharer.status()["status"] == "uploading"
        assert len(calls) == 1
        release.set()
        assert finish_after_real_publish(sharer, session)["status"] == "shared"
        with service.operations.transaction() as db:
            assert db.execute(
                "SELECT COUNT(*) FROM events WHERE operation='live_evaluation_shared'"
            ).fetchone()[0] == 1
    finally:
        release.set()
        sharer.close()
        if sharer.thread is not None:
            sharer.thread.join(timeout=5)


def test_account_switch_before_post_cancels_and_hides_previous_operation(sharing, monkeypatch):
    import spireagent.workbench.evaluation_sharing as module

    sharer, identity, session, calls, _ = sharing
    encode = module.encoded_evidence

    def switched(*args):
        result = encode(*args)
        session["token"] = "different-person"
        return result

    monkeypatch.setattr(module, "encoded_evidence", switched)
    sharer.share(identity, True)
    assert finish(sharer) == {"status": "idle"}
    assert calls == []
    session["token"] = "personal-synthetic"
    assert sharer.status()["error"] == "evaluation_share_session_changed"


def test_duplicate_submit_while_busy_does_not_start_second_upload(sharing, monkeypatch):
    import spireagent.workbench.evaluation_sharing as module

    sharer, identity, _, calls, _ = sharing
    entered, release = threading.Event(), threading.Event()
    encode = module.encoded_evidence

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=3)
        return encode(*args)

    monkeypatch.setattr(module, "encoded_evidence", blocked)
    sharer.share(identity, True)
    assert entered.wait(timeout=2)
    try:
        with pytest.raises(BoundaryError, match="evaluation_share_in_progress"):
            sharer.share(identity, True)
        sharer.close()
    finally:
        release.set()
    assert finish(sharer)["error"] == "evaluation_share_session_changed"
    assert calls == []


def test_local_evaluation_cannot_substitute_different_run_bytes(sharing):
    sharer, identity, _, calls, _ = sharing
    manifest = next((sharer.models.directory / "agent-runs").glob("*/manifest.json"))
    raw = manifest.read_text()
    manifest.write_text(raw.replace('"b' + "b" * 63 + '"', '"a' + "a" * 63 + '"'))
    sharer.share(identity, True)
    assert finish(sharer)["error"] == "local_evaluation_identity_drift"
    assert calls == []
