from __future__ import annotations

import hashlib
import importlib
import json
import runpy
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from stpd.cloud_jobs import m0_modal
from stpd.workers.token_ranking import LightActionM0Config, TokenTargetRuntime

PRODUCER = Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "a" * 40, "b" * 64)
CONFIG = LightActionM0Config(device="cuda")
OBSERVED = {
    "torch_version": "2.7.1+cu128",
    "python_version": "3.11.9",
    "cuda_version": "12.8",
    "cuda_available": True,
    "gpu_name": "NVIDIA L4",
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


def _request(raw: bytes = b"request", **changes: object) -> FakeRequest:
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
        "attempt_id": "c" * 32,
        "request_sha256": hashlib.sha256(b"request").hexdigest(),
        "target_runtime": TokenTargetRuntime(OBSERVED["torch_version"], OBSERVED["cpu_threads"]),
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
        "torch_version": target.target_runtime.torch_version,
        "cpu_threads": target.target_runtime.cpu_threads,
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
    request: FakeRequest | None = None,
) -> m0_modal.ModalM0Provider:
    if target is None:
        request = request or _request()
        target = _target(
            attempt_id=request.attempt_id,
            request_sha256=request.request_sha256,
            target_runtime=TokenTargetRuntime(request.torch_version, request.cpu_threads),
        )
    return m0_modal.ModalM0Provider(target, _target_runtime(target), sdk=sdk)


def _attempt_spec(request: FakeRequest | None = None) -> m0_modal.M0AttemptSpec:
    request = request or _request()
    return m0_modal.M0AttemptSpec(
        attempt_id=request.attempt_id,
        request_sha256=request.request_sha256,
        account_id="workspace-01",
        environment_name="staging",
        image_object_id="im-image01",
        producer=request.producer,
        target_runtime=TokenTargetRuntime(request.torch_version, request.cpu_threads),
    )


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
        self.cancel_calls: list[bool] = []
        self.cancel_error: Exception | None = None

    def num_inputs(self) -> int:
        return self._input_count

    def get(self, *, timeout: float | None = None, index: int = 0) -> object:
        self.get_calls.append((timeout, index))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def cancel(self, *, terminate_containers: bool = False) -> None:
        self.cancel_calls.append(terminate_containers)
        if self.cancel_error is not None:
            raise self.cancel_error


class SizedBytes(bytes):
    """Small byte content with a virtual wire length for boundary tests."""

    def __new__(cls, value: bytes, reported_length: int):
        result = super().__new__(cls, value)
        result.reported_length = reported_length
        return result

    def __len__(self) -> int:
        return self.reported_length


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
        self.app_name_lookup_calls: list[tuple[str, str]] = []
        self.app_name_lookup_options: list[tuple[None, float]] = []
        self.app_name_lookup_error: Exception | None = FakeModalNotFound("app not found")
        self.app_name_lookup_response: object | None = None

        class AppLookupStub:
            async def AppGetByDeploymentName(
                _stub_self,
                request: Any,
                *,
                retry: None,
                timeout: float,
            ) -> object:
                self.app_name_lookup_calls.append(
                    (request.name, request.environment_name),
                )
                self.app_name_lookup_options.append((retry, timeout))
                if self.app_name_lookup_error is not None:
                    raise self.app_name_lookup_error
                return self.app_name_lookup_response

        class ClientAPI:
            @staticmethod
            def from_env() -> SimpleNamespace:
                return SimpleNamespace(stub=AppLookupStub())

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
        self.Client = ClientAPI
        self.exception = SimpleNamespace(
            TimeoutError=FakeModalTimeout,
            InputCancellation=FakeInputCancellation,
            NotFoundError=FakeModalNotFound,
        )


class FakeModalNotFound(Exception):
    pass


class FakeModalPermissionDenied(Exception):
    pass


