"""Sampled replay runs outside preservation and never relabels an unavailable audit."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from spireagent.rl_exchange import Exchange
from spireagent.workbench.reference_suite import _Artifacts
from spireagent.workbench.rl_sampling_audit import SamplingAudits


@pytest.mark.parametrize(
    "codes,status",
    [
        (["global_shared_replay_unavailable"], "audit_inconclusive"),
        (["global_shared_replay_identity_unavailable"], "audit_inconclusive"),
        (["audit_counterexample"], "fail"),
        (["global_shared_replay_unavailable", "audit_counterexample"], "fail"),
        ([], "pass"),
    ],
)
def test_sampled_outcome_preserves_typed_row_and_keeps_operational_failure_separate(
    tmp_path, monkeypatch, codes, status
):
    from sts2_platform_evidence import global_agent_session_run_evidence as evidence

    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "store")}, "audit")
    run = tmp_path / "native"
    run.mkdir()
    (run / "original").write_bytes(b"original")
    artifacts = _Artifacts(tmp_path / "attempt")
    released = []
    failed = []
    monkeypatch.setattr(
        evidence,
        "audit_global_session",
        lambda *a: SimpleNamespace(
            passed=not codes,
            value=SimpleNamespace(content_id="same"),
            findings=[SimpleNamespace(code=c, detail=c) for c in codes],
        ),
    )
    audits = SamplingAudits(exchange, "actor", "budget", failed.append)
    row = {"run_id": "run", "status": "saved"}
    audits.submit(row, artifacts, run, {}, "same", lambda: released.append(True))
    audits.close()
    result = json.loads((artifacts.root / "sampling-audit-result.json").read_bytes())
    assert result["status"] == status
    assert row["status"] == "saved"
    assert bool(released) == (status == "pass")
    assert bool(failed) == (status == "fail")
    assert (run / "original").read_bytes() == b"original"
    assert exchange.event_records()[-1]["status"] == status
    assert exchange.stop_request() is None


def test_slow_replay_is_not_in_the_save_worker_critical_path(tmp_path, monkeypatch):
    from sts2_platform_evidence import global_agent_session_run_evidence as evidence

    entered = threading.Event()
    finish = threading.Event()

    def replay(*args):
        entered.set()
        assert finish.wait(3)
        return SimpleNamespace(passed=True, value=SimpleNamespace(content_id="same"), findings=[])

    monkeypatch.setattr(evidence, "audit_global_session", replay)
    exchange = Exchange({"kind": "local", "directory": str(tmp_path / "store")}, "async-audit")
    artifacts = _Artifacts(tmp_path / "attempt")
    row = {"run_id": "run", "status": "saved"}
    audits = SamplingAudits(exchange, "actor", "budget", lambda reason: None)
    try:
        audits.submit(row, artifacts, tmp_path, {}, "same", lambda: None)
        assert entered.wait(2)
        assert not audits.futures[0].done()
        assert row["status"] == "saved"
        assert (
            json.loads((artifacts.root / row["sampling_audit"]["path"]).read_bytes())["status"]
            == "pending"
        )
    finally:
        finish.set()
        audits.close()
