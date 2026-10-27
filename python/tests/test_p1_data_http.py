"""Normal authenticated Application/CLI maps to the same P1 Store/Curation owner."""

from __future__ import annotations

import copy
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from test_p1_snapshot import projection_fixture
from test_remote_training_http import client

from spireagent.workbench.developer_cli import main
from spireagent.workbench.p1_data_cli import p1_data_command
from stpd.p1.dataset import iter_snapshot_points


def test_normal_data_prepare_freeze_increment_and_fixed_reader(tmp_path, capsys):
    with client(tmp_path, reserve=False, human_cohort=True) as items:
        app, _call, _provider, store, owner, _snapshot, path = items
        request = {"allocation_policy": "pool"}
        first = p1_data_command(app.config, "prepare", request)
        assert first["cache"]["decoded_projections"] == 3
        assert not first["ready_to_freeze"] and not first["purpose_reserved"]
        assert set(first["allocation"]["split_by_group"].values()) == {"pool"}
        preparation = tmp_path / "prepare.json"
        preparation.write_text(json.dumps(request))
        assert (
            main(
                [
                    "p1-data",
                    "--config",
                    str(path),
                    "--action",
                    "prepare",
                    "--input",
                    str(preparation),
                ]
            )
            == 0
        )
        second = json.loads(capsys.readouterr().out)
        assert second["selection"] == first["selection"]
        assert second["cache"]["decoded_projections"] == 0 and second["cache"]["hits"] == 3
        from spireagent.workbench.p1_cohort_cli import weight_rule_identity
        from spireagent.workbench.training_cohort import _loss_weighting
        from stpd.p1.cohort import experiment_plan, pool_plan

        facts = {
            r["cohort_facts"]["root_run_id"]: r["cohort_facts"]
            for r in second["classification"].values()
        }
        pool = pool_plan(second["index"], facts)
        plan = experiment_plan(
            pool,
            owner.ledger,
            {
                "model_id": "synthetic-http-experiment",
                "parent_model_id": None,
                "split_salt": "http",
                "dev_fraction": 1 / 3,
                "test_fraction": 1 / 3,
            },
        )
        rule, definition = weight_rule_identity(plan)
        with owner.ledger.operations.transaction() as db:
            db.execute("INSERT INTO curation_p1_weight_approvals VALUES(?,?)", (rule, definition))
        selection = {
            **second["selection"],
            "split_by_group": plan["splits"],
            "experiment": {
                **plan["experiment"],
                "configuration_id": plan["configuration_id"],
                "weight_definition": plan["definition"],
                "weights_by_root": _loss_weighting(plan)["weights_by_root"],
            },
        }
        frozen = p1_data_command(app.config, "freeze", {"selection": selection, "reserve": True})
        assert frozen["purpose_reserved"] and frozen["unique_N"] == 3
        assert len(list(iter_snapshot_points(store, frozen["snapshot_id"]))) == 3
        projection_fixture(store, "normal-data-new")
        third = p1_data_command(app.config, "prepare", request)
        assert third["cache"]["decoded_projections"] == 1 and third["cache"]["hits"] == 3
        assert third["index"]["unique_N"] == 4
        assert len(list(iter_snapshot_points(store, frozen["snapshot_id"]))) == 3
        with owner.transaction() as db:
            assert db.execute("SELECT count(*) FROM curation_uses").fetchone() == (0,)
        assert app.remote_training._operations is None


def test_data_write_requires_auth_and_wrong_reserve_never_claims(tmp_path):
    with client(tmp_path, reserve=False) as items:
        app, _call, _provider, _store, owner, _snapshot, _path = items
        runtime = json.loads((app.config.state_dir / "runtime.json").read_bytes())
        request = Request(
            f"http://127.0.0.1:{runtime['port']}/api/p1-data/prepare",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 403
        prepared = p1_data_command(app.config, "prepare", {})
        with pytest.raises(ValueError, match="invalid_p1_data_request"):
            p1_data_command(
                app.config, "freeze", {"selection": prepared["selection"], "reserve": "false"}
            )
        with owner.transaction() as db:
            assert db.execute("SELECT count(*) FROM curation_claims").fetchone() == (0,)


def test_normal_cli_reads_full_current_scale_preparation_response(tmp_path, capsys):
    # The real 7206-N preparation is about 8 MiB, much larger than health/status.
    # Produce the index through the real Store/prepare/HTTP serialization paths.
    with client(tmp_path, reserve=False) as (app, _call, _provider, store, _owner, _snapshot, path):
        _, original, value = projection_fixture(store, "large-prepare")
        template = value["segments"][0]["points"][0]
        points = []
        for index in range(7206):
            point = copy.deepcopy(template)
            # Native correlation identifiers are substantially longer than the
            # tiny fixture's names; retain realistic bytes as well as N count.
            point.update(
                index=index,
                source_event_id=f"native-event-{index:064d}",
                source_decision_id=f"native-decision-{index:064d}",
            )
            points.append(point)
        value["segments"][0]["points"] = points
        projection_fixture(store, "large-prepare", original=original, value=value)
        # Current data also has 1922 alias occurrences; these remain visible in
        # the index while unique N keeps the original decision identities.
        value["segments"][0]["points"] = points[:1922]
        projection_fixture(store, "large-prepare", original=original, value=value)
        assert main(["p1-data", "--config", str(path), "--action", "prepare"]) == 0
        raw = capsys.readouterr().out
        assert len(raw.encode()) > 8 * 1024**2
        prepared = json.loads(raw)
        assert prepared["index"]["unique_N"] == 7210
        selection_body = {"selection": prepared["selection"], "reserve": False}
        assert len(json.dumps(selection_body).encode()) < 1024**2
        frozen = p1_data_command(app.config, "freeze", selection_body)
        assert frozen["unique_N"] == 7210 and frozen["purpose_reserved"] is False
        assert len(list(iter_snapshot_points(store, frozen["snapshot_id"]))) == 7210
