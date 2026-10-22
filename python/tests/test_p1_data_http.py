"""Normal authenticated Application/CLI maps to the same P1 Store/Curation owner."""

from __future__ import annotations

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
    with client(tmp_path, reserve=False) as (app, _call, _provider, store, owner, _snapshot, path):
        first = p1_data_command(app.config, "prepare", {})
        assert first["cache"]["decoded_projections"] == 3
        assert first["ready_to_freeze"] and not first["purpose_reserved"]
        assert main(["p1-data", "--config", str(path), "--action", "prepare"]) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["selection"] == first["selection"]
        assert second["cache"]["decoded_projections"] == 0 and second["cache"]["hits"] == 3
        frozen = p1_data_command(app.config, "freeze", {"selection": second["selection"],
                                                        "reserve": True})
        assert frozen["purpose_reserved"] and frozen["unique_N"] == 3
        assert len(list(iter_snapshot_points(store, frozen["snapshot_id"]))) == 3
        projection_fixture(store, "normal-data-new")
        third = p1_data_command(app.config, "prepare", {})
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
        request = Request(f"http://127.0.0.1:{runtime['port']}/api/p1-data/prepare", data=b"{}",
                          headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 403
        prepared = p1_data_command(app.config, "prepare", {})
        with pytest.raises(ValueError, match="invalid_p1_data_request"):
            p1_data_command(app.config, "freeze", {"selection": prepared["selection"],
                                                  "reserve": "false"})
        with owner.transaction() as db:
            assert db.execute("SELECT count(*) FROM curation_claims").fetchone() == (0,)
