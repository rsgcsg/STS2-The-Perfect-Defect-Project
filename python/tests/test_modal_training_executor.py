"""Faithful journal/effect ordering over fake provider, not GPU qualification."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from training_executor_fixtures import app_fixture, prepared_fixture, response_fixture

from spireagent.json_boundary import BoundaryError
from spireagent.modal_training_executor import ModalTrainingExecutor, SdkModalTrainingBackend
from spireagent.training_execution_contracts import (
    CandidateResult,
    KnownFailure,
    NotStarted,
    StopInspection,
    TrainingAppRef,
    TrainingCallHandle,
    TrainingClaim,
    TrainingProviderBudget,
    Unknown,
)


class JournalOwner:
    """Private fake application journal, deliberately surviving object restart."""

    def __init__(self, path):
        self.path = path
        self.fail_app_persist = self.fail_handle_persist = False
        self.available = 120.0
        self.withhold_prepare = self.withhold_submit = False

    def remaining_seconds(self, prepared_id):
        return self.available

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def write(self, value):
        self.path.write_text(json.dumps(value))

    def load_app_ref(self, prepared_id):
        value = self.read().get(prepared_id, {}).get("app_ref")
        return TrainingAppRef.from_dict(value) if value else None

    def claim_preparation(self, attempt):
        value = self.read()
        row = value.setdefault(attempt.identity, {})
        if row.get("prepare_claimed"):
            return TrainingClaim.PRIOR
        if self.withhold_prepare:
            return TrainingClaim.WITHHELD
        row["prepare_claimed"] = True
        self.write(value)
        return TrainingClaim.CLAIMED

    def persist_app_ref(self, attempt, ref):
        if self.fail_app_persist:
            raise OSError("synthetic app persistence failure")
        value = self.read()
        value[attempt.identity]["app_ref"] = ref.to_dict()
        self.write(value)

    def claim_submission_once(self, attempt, ref):
        value = self.read()
        row = value[attempt.identity]
        assert row["app_ref"] == ref.to_dict()
        if row.get("submit_claimed"):
            return TrainingClaim.PRIOR
        if self.withhold_submit:
            return TrainingClaim.WITHHELD
        row["submit_claimed"] = True
        self.write(value)
        return TrainingClaim.CLAIMED

    def load_call_handle(self, prepared_id):
        value = self.read().get(prepared_id, {}).get("handle")
        return TrainingCallHandle.from_dict(value) if value else None

    def persist_call_handle(self, attempt, handle):
        if self.fail_handle_persist:
            raise OSError("synthetic handle persistence failure")
        value = self.read()
        value[attempt.identity]["handle"] = handle.to_dict()
        self.write(value)


class Provider:
    def __init__(self, owner):
        self.owner = owner
        self.ref = self.handle = None
        self.prepares = self.submits = self.stops = self.cancels = 0
        self.lose_prepare = self.lose_submit = False
        self.recovery_available = True
        self.extra_call = False
        self.result = None
        self.tasks, self.containers = 0, 0
        self.cancel_ack = True
        self.prepare_elapsed = self.submit_elapsed = self.poll_elapsed = 0
        self.resolves = 0

    def prepare(self, attempt, *, budget):
        budget.timeout(30)
        assert self.owner.read()[attempt.identity]["prepare_claimed"]
        self.prepares += 1
        self.owner.available = max(0, self.owner.available - self.prepare_elapsed)
        self.ref = app_fixture(attempt)
        if self.lose_prepare:
            raise ConnectionError("accepted prepare response lost")
        return self.ref

    def resolve(self, attempt, *, budget):
        budget.timeout(30)
        self.resolves += 1
        return self.ref

    def submit(self, attempt, ref, request, projection, *, budget):
        budget.require_submission_window(attempt)
        assert self.owner.read()[attempt.identity]["submit_claimed"]
        self.submits += 1
        self.owner.available = max(0, self.owner.available - self.submit_elapsed)
        self.handle = TrainingCallHandle(ref, "fc-fixture")
        if self.lose_submit:
            raise ConnectionError("accepted submit response lost")
        return self.handle

    def poll(self, attempt, handle, *, budget):
        budget.timeout(30)
        self.owner.available = max(0, self.owner.available - self.poll_elapsed)
        assert handle == self.handle
        return self.result

    def recover_calls(self, attempt, ref, *, budget):
        budget.timeout(30)
        if not self.recovery_available:
            return None
        if self.extra_call:
            return (self.handle, TrainingCallHandle(ref, "fc-second"))
        return (self.handle,) if self.handle else ()

    def cancel(self, handle, *, budget):
        budget.timeout(30)
        self.cancels += 1
        return self.cancel_ack

    def stop(self, ref, *, budget):
        budget.timeout(30)
        self.stops += 1
        return True

    def inspect_stop(self, ref, *, budget):
        budget.timeout(30)
        return StopInspection(ref, "stopped", self.tasks, self.containers)


def setup(tmp_path):
    owner = JournalOwner(tmp_path / "original-application-journal.json")
    provider = Provider(owner)
    executor = ModalTrainingExecutor(owner, backend=provider)
    attempt, request, projection = prepared_fixture()
    return owner, provider, executor, attempt, request, projection


def test_prepare_and_submit_durable_before_provider_effect(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    handle = executor.submit_once(attempt, request, projection, ref)
    assert owner.load_app_ref(attempt.identity) == ref
    assert owner.load_call_handle(attempt.identity) == handle
    assert provider.prepares == provider.submits == 1
    assert executor.poll(attempt, handle) is None
    provider.result = response_fixture(attempt)
    assert isinstance(executor.poll(attempt, handle), CandidateResult)
    # Provider/candidate methods have no owner-completion API.
    assert set(owner.read()[attempt.identity]) == {
        "prepare_claimed",
        "app_ref",
        "submit_claimed",
        "handle",
    }


def test_adapter_and_owner_restart_cannot_repeat_lost_submission(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    provider.lose_submit = True
    unknown = executor.submit_once(attempt, request, projection, ref)
    assert isinstance(unknown, Unknown)
    restarted_owner = JournalOwner(owner.path)
    restarted = ModalTrainingExecutor(restarted_owner, backend=provider)
    assert isinstance(restarted.submit_once(attempt, request, projection, ref), Unknown)
    assert provider.submits == 1
    provider.result = response_fixture(attempt)
    result = restarted.reconcile(attempt, ref)
    assert isinstance(result, CandidateResult)
    assert restarted_owner.load_call_handle(attempt.identity) == provider.handle
    assert provider.submits == provider.prepares == 1


def test_prepare_persist_failure_recovery_never_redeploys(tmp_path):
    owner, provider, executor, attempt, _, _ = setup(tmp_path)
    owner.fail_app_persist = True
    assert isinstance(executor.prepare(attempt), Unknown)
    restarted_owner = JournalOwner(owner.path)
    restarted = ModalTrainingExecutor(restarted_owner, backend=provider)
    assert restarted.recover_preparation(attempt) == provider.ref
    assert restarted.prepare(attempt) == provider.ref
    assert provider.prepares == 1


def test_absent_original_app_after_claim_is_unknown_not_new_prepare(tmp_path):
    owner, provider, executor, attempt, _, _ = setup(tmp_path)
    assert owner.claim_preparation(attempt)
    assert isinstance(executor.prepare(attempt), Unknown)
    assert isinstance(executor.recover_preparation(attempt), Unknown)
    assert provider.prepares == 0


def test_lost_handle_or_multiple_provider_calls_remain_unknown(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    owner.fail_handle_persist = True
    assert isinstance(executor.submit_once(attempt, request, projection, ref), Unknown)
    owner.fail_handle_persist = False
    provider.extra_call = True
    assert isinstance(executor.reconcile(attempt, ref), Unknown)
    provider.recovery_available = False
    assert isinstance(executor.reconcile(attempt, ref), Unknown)
    assert provider.submits == 1


def test_cancel_ack_call_terminal_and_app_stop_are_distinct(tmp_path):
    _, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    handle = executor.submit_once(attempt, request, projection, ref)
    assert executor.cancel(attempt, handle)
    provider.result = KnownFailure(attempt.identity, "provider_execution_timeout", handle)
    assert isinstance(executor.poll(attempt, handle), KnownFailure)
    provider.tasks = 1
    assert not executor.stop_and_inspect(attempt, ref).confirmed
    provider.tasks, provider.containers = 0, 1
    assert not executor.stop_and_inspect(attempt, ref).confirmed
    provider.containers = 0
    assert executor.stop_and_inspect(attempt, ref).confirmed


def test_foreign_handle_or_unbound_failure_cannot_end_original(tmp_path):
    _, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    handle = executor.submit_once(attempt, request, projection, ref)
    provider.result = KnownFailure(attempt.identity, "worker_failed")
    assert isinstance(executor.poll(attempt, handle), Unknown)
    with pytest.raises(BoundaryError, match="app_ref_binding_mismatch"):
        executor.cancel(
            attempt, TrainingCallHandle(replace(ref, prepared_id="a" * 64), "fc-foreign")
        )


def test_ordinary_library_constructor_is_lazy_real_backend(tmp_path):
    owner = JournalOwner(tmp_path / "journal.json")
    ordinary = ModalTrainingExecutor(owner)
    assert isinstance(ordinary.backend, SdkModalTrainingBackend)
    assert ordinary.backend._sdk is None


def test_same_app_foreign_call_cannot_be_polled_cancelled_or_reconciled(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    original = executor.submit_once(attempt, request, projection, ref)
    foreign = TrainingCallHandle(ref, "fc-foreign")
    for operation in (
        lambda: executor.poll(attempt, foreign),
        lambda: executor.cancel(attempt, foreign),
        lambda: executor.reconcile(attempt, ref, foreign),
    ):
        with pytest.raises(BoundaryError, match="durable_original_call_handle_required"):
            operation()
    assert provider.cancels == 0
    assert owner.load_call_handle(attempt.identity) == original
    assert provider.submits == 1


def test_matching_attempt_fields_do_not_authorize_stopping_another_app(tmp_path):
    _, provider, executor, attempt, _, _ = setup(tmp_path)
    ref = executor.prepare(attempt)
    foreign = replace(ref, app_id="ap-foreign")
    # Prepared binding contains placement/name, but the owner retains actual App ID.
    foreign.bind(attempt)
    with pytest.raises(BoundaryError, match="durable_original_app_ref_required"):
        executor.stop_and_inspect(attempt, foreign)
    assert provider.stops == 0


def test_withheld_first_prepare_is_known_no_effect_not_prior_reconciliation(tmp_path):
    owner, provider, executor, attempt, _, _ = setup(tmp_path)
    owner.withhold_prepare = True
    result = executor.prepare(attempt)
    assert result == NotStarted(attempt.identity, "prepare", "owner_control_withheld")
    assert provider.prepares == provider.resolves == provider.submits == 0
    assert owner.load_app_ref(attempt.identity) is None


def test_withheld_first_submit_keeps_original_app_and_does_not_resolve_calls(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    owner.withhold_submit = True
    result = executor.submit_once(attempt, request, projection, ref)
    assert result == NotStarted(attempt.identity, "submit", "owner_control_withheld")
    assert provider.submits == provider.resolves == 0
    assert owner.load_app_ref(attempt.identity) == ref
    assert not owner.read()[attempt.identity].get("submit_claimed")
    assert executor.stop_and_inspect(attempt, ref).confirmed


def test_prior_claim_stays_unknown_or_original_recovery_after_control_withheld(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    assert owner.claim_preparation(attempt) is TrainingClaim.CLAIMED
    owner.withhold_prepare = True
    provider.ref = app_fixture(attempt)
    ref = executor.prepare(attempt)
    assert ref == provider.ref and provider.resolves == 1 and provider.prepares == 0
    provider.lose_submit = True
    assert isinstance(executor.submit_once(attempt, request, projection, ref), Unknown)
    owner.withhold_submit = True
    assert isinstance(executor.submit_once(attempt, request, projection, ref), Unknown)
    assert provider.submits == 1


def test_preparation_time_consumes_original_window_before_submit(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    provider.prepare_elapsed = 80
    ref = executor.prepare(attempt)
    assert owner.available == 40  # Original120 minus preparation80; remote window50.
    result = executor.submit_once(attempt, request, projection, ref)
    assert result == NotStarted(attempt.identity, "submit", "submission_window_exhausted")
    assert provider.submits == 0


def test_late_submit_ack_preserves_exact_handle_without_fresh_budget_or_retry(tmp_path):
    owner, provider, executor, attempt, request, projection = setup(tmp_path)
    ref = executor.prepare(attempt)
    provider.submit_elapsed = 90
    result = executor.submit_once(attempt, request, projection, ref)
    assert isinstance(result, Unknown) and result.code == "late_submission_acknowledgement"
    assert result.handle == owner.load_call_handle(attempt.identity) == provider.handle
    assert executor.submit_once(attempt, request, projection, ref) == provider.handle
    assert provider.submits == 1
    owner.available = 0
    assert isinstance(executor.poll(attempt, provider.handle), Unknown)
    assert not executor.stop_and_inspect(attempt, ref).confirmed
    assert provider.stops == 0


def test_real_deploy_runner_timeout_is_original_remaining_not_fixed120(tmp_path, monkeypatch):
    from spireagent import source

    owner, _, _, attempt, _, _ = setup(tmp_path)
    owner.available = 3.0
    observed = []

    def blocked_runner(command, **kwargs):
        observed.append((command, kwargs["timeout"]))
        if command[-2:] == ["token", "info"]:
            owner.available -= .5
            return SimpleNamespace(returncode=0, stdout="Workspace: fixture (ac-fixture)\n")
        assert "deploy" in command
        owner.available = 0
        import subprocess

        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(source, "source_identity", lambda root: attempt.producer)
    executor = ModalTrainingExecutor(owner, backend=SdkModalTrainingBackend(
        command_runner=blocked_runner,
    ))
    result = executor.prepare(attempt)
    assert isinstance(result, Unknown)
    assert [timeout for _, timeout in observed] == [3.0, 2.5]
    assert owner.read()[attempt.identity]["prepare_claimed"]
    assert isinstance(executor.prepare(attempt), Unknown)
    assert len(observed) == 2  # Expired original budget cannot resolve or redeploy.


def test_readonly_source_overrun_is_rechecked_before_any_provider_call(tmp_path, monkeypatch):
    from spireagent import source

    owner, _, _, attempt, _, _ = setup(tmp_path)
    observed = []

    def source_read(root):
        owner.available = 0
        return attempt.producer

    def runner(command, **kwargs):
        observed.append(command)
        raise AssertionError("provider call after original deadline")

    monkeypatch.setattr(source, "source_identity", source_read)
    executor = ModalTrainingExecutor(owner, backend=SdkModalTrainingBackend(command_runner=runner))
    assert executor.prepare(attempt) == NotStarted(attempt.identity, "prepare", "attempt_deadline")
    assert observed == []


def test_each_cli_lookup_consumes_same_dynamic_budget_without_reset():
    remaining = [3.0]
    observed = []

    def runner(command, **kwargs):
        observed.append(kwargs["timeout"])
        remaining[0] -= 2
        return SimpleNamespace(returncode=0, stdout="[]")

    backend = SdkModalTrainingBackend(command_runner=runner)
    budget = TrainingProviderBudget(lambda: max(0, remaining[0]))
    assert backend._json(["app", "list"], budget=budget) == []
    with pytest.raises(BoundaryError, match="attempt_deadline"):
        backend._json(["container", "list"], budget=budget)
    with pytest.raises(BoundaryError, match="attempt_deadline"):
        backend._json(["app", "list"], budget=budget)
    assert observed == [3.0, 1.0]


def test_late_sdk_spawn_handle_is_persisted_then_fenced_without_resubmission(tmp_path):
    owner, _, _, attempt, request, projection = setup(tmp_path)
    ref = app_fixture(attempt)
    owner.claim_preparation(attempt)
    owner.persist_app_ref(attempt, ref)
    owner.available = 53
    spawned = []

    async def hydrate():
        pass

    async def spawn(*args):
        owner.available = 49
        spawned.append(args)
        return SimpleNamespace(object_id="fc-fixture")

    function = SimpleNamespace(
        object_id=ref.function_id,
        _get_metadata=lambda: SimpleNamespace(app_id=ref.app_id),
        hydrate=SimpleNamespace(aio=hydrate), spawn=SimpleNamespace(aio=spawn),
    )
    sdk = SimpleNamespace(Function=SimpleNamespace(from_name=lambda *args, **kwargs: function))

    class OriginalBackend(SdkModalTrainingBackend):
        def resolve(self, selected, *, budget):
            budget.timeout(30)
            return ref

    executor = ModalTrainingExecutor(owner, backend=OriginalBackend(sdk=sdk))
    result = executor.submit_once(attempt, request, projection, ref)
    assert isinstance(result, Unknown) and result.code == "late_submission_acknowledgement"
    assert owner.load_call_handle(attempt.identity) == result.handle
    assert executor.submit_once(attempt, request, projection, ref) == result.handle
    assert len(spawned) == 1
