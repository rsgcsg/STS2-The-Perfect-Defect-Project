from __future__ import annotations

import asyncio
import builtins
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs import public_m2_modal
from stpd.cloud_jobs.public_m2_modal import (
    ModalPublicM2Provider,
    PublicM2ModalBinding,
    PublicM2ModalCall,
    PublicM2ModalResources,
    PublicM2ModalTarget,
    encode_modal_response,
)


class _Proto(SimpleNamespace):
    def HasField(self, name: str) -> bool:
        return hasattr(self, name)


class _Api:
    AppGetByDeploymentNameRequest = _Proto
    AppGetLayoutRequest = _Proto
    FunctionGetRequest = _Proto
    FunctionCallFromIdRequest = _Proto

    class AppGetByDeploymentNameResponse(_Proto):
        pass

    class AppGetLayoutResponse(_Proto):
        pass

    class FunctionGetResponse(_Proto):
        pass

    class FunctionCallFromIdResponse(_Proto):
        pass

    class Function:
        FUNCTION_TYPE_FUNCTION = 1


class _AsyncUtils:
    synchronizer = SimpleNamespace(create_blocking=lambda read: lambda: asyncio.run(read()))


class _FakeSdk:
    class TimeoutError(builtins.TimeoutError):
        pass

    class FunctionTimeoutError(builtins.TimeoutError):
        pass

    class OutputExpiredError(Exception):
        pass

    class RemoteError(Exception):
        pass

    class InputCancellation(Exception):
        pass

    exception = SimpleNamespace(
        TimeoutError=TimeoutError, FunctionTimeoutError=FunctionTimeoutError,
        OutputExpiredError=OutputExpiredError, RemoteError=RemoteError,
        InputCancellation=InputCancellation,
    )

    def __init__(self, target: PublicM2ModalTarget) -> None:
        self.target = target
        self.image_id = target.binding.image_object_id
        self.gpu = target.binding.resources.gpu
        self.app_id = target.app_id
        self.function_id = target.function_id
        self.saved_app_id = target.app_id
        self.saved_function_id = target.function_id
        self.previous_app_id = ""
        self.single_use_containers = True
        self.max_inputs = 1
        self.max_concurrent_inputs = 0
        self.spawn_calls = 0
        self.spawn_error = False
        self.poll_value: bytes | BaseException = encode_modal_response(
            b"opaque-result", target.binding)
        self.function_get_calls = 0
        state = self

        class Client:
            @staticmethod
            def from_env() -> Any:
                return SimpleNamespace(stub=state)

        class Function:
            @staticmethod
            def from_name(*args: Any, **kwargs: Any) -> Any:
                assert args == (target.app_name, target.function_name)
                assert kwargs["environment_name"] == target.environment_name
                return SimpleNamespace(object_id=state.function_id,
                                       hydrate=lambda: None, spawn=state.spawn)

        class FunctionCall:
            @staticmethod
            def from_id(call_id: str, *, client: Any) -> Any:
                assert call_id == "fc-one"
                assert client.stub is state
                return SimpleNamespace(get=state.get)

        self.Client = Client
        self.Function = Function
        self.FunctionCall = FunctionCall

    async def AppGetByDeploymentName(self, request: Any, **kwargs: Any) -> Any:
        return _Api.AppGetByDeploymentNameResponse(
            app_id=self.app_id, previous_app_id=self.previous_app_id,
            environment_name=self.target.environment_name,
        )

    async def AppGetLayout(self, request: Any, **kwargs: Any) -> Any:
        handle = _Proto(
            object_id=self.function_id,
            function_handle_metadata=_Proto(
                app_id=self.app_id, function_name=self.target.function_name),
        )
        return _Api.AppGetLayoutResponse(app_layout=_Proto(
            objects=[handle], function_ids={self.target.function_name: self.function_id}))

    async def FunctionGet(self, request: Any, **kwargs: Any) -> Any:
        self.function_get_calls += 1
        plan = self.target.binding.resources
        definition = _Proto(
            function_name=self.target.function_name, image_id=self.image_id,
            resources=_Proto(
                gpu_config=_Proto(gpu_type=self.gpu, count=1),
                milli_cpu=int(plan.cpu * 1000), memory_mb=plan.memory_mib),
            startup_timeout_secs=plan.startup_timeout_seconds,
            retry_policy=_Proto(retries=0),
            single_use_containers=self.single_use_containers,
            max_inputs=self.max_inputs,
            max_concurrent_inputs=self.max_concurrent_inputs,
        )
        return _Api.FunctionGetResponse(
            function_id=self.function_id,
            handle_metadata=_Proto(
                app_id=self.app_id, function_name=self.target.function_name,
                is_method=False, function_type=1),
            function=_Proto(
                function_name=self.target.function_name,
                ranked_functions=[_Proto(rank=0, function=definition)],
                timeout_secs=plan.deadline_seconds,
                autoscaler_settings=_Proto(
                    max_containers=1, min_containers=0, buffer_containers=0,
                    scaledown_window=plan.scaledown_seconds),
            ),
        )

    async def FunctionCallFromId(self, request: Any, **kwargs: Any) -> Any:
        return _Api.FunctionCallFromIdResponse(
            function_call_id=request.function_call_id, num_inputs=1,
            metadata=_Proto(app_id=self.saved_app_id, function_id=self.saved_function_id),
        )

    def spawn(self, request_bytes: bytes) -> Any:
        self.spawn_calls += 1
        if self.spawn_error:
            raise RuntimeError("ambiguous after invocation")
        return _Proto(object_id="fc-one")

    def get(self, *, timeout: float, index: int) -> bytes:
        assert index == 0
        if isinstance(self.poll_value, BaseException):
            raise self.poll_value
        return self.poll_value


