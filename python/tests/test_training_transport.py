from dataclasses import replace
from hashlib import sha256

import pytest
from training_executor_fixtures import candidate_fixture, prepared_fixture, response_fixture

from spireagent.artifact_contracts import Manifest, Parent, Payload
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.training_execution_contracts import TrainingCallHandle
from spireagent.training_transport import (
    CandidateArtifact,
    ProjectionPart,
    decode_candidate,
    decode_projection,
    decode_response,
    encode_candidate,
    encode_response,
)


def test_only_disposable_projection_roles_and_exact_original_binding():
    attempt, _, projection = prepared_fixture()
    parts = decode_projection(projection, attempt)
    assert [part.role for part in parts] == ["training_projection"]
    with pytest.raises(BoundaryError, match="authority_payload_forbidden"):
        ProjectionPart("original_journal", b"not-a-journal")
    with pytest.raises(BoundaryError, match="projection_original_identity_mismatch"):
        decode_projection(projection, replace(attempt, attempt_id="a" * 32))


def test_payload_damage_or_extra_bytes_remain_invalid():
    attempt, _, _ = prepared_fixture()
    raw = candidate_fixture(attempt)
    assert len(decode_candidate(raw, attempt)) == 1
    with pytest.raises(BoundaryError):
        decode_candidate(raw + b"unreferenced", attempt)
    with pytest.raises(BoundaryError):
        decode_candidate(raw[:20], attempt)


def test_canonical_source_or_foreign_run_cannot_be_candidate_delta():
    attempt, _, _ = prepared_fixture()
    for kind, run in (("dataset", attempt.run_id), ("checkpoint", "a" * 64)):
        manifest = Manifest(kind, attempt.producer, (Parent("run", run),))
        with pytest.raises(BoundaryError, match="canonical_or_foreign_artifact_forbidden"):
            encode_candidate(attempt, (CandidateArtifact(manifest, ()),))


def test_complete_result_is_still_candidate_and_work_bound_is_real():
    from training_executor_fixtures import app_fixture

    attempt, _, _ = prepared_fixture()
    handle = TrainingCallHandle(app_fixture(attempt), "fc-fixture")
    result = decode_response(response_fixture(attempt), attempt, handle)
    assert result.handle == handle and result.finish_reason == "slice_boundary"
    assert result.counters.value()["optimizer_updates"] == 1
    with pytest.raises(BoundaryError, match="slice_work_limit_exceeded"):
        encode_response(
            attempt,
            candidate=candidate_fixture(attempt),
            runtime=attempt.runtime,
            counters=FrozenObject.of({"optimizer_updates": 3, "boundaries": 3}),
            finish_reason="workload_complete",
        )


def test_worker_runtime_identity_cannot_be_relabelled():
    attempt, _, _ = prepared_fixture()
    with pytest.raises(BoundaryError, match="runtime_or_finish_mismatch"):
        encode_response(
            attempt,
            candidate=candidate_fixture(attempt),
            runtime=FrozenObject.of({**attempt.runtime.value(), "torch_version": "changed"}),
            counters=FrozenObject.of({"optimizer_updates": 1, "boundaries": 1}),
            finish_reason="slice_boundary",
        )


def test_nonempty_candidate_payload_roundtrips_and_corruption_is_rejected():
    attempt, _, _ = prepared_fixture()
    payload = b"synthetic-checkpoint-bytes"
    manifest = Manifest(
        "checkpoint",
        attempt.producer,
        (Parent("run", attempt.run_id), Parent("training_input", attempt.training_input_id)),
        (
            Payload(
                "checkpoint", sha256(payload).hexdigest(), len(payload), "application/octet-stream"
            ),
        ),
    )
    artifact = CandidateArtifact(manifest, (payload,))
    encoded = encode_candidate(attempt, (artifact,))
    assert decode_candidate(encoded, attempt) == (artifact,)
    with pytest.raises(BoundaryError, match="candidate_blob_digest"):
        decode_candidate(encoded[:-1] + bytes([encoded[-1] ^ 1]), attempt)
