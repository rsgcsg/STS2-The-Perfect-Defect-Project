"""Modal boundary tests; every cloud-facing operation is mocked."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "deploy"
    / "cloud-worker"
    / "m0_synthetic_gpu_smoke.py"
)
spec = importlib.util.spec_from_file_location("m0_synthetic_gpu_smoke", MODULE_PATH)
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)

SHA40 = "a" * 40
SHA64 = "b" * 64
IMAGE = "registry.example/project/candidate@sha256:" + "c" * 64
MODAL_IMAGE_ID = "im-candidate-123"


def make_plan() -> smoke.SmokePlan:
    return smoke.build_plan(
        image=IMAGE,
        environment="existing-dev",
        source_revision=SHA40,
        uv_lock_sha256=SHA64,
        harness_sha256=SHA64,
        run_id="d" * 32,
    )


def make_modal_image_plan() -> smoke.SmokePlan:
    return smoke.build_plan(
        image=MODAL_IMAGE_ID,
        image_kind=smoke.MODAL_IMAGE_KIND,
        environment="existing-dev",
        source_revision=SHA40,
        uv_lock_sha256=SHA64,
        harness_sha256=SHA64,
        run_id="e" * 32,
    )


def valid_receipt(plan: smoke.SmokePlan) -> dict[str, Any]:
    return {
        "schema": smoke.SMOKE_RECEIPT_SCHEMA,
        "run_id": plan.run_id,
        "environment": plan.environment,
        "input_scope": "synthetic-only",
        "synthetic_input_artifact_id": "sha256:" + "e" * 64,
        "repository": plan.repository,
        "source_revision": plan.source_revision,
        "uv_lock_sha256": plan.uv_lock_sha256,
        "harness_sha256": plan.harness_sha256,
        "image": plan.image,
        "image_kind": plan.image_kind,
        "runtime_modal_image_id": (
            plan.image if plan.image_kind == smoke.MODAL_IMAGE_KIND else "im-runtime-123"
        ),
        "gpu": "L4",
        "device": "cuda",
        "device_index": 0,
        "device_name": "NVIDIA L4",
        "torch_version": "2.7.1+cu128",
        "cuda_runtime_version": "12.8",
        "completed_steps": 2,
        "resume_checkpoint_step": 1,
        "continuous_steps": 2,
        "first_loss": 0.75,
        "first_loss_finite": True,
        "resumed_loss": 0.5,
        "resumed_loss_finite": True,
        "continuous_loss": 0.5,
        "continuous_loss_finite": True,
        "resume_loss_abs_error": 0.0,
        "resume_loss_matches_continuous": True,
        "resume_matches_continuous": True,
        "resumed_weight_inventory_sha256": "2" * 64,
        "continuous_weight_inventory_sha256": "2" * 64,
        "resumed_weight_tensor_count": 2,
        "continuous_weight_tensor_count": 2,
        "resumed_weight_element_count": 5,
        "continuous_weight_element_count": 5,
        "weight_max_abs_difference": 0.0,
        "weight_comparison_tolerance": {"abs_tol": smoke.WEIGHT_COMPARISON_ABS_TOL},
        "resume_weights_match_continuous": True,
        "loss_comparison_tolerance": {
            "rel_tol": smoke.LOSS_COMPARISON_REL_TOL,
            "abs_tol": smoke.LOSS_COMPARISON_ABS_TOL,
        },
        "checkpoint_sha256": "f" * 64,
        "export_sha256": "1" * 64,
        "export_bytes": 128,
        "worker_function_seconds": 0.25,
    }


class FakeRemoteError(Exception):
    pass


class FakeModalTimeoutError(Exception):
    pass


class FakeFunctionTimeoutError(FakeModalTimeoutError):
    pass


class FakeOutputExpiredError(FakeModalTimeoutError):
    pass


class FakeCall:
    def __init__(self, result: object, *, pending: bool = False) -> None:
        self.object_id = "fc-test-123"
        self.result = result
        self.pending = pending
        self.slow_poll = False
        self.get_timeouts: list[float] = []
        self.cancel_options: list[dict[str, bool]] = []

    def get(self, *, timeout: float) -> object:
        self.get_timeouts.append(timeout)
        if self.pending:
            if timeout == 0 and self.slow_poll:
                smoke.time.sleep(5)
            raise FakeModalTimeoutError("still running")
        if timeout == 0:
            return self.result
        return self.result

    def cancel(self, *, terminate_containers: bool) -> None:
        self.cancel_options.append({"terminate_containers": terminate_containers})


class FakeModalBoundary:
    def __init__(
        self,
        result: object,
        *,
        pending: bool = False,
        spawn_error: Exception | None = None,
    ) -> None:
        self.call = FakeCall(result, pending=pending)
        self.spawn_error = spawn_error
        self.listed_environments = 0
        self.registry_image_refs: list[str] = []
        self.modal_image_ids: list[str] = []
        self.apps: list[FakeApp] = []
        self.modal = SimpleNamespace(
            Environment=SimpleNamespace(objects=SimpleNamespace(list=self._list_envs)),
            Image=SimpleNamespace(
                from_registry=self._from_registry,
                from_id=self._from_id,
            ),
            App=self._make_app,
            exception=SimpleNamespace(
                RemoteError=FakeRemoteError,
                TimeoutError=FakeModalTimeoutError,
                FunctionTimeoutError=FakeFunctionTimeoutError,
                OutputExpiredError=FakeOutputExpiredError,
            ),
        )

    def _list_envs(self) -> list[SimpleNamespace]:
        self.listed_environments += 1
        return [SimpleNamespace(name="existing-dev")]

    def _from_registry(self, reference: str) -> object:
        assert reference == IMAGE
        self.registry_image_refs.append(reference)
        return object()

    def _from_id(self, image_id: str) -> object:
        self.modal_image_ids.append(image_id)
        return object()

    def _make_app(self, name: str) -> FakeApp:
        app = FakeApp(name, self)
        self.apps.append(app)
        return app


class FakeApp:
    def __init__(self, name: str, boundary: FakeModalBoundary) -> None:
        self.name = name
        self.boundary = boundary
        self.app_id = "ap-test-123"
        self.decorator_options: dict[str, Any] | None = None
        self.payload: dict[str, str] | None = None
        self.run_environment: str | None = None
        self.context_exited = False

    def function(self, **options: Any):
        self.decorator_options = options
        app = self

        class FakeFunction:
            def spawn(self, payload: dict[str, str]) -> FakeCall:
                app.payload = payload
                if app.boundary.spawn_error is not None:
                    raise app.boundary.spawn_error
                return app.boundary.call

        return lambda _function: FakeFunction()

    def run(self, *, environment_name: str):
        app = self

        class RunContext:
            def __enter__(self) -> FakeApp:
                app.run_environment = environment_name
                return app

            def __exit__(self, *_exc: object) -> bool:
                app.context_exited = True
                return False

        return RunContext()


def install_mocks(
    monkeypatch: pytest.MonkeyPatch,
    plan: smoke.SmokePlan,
    *,
    receipt: object | None = None,
    pending: bool = False,
    spawn_error: Exception | None = None,
    app_state: str = "stopped",
    app_tasks: str = "0",
    containers: list[object] | None = None,
) -> FakeModalBoundary:
    boundary = FakeModalBoundary(
        valid_receipt(plan) if receipt is None else receipt,
        pending=pending,
        spawn_error=spawn_error,
    )
    monkeypatch.setattr(smoke, "_require_cuda_configuration", lambda: None)
    monkeypatch.setitem(sys.modules, "modal", boundary.modal)

    def cli_json(args: list[str], *, timeout_seconds: float) -> object:
        assert 0 < timeout_seconds <= max(30, smoke.MAX_CLEANUP_COMMAND_SECONDS)
        if args[:2] == ["environment", "list"]:
            boundary.listed_environments += 1
            return [{"name": "existing-dev"}]
        if args[:2] == ["app", "list"]:
            return [{"app_id": "ap-test-123", "state": app_state, "tasks": app_tasks}]
        if args[:2] == ["container", "list"]:
            return [] if containers is None else containers
        pytest.fail(f"unexpected Modal CLI command: {args}")

    monkeypatch.setattr(smoke, "_modal_cli_json", cli_json)
    monkeypatch.setattr(
        smoke,
        "_probe_call_status",
        lambda _call_id, *, timeout_seconds: smoke._call_status_from_call(
            boundary.call, boundary.modal,
        ),
    )
    return boundary


def test_complete_receipt_validation_rejects_the_old_minimal_receipt():
    plan = make_plan()
    assert smoke.validate_receipt(valid_receipt(plan), plan)["export_bytes"] == 128
    with pytest.raises(smoke.PlanError, match="smoke_receipt_environment_mismatch"):
        smoke.validate_receipt(
            {"schema": smoke.SMOKE_RECEIPT_SCHEMA, "run_id": plan.run_id,
             "input_scope": "synthetic-only"},
            plan,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "e" * 32, "run_id_mismatch"),
        ("environment", "other-dev", "environment_mismatch"),
        ("input_scope", "real-training", "input_scope_mismatch"),
        ("source_revision", "9" * 40, "source_revision_mismatch"),
        ("uv_lock_sha256", "9" * 64, "uv_lock_sha256_mismatch"),
        ("harness_sha256", "9" * 64, "harness_sha256_mismatch"),
        ("image", "mutable:latest", "image_mismatch"),
        ("image_kind", smoke.MODAL_IMAGE_KIND, "image_kind_mismatch"),
        ("runtime_modal_image_id", "not-an-image-id", "runtime_modal_image_id_invalid"),
        ("device_name", "NVIDIA A10", "actual_l4_device_required"),
        ("first_loss", float("nan"), "first_loss_must_be_finite_number"),
        ("resume_matches_continuous", False, "resume_matches_continuous_must_be_true"),
        ("resume_weights_match_continuous", False,
         "resume_weights_match_continuous_must_be_true"),
        ("resume_loss_abs_error", 1.0, "resume_continuous_comparison_failed"),
        ("continuous_weight_inventory_sha256", "3" * 64,
         "smoke_receipt_weight_inventory_mismatch"),
        ("continuous_weight_tensor_count", 3, "smoke_receipt_weight_count_mismatch"),
        ("continuous_weight_element_count", 6, "smoke_receipt_weight_count_mismatch"),
        ("weight_max_abs_difference", 2e-6,
         "smoke_receipt_weight_comparison_tolerance_mismatch"),
        ("checkpoint_sha256", "not-a-hash", "checkpoint_sha256_invalid"),
        ("export_bytes", 0, "export_bytes_must_be_positive"),
        ("worker_function_seconds", float("inf"), "worker_function_seconds_invalid"),
        ("synthetic_input_artifact_id", " ", "synthetic_input_artifact_id_required"),
        ("cuda_runtime_version", "", "cuda_runtime_version_required"),
    ],
)
def test_receipt_validation_rejects_bad_or_unpinned_fields(
    field: str, value: object, message: str,
):
    plan = make_plan()
    receipt = valid_receipt(plan)
    receipt[field] = value
    with pytest.raises(smoke.PlanError, match=message):
        smoke.validate_receipt(receipt, plan)


def test_execute_spawns_once_with_hard_limits_and_confirms_ephemeral_stop(monkeypatch):
    plan = make_plan()
    boundary = install_mocks(monkeypatch, plan)

    receipt = smoke.execute(plan)

    assert plan.input_scope == "synthetic-only"
    assert plan.secrets == ()
    assert plan.volumes == ()
    assert plan.estimated_function_upper_seconds == 90
    assert plan.function_call_wall_seconds == 60
    assert plan.outer_wall_seconds == 120
    assert boundary.listed_environments == 1
    assert boundary.registry_image_refs == [IMAGE]
    assert boundary.modal_image_ids == []
    assert len(boundary.apps) == 1
    app = boundary.apps[0]
    assert app.name == plan.app_name
    assert app.run_environment == plan.environment
    assert app.context_exited is True
    assert app.payload == smoke.asdict(plan)
    assert boundary.call.get_timeouts == [plan.function_call_wall_seconds]
    assert boundary.call.cancel_options == []
    options = app.decorator_options
    assert options is not None
    assert options["serialized"] is True
    assert options["gpu"] == "L4"
    assert options["cpu"] == (2.0, 2.0)
    assert options["memory"] == (4096, 4096)
    assert options["max_containers"] == 1
    assert options["min_containers"] == 0
    assert options["retries"] == 0
    assert options["timeout"] == 30
    assert options["startup_timeout"] == 30
    assert options["scaledown_window"] == 30
    assert options["block_network"] is True
    assert "secrets" not in options
    assert "volumes" not in options
    assert receipt["input_scope"] == "synthetic-only"
    execution = receipt["execution"]
    assert execution["call_terminal"] == "completed"
    assert execution["stop_confirmation"] == "confirmed"
    assert execution["app_state"] == "stopped"
    assert execution["app_tasks"] == 0
    assert execution["running_containers"] == 0
    assert execution["actual_provider_usage"] == "unknown_not_returned_by_function_call"
    assert execution["estimated_function_cost"]["status"] == "estimate_only_not_provider_usage"
    assert execution["estimated_function_cost"]["image_build_usd"] is None


def test_execute_cancels_timed_out_call_once_and_never_claims_completion(monkeypatch):
    plan = make_plan()
    boundary = install_mocks(monkeypatch, plan, pending=True)
    monkeypatch.setattr(smoke, "MAX_CLEANUP_SECONDS", 0.01)

    with pytest.raises(smoke.SmokeExecutionError) as error:
        smoke.execute(plan)

    assert boundary.call.cancel_options == [{"terminate_containers": True}]
    assert error.value.outcome["call_terminal"] == "terminal_after_app_stop"
    assert error.value.outcome["stop_confirmation"] == "confirmed"
    assert error.value.outcome["status"] == "failed"
    assert error.value.outcome["reason"] == "modal_function_call_wall_deadline"


def test_ambiguous_spawn_failure_stays_unknown_even_if_app_is_stopped(monkeypatch):
    plan = make_plan()
    boundary = install_mocks(monkeypatch, plan, spawn_error=TimeoutError("ambiguous submit"))
    monkeypatch.setattr(smoke, "MAX_CLEANUP_SECONDS", 0.01)

    with pytest.raises(smoke.SmokeExecutionError) as error:
        smoke.execute(plan)

    assert boundary.call.cancel_options == []
    assert error.value.outcome["call_terminal"] == "unknown"
    assert error.value.outcome["stop_confirmation"] == "confirmed"
    assert error.value.outcome["status"] == "unknown"


def test_unreadable_terminal_probe_remains_unknown_after_app_stop(monkeypatch):
    plan = make_plan()
    boundary = install_mocks(monkeypatch, plan, pending=True)
    monkeypatch.setattr(smoke, "MAX_CLEANUP_SECONDS", 0.01)
    monkeypatch.setattr(smoke, "_probe_call_status", lambda *_args, **_kwargs: "unknown")

    with pytest.raises(smoke.SmokeExecutionError) as error:
        smoke.execute(plan)

    assert boundary.call.cancel_options == [{"terminate_containers": True}]
    assert error.value.outcome["call_terminal"] == "unknown"
    assert error.value.outcome["stop_confirmation"] == "confirmed"
    assert error.value.outcome["status"] == "unknown"


def test_malformed_remote_receipt_fails_after_confirmed_cleanup(monkeypatch):
    plan = make_plan()
    malformed = valid_receipt(plan)
    malformed["input_scope"] = "training"
    install_mocks(monkeypatch, plan, receipt=malformed)

    with pytest.raises(smoke.SmokeExecutionError) as error:
        smoke.execute(plan)

    assert error.value.outcome["reason"] == "smoke_receipt_input_scope_mismatch"
    assert error.value.outcome["call_terminal"] == "completed"
    assert error.value.outcome["stop_confirmation"] == "confirmed"
    assert error.value.outcome["status"] == "failed"


def test_running_app_or_container_blocks_stop_confirmation(monkeypatch):
    plan = make_plan()
    install_mocks(
        monkeypatch,
        plan,
        app_state="running",
        app_tasks="1",
        containers=[{"container_id": "ct-live"}],
    )
    monkeypatch.setattr(smoke, "MAX_CLEANUP_SECONDS", 0.01)

    with pytest.raises(smoke.SmokeExecutionError) as error:
        smoke.execute(plan)

    assert error.value.outcome["call_terminal"] == "completed"
    assert error.value.outcome["stop_confirmation"] == "unknown"
    assert error.value.outcome["status"] == "unknown"


def test_missing_environment_is_rejected_before_app_creation(monkeypatch):
    monkeypatch.setattr(smoke, "_modal_cli_json", lambda _args, **_kwargs: [])
    with pytest.raises(smoke.PlanError, match="existing_modal_environment_not_found"):
        smoke._preflight_environment("existing-dev")


def test_build_plan_requires_immutable_image_digest():
    with pytest.raises(smoke.PlanError, match="immutable_registry_digest"):
        smoke.build_plan(
            image="registry.example/project:mutable-tag",
            environment="existing-dev",
            source_revision=SHA40,
            uv_lock_sha256=SHA64,
            harness_sha256=SHA64,
            run_id="e" * 32,
        )


def test_build_plan_accepts_modal_image_id_as_a_distinct_kind():
    plan = make_modal_image_plan()

    assert plan.image == MODAL_IMAGE_ID
    assert plan.image_kind == smoke.MODAL_IMAGE_KIND
    with pytest.raises(smoke.PlanError, match="modal_image_id_required"):
        smoke.build_plan(
            image=IMAGE,
            image_kind=smoke.MODAL_IMAGE_KIND,
            environment="existing-dev",
            source_revision=SHA40,
            uv_lock_sha256=SHA64,
            harness_sha256=SHA64,
            run_id="f" * 32,
        )
    with pytest.raises(smoke.PlanError, match="unsupported_image_kind"):
        smoke.build_plan(
            image=IMAGE,
            image_kind="unknown",
            environment="existing-dev",
            source_revision=SHA40,
            uv_lock_sha256=SHA64,
            harness_sha256=SHA64,
            run_id="f" * 32,
        )


def test_runtime_modal_image_id_is_checked_and_receipt_bound():
    plan = make_modal_image_plan()

    assert smoke._validate_runtime_modal_image_id(
        plan, {"MODAL_IMAGE_ID": MODAL_IMAGE_ID},
    ) == MODAL_IMAGE_ID
    with pytest.raises(smoke.PlanError, match="runtime_modal_image_id_mismatch"):
        smoke._validate_runtime_modal_image_id(
            plan, {"MODAL_IMAGE_ID": "im-other-123"},
        )

    receipt = valid_receipt(plan)
    assert smoke.validate_receipt(receipt, plan)["runtime_modal_image_id"] == MODAL_IMAGE_ID
    receipt["runtime_modal_image_id"] = "im-other-123"
    with pytest.raises(smoke.PlanError, match="runtime_modal_image_id_mismatch"):
        smoke.validate_receipt(receipt, plan)


def test_execute_reuses_modal_image_id_without_touching_registry_path(monkeypatch):
    plan = make_modal_image_plan()
    boundary = install_mocks(monkeypatch, plan)

    receipt = smoke.execute(plan)

    assert boundary.modal_image_ids == [MODAL_IMAGE_ID]
    assert boundary.registry_image_refs == []
    assert boundary.apps[0].payload == smoke.asdict(plan)
    assert receipt["image_kind"] == smoke.MODAL_IMAGE_KIND
    assert receipt["runtime_modal_image_id"] == MODAL_IMAGE_ID


def test_cli_image_inputs_are_explicit_and_mutually_exclusive():
    common = [
        "--environment", "existing-dev",
        "--source-revision", SHA40,
        "--uv-lock-sha256", SHA64,
    ]
    oci_args = smoke._parser().parse_args([*common, "--image", IMAGE])
    modal_args = smoke._parser().parse_args([*common, "--modal-image-id", MODAL_IMAGE_ID])

    assert oci_args.image == IMAGE
    assert oci_args.modal_image_id is None
    assert modal_args.image is None
    assert modal_args.modal_image_id == MODAL_IMAGE_ID
    with pytest.raises(SystemExit):
        smoke._parser().parse_args([
            *common, "--image", IMAGE, "--modal-image-id", MODAL_IMAGE_ID,
        ])


def test_sdk_timeout_error_is_classified_as_pending_not_builtin_timeout():
    plan = make_plan()
    boundary = FakeModalBoundary(valid_receipt(plan), pending=True)

    assert not issubclass(FakeModalTimeoutError, TimeoutError)
    assert smoke._call_status_from_call(boundary.call, boundary.modal) == "pending"


def test_status_probe_kills_slow_sdk_process_at_its_deadline(monkeypatch):
    monkeypatch.setattr(smoke, "MODAL_CALL_STATUS_PROBE_SCRIPT", "import time; time.sleep(5)")
    started = smoke.time.monotonic()

    status = smoke._terminal_call_status(
        "fc-test-123", True, timeout_seconds=0.05,
    )

    assert status == "unknown"
    assert smoke.time.monotonic() - started < 1.0


def test_model_comparison_checks_final_cpu_tensor_inventory_and_values():
    torch = pytest.importorskip("torch")
    resumed = {
        "head.weight": torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
        "head.bias": torch.tensor([0.25]),
    }
    continuous = {
        "head.weight": torch.tensor([[1.0, 2.0], [3.0, 4.0 + 5e-7]]),
        "head.bias": torch.tensor([0.25]),
    }

    result = smoke._compare_model_state_tensors(torch, resumed, continuous)
    assert result["resume_weights_match_continuous"] is True
    assert result["resumed_weight_inventory_sha256"] == result[
        "continuous_weight_inventory_sha256"
    ]
    assert result["resumed_weight_tensor_count"] == 2
    assert result["continuous_weight_tensor_count"] == 2
    assert result["resumed_weight_element_count"] == 5
    assert result["continuous_weight_element_count"] == 5
    assert result["weight_max_abs_difference"] <= smoke.WEIGHT_COMPARISON_ABS_TOL

    excessive = {**continuous, "head.bias": torch.tensor([0.25 + 2e-6])}
    mismatch = smoke._compare_model_state_tensors(torch, resumed, excessive)
    assert mismatch["resume_weights_match_continuous"] is False
    assert mismatch["weight_max_abs_difference"] > smoke.WEIGHT_COMPARISON_ABS_TOL

    with pytest.raises(RuntimeError, match="weight_keys_mismatch"):
        smoke._compare_model_state_tensors(torch, resumed, {"other.weight": resumed["head.weight"]})
    with pytest.raises(RuntimeError, match="weight_shape_or_dtype_mismatch"):
        smoke._compare_model_state_tensors(
            torch, resumed, {**continuous, "head.weight": torch.zeros((5,))},
        )
