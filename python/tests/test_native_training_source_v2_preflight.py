"""Application parent admission to the common recorded-source recipe stays pure."""

from __future__ import annotations

import copy
import io
from dataclasses import replace
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_native_agent_sampled_source import PROJECTOR, publish
from test_native_agent_sampled_source import original as original
from test_native_training_source_v2 import source3
from test_native_workbench_api import native_http as native_http
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.replaceable_file import write_replaceable_json
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_training import LOCK_FILE, LocalTrainingService
from spireagent.workbench.native_workbench_api import COMMAND_SCHEMA, PREFIX
from spireagent.workbench.recipe_contracts import TrainingRequest, validate_limits
from spireagent.workbench.recipes.structured import (
    StructuredRecipeAdapter,
    verify_run_execution_policy,
)
from spireagent.workbench.recipes.structured_child import (
    FencedSlots,
    FencedStore,
    ReadOnlyAttemptFence,
)
from spireagent.workbench.trusted_recipes import (
    STRUCTURED_RECIPE,
    describe_recipe,
    structured_recipe_run_schema,
    validate_recipe_config,
)
from stpd.fullrun.ordered_source import OrderedSourceRef, publish_ordered_source_partition
from stpd.native_training_source_spec import RECIPE
from stpd.ordered_source_spec import DEFAULT_RECIPE as FULL_REFERENCE_RECIPE
from stpd.ordered_source_spec import PRETRAIN_VIEW, SAMPLED_RECIPE
from stpd.ordered_source_spec import RECIPES as ORDERED_RECIPE_CONFIGS
from stpd.policy.native_operational_outcome import owned_current_known_stale_policy


@pytest.mark.parametrize("which", ["direct", "source3"])
def test_parent_requires_exact_prior_train_reservation(tmp_path, original, which):
    store, owner = setup_store(tmp_path)
    partition = publish(store, original)[2] if which == "direct" else source3(store)
    adapter = StructuredRecipeAdapter(RECIPE)
    with pytest.raises(BoundaryError, match="source_not_reserved_for_training"):
        adapter.preflight(store, owner, partition.manifest.artifact_id)
    reserve = (
        owner.reserve_verified_native_agent_sampled_source if which == "direct"
        else owner.reserve_verified_ordered_source
    )
    reserve(store, partition.manifest.artifact_id)
    assert adapter.preflight(store, owner, partition.manifest.artifact_id) == partition.dataset
    with owner.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_source_uses").fetchone() == (0,)


def test_common_parent_does_not_admit_Source3_heldout_partition(tmp_path):
    store, owner = setup_store(tmp_path)
    train = source3(store)
    refs = tuple(OrderedSourceRef(**ref) for ref in train.manifest.parameters.value()["raw_refs"])
    heldout = publish_ordered_source_partition(store, refs, "dev", PROJECTOR)
    with pytest.raises(BoundaryError, match="sampled_train_partition_required"):
        StructuredRecipeAdapter(RECIPE).preflight(store, owner, heldout.manifest.artifact_id)


def _policy_metadata(tmp_path, monkeypatch, *, policy=True):
    """Real journal/store owners with metadata-only Runs, never numerical admission."""
    tmp_path.mkdir(exist_ok=True)
    store, owner = setup_store(tmp_path)
    source = Manifest("dataset", PROJECTOR)
    store.publish(source)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalTrainingService(config)
    # These metadata-only records declare a synthetic producer. The separate
    # actual private-child gate retains the real clean-checkout identity owner.
    monkeypatch.setattr("spireagent.workbench.local_training.source_identity", lambda _: PROJECTOR)
    monkeypatch.setattr(service, "_selected", lambda: (owner, store, tmp_path / "registry"))
    request = TrainingRequest(
        "1" * 32, RECIPE, source.artifact_id, validate_recipe_config(RECIPE, {}),
        limits=validate_limits({"wall_seconds": 600}),
        execution_policy=owned_current_known_stale_policy() if policy else None,
    )
    journal = service._new_operation(request, owner, "a" * 32, previous={"status": "idle"})
    journal["use_state"] = "reserved"
    path, _ = service._paths(owner)
    write_replaceable_json(path, journal)
    return service, request, store, journal, path