@pytest.fixture
def modal_api_pb2_fixture(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    async_utils = pytest.importorskip("modal._utils.async_utils")
    import_module = importlib.import_module
    api_pb2 = import_module("modal_proto.api_pb2")

    def import_for_provider(name: str) -> object:
        if name == "modal_proto.api_pb2":
            return api_pb2
        if name == "modal._utils.async_utils":
            return async_utils
        return import_module(name)

    monkeypatch.setattr(m0_modal.importlib, "import_module", import_for_provider)
    return api_pb2


class FakeModalTimeout(Exception):
    pass


class FakeInputCancellation(BaseException):
    pass


class FakeModalCLI:
    def __init__(
        self,
        spec: m0_modal.M0AttemptSpec | None = None,
        *,
        workspace_name: str = "test",
        workspace_id: str = "workspace-01",
    ) -> None:
        self.spec = spec
        self.deployed = False
        self.deploy_calls: list[list[str]] = []
        self.stop_calls: list[list[str]] = []
        self.app_list_calls = 0
        self.named_history_calls = 0
        self.workspace_name = workspace_name
        self.workspace_id = workspace_id
        self.app_list_override: object | None = None
        self.named_history_override: tuple[int, str, str] | None = None
        self.deploy_returncode = 0
        self.app_state = "deployed"
        self.app_tasks = "1"

    def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        args = command[1:]
        if args[:2] == ["token", "info"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"Workspace: {self.workspace_name} ({self.workspace_id})\n",
                stderr="",
            )
        if args[:2] == ["app", "list"]:
            self.app_list_calls += 1
            if self.app_list_override is not None:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(self.app_list_override),
                    stderr="",
                )
            rows = []
            if self.deployed and self.spec is not None:
                rows = [{
                    "app_id": "ap-app01",
                    "description": self.spec.app_name,
                    "state": self.app_state,
                    "tasks": self.app_tasks,
                }]
            return SimpleNamespace(returncode=0, stdout=json.dumps(rows))
        if args[:2] == ["app", "history"]:
            identifier = args[2]
            if self.spec is not None and identifier == self.spec.app_name:
                self.named_history_calls += 1
                if self.named_history_override is not None:
                    code, stdout, stderr = self.named_history_override
                    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
                if not self.deployed:
                    return SimpleNamespace(
                        returncode=1, stdout="",
                        stderr=(
                            f"No App with name '{self.spec.app_name}' found in the "
                            f"'{self.spec.environment_name}' environment."
                        ),
                    )
            history = []
            if self.deployed and self.spec is not None:
                history = [{
                    "version": "v1",
                    "tag": m0_modal.ModalM0Provider._spec_sha256(self.spec),
                }]
            return SimpleNamespace(returncode=0, stdout=json.dumps(history), stderr="")
        if args and args[0] == "deploy":
            self.deploy_calls.append(args)
            if self.deploy_returncode == 0:
                self.deployed = True
            return SimpleNamespace(returncode=self.deploy_returncode, stdout="", stderr="")
        if args[:2] == ["app", "stop"]:
            self.stop_calls.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["container", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        raise AssertionError(f"unexpected Modal command {args!r}")


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
    call = _provider(target, sdk=sdk).submit(raw, target)

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
            m0_modal.M0_MODAL_APP_NAME + request.attempt_id,
            m0_modal.M0_MODAL_FUNCTION_NAME,
            3,
            "staging",
        )
    ]
    assert sdk.function.spawn_calls == [raw]
    foreign_target = _target(account_id="workspace-02")
    different_account = m0_modal.ModalM0Provider(
        foreign_target,
        _target_runtime(foreign_target),
        sdk=FakeSDK(FakeFunction()),
    )
    with pytest.raises(BoundaryError, match="foreign_target"):
        different_account.restore_handle(call.to_bytes())


def test_app_ref_is_durable_and_prepare_reconciles_without_redeploy():
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    provider = m0_modal.ModalM0Provider(spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli)

    app_ref = provider.prepare_app(spec)
    assert app_ref == provider.resolve_app(spec)
    assert app_ref == m0_modal.M0AppRef.from_bytes(app_ref.to_bytes())
    assert app_ref.account_id == spec.account_id
    assert app_ref.environment_name == spec.environment_name
    assert app_ref.app_name == spec.app_name
    assert app_ref.attempt_id == spec.attempt_id
    assert app_ref.request_sha256 == spec.request_sha256
    assert app_ref.image_object_id == spec.image_object_id
    assert len(cli.deploy_calls) == 1

    restarted = m0_modal.ModalM0Provider(spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli)
    restored = restarted.prepare_app(spec)
    assert restored == app_ref
    assert cli.deploy_calls == [cli.deploy_calls[0]]


def test_unknown_prepare_is_never_redeployed_and_matching_late_app_is_recovered():
    spec = _attempt_spec()

    class LostDeployReply(FakeModalCLI):
        def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
            if command[1:2] == ["deploy"]:
                self.deploy_calls.append(command[1:])
                self.deployed = True
                raise subprocess.TimeoutExpired(command, 120)
            return super().__call__(command, **kwargs)

    cli = LostDeployReply(spec)
    provider = m0_modal.ModalM0Provider(spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli)
    app_ref = provider.prepare_app(spec)
    assert app_ref.app_id == "ap-app01"
    assert len(cli.deploy_calls) == 1


def test_unknown_prepare_without_visible_app_requires_read_only_reconciliation(
    modal_api_pb2_fixture: ModuleType,
):
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    cli.deploy_returncode = 1
    provider = m0_modal.ModalM0Provider(spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli)

    with pytest.raises(BoundaryError, match="prepare_unknown"):
        provider.prepare_app(spec)
    assert len(cli.deploy_calls) == 1
    with pytest.raises(BoundaryError, match="prepare_unknown"):
        provider.prepare_app(spec)
    assert len(cli.deploy_calls) == 1
    assert provider.resolve_app(spec) is None
    assert len(cli.deploy_calls) == 1


