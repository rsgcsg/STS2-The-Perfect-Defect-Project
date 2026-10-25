"""Normal client prepare/package/start avoids repeated fitting, keeps original intent."""
import json

import pytest
from test_remote_training_http import client, settled

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.training_cli import main
from spireagent.workbench.training_package import package_start
from stpd.p1.spec import MStdConfig
from stpd.p1.weighting import CHECKPOINT_POLICY, SCHEMA
from stpd.p1.workload import verify_p1_run


def test_normal_package_then_start_original_prepared_run(tmp_path, capsys, monkeypatch):
    with client(tmp_path) as (app, call, provider, store, owner, snapshot, config_path):
        from stpd.p1.dataset import SnapshotReader

        selection = SnapshotReader(store, snapshot.artifact_id).selection
        roots = {p["root_run_id"] for p in selection["points"] if p["split"] == "train"}
        body = {"intent_id": "b" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=2).to_dict(), "max_seconds": 300,
                "stop_after": 1, "checkpoint_policy": CHECKPOINT_POLICY,
                "loss_weighting": {"schema": SCHEMA, "rule_id": "unit-control",
                                   "weights_by_root": dict.fromkeys(roots, 1)}}
        request = tmp_path / "request.json"
        package = tmp_path / "package.json"
        request.write_text(json.dumps(body))
        assert main(["package", "--config", str(config_path), "--execution", "remote",
                     "--input", str(request), "--output", str(package)]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["provider_submitted"] is False
        assert provider.calls == []
        saved = json.loads(package.read_text())
        run_id = saved["start"]["run_id"]
        assert verify_p1_run(store, run_id, provider.target.producer).training.parameters.value()[
            "loss_weighting"] == body["loss_weighting"]
        # Poison prepare: a start of this package must not fit/transfer again.
        monkeypatch.setattr(app.remote_training, "prepare",
                            lambda *a: pytest.fail("prepared run was rebuilt"))
        provider.ready = True
        assert main(["start", "--config", str(config_path), "--execution", "remote",
                     "--input", str(package)]) == 0
        started = json.loads(capsys.readouterr().out)["jobs"][0]
        assert started["input_id"] == run_id
        paused = settled(call, started["id"], "paused")
        assert paused["result"]["checkpoint_id"]
        assert len(provider.calls) == 1
        # Mismatching the original purpose-use intent fails before any submission.
        with pytest.raises(BoundaryError, match="prepared_run_intent_mismatch"):
            call("start-prepared", body={**saved["start"], "intent_id": "c" * 32})
        assert len(provider.calls) == 1
        saved["start"]["stop_after"] = 8
        with pytest.raises(BoundaryError, match="prepared_package_digest_mismatch"):
            package_start(saved)
        assert main(["package", "--config", str(config_path), "--execution", "remote",
                     "--input", str(request), "--output", str(package)]) == 1
        assert json.loads(capsys.readouterr().out)["error_code"] == (
            "new_remote_package_output_required")


def test_prepare_before_allocation_has_no_target_or_provider_call(tmp_path, monkeypatch):
    from dataclasses import replace

    from spireagent.workbench.remote_training import RemoteTrainingService

    with client(tmp_path) as (app, _call, provider, store, _owner, snapshot, _path):
        config = replace(app.config, remote_compute=replace(
            app.config.remote_compute,
            compute_store={"kind": "s3", "bucket": "test-bucket", "prefix": "test",
                           "endpoint": "https://example.invalid", "region": "auto"},
            execution_producer=provider.target.producer))
        service = RemoteTrainingService(config, app.local_datasets._selected)
        monkeypatch.setattr(service, "_context", lambda: pytest.fail("provider was constructed"))
        monkeypatch.setattr("spireagent.workbench.remote_training_storage.transfer_p1_input",
                            lambda *a, **k: {"provider_submitted": False})
        service.start_loop()
        assert service._thread is None
        prepared = service.prepare({"intent_id": "d" * 32, "source_id": snapshot.artifact_id,
                                    "config": MStdConfig(max_epochs=2).to_dict(),
                                    "max_seconds": 300, "stop_after": 1})
        assert prepared["compute_target_bound"] is False
        assert prepared["provider_submitted"] is False
        assert provider.calls == []
        assert store.get_manifest(prepared["run_id"]).producer == provider.target.producer


def test_cohort_binds_exact_split_membership_counts_and_definition(tmp_path):
    from collections import Counter
    from copy import deepcopy

    from spireagent.workbench.training_cohort import cohort_weighting
    from stpd.p1.data import digest, json_bytes
    from stpd.p1.dataset import SnapshotReader

    with client(tmp_path) as (app, _call, _provider, store, _owner, snapshot, _path):
        selection = SnapshotReader(store, snapshot.artifact_id).selection
        counts = Counter((p["root_run_id"], p["group_id"], p["split"])
                         for p in selection["points"])
        body = {"schema": "stpd/p1-cohort-plan-1", "definition": {"rule": "fixture-v1"},
                "sources": selection["sources"], "selection": selection["selection"],
                "splits": selection["split_by_group"],
                "runs": [{"root_run_id": r, "group_id": g, "split": s,
                          "N": n, "train_weight": 3 if s == "train" else None}
                         for (r, g, s), n in counts.items()]}
        identity = digest(json_bytes(body))
        plan = {**body, "configuration_id": identity}
        weighting = cohort_weighting(app.config, plan, identity, snapshot.artifact_id)
        assert weighting["rule_id"] == identity
        assert weighting["definition"] == body["definition"]
        assert set(weighting["weights_by_root"].values()) == {3.0}
        for key in ("definition", "splits"):
            changed = deepcopy(plan)
            changed[key]["changed"] = "changed"
            with pytest.raises(BoundaryError, match="exact_cohort_configuration_required"):
                cohort_weighting(app.config, changed, identity, snapshot.artifact_id)
        changed_body = deepcopy(body)
        changed_body["runs"][0]["N"] += 1
        new_id = digest(json_bytes(changed_body))
        with pytest.raises(BoundaryError, match="cohort_snapshot_run_counts_mismatch"):
            cohort_weighting(app.config, {**changed_body, "configuration_id": new_id},
                             new_id, snapshot.artifact_id)


@pytest.mark.parametrize("already_frozen", [False, True])
def test_selected_cohort_reuses_Q_snapshot_or_uses_normal_freeze(tmp_path, capsys, already_frozen):
    from collections import Counter

    from stpd.p1.data import digest, json_bytes
    from stpd.p1.dataset import SnapshotReader

    with client(tmp_path) as (app, _call, provider, store, _owner, snapshot, config_path):
        selected = SnapshotReader(store, snapshot.artifact_id).selection
        counts = Counter((p["root_run_id"], p["group_id"], p["split"])
                         for p in selected["points"])
        plan = {"schema": "stpd/p1-cohort-plan-1", "definition": {"rule": "fixture-v1"},
                "sources": selected["sources"], "selection": selected["selection"],
                "splits": selected["split_by_group"],
                "runs": [{"root_run_id": r, "group_id": g, "split": s,
                          "N": n, "train_weight": 2 if s == "train" else None}
                         for (r, g, s), n in counts.items()]}
        identity = digest(json_bytes(plan))
        plan["configuration_id"] = identity
        envelope = {"schema": "training-data-weight-selection-20261025-v1",
                    "status": "frozen_user_confirmed", "selected_configuration_id": identity,
                    "configuration": plan}
        if already_frozen:
            envelope["snapshot_id"] = snapshot.artifact_id
        cohort = tmp_path / "cohort.json"
        cohort.write_text(json.dumps(envelope))
        request = tmp_path / "recipe.json"
        request.write_text(json.dumps({"intent_id": "e" * 32,
                                       "config": MStdConfig(max_epochs=2).to_dict(),
                                       "max_seconds": 300, "stop_after": None}))
        output = tmp_path / "ready.json"
        assert main(["package", "--config", str(config_path), "--execution", "remote",
                     "--input", str(request), "--cohort-plan", str(cohort),
                     "--configuration-id", identity, "--output", str(output)]) == 0
        assert json.loads(capsys.readouterr().out)["provider_submitted"] is False
        package = json.loads(output.read_text())
        run = verify_p1_run(store, package["start"]["run_id"], provider.target.producer)
        assert (run.snapshot.artifact_id == snapshot.artifact_id) is already_frozen
        assert run.reader.selection["split_by_group"] == plan["splits"]
        assert run.reader.selection["split_salt"] == (
            selected["split_salt"] if already_frozen else "cohort-" + identity)
        assert run.training.parameters.value()["loss_weighting"]["rule_id"] == identity
        assert run.training.parameters.value()["loss_weighting"]["definition"] == plan["definition"]
        assert provider.calls == []


@pytest.mark.parametrize("value", [None, [], {}, {"schema": "stpd/p1-cohort-plan-1"}])
def test_malformed_cohort_is_a_typed_boundary_error(value):
    from spireagent.workbench.training_cohort import checked_cohort_plan

    with pytest.raises(BoundaryError):
        checked_cohort_plan(value, "a" * 64)


def test_existing_output_rejects_before_any_cohort_freeze(tmp_path, monkeypatch):
    from spireagent.workbench.training_package import prepare_package

    output = tmp_path / "existing.json"
    output.write_text("original")
    monkeypatch.setattr("spireagent.workbench.training_cohort.freeze_cohort",
                        lambda *a: pytest.fail("output rejected after freezing"))
    with pytest.raises(BoundaryError, match="new_remote_package_output_required"):
        prepare_package(tmp_path / "no-config-needed.json", {}, output, "remote",
                        cohort_plan={}, configuration_id="a" * 64)
    assert output.read_text() == "original"


def test_invalid_weight_or_checkpoint_policy_does_not_record_training_use(tmp_path, monkeypatch):
    with client(tmp_path) as (app, call, _provider, _store, _owner, snapshot, _path):
        body = {"intent_id": "f" * 32, "source_id": snapshot.artifact_id,
                "config": MStdConfig(max_epochs=2).to_dict(), "max_seconds": 300,
                "stop_after": 1}
        monkeypatch.setattr("spireagent.workbench.p1_training_data.record_p1_training_use",
                            lambda *a: pytest.fail("invalid recipe recorded use"))
        with pytest.raises(BoundaryError):
            call("prepare", body={**body, "loss_weighting": None})
        with pytest.raises(BoundaryError, match="unsupported_checkpoint_policy"):
            call("prepare", body={**body, "checkpoint_policy": []})


@pytest.mark.parametrize("wait_seconds,expected", [(None, 600), (7200, 7200)])
def test_freeze_transport_wait_is_explicit_and_single_offer(monkeypatch, wait_seconds, expected):
    from spireagent.workbench import p1_data_cli

    calls = []
    monkeypatch.setattr(p1_data_cli, "running",
                        lambda config: {"port": 12345, "control_token": "fixture-token"})

    def exchange(url, **kwargs):
        calls.append((url, kwargs))
        return {"purpose_reserved": True}

    monkeypatch.setattr(p1_data_cli, "_local_request", exchange)
    result = p1_data_cli.p1_data_command(None, "freeze", {}, wait_seconds=wait_seconds)
    assert result == {"purpose_reserved": True}
    assert len(calls) == 1 and calls[0][1]["timeout"] == expected
    assert calls[0][1]["body"] == {} and calls[0][1]["method"] == "POST"

    def lost_response(url, **kwargs):
        calls.append((url, kwargs))
        raise OSError("fixture response lost")

    monkeypatch.setattr(p1_data_cli, "_local_request", lost_response)
    with pytest.raises(BoundaryError, match="request_failed_inspect_snapshot_no_automatic_retry"):
        p1_data_cli.p1_data_command(None, "freeze", {}, wait_seconds=wait_seconds)
    assert len(calls) == 2  # One new explicit offer, never an implicit second send.


@pytest.mark.parametrize("wait_seconds", [True, 0, 86401, 1.5, "7200"])
def test_invalid_freeze_wait_fails_before_opening_or_offering(monkeypatch, wait_seconds):
    from spireagent.workbench import p1_data_cli

    monkeypatch.setattr(p1_data_cli, "running",
                        lambda *a: pytest.fail("invalid wait reached owner"))
    with pytest.raises(BoundaryError, match="invalid_transport_wait_seconds"):
        p1_data_cli.p1_data_command(None, "freeze", {}, wait_seconds=wait_seconds)