def _policy_run(store, journal, *, policy=True, seed=0):
    training = Manifest("training_input", PROJECTOR,
                        (Parent("source", journal["dataset_id"]),))
    store.publish(training)
    run = Manifest("run", PROJECTOR, (Parent("training_input", training.artifact_id),),
                   parameters=FrozenObject.of({
                       "schema": structured_recipe_run_schema(journal["recipe"]),
                       "operation_id": journal["operation_id"], "config": {"seed": seed},
                       **({"execution_policy": owned_current_known_stale_policy()}
                          if policy else {}),
                   }))
    store.publish(run)
    return run


@pytest.mark.parametrize("recipe", [RECIPE, SAMPLED_RECIPE])
def test_request_optional_policy_is_closed_copied_and_separate_from_config(recipe):
    request = TrainingRequest("1" * 32, recipe, "2" * 64, {})
    legacy = request.to_dict()
    assert "execution_policy" not in legacy
    assert TrainingRequest.from_dict(legacy).to_dict() == legacy
    declaration = owned_current_known_stale_policy()
    opted_in = replace(request, execution_policy=declaration)
    declaration["max_known_stale_rejections"] = 7
    wire = opted_in.to_dict()
    assert wire["execution_policy"] == owned_current_known_stale_policy()
    decoded = TrainingRequest.from_dict(wire)
    wire["execution_policy"]["max_known_stale_rejections"] = 6
    assert decoded.to_dict()["execution_policy"] == owned_current_known_stale_policy()
    assert decoded.config == request.config
    descriptor = describe_recipe(recipe)
    assert descriptor["execution_policy"]["default"] is None
    assert descriptor["execution_policy"]["example"] == owned_current_known_stale_policy()
    assert "execution_policy" not in descriptor["config_defaults"]


@pytest.mark.parametrize("change", ["null", "bool", "zero", "mode", "extra", "recipe"])
def test_invalid_request_policy_refuses_before_state_selection(tmp_path, monkeypatch, change):
    service = LocalTrainingService(ProjectConfig(tmp_path, "", "", None, combination()))
    monkeypatch.setattr(service, "_selected", lambda: pytest.fail("state was selected"))
    request = TrainingRequest("1" * 32, RECIPE, "2" * 64, {}).to_dict()
    policy = owned_current_known_stale_policy()
    if change == "null":
        policy = None
    elif change == "bool":
        policy["max_known_stale_rejections"] = True
    elif change == "zero":
        policy["max_consecutive_known_stale_rejections"] = 0
    elif change == "mode":
        policy["current_mode"] = "invented"
    elif change == "extra":
        policy["callback"] = "invented"
    else:
        request["recipe_id"] = STRUCTURED_RECIPE
    request["execution_policy"] = policy
    with pytest.raises(BoundaryError):
        service.start(request)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("recipe", [
    STRUCTURED_RECIPE, FULL_REFERENCE_RECIPE,
    next(recipe for recipe, (_, _, view) in ORDERED_RECIPE_CONFIGS.items()
         if view == PRETRAIN_VIEW),
])
def test_non_sampled_recipe_cannot_opt_in_before_state_selection(tmp_path, monkeypatch, recipe):
    service = LocalTrainingService(ProjectConfig(tmp_path, "", "", None, combination()))
    monkeypatch.setattr(service, "_selected", lambda: pytest.fail("state was selected"))
    request = TrainingRequest("1" * 32, recipe, "2" * 64, {}).to_dict()
    with pytest.raises(BoundaryError, match="invalid_training_request"):
        service.start({**request, "execution_policy": owned_current_known_stale_policy()})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("policy", [False, True])