def test_new_prepare_requires_canonical_workspace_id_even_when_slug_is_live_bound():
    spec = replace(_attempt_spec(), account_id="chensunguo1210")
    cli = FakeModalCLI(
        spec, workspace_name="chensunguo1210", workspace_id="ac-BRJL3wJjpxPVWozQkvp9xf",
    )
    provider = m0_modal.ModalM0Provider(
        spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli,
    )

    with pytest.raises(BoundaryError, match="modal_account_mismatch"):
        provider.prepare_app(spec)

    assert cli.app_list_calls == cli.named_history_calls == 0
    assert cli.deploy_calls == []


def test_recovery_accepts_old_slug_only_from_same_live_workspace_binding():
    spec = replace(_attempt_spec(), account_id="chensunguo1210")
    cli = FakeModalCLI(
        spec, workspace_name="chensunguo1210", workspace_id="ac-BRJL3wJjpxPVWozQkvp9xf",
    )
    cli.deployed = True
    provider = m0_modal.ModalM0Provider(
        spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=cli,
    )

    recovered = provider.resolve_app(spec, allow_legacy_workspace_alias=True)

    assert recovered is not None and recovered.account_id == "chensunguo1210"
    assert cli.app_list_calls == 1
    assert cli.deploy_calls == []

    wrong_workspace = FakeModalCLI(
        spec, workspace_name="other-workspace", workspace_id="ac-foreign",
    )
    wrong = m0_modal.ModalM0Provider(
        spec=spec, sdk=FakeSDK(FakeFunction()), command_runner=wrong_workspace,
    )
    with pytest.raises(BoundaryError, match="modal_account_mismatch"):
        wrong.resolve_app(spec, allow_legacy_workspace_alias=True)
    assert wrong_workspace.app_list_calls == wrong_workspace.named_history_calls == 0

    canonical_spec = replace(spec, account_id="ac-original")
    changed_account = FakeModalCLI(
        canonical_spec, workspace_name="ac-original", workspace_id="ac-current",
    )
    canonical_provider = m0_modal.ModalM0Provider(
        spec=canonical_spec, sdk=FakeSDK(FakeFunction()), command_runner=changed_account,
    )
    with pytest.raises(BoundaryError, match="modal_account_mismatch"):
        canonical_provider.resolve_app(
            canonical_spec, allow_legacy_workspace_alias=True,
        )
    assert changed_account.app_list_calls == changed_account.named_history_calls == 0


def test_absence_uses_sdk_empty_id_contract_instead_of_rich_cli_diagnostic(
    modal_api_pb2_fixture: ModuleType,
):
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    # This wraps the reported no-App sentence as Modal's CLI does. Reconciliation
    # must use the typed RPC and must not parse this presentation text.
    cli.named_history_override = (
        1,
        "",
        "╭─ Error ─────────────────────────────────────────╮\n"
        f"│ No App with name '{spec.app_name}' found in the '│\n"
        f"│ {spec.environment_name}' environment.             │\n"
        "╰─────────────────────────────────────────────────╯",
    )
    sdk = FakeSDK(FakeFunction())
    sdk.app_name_lookup_error = None
    sdk.app_name_lookup_response = modal_api_pb2_fixture.AppGetByDeploymentNameResponse(
        environment_name=spec.environment_name,
    )
    provider = m0_modal.ModalM0Provider(
        spec=spec, sdk=sdk, command_runner=cli,
    )

    assert provider.resolve_app(spec) is None
    assert cli.app_list_calls == 1
    assert cli.named_history_calls == 0
    assert sdk.app_name_lookup_calls == [(spec.app_name, spec.environment_name)]
    assert sdk.app_name_lookup_options == [(None, m0_modal.MAX_MODAL_CONTROL_SECONDS)]

    cli.app_list_override = [{"app_id": "ap-app01"}]
    with pytest.raises(BoundaryError, match="modal_app_list_incomplete"):
        provider.resolve_app(spec)


def test_named_lookup_current_or_recently_stopped_app_stays_unknown(
    modal_api_pb2_fixture: ModuleType,
):
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    sdk = FakeSDK(FakeFunction())
    sdk.app_name_lookup_error = None
    sdk.app_name_lookup_response = modal_api_pb2_fixture.AppGetByDeploymentNameResponse(
        environment_name=spec.environment_name,
        app_id="ap-current01",
        previous_app_id="ap-previous01",
    )
    provider = m0_modal.ModalM0Provider(
        spec=spec, sdk=sdk, command_runner=cli,
    )

    with pytest.raises(BoundaryError, match="modal_app_lookup_incomplete"):
        provider.resolve_app(spec)
    assert sdk.app_name_lookup_calls == [(spec.app_name, spec.environment_name)]
    assert cli.named_history_calls == 0


