"""Qualification seams preserve complete shapes, exact state and failure evidence."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_p1_calibration import calibration_input  # noqa: F401

from spireagent.artifact_contracts import Manifest
from spireagent.json_boundary import BoundaryError
from stpd.p1 import qualification
from stpd.p1.data import json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.formal_training import Population
from stpd.p1.input import fit_vocabulary
from stpd.p1.profiling import synthetic_frame
from stpd.p1.spec import MStdConfig


def binding(capsule):
    return {"calibration_input_id": capsule.artifact_id,
            "source_run_id": capsule.parent("run"), "checkpoint_id": capsule.parent("checkpoint"),
            "snapshot_id": capsule.parent("snapshot"), "operation_id": "d" * 32,
            "training_use_recorded": False, "original_training_use_verified": True,
            "training_use_reference": capsule.parameters.value()["operation_id"]}


def test_qualification_binding_keeps_original_Curation_operation(calibration_input):  # noqa: F811
    store, capsule = calibration_input
    bound = binding(capsule)
    actual = qualification.require_binding(store, capsule.artifact_id, bound)
    assert actual.artifact_id == capsule.artifact_id
    for changed in ({"original_training_use_verified": False}, {"operation_id": "bad"},
                    {"checkpoint_id": "e" * 64}, {"snapshot_id": "e" * 64}):
        with pytest.raises(BoundaryError, match="original_Curation_use_binding_required"):
            qualification.require_binding(store, capsule.artifact_id, {**bound, **changed})


def test_full_shape_tail_and_projection_cache_cannot_be_replaced(tmp_path):
    frames = tuple(synthetic_frame(n) for n in (2, 3, 7, 101))
    population = Population(frames, (0, 0, 0, 0), tuple(str(n) for n in range(4)))
    model = SimpleNamespace(execution=MStdExecution(), tokenize=lambda text: ([1, 2], []))
    report, indices = qualification.shape_report(population, model)
    assert report["TRAIN_N"] == 4 and report["all_TRAIN_shapes_scanned"]
    assert "not_sampler" in report["reference_grouping_order"]
    assert report["distributions"]["entities"]["max"]["value"] == len(frames[-1].entities)
    assert 3 in indices and report["distributions"]["entities"]["max"]["point_id"] == "3"
    cache = tmp_path / "cache.json"
    populations = {"train": population}
    expected = qualification._cache_populations(cache, populations, {"fixture": True})
    actual, vocabulary = qualification._read_cache(cache, expected)
    assert actual == populations and vocabulary == {"fixture": True}
    cache.write_bytes(cache.read_bytes() + b" ")
    with pytest.raises(BoundaryError, match="projection_cache_checksum_mismatch"):
        qualification._read_cache(cache, expected)


def test_v2_projection_cache_keeps_multi_view_roles_and_weights(tmp_path):
    from test_p1_input_v2 import public_state

    from stpd.p1.input import V2_INPUT_SPEC, project
    from stpd.p1.vocabulary import public_vocabulary

    frame = project(public_state(), input_projection=V2_INPUT_SPEC)
    populations = {"train": Population((frame,), (0,), ("point",), (1.5,))}
    path = tmp_path / "v2-cache.json"
    digest = qualification._cache_populations(path, populations, public_vocabulary())
    restored, vocabulary = qualification._read_cache(path, digest)
    assert restored == populations and vocabulary == public_vocabulary()


def test_failure_preserves_phase_and_original_redacted_reason(tmp_path, monkeypatch):
    from test_p1_snapshot import store_at

    store = store_at(tmp_path / "store")
    parent = Manifest("analysis", PRODUCER)
    store.publish(parent)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-credential-value")
    monkeypatch.setattr(qualification, "require_binding", lambda *args: (_ for _ in ()).throw(
        RuntimeError("first wrong fact private-credential-value https://private/path?token=secret")))
    artifact = qualification.qualify(store, parent.artifact_id, PRODUCER, {}, {}, tmp_path / "work")
    report = artifact.parameters.value()
    assert report["state"] == "failed" and report["failed_phase"] == "binding"
    assert "first wrong fact" in report["error"]["traceback"]
    assert "private-credential-value" not in report["error"]["traceback"]
    assert "https://" not in report["error"]["traceback"]
    assert json.loads((tmp_path / "work/progress.json").read_bytes())["performance_id"]


def test_two_real_processes_restore_complete_state_and_export(tmp_path):
    """Small fixture tests process/Store/codec seams, not real-TRAIN qualification."""
    frame = synthetic_frame(2)
    populations = {split: Population((frame,), (0,), (split + "-point",))
                   for split in ("train", "dev", "test")}
    cache = tmp_path / "cache.json"
    vocabulary = fit_vocabulary([frame])
    cache_sha = qualification._cache_populations(cache, populations, vocabulary)
    from test_p1_snapshot import store_at

    store = store_at(tmp_path / "store")
    parent = Manifest("analysis", PRODUCER)
    store.publish(parent)
    job = {"cache": str(cache), "cache_sha256": cache_sha,
           "config": MStdConfig().to_dict(), "snapshot_id": "c" * 64,
           "execution": MStdExecution().to_dict(), "store": str(tmp_path / "store"),
           "producer": PRODUCER.to_dict(), "directory": str(tmp_path),
           "input_id": parent.artifact_id,
           "steps": 1}
    path = tmp_path / "job.json"
    path.write_bytes(json_bytes(job))
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    for mode in ("train", "resume"):
        result_path = tmp_path / (mode + "-result.json")
        result = subprocess.run([sys.executable, "-m", "stpd.p1.qualification", "--child", mode,
                                 "--job", str(path), "--result", str(result_path)],
                                env=environment, capture_output=True, text=True, timeout=45)
        value = json.loads(result_path.read_bytes())
        assert result.returncode == 0, value
    trained = json.loads((tmp_path / "train-result.json").read_bytes())
    resumed = json.loads((tmp_path / "resume-result.json").read_bytes())
    assert trained["pid"] != resumed["pid"]
    assert resumed["complete_state_equal"] and resumed["fresh_Store_full_readback"]
    assert resumed["continued_step"] == 2
    assert trained["export"]["reader_equal"] and not trained["TEST_scored"]


def test_qualification_wrapper_keeps_exact_binding_and_remote_entry(tmp_path, monkeypatch):
    import runpy

    owner = runpy.run_path(str(Path(__file__).parents[1]
                              / "deploy/cloud-worker/calibrate_p1.py"))

    class App:
        def function(self, **settings):
            assert settings["retries"] == 0 and settings["timeout"] == 1800
            return lambda function: function

    sdk = SimpleNamespace(Image=SimpleNamespace(from_id=lambda value: value),
                          Secret=SimpleNamespace(from_name=lambda value: value))
    function = owner["definition"](App(), sdk, "im-" + "a" * 22, timeout=1800,
                                   cpu=2, memory=32768, secret_name="store")
    bound = {"operation_id": "a" * 32, "training_use_recorded": True}

    def run(command, **settings):
        assert command[2] == "stpd.p1.qualification"
        assert json.loads(Path(command[command.index("--binding") + 1]).read_bytes()) == bound
        assert command[command.index("--steps") + 1] == "256"
        assert settings["timeout"] == 1790
        return SimpleNamespace(returncode=0, stdout='{"state":"completed"}')

    monkeypatch.setenv("STPD_IMAGE_PROFILE", "worker")
    monkeypatch.setattr(subprocess, "run", run)
    assert function("b" * 64, {}, "c" * 40, "d" * 64,
                    {"binding": bound, "steps": 256}) == {"state": "completed"}


def test_application_checks_existing_use(calibration_input, monkeypatch):  # noqa: F811
    from spireagent import p1_curation
    from spireagent.workbench import inplace_curation, training_calibration

    store, capsule = calibration_input
    owner = SimpleNamespace(store_dir=store.blobs.root, ledger=object())
    monkeypatch.setattr(inplace_curation, "configured_owner", lambda config: owner)
    monkeypatch.setattr(training_calibration, "source_identity", lambda root: PRODUCER)
    checked = []
    monkeypatch.setattr(p1_curation, "require_recorded_p1_training_use", lambda *args:
                        checked.append(args), raising=False)
    monkeypatch.setattr(training_calibration, "record_p1_training_use", lambda *args:
                        pytest.fail("qualification reuses the already admitted capsule use"))
    result = training_calibration.authorize_qualification(
        SimpleNamespace(research_workspace=object()), capsule.artifact_id, "e" * 32)
    assert checked[0][0] is owner.ledger
    assert checked[0][2:] == (capsule.parent("snapshot"),
                              capsule.parameters.value()["operation_id"])
    assert result["original_training_use_verified"]
    assert result["training_use_recorded"] is False
    assert result["training_use_reference"] == capsule.parameters.value()["operation_id"]
    assert result["operation_id"] == "e" * 32



def test_module_entry_orchestrates_canonical_replacement_children(
    calibration_input, tmp_path, monkeypatch,  # noqa: F811
):
    """The production -m entry has __name__='__main__'; children use its module."""
    store, capsule = calibration_input
    frame = synthetic_frame(2)
    populations = {split: Population((frame,), (0,), (split + "-point",))
                   for split in ("train", "dev", "test")}
    monkeypatch.setattr(qualification, "__name__", "__main__")
    source_run = store.get_manifest(capsule.parent("run"))
    training = store.get_manifest(source_run.parent("training_input"))
    vocabulary = json.loads(b"".join(store.read_payload(capsule.payload("vocabulary"))))
    monkeypatch.setattr(qualification, "verify_p1_run", lambda *args:
                        SimpleNamespace(reader=object(), training=training, vocabulary=vocabulary))
    def project_populations(reader, weights, projection):
        assert weights == training.parameters.value().get("loss_weighting")
        assert projection == vocabulary["input_spec"]
        return populations
    monkeypatch.setattr(qualification, "populations_from_reader", project_populations)
    monkeypatch.setattr(qualification, "read_input", lambda *args: (None, [], [], {}, {}))
    monkeypatch.setattr(MStdExecution, "for_plan", classmethod(lambda cls, name: cls()),
                        raising=False)
    monkeypatch.setattr(qualification, "MStd", lambda vocabulary: object())
    monkeypatch.setattr(qualification, "shape_report", lambda *args: ({"TRAIN_N": 1}, [0]))
    monkeypatch.setattr(qualification, "parity", lambda *args: {"passed": True})
    throughput = Manifest("performance", PRODUCER)
    monkeypatch.setattr(qualification, "measure", lambda *args, **kwargs: throughput)
    monkeypatch.setattr(qualification, "_cache_populations", lambda *args: "f" * 64)
    modes = []

    def run(command, **settings):
        assert command[:3] == [sys.executable, "-m", "stpd.p1.qualification"]
        mode = command[command.index("--child") + 1]
        modes.append(mode)
        result_path = Path(command[command.index("--result") + 1])
        result_path.write_bytes(json_bytes({"pid": len(modes)}))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    report = qualification.qualify(store, capsule.artifact_id, PRODUCER, {}, binding(capsule),
                                   tmp_path / "qualification", device="cpu")
    assert report.parameters.value()["state"] == "completed"
    assert modes == ["train", "resume"]


@pytest.mark.parametrize("timeout,memory", [(1800, 32768), (7200, 131072)])
def test_qualification_reads_original_curation_profile_without_runtime_upgrade(
    tmp_path, monkeypatch, timeout, memory,
):
    import importlib.metadata
    import runpy

    import spireagent.source
    from spireagent.workbench import training_calibration
    from spireagent.workbench.developer import ProjectConfig

    class BeforeAnyModalOffer(Exception):
        pass

    original = ProjectConfig(tmp_path / "original-state", "", "", None,
                             {"old_installed_combination": "retained"})
    config_path = tmp_path / "project.json"
    config_path.write_bytes(json_bytes(original.to_dict()))
    with pytest.raises(BoundaryError, match="combination_changed"):
        ProjectConfig.load(config_path)
    seen = []
    monkeypatch.setattr(training_calibration, "authorize_qualification", lambda config, *args:
                        seen.append(config.combination))
    monkeypatch.setattr(spireagent.source, "source_identity", lambda root: PRODUCER)
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "1.5.5")
    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(App=lambda name:
                        (_ for _ in ()).throw(BeforeAnyModalOffer())))
    plan = tmp_path / "plan.json"
    plan.write_bytes(json_bytes({"variants": [MStdExecution().to_dict()], "profile": False}))
    monkeypatch.setattr(sys, "argv", ["calibrate", "--image", "im-" + "a" * 22,
        "--input", "b" * 64, "--plan", str(plan), "--receipt", str(tmp_path / "receipt.json"),
        "--qualify", "--config", str(config_path), "--operation-id", "c" * 32, "--steps", "200",
        "--timeout", str(timeout), "--memory-mib", str(memory)])
    owner = runpy.run_path(str(Path(__file__).parents[1]
                              / "deploy/cloud-worker/calibrate_p1.py"))
    with pytest.raises(BeforeAnyModalOffer):
        owner["main"]()
    assert seen == [original.combination]
    assert json.loads(config_path.read_bytes()) == original.to_dict()
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("qualify,timeout,memory", [
    (False, 1801, 32768), (False, 1800, 32769),
    (True, 7201, 131072), (True, 7200, 131073),
])
def test_qualification_resource_limits_preserve_legacy_probe_bounds(
    tmp_path, monkeypatch, qualify, timeout, memory,
):
    import importlib.metadata
    import runpy

    import spireagent.source

    monkeypatch.setattr(spireagent.source, "source_identity", lambda root: PRODUCER)
    monkeypatch.setattr(importlib.metadata, "version", lambda package: "1.5.5")
    monkeypatch.setattr(sys, "argv", ["calibrate", "--image", "im-" + "a" * 22,
        "--input", "b" * 64, "--plan", str(tmp_path / "unused.json"),
        "--receipt", str(tmp_path / "receipt.json"), "--timeout", str(timeout),
        "--memory-mib", str(memory), *(["--qualify"] if qualify else [])])
    owner = runpy.run_path(str(Path(__file__).parents[1]
                              / "deploy/cloud-worker/calibrate_p1.py"))
    with pytest.raises(ValueError, match="fresh_receipt_and_bounded_resources_required"):
        owner["main"]()