def test_snapshot_and_same_intent_preserve_exact_policy(tmp_path, monkeypatch, policy):
    service, request, _store, journal, path = _policy_metadata(
        tmp_path, monkeypatch, policy=policy)
    journal.update(status="failed", writer_terminal=True)
    write_replaceable_json(path, journal)
    first = service.start(request)["operation"]
    assert ("execution_policy" in first) is policy
    if policy:
        first["execution_policy"]["max_known_stale_rejections"] = 7
        assert service.status()["operation"]["execution_policy"] == request.execution_policy
    changed = replace(request, execution_policy=(
        {**owned_current_known_stale_policy(), "max_known_stale_rejections": 7}
        if policy else owned_current_known_stale_policy()))
    before = path.read_bytes()
    with pytest.raises(BoundaryError, match="intent_payload_mismatch"):
        service.start(changed)
    assert path.read_bytes() == before


@pytest.mark.parametrize("policy", [False, True])
def test_browser_native_existing_routes_expose_policy_without_starting_worker(
    tmp_path, monkeypatch, native_http, policy,
):
    app, call, pair, _root, _secret, _peer = native_http
    service, request, _store, journal, path = _policy_metadata(
        tmp_path / "research", monkeypatch, policy=policy)
    journal.update(status="failed", writer_terminal=True)
    write_replaceable_json(path, journal)
    app.local_training = service
    browser_headers = {"Cookie": f"{app.account.cookie_name}={app.account.cookie}",
                       "Content-Type": "application/json", "Origin": pair.workbench_url[:-1],
                       "X-CSRF-Token": app.account.csrf}
    browser = call("/api/local-training/start", request.to_dict(), headers=browser_headers)
    assert ("execution_policy" in browser["operation"]) is policy
    ordinary = call("/api/local-training/status", headers=browser_headers)
    assert ordinary["operation"].get("execution_policy") == request.execution_policy
    result = call(PREFIX + "/actions/training.start", {
        "schema": COMMAND_SCHEMA, "request_id": "9" * 32, "payload": request.to_dict(),
    })
    assert result["status"] == "accepted", result
    assert result["owner_response"]["operation"].get("execution_policy") == request.execution_policy
    native = call(PREFIX + "/view?page=training")
    card = next(card for card in native["cards"] if card["owner"] == "training")
    assert ("execution_policy" in card["data"]["operation"]) is policy
    assert service._thread is None
    with pytest.raises(HTTPError) as denied:
        call("/api/local-training/start", {**request.to_dict(), "execution_policy": None},
             headers=browser_headers)
    assert denied.value.code == 409
    invalid = call(PREFIX + "/actions/training.start", {
        "schema": COMMAND_SCHEMA, "request_id": "8" * 32,
        "payload": {**request.to_dict(), "execution_policy": None},
    })
    assert invalid["status"] == "rejected"
    assert invalid["error"]["code"] == "invalid_training_request"
    assert service._thread is None


def test_run_policy_mismatch_blocks_parent_ack_orphan_adoption_and_resume(tmp_path, monkeypatch):
    service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    run = _policy_run(store, journal, policy=False)
    before = path.read_bytes()
    with pytest.raises(BoundaryError, match="training_execution_policy_mismatch"):
        verify_run_execution_policy(run, journal)
    with pytest.raises(BoundaryError, match="training_execution_policy_mismatch"):
        service._resolve_prepared_run(store, journal)
    assert path.read_bytes() == before
    journal.update(mode="resume", run_id=run.artifact_id, input_id=run.parent("training_input"))
    write_replaceable_json(path, journal)
    with pytest.raises(BoundaryError, match="attempt_run_binding_mismatch"):
        ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    checkpoint = Manifest("checkpoint", PROJECTOR,
                          (Parent("run", run.artifact_id),
                           Parent("training_input", run.parent("training_input"))))
    store.publish(checkpoint)
    journal.update(mode="start", status="paused", writer_terminal=True,
                   checkpoint_id=checkpoint.artifact_id)
    (path.parent / LOCK_FILE).touch()
    service._prepare_child_ownership(path, journal)
    write_replaceable_json(path, journal)
    before = path.read_bytes()
    for mode in ("resume", "reconcile"):
        with pytest.raises(BoundaryError, match="training_execution_policy_mismatch"):
            if mode == "resume":
                service.resume(journal["operation_id"], journal["attempt_id"],
                               checkpoint.artifact_id, "2" * 32, journal["request"]["limits"])
            else:
                service.reconcile(journal["operation_id"], journal["attempt_id"])
        assert path.read_bytes() == before