def test_named_lookup_wrong_type_environment_or_app_id_stays_unknown(
    modal_api_pb2_fixture: ModuleType,
):
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    sdk = FakeSDK(FakeFunction())
    sdk.app_name_lookup_error = None
    invalid_responses = (
        object(),
        modal_api_pb2_fixture.AppGetByDeploymentNameResponse(
            environment_name="other-environment",
        ),
        modal_api_pb2_fixture.AppGetByDeploymentNameResponse(
            environment_name=spec.environment_name,
            app_id="malformed-app-id",
        ),
    )
    for response in invalid_responses:
        sdk.app_name_lookup_response = response
        provider = m0_modal.ModalM0Provider(
            spec=spec, sdk=sdk, command_runner=cli,
        )
        with pytest.raises(BoundaryError, match="modal_app_name_lookup_unknown"):
            provider.resolve_app(spec)
    assert sdk.app_name_lookup_calls == [
        (spec.app_name, spec.environment_name),
    ] * len(invalid_responses)


@pytest.mark.parametrize(
    "error_type",
    [FakeModalPermissionDenied, RuntimeError],
)
def test_named_lookup_non_not_found_errors_stay_unknown(
    modal_api_pb2_fixture: ModuleType,
    error_type: type[Exception],
):
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    sdk = FakeSDK(FakeFunction())
    # Even a generic error containing the exact no-App sentence is not evidence.
    sdk.app_name_lookup_error = error_type(
        f"No App with name '{spec.app_name}' found in the "
        f"'{spec.environment_name}' environment."
    )
    provider = m0_modal.ModalM0Provider(
        spec=spec, sdk=sdk, command_runner=cli,
    )

    with pytest.raises(BoundaryError, match="modal_app_name_lookup_unknown"):
        provider.resolve_app(spec)
    assert sdk.app_name_lookup_calls == [(spec.app_name, spec.environment_name)]
    assert cli.named_history_calls == 0


def test_stop_confirmation_rejects_incomplete_provider_app_rows():
    smoke = Path(__file__).parents[1] / "deploy" / "cloud-worker" / "m0_synthetic_gpu_smoke.py"
    namespace = runpy.run_path(str(smoke))
    namespace["_stop_evidence"].__globals__["_modal_cli_json"] = (
        lambda _args, **_kwargs: [{"app_id": "ap-app01"}]
    )

    with pytest.raises(namespace["PlanError"], match="modal_app_list_incomplete"):
        namespace["_stop_evidence"](
            "ap-app01", "staging", deadline=m0_modal.time.monotonic() + 10,
        )


def test_existing_attempt_with_different_spec_tag_is_rejected_without_deploy():
    spec = _attempt_spec()
    cli = FakeModalCLI(spec)
    cli.deployed = True
    provider = m0_modal.ModalM0Provider(sdk=FakeSDK(FakeFunction()), command_runner=cli)
    bad_spec = replace(spec, request_sha256="9" * 64)

    with pytest.raises(BoundaryError, match="existing_app_spec_mismatch"):
        provider.prepare_app(bad_spec)
    assert cli.deploy_calls == []


def test_cancel_stop_ack_and_confirmed_zero_container_inspection_are_distinct():
    request = _request()
    target = _target()
    call = FakeCall("fc-call01")
    cli = FakeModalCLI(_attempt_spec(request))
    cli.deployed = True
    provider = _provider(target, sdk=FakeSDK(FakeFunction(), call))
    provider._command_runner = cli
    handle = m0_modal.ModalM0Call(
        target,
        _target_runtime(target),
        "fc-call01",
        request.request_sha256,
        len(request.raw),
        request.attempt_id,
        request.run_id,
        request.input_id,
        request.operation_id,
        request.producer,
        m0_modal._request_result_identity_sha256(request),
        request.target_device,
        request.target_step,
        request.resume_checkpoint_id,
    )

    cancel_ack = provider.cancel(handle)
    assert cancel_ack.status == "acknowledged"
    assert call.cancel_calls == [True]
    assert provider.inspect_stop(target).confirmed is False

    stop_ack = provider.stop_app(target)
    assert stop_ack.status == "acknowledged"
    assert stop_ack.app_id == target.app_id
    assert cli.stop_calls == [["app", "stop", target.app_id, "--yes", "--env", "staging"]]
    cli.app_state = "stopped"
    cli.app_tasks = "0"
    provider._smoke_helpers = lambda: {
        "_stop_evidence": lambda app_id, environment, *, deadline: {
            "app_state": "stopped", "app_tasks": 0, "running_containers": 0,
        }
    }
    inspection = provider.inspect_stop(target)
    assert inspection == m0_modal.M0StopInspection(target.app_id, "stopped", 0, 0, True)


def test_cancel_error_is_unknown_and_foreign_saved_call_cannot_be_cancelled():
    target = _target()
    call = FakeCall("fc-call01", app_id="ap-foreign")
    provider = _provider(target, sdk=FakeSDK(FakeFunction(), call))
    request = _request()
    handle = m0_modal.ModalM0Call(
        target,
        _target_runtime(target),
        "fc-call01",
        request.request_sha256,
        len(request.raw),
        request.attempt_id,
        request.run_id,
        request.input_id,
        request.operation_id,
        request.producer,
        m0_modal._request_result_identity_sha256(request),
        request.target_device,
        request.target_step,
        request.resume_checkpoint_id,
    )

    ack = provider.cancel(handle)
    assert ack.status == "unknown" and ack.acknowledged is False
    assert call.cancel_calls == []


