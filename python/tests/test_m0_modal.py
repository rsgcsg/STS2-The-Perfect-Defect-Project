from __future__ import annotations

import hashlib
import runpy
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.cloud_jobs import m0_modal
from stpd.workers import token_ranking
from stpd.workers.token_ranking import LightActionM0Config

PRODUCER = Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "a" * 40, "b" * 64)
CONFIG = LightActionM0Config(device="cuda")
OBSERVED = {
    "torch_version": "2.7.1+cu128",
    "python_version": "3.11.9",
    "cuda_version": "12.8",
    "cuda_available": True,
    "gpu_name": "NVIDIA A10",
    "cpu_threads": 4,
}


@dataclass(frozen=True)
class FakeRequest:
    raw: bytes
    producer: Producer = PRODUCER
    attempt_id: str = "c" * 32
    run_id: str = "d" * 64
    input_id: str = "e" * 64
    operation_id: str = "f" * 32
    target_device: str = "cuda"
    target_step: int = 17
    resume_checkpoint_id: str | None = None
    config: object = CONFIG
    torch_version: str = OBSERVED["torch_version"]
    cpu_threads: int = OBSERVED["cpu_threads"]

    @property
    def training_binding(self) -> FrozenObject:
        return FrozenObject.of({"training": "binding"})

    @property
    def run_manifest(self) -> SimpleNamespace:
        return SimpleNamespace(
            parameters=FrozenObject.of(
                {
                    "torch_version": self.torch_version,
                    "cpu_threads": self.cpu_threads,
                }
            )
        )

    @property
    def backbone_identity(self) -> FrozenObject:
        return FrozenObject.of({"backbone": "identity"})

    @property
    def resume_checkpoint(self) -> bytes | None:
        return b"resume-checkpoint" if self.resume_checkpoint_id is not None else None

    @property
    def request_sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    def to_bytes(self) -> bytes:
        return self.raw


@dataclass(frozen=True)
class FakeResult:
    request_sha256: str
    attempt_id: str
    run_id: str
    input_id: str
    operation_id: str
    producer: Producer
    target_device: str
    target_step: int
    resume_checkpoint_id: str | None
    training_binding: FrozenObject
    resume_checkpoint_sha256: str | None
    backbone_identity: FrozenObject
    config: object = CONFIG


def _request(raw: bytes = b"request-bytes", **changes: object) -> FakeRequest:
    return FakeRequest(raw=raw, **changes)


def _result(request: FakeRequest, **changes: object) -> FakeResult:
    values = {
        "request_sha256": request.request_sha256,
        "attempt_id": request.attempt_id,
        "run_id": request.run_id,
        "input_id": request.input_id,
        "operation_id": request.operation_id,
        "producer": request.producer,
        "target_device": request.target_device,
        "target_step": request.target_step,
        "resume_checkpoint_id": request.resume_checkpoint_id,
        "training_binding": request.training_binding,
        "resume_checkpoint_sha256": (
            hashlib.sha256(request.resume_checkpoint).hexdigest()
            if request.resume_checkpoint is not None
            else None
        ),
        "backbone_identity": request.backbone_identity,
        "config": request.config,
    }
    values.update(changes)
    return FakeResult(**values)


def _runtime_observation(**changes: object) -> SimpleNamespace:
    values = dict(OBSERVED)
    values.update(changes)
    return SimpleNamespace(**values)


def _frame(request: FakeRequest, result_bytes: bytes, target: m0_modal.ModalM0Target) -> bytes:
    return m0_modal._encode_worker_response(
        request,
        result_bytes,
        expected_image_object_id=target.image_object_id,
        runtime=_runtime_observation(),
    )


def _target(**changes: object) -> m0_modal.ModalM0Target:
    values = {
        "account_id": "workspace-01",
        "environment_name": "staging",
        "app_id": "ap-app01",
        "app_version": 3,
        "function_id": "fu-function01",
        "image_object_id": "im-image01",
        "producer": PRODUCER,
    }
    values.update(changes)
    return m0_modal.ModalM0Target(**values)