@pytest.mark.parametrize("phase", ["preparation", "bound"])
@pytest.mark.parametrize("role", ["payload", "manifest", "slot"])
def test_none_reservation_poll_cannot_adopt_changed_policy_or_run(
    tmp_path, monkeypatch, phase, role,
):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    if phase == "bound":
        run = _policy_run(store, journal)
        fence.bind_run(run)
        journal.update(run_id=run.artifact_id, input_id=run.parent("training_input"))
        write_replaceable_json(path, journal)
        fence.wait_for_parent_run(run.artifact_id, run.parent("training_input"))
        foreign = _policy_run(store, journal, seed=1)

    def change_at_ack(kind, **details):
        assert kind == "reserve_artifact"
        journal["artifact_reservation"] = {"attempt_id": journal["attempt_id"], **details}
        if phase == "preparation":
            journal["request"]["execution_policy"]["max_known_stale_rejections"] = 7
        else:
            journal["run_id"] = foreign.artifact_id
        write_replaceable_json(path, journal)

    fence.channel = SimpleNamespace(emit=change_at_ack)
    delegates = []
    monkeypatch.setattr(store, "put_payload", lambda *a, **k: delegates.append("payload"))
    monkeypatch.setattr(store, "publish", lambda *a, **k: delegates.append("manifest"))
    monkeypatch.setattr(store.blobs, "put_if_absent", lambda *a, **k: delegates.append("slot"))
    with pytest.raises(BoundaryError, match=("attempt_execution_selection_mismatch"
                                            if phase == "preparation"
                                            else "attempt_run_binding_mismatch")):
        if role == "payload":
            FencedStore(store, fence).put_payload("payload", io.BytesIO(b"x"), "text/plain")
        elif role == "manifest":
            FencedStore(store, fence).publish(Manifest("dataset", PROJECTOR))
        else:
            FencedSlots(store.blobs, fence).put_if_absent("test-slot", b"x")
    assert delegates == []


@pytest.mark.parametrize("change", ["policy", "foreign_run", "partial"])
def test_prepared_ack_poll_checks_frozen_selection(tmp_path, monkeypatch, change):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    run = _policy_run(store, journal)
    foreign = _policy_run(store, journal, seed=1)
    fence.bind_run(run)
    with pytest.raises(BoundaryError, match="prepared_run_ack_required"):
        fence.assert_publication_current()

    def changed(_seconds):
        if change == "policy":
            journal["request"]["execution_policy"]["max_known_stale_rejections"] = 7
        elif change == "foreign_run":
            journal.update(run_id=foreign.artifact_id, input_id=foreign.parent("training_input"))
        else:
            journal["run_id"] = run.artifact_id
        write_replaceable_json(path, journal)

    monkeypatch.setattr("spireagent.workbench.recipes.structured_child.time.sleep", changed)
    with pytest.raises(BoundaryError):
        fence.wait_for_parent_run(run.artifact_id, run.parent("training_input"))


