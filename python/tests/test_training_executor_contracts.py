from dataclasses import FrozenInstanceError, replace

import pytest
from training_executor_fixtures import app_fixture, prepared_fixture

from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.training_execution_contracts import (
    PreparedTrainingAttempt,
    StopInspection,
    TrainingProviderBudget,
    training_execution_binding,
)


def test_prepared_identity_roundtrip_and_request_change():
    attempt, request, projection = prepared_fixture()
    assert PreparedTrainingAttempt.from_bytes(attempt.to_bytes()) == attempt
    attempt.bind_payloads(request, projection)
    with pytest.raises(BoundaryError, match="prepared_payload_mismatch"):
        attempt.bind_payloads(request + b"x", projection)
    with pytest.raises(FrozenInstanceError):
        attempt.run_id = "a" * 64
    assert replace(attempt, request_sha256="a" * 64).identity != attempt.identity
    assert (
        replace(attempt, request_sha256="a" * 64).execution_binding_id
        == attempt.execution_binding_id
    )


def test_attempt_device_runtime_and_secret_metadata_reject():
    attempt, _, _ = prepared_fixture()
    with pytest.raises(BoundaryError, match="runtime_device"):
        replace(attempt, runtime=FrozenObject.of({**attempt.runtime.value(), "device": "cuda:0"}))
    with pytest.raises(BoundaryError, match="secret_metadata"):
        replace(
            attempt, runtime=FrozenObject.of({**attempt.runtime.value(), "api_key": "not-a-secret"})
        )


def test_stop_acknowledgement_or_incomplete_counts_cannot_confirm():
    attempt, _, _ = prepared_fixture()
    ref = app_fixture(attempt)
    assert not StopInspection(ref, "unknown", 0, 0).confirmed
    assert not StopInspection(ref, "stopped", 0, None).confirmed
    assert not StopInspection(ref, "stopped", 0, 1).confirmed
    proof = StopInspection(ref, "stopped", 0, 0)
    assert proof.confirmed
    assert StopInspection.from_dict(proof.to_dict()) == proof


def test_foreign_app_ref_rejected_before_effect():
    attempt, _, _ = prepared_fixture()
    with pytest.raises(BoundaryError, match="app_ref_binding_mismatch"):
        replace(app_fixture(attempt), image_object_id="im-foreign").bind(attempt)


@pytest.mark.parametrize(
    "capability", ["public-m2-slice-v1", "light-action-m0-slice-v1", "arbitrary.module"]
)
def test_unimplemented_capability_cannot_enter_prepared_or_target_binding(capability):
    attempt, _, _ = prepared_fixture()
    with pytest.raises(BoundaryError, match="unsupported_worker_capability"):
        replace(attempt, worker_capability=capability)
    with pytest.raises(BoundaryError, match="unsupported_worker_capability"):
        training_execution_binding(
            attempt.producer,
            attempt.code_closure_sha256,
            capability,
            attempt.runtime,
            attempt.placement,
            attempt.limits,
        )


def test_dynamic_budget_and_submission_reserve_are_not_a_fresh_timer():
    remaining = [60.0]
    budget = TrainingProviderBudget(lambda: remaining[0])
    reserved = budget.reserving(50)
    assert reserved.timeout(30) == 10
    remaining[0] = 53
    assert reserved.timeout(30) == 3
    remaining[0] = 50
    with pytest.raises(BoundaryError, match="attempt_deadline"):
        reserved.timeout(30)


@pytest.mark.parametrize("remaining", [True, -1, float("inf"), float("nan")])
def test_invalid_owner_budget_cannot_enter_provider_timeouts(remaining):
    with pytest.raises(BoundaryError, match="invalid_owner_remaining_budget"):
        TrainingProviderBudget(lambda: remaining).timeout(30)