def test_fake_target_metadata_binds_runtime_to_account_image_and_source_lock():
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

    from stpd.workers.token_ranking import TokenTargetRuntime

    runtime = metadata.token_target_runtime(target)
    assert runtime == TokenTargetRuntime(OBSERVED["torch_version"], OBSERVED["cpu_threads"])


def test_submit_unknown_is_reported_once_without_internal_retry(
    parse_request: dict[bytes, FakeRequest],
):
    raw = b"ambiguous"
    parse_request[raw] = _request(raw)
    function = FakeFunction(spawn_error=RuntimeError("do not expose provider details"))
    provider = _provider(sdk=FakeSDK(function), request=parse_request[raw])

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


def test_request_from_another_prepared_attempt_is_rejected_before_submission(
    parse_request: dict[bytes, FakeRequest],
):
    raw = b"request"
    parse_request[raw] = _request(raw, attempt_id="9" * 32)
    function = FakeFunction()
    provider = _provider(sdk=FakeSDK(function))

    with pytest.raises(BoundaryError, match="app_ref_request_identity_mismatch"):
        provider.submit(raw)
    assert function.spawn_calls == []


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


@pytest.mark.parametrize(
    ("exception_name", "expected_code"),
    [
        ("FunctionTimeoutError", "call_terminal_failure"),
        ("OutputExpiredError", "call_terminal_failure"),
        ("TimeoutError", "pending"),
    ],
)
def test_real_modal_timeout_classes_distinguish_terminal_from_pending(
    parse_request: dict[bytes, FakeRequest],
    exception_name: str,
    expected_code: str,
):
    import modal

    raw = b"request"
    parse_request[raw] = _request(raw)
    request = parse_request[raw]
    function = FakeFunction()
    error_type = getattr(modal.exception, exception_name)
    call = FakeCall("fc-call01", result=error_type())
    sdk = FakeSDK(function, call)
    sdk.exception = modal.exception
    provider = _provider(sdk=sdk, request=request)
    handle = provider.submit(raw)

    if expected_code == "pending":
        assert provider.poll(handle, timeout_seconds=0.25) is None
    else:
        with pytest.raises(BoundaryError, match=expected_code):
            provider.poll(handle, timeout_seconds=0.25)
    assert call.get_calls == [(0.25, 0)]


def test_poll_ignores_non_exception_modal_terminal_types(
    parse_request: dict[bytes, FakeRequest],
):
    raw = b"request"
    parse_request[raw] = _request(raw)
    call = FakeCall("fc-call01", result=ValueError("unexpected call failure"))
    sdk = FakeSDK(FakeFunction(), call)
    sdk.exception = SimpleNamespace(
        TimeoutError=FakeModalTimeout,
        FunctionTimeoutError=str,
        OutputExpiredError=int,
        RemoteError=dict,
        InputCancellation=FakeInputCancellation,
    )
    provider = _provider(sdk=sdk, request=parse_request[raw])
    handle = provider.submit(raw)

    with pytest.raises(BoundaryError, match="result_unavailable"):
        provider.poll(handle, timeout_seconds=0.25)

    assert call.get_calls == [(0.25, 0)]


@pytest.mark.parametrize("timeout_error", [FakeModalTimeout(), FakeInputCancellation()])
def test_poll_classifies_modal_timeout_and_cancellation_without_fabricating_result(
    parse_request: dict[bytes, FakeRequest],
    timeout_error: BaseException,
):
    raw = b"request"
    parse_request[raw] = _request(raw)
    call = FakeCall("fc-call01", result=timeout_error)
    provider = _provider(sdk=FakeSDK(FakeFunction(), call))
    handle = provider.submit(raw)

    if isinstance(timeout_error, FakeModalTimeout):
        assert provider.poll(handle, timeout_seconds=0.25) is None
    else:
        with pytest.raises(BoundaryError, match="call_cancelled"):
            provider.poll(handle, timeout_seconds=0.25)
    with pytest.raises(BoundaryError, match="runtime_evidence_unavailable"):
        provider.get_runtime_evidence(handle)


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
    provider = _provider(sdk=FakeSDK(function, call), request=request)
    handle = provider.submit(raw)
    target = _target(
        attempt_id=request.attempt_id,
        request_sha256=request.request_sha256,
        target_runtime=TokenTargetRuntime(request.torch_version, request.cpu_threads),
    )
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
    assert evidence.gpu_name == "NVIDIA L4"
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
    provider = _provider(sdk=FakeSDK(function, call), request=request)

    handle = provider.submit(raw)
    call.result = _frame(request, result_bytes, provider.target)
    assert function.spawn_calls == [raw]
    assert handle.request_size_bytes == len(raw)
    assert provider.poll(handle) == result_bytes
    assert provider.get_runtime_evidence(handle).result_size_bytes == len(result_bytes)
    assert m0_modal.MAX_M0_REQUEST_BYTES == 256 * 1024 * 1024
    assert m0_modal.MAX_M0_RESULT_BYTES == 128 * 1024 * 1024