def test_ack_fixes_one_run_and_none_calls_cannot_reopen_missing_pair(tmp_path, monkeypatch):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    run = _policy_run(store, journal)
    fence.bind_run(run)
    with pytest.raises(BoundaryError, match="attempt_run_already_bound"):
        fence.bind_run(run)
    def acknowledge(_seconds):
        journal.update(run_id=run.artifact_id, input_id=run.parent("training_input"))
        write_replaceable_json(path, journal)

    monkeypatch.setattr("spireagent.workbench.recipes.structured_child.time.sleep", acknowledge)
    fence.wait_for_parent_run(run.artifact_id, run.parent("training_input"))
    fence.assert_current(None, journal["operation_id"], journal["attempt_id"])
    journal.pop("run_id")
    journal.pop("input_id")
    write_replaceable_json(path, journal)
    with pytest.raises(BoundaryError, match="attempt_run_binding_mismatch"):
        fence.assert_current(None, journal["operation_id"], journal["attempt_id"])


def test_frozen_choice_is_not_rederived_from_later_journal_reads(tmp_path, monkeypatch):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    returned = fence.execution_policy
    returned["max_known_stale_rejections"] = 7
    assert fence.execution_policy == owned_current_known_stale_policy()
    journal["request"] = copy.deepcopy(journal["request"])
    journal["request"]["execution_policy"]["max_known_stale_rejections"] = 7
    write_replaceable_json(path, journal)
    with pytest.raises(BoundaryError, match="attempt_execution_selection_mismatch"):
        fence.operation()


@pytest.mark.parametrize("mode", ["resume", "reconcile"])
def test_recovered_child_has_one_fixed_run_before_any_numerical_import(tmp_path, monkeypatch, mode):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    run = _policy_run(store, journal)
    foreign = _policy_run(store, journal, seed=1)
    journal.update(mode=mode, run_id=run.artifact_id, input_id=run.parent("training_input"))
    write_replaceable_json(path, journal)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)
    fence.assert_current(run.artifact_id, journal["operation_id"], journal["attempt_id"])
    journal["run_id"] = foreign.artifact_id
    write_replaceable_json(path, journal)
    with pytest.raises(BoundaryError, match="attempt_run_binding_mismatch"):
        fence.assert_current(None, journal["operation_id"], journal["attempt_id"])


@pytest.mark.parametrize("role", ["payload", "manifest", "slot"])
def test_checked_reservation_ack_cannot_bypass_immediate_prewrite_guard(
    tmp_path, monkeypatch, role,
):
    _service, _request, store, journal, path = _policy_metadata(tmp_path, monkeypatch)
    fence = ReadOnlyAttemptFence(path, journal["operation_id"], journal["attempt_id"], 10, store)

    def acknowledge(kind, **details):
        assert kind == "reserve_artifact"
        journal["artifact_reservation"] = {"attempt_id": journal["attempt_id"], **details}
        write_replaceable_json(path, journal)

    fence.channel = SimpleNamespace(emit=acknowledge)
    reserve = fence.reserve_artifact

    def after_ack(selected_role, size):
        reserve(selected_role, size)
        journal["request"]["execution_policy"]["max_known_stale_rejections"] = 7
        write_replaceable_json(path, journal)

    monkeypatch.setattr(fence, "reserve_artifact", after_ack)
    delegates = []
    monkeypatch.setattr(store, "put_payload", lambda *a, **k: delegates.append("payload"))
    monkeypatch.setattr(store, "publish", lambda *a, **k: delegates.append("manifest"))
    monkeypatch.setattr(store.blobs, "put_if_absent", lambda *a, **k: delegates.append("slot"))
    with pytest.raises(BoundaryError, match="attempt_execution_selection_mismatch"):
        if role == "payload":
            FencedStore(store, fence).put_payload("payload", io.BytesIO(b"x"), "text/plain")
        elif role == "manifest":
            FencedStore(store, fence).publish(Manifest("dataset", PROJECTOR))
        else:
            FencedSlots(store.blobs, fence).put_if_absent("test-slot", b"x")
    assert delegates == []