def _target_runtime(
    target: m0_modal.ModalM0Target,
    **changes: object,
) -> m0_modal.ModalM0TargetRuntimeMetadata:
    values: dict[str, object] = {
        "target_id": target.target_id,
        "producer": target.producer.to_dict(),
        "image_object_id": target.image_object_id,
        "torch_version": OBSERVED["torch_version"],
        "cpu_threads": OBSERVED["cpu_threads"],
    }
    values.update(changes)
    return m0_modal.ModalM0TargetRuntimeMetadata.from_provider_metadata(
        values,
        target=target,
    )


def _provider(
    target: m0_modal.ModalM0Target | None = None,
    *,
    sdk: object = None,
) -> m0_modal.ModalM0Provider:
    target = target or _target()
    return m0_modal.ModalM0Provider(target, _target_runtime(target), sdk=sdk)


class FakeCall:
    def __init__(
        self,
        call_id: str,
        *,
        result: object = b"result-bytes",
        app_id: str = "ap-app01",
        function_id: str = "fu-function01",
        input_count: int = 1,
    ) -> None:
        self.object_id = call_id
        self._app_id = app_id
        self._function_id = function_id
        self._input_count = input_count
        self.result = result
        self.get_calls: list[tuple[float, int]] = []
        self.client: object | None = None

    def num_inputs(self) -> int:
        return self._input_count

    def get(self, *, timeout: float | None = None, index: int = 0) -> object:
        self.get_calls.append((timeout, index))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeFunction:
    def __init__(
        self,
        *,
        function_id: str = "fu-function01",
        app_id: str = "ap-app01",
        image_id: str = "im-image01",
        call_id: str = "fc-call01",
        spawn_error: Exception | None = None,
    ) -> None:
        self.object_id = function_id
        self._app_id = app_id
        self._metadata = SimpleNamespace(function_name="token_remote_update", image_id=image_id)
        self.client = object()
        self.call_id = call_id
        self.spawn_error = spawn_error
        self.spawn_calls: list[bytes] = []

    def hydrate(self) -> FakeFunction:
        return self

    def spawn(self, request_bytes: bytes) -> SimpleNamespace:
        self.spawn_calls.append(request_bytes)
        if self.spawn_error:
            raise self.spawn_error
        return SimpleNamespace(object_id=self.call_id)


class FakeSDK:
    def __init__(self, function: FakeFunction, call: FakeCall | None = None) -> None:
        self.function = function
        self.call = call
        self.lookups: list[tuple[str, str, int, str]] = []
        self.call_lookups: list[tuple[str, object]] = []

        class FunctionAPI:
            @staticmethod
            def from_name(
                app_name: str, function_name: str, *, version: int, environment_name: str
            ) -> FakeFunction:
                self.lookups.append((app_name, function_name, version, environment_name))
                return self.function

        class FunctionCallAPI:
            @staticmethod
            def from_id(call_id: str, *, client: object) -> FakeCall:
                self.call_lookups.append((call_id, client))
                assert self.call is not None
                return self.call

        self.Function = FunctionAPI
        self.FunctionCall = FunctionCallAPI


@pytest.fixture
def parse_request(monkeypatch: pytest.MonkeyPatch):
    requests: dict[bytes, FakeRequest] = {}

    def decode(raw: bytes) -> FakeRequest:
        return requests[raw]

    monkeypatch.setattr(m0_modal, "_decode_request", decode)
    return requests


@pytest.fixture
def parse_result(monkeypatch: pytest.MonkeyPatch):
    results: dict[bytes, FakeResult] = {}

    def decode(raw: bytes) -> FakeResult:
        return results[raw]

    monkeypatch.setattr(m0_modal, "_decode_result", decode)
    return results


def test_target_and_call_handle_round_trip_all_provider_and_source_identity(
    parse_request: dict[bytes, FakeRequest],
):
    target = _target()
    raw = b"request"
    request = _request(raw, resume_checkpoint_id="1" * 64)
    parse_request[raw] = request
    sdk = FakeSDK(FakeFunction())
    call = _provider(target, sdk=sdk).submit(raw)

    restored = m0_modal.ModalM0Call.from_bytes(call.to_bytes())
    assert restored == call
    assert restored.target.account_id == "workspace-01"
    assert restored.target.environment_name == "staging"
    assert restored.target.app_id == "ap-app01"
    assert restored.target.app_version == 3
    assert restored.target.function_id == "fu-function01"
    assert restored.target.image_object_id == "im-image01"
    assert restored.producer.uv_lock_sha256 == "b" * 64
    assert restored.request_sha256 == request.request_sha256
    assert restored.resume_checkpoint_id == "1" * 64
    assert sdk.lookups == [
        (
            m0_modal.M0_MODAL_APP_NAME,
            m0_modal.M0_MODAL_FUNCTION_NAME,
            3,
            "staging",
        )
    ]
    assert sdk.function.spawn_calls == [raw]

    different_account = m0_modal.ModalM0Provider(
        _target(account_id="workspace-02"),
        _target_runtime(_target(account_id="workspace-02")),
        sdk=FakeSDK(FakeFunction()),
    )
    with pytest.raises(BoundaryError, match="foreign_target"):
        different_account.restore_handle(call.to_bytes())