def test_real_core_resume_request_passes_256_mib_provider_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from test_token_remote_update import _canonical_run

    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
    from stpd.workers.token_ranking import (
        CUDA_STEP_RNG_PROTOCOL,
        TokenRankingEngine,
        TokenTargetRuntime,
        config_payload,
        token_training_identity,
    )
    from stpd.workers.token_remote_update import prepare_token_remote_update
    from stpd.workers.token_worker import _checkpoint_metadata, prepare_token_run

    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, _ = case
    try:
        target_runtime = TokenTargetRuntime.current()
        cuda_config = replace(config, device="cuda")
        cuda_run = prepare_token_run(
            store,
            inputs,
            cuda_config,
            producer,
            replicate="modal-resume-preflight",
            target_runtime=target_runtime,
        )
        partial_request = prepare_token_remote_update(
            store, owner, cuda_run.artifact_id, producer, operation, 2,
            attempt_id="9" * 32,
        )

        # Advance the same scratch M0 model on CPU, then bind the checkpoint to
        # the declared CUDA target as in the existing no-CUDA integration path.
        cpu_engine = TokenRankingEngine(inputs, config)
        cpu_engine.advance()
        state = decode_checkpoint(cpu_engine.checkpoint())
        _, expected_identity = token_training_identity(
            inputs, cuda_config, partial_request.backbone_identity.value(),
        )
        state["config"] = config_payload(cuda_config)
        state["data_identity"] = expected_identity
        state["rng_protocol"] = CUDA_STEP_RNG_PROTOCOL
        state["torch_version"] = target_runtime.torch_version
        state["cpu_threads"] = target_runtime.cpu_threads
        checkpoint_bytes = encode_checkpoint(state)
        input_manifest = store.get_manifest(cuda_run.parent("training_input"))
        checkpoint_info = _checkpoint_metadata(cpu_engine)
        checkpoint_info["data_identity"] = expected_identity
        checkpoint_info["step"] = state["step"]
        checkpoint = Manifest(
            "checkpoint",
            producer,
            (
                Parent("run", cuda_run.artifact_id),
                Parent("training_input", input_manifest.artifact_id),
            ),
            (
                store.put_bytes(
                    "checkpoint", checkpoint_bytes, "application/vnd.stpd.tensor-tree",
                ),
            ),
            FrozenObject.of(checkpoint_info),
        )
        store.publish(checkpoint)

        request = prepare_token_remote_update(
            store,
            owner,
            cuda_run.artifact_id,
            producer,
            operation,
            3,
            resume_checkpoint_id=checkpoint.artifact_id,
            attempt_id="a" * 32,
        )
        assert request.resume_checkpoint is not None
        raw = request.to_bytes()
        # Model the larger base64 wire envelope of the 81-MiB checkpoint case
        # without allocating a second 100+ MiB JSON request in this CPU test.
        wire = SizedBytes(raw, 120 * 1024 * 1024)
        target = _target(
            producer=producer,
            attempt_id=request.attempt_id,
            request_sha256=request.request_sha256,
            target_runtime=target_runtime,
        )
        function = FakeFunction()
        provider = _provider(target, sdk=FakeSDK(function))

        handle = provider.submit(wire, target)

        assert handle.resume_checkpoint_id == checkpoint.artifact_id
        assert handle.request_size_bytes == 120 * 1024 * 1024
        assert function.spawn_calls == [wire]
        assert m0_modal.MAX_M0_REQUEST_BYTES == 256 * 1024 * 1024
    finally:
        torch.set_num_threads(previous_threads)


def test_serialized_request_and_result_bounds_fail_closed_without_retry(
    parse_request: dict[bytes, FakeRequest],
    parse_result: dict[bytes, FakeResult],
):
    target = _target()
    function = FakeFunction()
    provider = _provider(target, sdk=FakeSDK(function))
    with pytest.raises(BoundaryError, match="request_size_limit"):
        provider.submit(SizedBytes(b"r", m0_modal.MAX_M0_REQUEST_BYTES + 1))
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
    monkeypatch.setattr(m0_modal, "_runtime_source_identity", lambda: PRODUCER)
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

    with pytest.raises(BoundaryError, match="runtime_source_lock_mismatch"):
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
    monkeypatch.setattr(m0_modal, "_runtime_source_identity", lambda: PRODUCER)
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
    monkeypatch.setattr(m0_modal, "_runtime_source_identity", lambda: PRODUCER)
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