def _setup() -> tuple[ModalPublicM2Provider, _FakeSdk, bytes]:
    raw = b"opaque-canonical-remote-request"
    resources = PublicM2ModalResources("L4", 2.0, 8192, 900, 120)
    binding = PublicM2ModalBinding(
        Producer("repo", "a" * 40, "b" * 64), hashlib.sha256(raw).hexdigest(),
        "im-exact", "c" * 64, resources,
    )
    target = PublicM2ModalTarget(
        "unique-public-m2-attempt", "stpd", "ap-one", "public_m2_remote",
        "fu-one", binding,
    )
    sdk = _FakeSdk(target)
    return ModalPublicM2Provider(
        target, binding, sdk=sdk, api=_Api, async_utils=_AsyncUtils), sdk, raw


def _code(error: pytest.ExceptionInfo[BoundaryError]) -> str:
    return error.value.code


def test_submit_poll_opaque_bytes_and_durable_handle() -> None:
    provider, sdk, raw = _setup()
    plan = provider.expected.resources
    assert PublicM2ModalResources.from_bytes(plan.to_bytes()) == plan
    assert len(plan.plan_sha256) == 64
    handle = provider.submit(raw)
    assert sdk.spawn_calls == 1
    assert sdk.function_get_calls == 2  # Before and after named-function hydration.
    assert PublicM2ModalCall.from_bytes(handle.to_bytes()) == handle
    assert provider.poll(handle) == b"opaque-result"
    assert sdk.spawn_calls == 1


def test_controller_pins_and_request_hash_are_checked_before_spawn() -> None:
    provider, sdk, raw = _setup()
    with pytest.raises(BoundaryError) as error:
        ModalPublicM2Provider(
            provider.target,
            replace(provider.expected, runtime_receipt_sha256="d" * 64),
            sdk=sdk, api=_Api, async_utils=_AsyncUtils,
        )
    assert _code(error) == "controller_target_binding_mismatch"
    with pytest.raises(BoundaryError) as error:
        provider.submit(raw + b"changed")
    assert _code(error) == "request_binding_mismatch"
    assert sdk.spawn_calls == 0
    assert sdk.function_get_calls == 0