def test_fake_target_metadata_binds_runtime_to_account_image_and_source_lock(
    monkeypatch: pytest.MonkeyPatch,
):
    target = _target()
    metadata = _target_runtime(target)

    with pytest.raises(BoundaryError, match="target_source_binding_mismatch"):
        m0_modal.ModalM0TargetRuntimeMetadata.from_provider_metadata(
            {
                **metadata.to_dict(),
                "producer": Producer("other/repository", "9" * 40, "8" * 64).to_dict(),
            },
            target=target,
        )
    with pytest.raises(BoundaryError, match="target_source_binding_mismatch"):
        m0_modal.ModalM0TargetRuntimeMetadata.from_provider_metadata(
            {**metadata.to_dict(), "image_object_id": "im-other-image"},
            target=target,
        )

    class FakeTargetRuntime:
        def __init__(self, *, torch_version: str, cpu_threads: int) -> None:
            self.torch_version = torch_version
            self.cpu_threads = cpu_threads

    monkeypatch.setattr(token_ranking, "TokenTargetRuntime", FakeTargetRuntime, raising=False)
    runtime = metadata.token_target_runtime(target)
    assert runtime.torch_version == OBSERVED["torch_version"]
    assert runtime.cpu_threads == OBSERVED["cpu_threads"]


def test_submit_unknown_is_reported_once_without_internal_retry(
    parse_request: dict[bytes, FakeRequest],
):
    raw = b"ambiguous"
    parse_request[raw] = _request(raw)
    function = FakeFunction(spawn_error=RuntimeError("do not expose provider details"))
    provider = _provider(sdk=FakeSDK(function))

    with pytest.raises(BoundaryError, match="submission_unknown") as error:
        provider.submit(raw)

    assert len(function.spawn_calls) == 1
    assert "provider details" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"app_id": "ap-foreign"},
        {"function_id": "fu-foreign"},
        {"image_id": "im-foreign"},
    ],
)
def test_wrong_app_function_or_image_fails_before_submission(
    parse_request: dict[bytes, FakeRequest],
    change: dict[str, str],
):
    raw = b"request"
    parse_request[raw] = _request(raw)
    function = FakeFunction(**change)
    provider = _provider(sdk=FakeSDK(function))

    with pytest.raises(BoundaryError, match="deployed_target_identity_mismatch"):
        provider.submit(raw)
    assert function.spawn_calls == []


@pytest.mark.parametrize(
    "change",
    [{"producer": Producer("other/repository", "9" * 40, "8" * 64)}, {"target_device": "cpu"}],
)
def test_request_source_or_device_mismatch_fails_before_sdk_lookup(
    parse_request: dict[bytes, FakeRequest],
    change: dict[str, object],
):
    raw = b"request"
    parse_request[raw] = _request(raw, **change)
    sdk = FakeSDK(FakeFunction())
    provider = _provider(sdk=sdk)

    with pytest.raises(BoundaryError, match="request_source_or_cuda_mismatch"):
        provider.submit(raw)
    assert sdk.lookups == []


@pytest.mark.parametrize(
    "runtime_change",
    [{"torch_version": "2.8.0+cu129"}, {"cpu_threads": 8}],
)
def test_request_runtime_must_match_provider_target_metadata_before_sdk_lookup(
    parse_request: dict[bytes, FakeRequest],
    runtime_change: dict[str, object],
):
    raw = b"request"
    parse_request[raw] = _request(raw, **runtime_change)
    sdk = FakeSDK(FakeFunction())
    provider = _provider(sdk=sdk)

    with pytest.raises(BoundaryError, match="request_target_runtime_mismatch"):
        provider.submit(raw)
    assert sdk.lookups == []


