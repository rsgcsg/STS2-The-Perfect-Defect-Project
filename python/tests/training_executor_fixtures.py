"""Synthetic contract bytes, no NN, real data or cloud SDK call."""

from hashlib import sha256

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import FrozenObject, json_bytes
from spireagent.training_execution_contracts import (
    PreparedTrainingAttempt,
    TrainingAppRef,
    TrainingPlacement,
    TrainingSliceLimits,
)
from spireagent.training_transport import (
    CandidateArtifact,
    ProjectionPart,
    encode_candidate,
    encode_projection,
    encode_response,
)


def prepared_fixture():
    identity = {
        "run_id": "3" * 64,
        "training_input_id": "4" * 64,
        "operation_id": "5" * 32,
        "attempt_id": "6" * 32,
    }
    request = json_bytes({"schema": "private/synthetic-domain-request", "updates": 1})
    projection = encode_projection(
        identity, (ProjectionPart("training_projection", b"synthetic-TRAIN-projection"),), 8192
    )
    runtime = FrozenObject.of(
        {
            "device": "cpu",
            "dtype": "float32",
            "torch_version": "synthetic",
            "python_version": "3.11",
        }
    )
    attempt = PreparedTrainingAttempt(
        **identity,
        request_sha256=sha256(request).hexdigest(),
        projection_sha256=sha256(projection).hexdigest(),
        producer=Producer("rsgcsg/synthetic-worker", "1" * 40, "2" * 64),
        code_closure_sha256="8" * 64,
        worker_capability="structured-m2-slice-v1",
        runtime=runtime,
        placement=TrainingPlacement("modal", "ac-fixture", "fixture", "im-fixture", "none", "cpu"),
        limits=TrainingSliceLimits(2, 4, 30, 20, 4096, 8192, 16384, 8, 1000, 2048, "9" * 64, 0),
    )
    return attempt, request, projection


def app_fixture(attempt):
    return TrainingAppRef(
        attempt.identity,
        "ap-fixture",
        "fu-fixture",
        attempt.app_name,
        attempt.placement.account_id,
        attempt.placement.environment,
        attempt.placement.image_object_id,
    )


def candidate_fixture(attempt):
    event = Manifest(
        "run_event",
        attempt.producer,
        (Parent("run", attempt.run_id),),
        parameters=FrozenObject.of({"schema": "stpd/run-event-v1", "kind": "checkpoint"}),
    )
    return encode_candidate(attempt, (CandidateArtifact(event, ()),))


def response_fixture(attempt):
    return encode_response(
        attempt,
        candidate=candidate_fixture(attempt),
        runtime=attempt.runtime,
        counters=FrozenObject.of({"optimizer_updates": 1, "boundaries": 1}),
        finish_reason="slice_boundary",
    )