@pytest.mark.parametrize("drift,code", [
    ("app_id", "deployment_identity_mismatch"),
    ("function_id", "deployment_identity_mismatch"),
    ("image_id", "deployment_resource_mismatch"),
    ("gpu", "deployment_resource_mismatch"),
    ("previous_app_id", "deployment_identity_mismatch"),
    ("single_use_containers", "deployment_resource_mismatch"),
    ("max_inputs", "deployment_resource_mismatch"),
    ("max_concurrent_inputs", "deployment_resource_mismatch"),
])
def test_live_deployment_drift_blocks_spawn(drift: str, code: str) -> None:
    provider, sdk, raw = _setup()
    bad: object = {
        "image_id": "im-other", "single_use_containers": False,
        "max_inputs": 2, "max_concurrent_inputs": 2,
    }.get(drift, "drift")
    setattr(sdk, drift, bad)
    with pytest.raises(BoundaryError) as error:
        provider.submit(raw)
    assert _code(error) == code
    assert sdk.spawn_calls == 0


def test_ambiguous_spawn_is_unknown_and_not_retried() -> None:
    provider, sdk, raw = _setup()
    sdk.spawn_error = True
    with pytest.raises(BoundaryError) as error:
        provider.submit(raw)
    assert _code(error) == "submission_unknown"
    assert sdk.spawn_calls == 1


def test_saved_call_drift_timeout_and_result_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, sdk, raw = _setup()
    handle = provider.submit(raw)
    sdk.saved_function_id = "fu-other"
    with pytest.raises(BoundaryError) as error:
        provider.poll(handle)
    assert _code(error) == "saved_call_identity_mismatch"
    sdk.saved_function_id = provider.target.function_id
    sdk.poll_value = sdk.TimeoutError()
    assert provider.poll(handle) is None
    sdk.poll_value = sdk.FunctionTimeoutError()
    with pytest.raises(BoundaryError) as error:
        provider.poll(handle)
    assert _code(error) == "call_terminal_failure"
    sdk.poll_value = b""
    with pytest.raises(BoundaryError) as error:
        provider.poll(handle)
    assert _code(error) == "result_size_limit"
    oversized_frame = encode_modal_response(b"x" * 5, provider.expected)
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", 4)
    sdk.poll_value = oversized_frame
    with pytest.raises(BoundaryError) as error:
        provider.poll(handle)
    assert _code(error) == "result_size_limit"


def test_transport_frame_binds_deployed_source_image_runtime_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, sdk, raw = _setup()
    handle = provider.submit(raw)
    variants = (
        replace(provider.expected, request_sha256="e" * 64),
        replace(provider.expected, producer=Producer("other", "a" * 40, "b" * 64)),
        replace(provider.expected, image_object_id="im-other"),
        replace(provider.expected, runtime_receipt_sha256="d" * 64),
    )
    for variant in variants:
        sdk.poll_value = encode_modal_response(b"opaque-result", variant)
        with pytest.raises(BoundaryError) as error:
            provider.poll(handle)
        assert _code(error) == "result_target_binding_mismatch"
    frame = encode_modal_response(b"opaque-result", provider.expected)
    sdk.poll_value = frame[:-1] + b"X"
    with pytest.raises(BoundaryError) as error:
        provider.poll(handle)
    assert _code(error) == "result_target_binding_mismatch"

    small_frame = encode_modal_response(b"four", provider.expected)
    monkeypatch.setattr(public_m2_modal, "MAX_PUBLIC_M2_RESULT_BYTES", 4)
    sdk.poll_value = small_frame
    assert provider.poll(handle) == b"four"  # Header allowance is separate.


def test_foreign_handle_and_invalid_resources_rejected() -> None:
    provider, _, raw = _setup()
    handle = provider.submit(raw)
    with pytest.raises(BoundaryError) as error:
        provider.poll(replace(handle, call_id="fc-other", target=replace(
            handle.target, app_id="ap-other")))
    assert _code(error) == "foreign_handle"
    with pytest.raises(BoundaryError) as error:
        replace(provider.expected.resources, retries=1)
    assert _code(error) == "retries_must_be_zero"
    with pytest.raises(BoundaryError) as error:
        replace(provider.expected.resources, max_containers=2)
    assert _code(error) == "one_container_required"