def test_poll_timeout_retains_handle_and_uses_exact_saved_call(
    parse_request: dict[bytes, FakeRequest],
):
    raw = b"request"
    parse_request[raw] = _request(raw)
    function = FakeFunction()
    call = FakeCall("fc-call01", result=TimeoutError())
    sdk = FakeSDK(function, call)
    provider = _provider(sdk=sdk)
    handle = provider.submit(raw)

    assert provider.poll(handle, timeout_seconds=0.25) is None
    assert call.get_calls == [(0.25, 0)]
    assert sdk.call_lookups == [(handle.call_id, function.client)]


def test_poll_accepts_bytes_only_after_call_and_result_identity_checks(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
):
    raw = b"request"
    result_bytes = b"verified-result-bytes"
    request = _request(raw)
    parse_request[raw] = request
    parse_result[result_bytes] = _result(request)
    function = FakeFunction()
    call = FakeCall("fc-call01")
    provider = _provider(sdk=FakeSDK(function, call))
    handle = provider.submit(raw)
    target = _target()
    call.result = _frame(request, result_bytes, target)

    assert provider.poll(handle) == result_bytes
    evidence = provider.get_runtime_evidence(handle)
    assert evidence.call_id == handle.call_id
    assert evidence.target_id == handle.target_id
    assert evidence.image_object_id == target.image_object_id
    assert evidence.producer == target.producer
    assert evidence.torch_version == OBSERVED["torch_version"]
    assert evidence.python_version == OBSERVED["python_version"]
    assert evidence.cuda_version == OBSERVED["cuda_version"]
    assert evidence.cuda_available is True
    assert evidence.gpu_name == "NVIDIA A10"
    assert evidence.cpu_threads == OBSERVED["cpu_threads"]
    assert m0_modal.ModalM0RuntimeEvidence.from_bytes(evidence.to_bytes()) == evidence


@pytest.mark.parametrize(
    "runtime_change",
    [{"torch_version": "2.8.0+cu129"}, {"cpu_threads": 8}],
)
def test_poll_rejects_worker_runtime_different_from_saved_target_metadata(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
    runtime_change: dict[str, object],
):
    raw = b"request"
    result_bytes = b"verified-result-bytes"
    request = _request(raw)
    parse_request[raw] = request
    parse_result[result_bytes] = _result(request)
    target = _target()
    call = FakeCall("fc-call01")
    provider = _provider(target, sdk=FakeSDK(FakeFunction(), call))
    handle = provider.submit(raw)
    call.result = m0_modal._encode_worker_response(
        request,
        result_bytes,
        expected_image_object_id=target.image_object_id,
        runtime=_runtime_observation(**runtime_change),
    )

    with pytest.raises(BoundaryError, match="saved_handle_identity_mismatch"):
        provider.poll(handle)


@pytest.mark.parametrize(
    "result_change",
    [
        {"attempt_id": "0" * 32},
        {"run_id": "0" * 64},
        {"producer": Producer("other/repository", "9" * 40, "8" * 64)},
        {"target_step": 18},
        {"training_binding": FrozenObject.of({"training": "foreign"})},
        {"backbone_identity": FrozenObject.of({"backbone": "foreign"})},
        {"config": LightActionM0Config(device="cuda", steps=11)},
    ],
)
def test_poll_rejects_result_not_bound_to_saved_request_handle(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
    result_change: dict[str, object],
):
    raw = b"request"
    result_bytes = b"forged-result"
    request = _request(raw)
    parse_request[raw] = request
    parse_result[result_bytes] = _result(request, **result_change)
    call = FakeCall("fc-call01")
    target = _target()
    provider = _provider(target, sdk=FakeSDK(FakeFunction(), call))
    handle = provider.submit(raw)
    call.result = _frame(request, result_bytes, target)

    with pytest.raises(BoundaryError, match="result_handle_identity_mismatch"):
        provider.poll(handle)