def test_remote_entry_accepts_cuda_runtime_without_a10_model_assumption(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_torch = SimpleNamespace(
        __version__=OBSERVED["torch_version"],
        version=SimpleNamespace(cuda=OBSERVED["cuda_version"]),
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda index: "NVIDIA H100",
        ),
        get_num_threads=lambda: OBSERVED["cpu_threads"],
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    observed = m0_modal._observe_worker_runtime()
    assert observed.cuda_available is True
    assert observed.gpu_name == "NVIDIA H100"


def test_runtime_source_identity_fails_closed_outside_fixed_image_checkout():
    with pytest.raises(BoundaryError, match="runtime_source_identity_unavailable"):
        m0_modal.execute_m0_request_bytes(
            b"request",
            expected_producer=PRODUCER,
            expected_image_object_id="im-image01",
        )


def test_remote_entry_rejects_measured_checkout_lock_mismatch_before_training(
    monkeypatch: pytest.MonkeyPatch,
):
    raw = b"request"
    request = _request(raw)
    monkeypatch.setattr(m0_modal, "_decode_request", lambda value: request)
    monkeypatch.setattr(
        m0_modal,
        "_runtime_source_identity",
        lambda: Producer(PRODUCER.repository, PRODUCER.source_revision, "9" * 64),
    )
    import stpd.workers.token_remote_update as worker

    calls: list[object] = []
    monkeypatch.setattr(worker, "execute_token_remote_update", lambda got: calls.append(got))
    with pytest.raises(BoundaryError, match="runtime_source_lock_mismatch"):
        m0_modal.execute_m0_request_bytes(
            raw,
            expected_producer=PRODUCER,
            expected_image_object_id="im-image01",
        )
    assert calls == []


def test_real_train_only_request_round_trips_through_mock_modal_and_local_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from test_token_remote_update import _canonical_run

    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
    from stpd.workers.token_ranking import (
        CUDA_STEP_RNG_PROTOCOL,
        TokenRankingEngine,
        TokenTargetRuntime,
        config_payload,
        token_training_identity,
    )
    from stpd.workers.token_remote_update import (
        TokenRemoteUpdateRequest,
        TokenRemoteUpdateResult,
        prepare_token_remote_update,
        validate_token_remote_update,
    )
    from stpd.workers.token_worker import prepare_token_run

    case = _canonical_run(tmp_path, monkeypatch)
    torch, previous_threads, store, owner, operation, producer, inputs, config, _ = case
    try:
        target_runtime = TokenTargetRuntime.current()
        cuda_config = replace(config, device="cuda")
        run = prepare_token_run(
            store,
            inputs,
            cuda_config,
            producer,
            replicate="modal-provider-integration",
            target_runtime=target_runtime,
        )
        request = prepare_token_remote_update(
            store,
            owner,
            run.artifact_id,
            producer,
            operation,
            2,
            attempt_id="a" * 32,
        )
        request_bytes = request.to_bytes()
        decoded_request = TokenRemoteUpdateRequest.from_bytes(request_bytes)
        assert decoded_request == request

        # Synthetic CPU tensors are tagged with the CUDA request identity solely for
        # structural validation. They are not evidence of remote CUDA execution.
        engine = TokenRankingEngine(inputs, config)
        engine.advance()
        engine.advance()
        state = decode_checkpoint(engine.checkpoint())
        _, expected_identity = token_training_identity(
            inputs, cuda_config, request.backbone_identity.value(),
        )
        state["config"] = config_payload(cuda_config)
        state["data_identity"] = expected_identity
        state["rng_protocol"] = CUDA_STEP_RNG_PROTOCOL
        state["torch_version"] = target_runtime.torch_version
        state["cpu_threads"] = target_runtime.cpu_threads
        checkpoint = encode_checkpoint(state)
        result = TokenRemoteUpdateResult(
            request.request_sha256,
            request.attempt_id,
            request.run_id,
            request.input_id,
            request.producer,
            request.operation_id,
            request.training_binding,
            request.target_device,
            request.target_step,
            request.config,
            request.resume_checkpoint_id,
            None,
            request.target_step,
            hashlib.sha256(checkpoint).hexdigest(),
            checkpoint,
            request.backbone_identity,
        )
        result_bytes = result.to_bytes()
        target = _target(
            producer=producer,
            attempt_id=request.attempt_id,
            request_sha256=request.request_sha256,
            target_runtime=target_runtime,
        )
        target_metadata = m0_modal.ModalM0TargetRuntimeMetadata.from_provider_metadata(
            {
                "target_id": target.target_id,
                "producer": producer.to_dict(),
                "image_object_id": target.image_object_id,
                "torch_version": target_runtime.torch_version,
                "cpu_threads": target_runtime.cpu_threads,
            },
            target=target,
        )
        function = FakeFunction()
        call = FakeCall("fc-call01")
        provider = m0_modal.ModalM0Provider(
            target,
            target_metadata,
            sdk=FakeSDK(function, call),
        )
        assert provider.token_target_runtime() == target_runtime
        handle = provider.submit(request_bytes)
        assert function.spawn_calls == [request_bytes]
        call.result = m0_modal._encode_worker_response(
            decoded_request,
            result_bytes,
            expected_image_object_id=target.image_object_id,
            runtime=_runtime_observation(
                torch_version=target_runtime.torch_version,
                cpu_threads=target_runtime.cpu_threads,
                gpu_name="NVIDIA L4",
            ),
        )
        before = store.manifest_ids()
        returned_bytes = provider.poll(handle)
        assert returned_bytes == result_bytes
        returned_result = TokenRemoteUpdateResult.from_bytes(returned_bytes)
        assert validate_token_remote_update(
            store, owner, request, returned_result, producer, operation,
        ) == returned_result
        assert store.manifest_ids() == before
        assert provider.get_runtime_evidence(handle).gpu_name == "NVIDIA L4"
    finally:
        torch.set_num_threads(previous_threads)


def test_deployment_entry_declares_fixed_image_bounded_ephemeral_worker_without_running_it(
    monkeypatch: pytest.MonkeyPatch,
):
    configured: dict[str, object] = {}
    subprocess_calls: list[tuple[list[str], dict[str, object]]] = []

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
    monkeypatch.setenv("STPD_M0_MODAL_APP_NAME", m0_modal.M0_MODAL_APP_NAME + "c" * 32)
    monkeypatch.setenv("STPD_M0_MODAL_ATTEMPT_ID", "c" * 32)
    monkeypatch.setenv("STPD_M0_MODAL_REQUEST_SHA256", "a" * 64)
    monkeypatch.setenv("STPD_M0_MODAL_SPEC_SHA256", "b" * 64)
    monkeypatch.setenv("STPD_M0_MODAL_PLAN_SHA256", m0_modal.M0_PILOT_EXECUTION_PLAN.plan_sha256)
    monkeypatch.setenv("STPD_M0_MODAL_IMAGE_ID", "im-image01")
    monkeypatch.setenv("STPD_M0_SOURCE_REVISION", PRODUCER.source_revision)
    monkeypatch.setenv("STPD_M0_UV_LOCK_SHA256", PRODUCER.uv_lock_sha256)
    monkeypatch.setenv("STPD_M0_TORCH_VERSION", OBSERVED["torch_version"])
    monkeypatch.setenv("STPD_M0_CPU_THREADS", str(OBSERVED["cpu_threads"]))

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        subprocess_calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"bounded-worker-frame", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    entry = Path(__file__).parents[1] / "deploy" / "cloud-worker" / "m0_update_modal.py"
    namespace = runpy.run_path(str(entry))

    assert namespace["app"] is namespace["_APP"]
    assert callable(namespace["token_remote_update"])
    assert namespace["MAX_M0_REQUEST_BYTES"] == m0_modal.MAX_M0_REQUEST_BYTES
    assert namespace["MAX_M0_RESULT_BYTES"] == m0_modal.MAX_M0_RESULT_BYTES
    assert configured["app_name"] == m0_modal.M0_MODAL_APP_NAME + "c" * 32
    assert configured["image_id"] == "im-image01"
    assert configured["name"] == m0_modal.M0_MODAL_FUNCTION_NAME
    assert configured["gpu"] == "L4"
    assert configured["cpu"] == 2.0
    assert configured["memory"] == 8_192
    assert configured["timeout"] == 900
    assert configured["startup_timeout"] == 120
    assert configured["max_containers"] == 1
    assert configured["max_inputs"] == 1
    assert configured["retries"] == 0
    assert configured["min_containers"] == 0
    assert configured["scaledown_window"] == 30
    assert configured["single_use_containers"] is True
    assert configured["serialized"] is True
    assert configured["include_source"] is False
    assert "secrets" not in configured and "volumes" not in configured
    assert configured["env"]["OMP_NUM_THREADS"] == str(OBSERVED["cpu_threads"])
    assert configured["env"]["MKL_NUM_THREADS"] == str(OBSERVED["cpu_threads"])
    assert namespace["token_remote_update"](b"request") == b"bounded-worker-frame"
    assert len(subprocess_calls) == 1
    command, options = subprocess_calls[0]
    assert command == [
        "/opt/stpd/python/.venv/bin/python",
        "-m",
        "stpd.cloud_jobs.m0_modal",
    ]
    assert options["cwd"] == "/opt/stpd/python"
    assert options["input"] == b"request"
    assert options["timeout"] == 900
    assert "PYTHONPATH" not in options["env"]
    assert "PYTHONHOME" not in options["env"]
    assert options["env"]["STPD_M0_MODAL_IMAGE_ID"] == "im-image01"
    assert options["env"]["STPD_M0_MODAL_ATTEMPT_ID"] == "c" * 32
    assert options["env"]["STPD_M0_MODAL_REQUEST_SHA256"] == "a" * 64
    assert options["env"]["STPD_M0_SOURCE_REVISION"] == PRODUCER.source_revision
    assert options["env"]["STPD_M0_UV_LOCK_SHA256"] == PRODUCER.uv_lock_sha256
