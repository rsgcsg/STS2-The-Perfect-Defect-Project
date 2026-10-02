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


def make_plan() -> smoke.SmokePlan:
    return smoke.build_plan(
        image=IMAGE,
        environment="existing-dev",
        source_revision=SHA40,
        uv_lock_sha256=SHA64,
        harness_sha256=SHA64,
        run_id="d" * 32,
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
        "resume_matches_continuous": True,
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


class FakeCall:
    def __init__(self, result: object, *, pending: bool = False) -> None:
        self.object_id = "fc-test-123"
        self.result = result
        self.pending = pending
        self.get_timeouts: list[float] = []
        self.cancel_options: list[dict[str, bool]] = []

    def get(self, *, timeout: float) -> object:
        self.get_timeouts.append(timeout)
        if self.pending:
            raise TimeoutError("still running")
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
        self.apps: list[FakeApp] = []
        self.modal = SimpleNamespace(
            Environment=SimpleNamespace(objects=SimpleNamespace(list=self._list_envs)),
            Image=SimpleNamespace(from_registry=self._from_registry),
            App=self._make_app,
            exception=SimpleNamespace(RemoteError=FakeRemoteError),
        )

    def _list_envs(self) -> list[SimpleNamespace]:
        self.listed_environments += 1
        return [SimpleNamespace(name="existing-dev")]

    @staticmethod
    def _from_registry(reference: str) -> object:
        assert reference == IMAGE
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

    def cli_json(args: list[str], *, timeout_seconds: int) -> object:
        assert 0 < timeout_seconds <= smoke.MAX_CLEANUP_COMMAND_SECONDS
        if args[:2] == ["app", "list"]:
            return [{"app_id": "ap-test-123", "state": app_state, "tasks": app_tasks}]
        if args[:2] == ["container", "list"]:
            return [] if containers is None else containers
        pytest.fail(f"unexpected Modal CLI command: {args}")

    monkeypatch.setattr(smoke, "_modal_cli_json", cli_json)
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
        ("device_name", "NVIDIA A10", "actual_l4_device_required"),
        ("first_loss", float("nan"), "first_loss_must_be_finite_number"),
        ("resume_matches_continuous", False, "resume_matches_continuous_must_be_true"),
        ("resume_loss_abs_error", 1.0, "resume_continuous_comparison_failed"),
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
    assert error.value.outcome["call_terminal"] == "pending"
    assert error.value.outcome["stop_confirmation"] == "confirmed"
    assert error.value.outcome["status"] == "unknown"
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
    class MissingEnvironmentManager:
        @staticmethod
        def list() -> list[object]:
            return []

    modal = SimpleNamespace(
        Environment=SimpleNamespace(objects=MissingEnvironmentManager()),
    )
    with pytest.raises(smoke.PlanError, match="existing_modal_environment_not_found"):
        smoke._preflight_environment(modal, "existing-dev")


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