@pytest.mark.parametrize(
    "call_change",
    [{"app_id": "ap-foreign"}, {"function_id": "fu-foreign"}, {"input_count": 2}],
)
def test_poll_rejects_call_identity_mismatch_before_reading_result(
    parse_request: dict[bytes, FakeRequest],
    call_change: dict[str, object],
):
    raw = b"request"
    parse_request[raw] = _request(raw)
    call = FakeCall("fc-call01", **call_change)
    provider = _provider(sdk=FakeSDK(FakeFunction(), call))
    handle = provider.submit(raw)

    with pytest.raises(BoundaryError, match="saved_call_identity_mismatch"):
        provider.poll(handle)
    assert call.get_calls == []


def test_modal_object_storage_payloads_over_two_mib_are_passed_with_explicit_app_bounds(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
):
    raw = b"r" * (3 * 1024 * 1024)
    result_bytes = b"z" * (70 * 1024 * 1024)
    request = _request(raw)
    parse_request[raw] = request
    parse_result[result_bytes] = _result(request)
    function = FakeFunction()
    call = FakeCall("fc-call01")
    provider = _provider(sdk=FakeSDK(function, call))

    handle = provider.submit(raw)
    call.result = _frame(request, result_bytes, provider.target)
    assert function.spawn_calls == [raw]
    assert handle.request_size_bytes == len(raw)
    assert provider.poll(handle) == result_bytes
    assert provider.get_runtime_evidence(handle).result_size_bytes == len(result_bytes)
    assert m0_modal.MAX_M0_REQUEST_BYTES == 64 * 1024 * 1024
    assert m0_modal.MAX_M0_RESULT_BYTES == 128 * 1024 * 1024


def test_serialized_request_and_result_bounds_fail_closed_without_retry(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
):
    target = _target()
    function = FakeFunction()
    provider = _provider(target, sdk=FakeSDK(function))
    with pytest.raises(BoundaryError, match="request_size_limit"):
        provider.submit(b"r" * (m0_modal.MAX_M0_REQUEST_BYTES + 1))
    assert function.spawn_calls == []

    raw = b"request"
    parse_request[raw] = _request(raw)
    result_bytes = b"oversized-result"
    parse_result[result_bytes] = _result(parse_request[raw])
    call = FakeCall("fc-call01", result=result_bytes)
    provider = _provider(target, sdk=FakeSDK(FakeFunction(), call))
    handle = provider.submit(raw)
    call.result = b"z" * (m0_modal.MAX_M0_RESULT_BYTES + 1)
    with pytest.raises(BoundaryError, match="result_size_or_type_mismatch"):
        provider.poll(handle)


def test_remote_entry_validates_source_and_calls_only_existing_update_executor(
    monkeypatch: pytest.MonkeyPatch,
):
    raw = b"request"
    request = _request(raw)
    result_bytes = b"existing-executor-result"
    result = SimpleNamespace(
        request_sha256=request.request_sha256,
        attempt_id=request.attempt_id,
        run_id=request.run_id,
        input_id=request.input_id,
        operation_id=request.operation_id,
        producer=request.producer,
        target_device=request.target_device,
        target_step=request.target_step,
        resume_checkpoint_id=request.resume_checkpoint_id,
        config=request.config,
        to_bytes=lambda: result_bytes,
    )
    monkeypatch.setattr(
        m0_modal,
        "_decode_request",
        lambda value: request if value == raw else None,
    )
    monkeypatch.setattr(m0_modal, "_result_matches_request", lambda got, expected: got is result)
    import stpd.workers.token_remote_update as worker

    calls: list[object] = []
    monkeypatch.setattr(
        worker,
        "execute_token_remote_update",
        lambda got: calls.append(got) or result,
    )
    monkeypatch.setattr(m0_modal, "_observe_worker_runtime", lambda: _runtime_observation())

    frame = m0_modal.execute_m0_request_bytes(
        raw,
        expected_producer=PRODUCER,
        expected_image_object_id="im-image01",
    )
    assert frame.startswith(m0_modal._FRAME_MAGIC)
    assert frame.endswith(result_bytes)
    assert calls == [request]

    with pytest.raises(BoundaryError, match="deployed_source_or_cuda_mismatch"):
        m0_modal.execute_m0_request_bytes(
            raw,
            expected_producer=Producer("other/repository", "9" * 40, "8" * 64),
            expected_image_object_id="im-image01",
        )
    assert calls == [request]


