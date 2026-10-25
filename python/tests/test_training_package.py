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
