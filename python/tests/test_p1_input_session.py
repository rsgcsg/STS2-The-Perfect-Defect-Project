"""Normal ComputeRequest producer/worker/consumer reuse only an explicit live session."""
from __future__ import annotations

from dataclasses import replace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at

from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.read_session import VerifiedReadSession
from stpd.cloud_jobs.__main__ import execute_request
from stpd.cloud_jobs.contracts import ComputeReceipt, ComputeRequest
from stpd.cloud_jobs.execution import validate_receipt
from stpd.p1.dataset import SnapshotReader
from stpd.p1.execution_plans import training_execution
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.spec import MStdConfig
from stpd.p1.tensor_pack import publish_tensor_pack
from stpd.p1.vocabulary import public_vocabulary
from stpd.p1.workload import prepare_p1_run, verify_p1_run


def fixture(tmp_path):
    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, f"shared-{i}")[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    pack, _ = publish_tensor_pack(store, SnapshotReader(store, snapshot.artifact_id),
                                  public_vocabulary(), PRODUCER)
    return store, snapshot, pack


def prepare(store, snapshot, pack, operation, *, policy=None):
    return prepare_p1_run(store, snapshot.artifact_id, PRODUCER, MStdConfig(max_epochs=3),
                         operation, input_projection=V2_INPUT_SPEC,
                         tensor_pack_id=pack.artifact_id, execution_policy=policy)


def test_two_normal_requests_reuse_verified_inputs_but_bind_each_run(tmp_path, monkeypatch):
    import stpd.p1.tensor_pack as packs

    store, snapshot, pack = fixture(tmp_path)
    original_load = packs.load_tensor_pack
    calls = []

    def observed(*args, **kwargs):
        calls.append(args[1])
        return original_load(*args, **kwargs)

    monkeypatch.setattr(packs, "load_tensor_pack", observed)
    monkeypatch.setattr("stpd.cloud_jobs.__main__.source_identity", lambda _: PRODUCER)
    with VerifiedReadSession(store) as session:
        first = prepare(session, snapshot, pack, "b" * 32)
        second = prepare(session, snapshot, pack, "c" * 32,
                          policy=training_execution("mstd-training-v2").to_dict())
        assert len(calls) == 1
        for run in [first, second]:
            request = ComputeRequest("training", run.artifact_id, PRODUCER,
                                     run.artifact_id[:16], stop_after=1)
            result = ComputeReceipt.decode(execute_request(
                request.to_dict(), input_session=session))
            assert result.state == "paused"
            validate_receipt(session, request, result)
            assert not session.closed
        assert len(calls) == 1
    assert session.closed
    # A new session repeats full population verification; the earlier context is
    # not a persisted trust token and cannot be supplied once closed.
    with VerifiedReadSession(store) as fresh:
        verify_p1_run(fresh, first.artifact_id, PRODUCER, prepare_populations=True)
    assert len(calls) == 2
    with pytest.raises(StoreError, match="read_session_closed"):
        execute_request(request.to_dict(), input_session=session)


def test_shared_session_does_not_bypass_run_or_pack_binding(tmp_path, monkeypatch):
    store, snapshot, pack = fixture(tmp_path)
    monkeypatch.setattr("stpd.cloud_jobs.__main__.source_identity", lambda _: PRODUCER)
    with VerifiedReadSession(store) as session:
        run = prepare(session, snapshot, pack, "d" * 32)
        valid = ComputeRequest("training", run.artifact_id, PRODUCER, "valid", stop_after=1)
        revision = ("0" if PRODUCER.source_revision[0] != "0" else "1")
        revision += PRODUCER.source_revision[1:]
        wrong = replace(valid, producer=replace(PRODUCER, source_revision=revision))
        with pytest.raises(BoundaryError, match="runtime_source_lock_mismatch"):
            execute_request(wrong.to_dict(), input_session=session)
        with pytest.raises(BoundaryError, match="explicit_training_input_session_required"):
            execute_request(valid.to_dict(), input_session=session, input_cache=tmp_path / "cache")
        # A different vocabulary cannot hit the cached population entry, even
        # though the pack artifact ID is unchanged.
        from stpd.p1.workload import _packed_populations

        vocabulary = {**public_vocabulary(), "fixture_binding_change": True}
        with pytest.raises(BoundaryError):
            _packed_populations(session, pack.artifact_id,
                                 SnapshotReader(session, snapshot.artifact_id),
                                 vocabulary, None, False)