def test_remote_entry_fails_closed_before_training_when_runtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    raw = b"request"
    request = _request(raw)
    monkeypatch.setattr(m0_modal, "_decode_request", lambda value: request)
    monkeypatch.setattr(
        m0_modal,
        "_observe_worker_runtime",
        lambda: (_ for _ in ()).throw(BoundaryError("modal_m0", "target_runtime_unavailable")),
    )
    import stpd.workers.token_remote_update as worker

    calls: list[object] = []
    monkeypatch.setattr(worker, "execute_token_remote_update", lambda got: calls.append(got))

    with pytest.raises(BoundaryError, match="target_runtime_unavailable"):
        m0_modal.execute_m0_request_bytes(
            raw,
            expected_producer=PRODUCER,
            expected_image_object_id="im-image01",
        )
    assert calls == []


@pytest.mark.parametrize(
    "runtime_change",
    [{"torch_version": "2.8.0+cu129"}, {"cpu_threads": 8}],
)
def test_remote_entry_rejects_runtime_metadata_mismatch_before_training(
    monkeypatch: pytest.MonkeyPatch,
    runtime_change: dict[str, object],
):
    raw = b"request"
    request = _request(raw)
    monkeypatch.setattr(m0_modal, "_decode_request", lambda value: request)
    monkeypatch.setattr(
        m0_modal,
        "_observe_worker_runtime",
        lambda: _runtime_observation(**runtime_change),
    )
    import stpd.workers.token_remote_update as worker

    calls: list[object] = []
    monkeypatch.setattr(worker, "execute_token_remote_update", lambda got: calls.append(got))

    with pytest.raises(BoundaryError, match="target_runtime_mismatch"):
        m0_modal.execute_m0_request_bytes(
            raw,
            expected_producer=PRODUCER,
            expected_image_object_id="im-image01",
        )
    assert calls == []


def test_deployment_entry_declares_fixed_image_bounded_ephemeral_worker_without_running_it(
    monkeypatch: pytest.MonkeyPatch,
):
    configured: dict[str, object] = {}
    executions: list[object] = []

    class FakeApp:
        def __init__(self, name: str) -> None:
            configured["app_name"] = name

        def function(self, **options: object):
            configured.update(options)

            def decorate(function):
                return function

            return decorate

    class FakeImage:
        @staticmethod
        def from_id(image_id: str) -> object:
            configured["image_id"] = image_id
            return SimpleNamespace(object_id=image_id)

    fake_modal = ModuleType("modal")
    fake_modal.App = FakeApp
    fake_modal.Image = FakeImage
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    monkeypatch.setenv("STPD_M0_MODAL_IMAGE_ID", "im-image01")
    monkeypatch.setenv("STPD_M0_SOURCE_REVISION", PRODUCER.source_revision)
    monkeypatch.setenv("STPD_M0_UV_LOCK_SHA256", PRODUCER.uv_lock_sha256)
    monkeypatch.setattr(
        m0_modal,
        "execute_m0_request_bytes",
        lambda *args, **kwargs: executions.append(args),
    )

    entry = Path(__file__).parents[1] / "deploy" / "cloud-worker" / "m0_update_modal.py"
    namespace = runpy.run_path(str(entry))

    assert namespace["app"] is namespace["_APP"]
    assert callable(namespace["token_remote_update"])
    assert configured["app_name"] == m0_modal.M0_MODAL_APP_NAME
    assert configured["image_id"] == "im-image01"
    assert configured["name"] == m0_modal.M0_MODAL_FUNCTION_NAME
    assert configured["gpu"] == "A10"
    assert configured["cpu"] == 4.0
    assert configured["memory"] == 32_768
    assert configured["ephemeral_disk"] == 20_480
    assert configured["timeout"] == 3_600
    assert configured["startup_timeout"] == 1_200
    assert configured["max_containers"] == 1
    assert configured["max_inputs"] == 1
    assert configured["retries"] == 0
    assert configured["min_containers"] == 0
    assert configured["scaledown_window"] == 60
    assert configured["single_use_containers"] is True
    assert "secrets" not in configured and "volumes" not in configured
    assert configured["env"]["OMP_NUM_THREADS"] == "4"
    assert configured["env"]["MKL_NUM_THREADS"] == "4"
    assert executions == []
