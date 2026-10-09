"""One-update synthetic Source3 package through real constructor and fresh stdio.

The public original verifier/store/trainer/export/loader/binder are exercised.
This is synthetic package conformance, never Human, game or model-quality proof.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import torch
from test_native_structured_training import origin
from test_ordered_source_training import source
from test_protocol_source import setup_store
from test_structured_resume import Authority

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.managed_local_workspace import ROOT_NAME, inspect_managed_workspace
from stpd.fullrun.native_structured_sequences import qualify_native
from stpd.models.structured_training import StructuredTrainingConfig
from stpd.native_code_scope import native_code_sha256
from stpd.native_sampled_carry_spec import (
    INPUT_SPEC,
    RECIPE,
    STATE_FORMAT,
    VIEW,
    sampled_agent_spec,
)
from stpd.ordered_source_spec import SCOPE, recipe_control
from stpd.policy.native_agent import SESSION_SCHEMA, NativeStructuredAgent, bind_native_agent
from stpd.policy.native_structured_export import export_native_model, load_native_package
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)

ROOT = Path(__file__).resolve().parents[2]
SHARED = json.loads(
    (
        ROOT / "components/policy-runtime/contracts/fixtures/sampled-current-carry-v1.json"
    ).read_bytes()
)
PROFILE_PATH = ROOT / "components/connector/contracts/native-logical-publication-profile-v1.json"
PROFILE_SHA = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf"


def build_package(directory: Path) -> tuple[Path, Path, dict]:
    """Reuse the public synthetic golden and existing trainer, with one update."""
    directory = directory.resolve()
    if (directory / "state").exists():
        selected = inspect_managed_workspace(directory / "state")
        store = ManifestArtifactStore(
            LocalBlobStore(
                directory / "state" / ROOT_NAME / selected["workspace_id"] / "store", create=False
            )
        )
    else:
        store, _ = setup_store(directory)
    raw, admission, partition = source(store, view=VIEW)
    control = recipe_control(RECIPE)
    config = StructuredTrainingConfig(epochs=1, max_updates=1)
    assert config.tbptt_advances == 4
    producer = origin()
    run = prepare_structured_workload(
        store,
        partition.dataset,
        producer,
        config,
        source_id=partition.manifest.artifact_id,
        operation_id="1" * 32,
        code_scope=SCOPE,
        model_control=control,
    )
    result = execute_structured_workload(
        store,
        ObjectStoreRunReporter(store, store.blobs),
        StructuredWorkloadRequest(
            run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
        ),
        producer,
        authority=Authority(),
        attempt_producer=producer,
    )
    assert result.state == "completed" and result.optimizer_updates == 1
    folder = directory / "package"
    export_native_model(store, result.model_id, folder)
    package, model = load_native_package(folder)
    assert package["input_spec"] == INPUT_SPEC
    assert package["agent_spec"] == sampled_agent_spec(control)
    assert package["state_format_version"] == STATE_FORMAT
    assert model.model_control == control
    assert package["source"]["source_artifact_id"] == partition.manifest.artifact_id
    profile_bytes = PROFILE_PATH.read_bytes()
    assert hashlib.sha256(profile_bytes).hexdigest() == PROFILE_SHA
    seams = json.loads(profile_bytes)["required_seams"]
    assert len(seams) == 13
    manifest_path = directory / "agent.json"
    template = SHARED["manifest"]
    manifest = bind_native_agent(
        folder,
        manifest_path,
        manifest_id="synthetic-sampled-package-convergence",
        requirements=copy.deepcopy(template["requirements"]),
        support=copy.deepcopy(template["support"]),
        required_seams=seams,
    )
    assert manifest["requirements"]["environment"]["host_kind"] == "test"
    assert manifest["input"]["input_spec"] == INPUT_SPEC
    assert manifest["input"]["state_recovery"] == {
        "mode": "none",
        "max_state_bytes": 0,
        "model_bindings": [],
    }
    assert manifest["adapter"]["code_sha256"] == native_code_sha256(graph=True)
    receipt = {
        "scope": "synthetic_package_conformance_only",
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_raw_id": raw.artifact_id,
        "admission_id": admission.admission_id,
        "source_partition_id": partition.manifest.artifact_id,
        "model_artifact_id": result.model_id,
        "package_model_id": package["model_id"],
        "checkpoint_id": result.checkpoint_id,
        "optimizer_updates": result.optimizer_updates,
        "input_spec": INPUT_SPEC,
        "weights_sha256": package["weights"]["sha256"],
        "adapter_code_sha256": manifest["adapter"]["code_sha256"],
        "package_manifest_sha256": manifest["artifact"]["sha256"],
        "publication_profile_sha256": PROFILE_SHA,
        "required_seams": len(seams),
        "torch_version": torch.__version__,
        "intra_threads": torch.get_num_threads(),
        "interop_threads": torch.get_num_interop_threads(),
        "tbptt_advances": config.tbptt_advances,
    }
    (directory / "receipt.json").write_bytes(json_bytes(receipt))
    return folder, manifest_path, receipt


@pytest.fixture(scope="module")
def closed_package(tmp_path_factory):
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        directory = (
            Path(os.environ["E6_SAMPLED_PACKAGE_ROOT"])
            if "E6_SAMPLED_PACKAGE_ROOT" in os.environ
            else tmp_path_factory.mktemp("sampled-package")
        ).resolve()
        if not (directory / "receipt.json").exists():
            directory.mkdir(parents=True, exist_ok=True)
            # Inter-op configuration is process-global and cannot be restored.
            # Apply the explicit numerical profile only in its fresh builder.
            script = """
import sys
from pathlib import Path
import torch
torch.set_num_threads(2)
torch.set_num_interop_threads(1)
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_sampled_carry_package_integration import build_package
build_package(Path(sys.argv[1]))
"""
            built = subprocess.run(
                [sys.executable, "-c", script, str(directory)], cwd=ROOT / "python",
                capture_output=True, text=True, timeout=60, check=False,
            )
            assert built.returncode == 0, built.stdout + built.stderr
        value = (
            directory / "package",
            directory / "agent.json",
            json.loads((directory / "receipt.json").read_bytes()),
        )
        # Reuse only its closed bytes; the real constructor revalidates current code.
        NativeStructuredAgent(value[0], value[1])
        assert value[2]["intra_threads"] == 2 and value[2]["interop_threads"] == 1
        yield value
    finally:
        torch.set_num_threads(previous)


def acknowledgement(report: dict) -> dict:
    return {
        **{
            key: report[key]
            for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
        },
        "prefix": {
            "continuity_token": "segment-1",
            "history_mode": "sampled_current",
            "consumption_mode": "once_per_occurrence",
            "received_cursor": "cursor",
            "consumed_publication_index": None,
            "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None},
        },
    }


def next_input(child: NativeStructuredAgent) -> dict:
    return {
        "continuity_token": "segment-1",
        "consumption_id": child.scorer.consumption_id,
        "state_version": child.scorer.state_version,
        "basis_acquisition_id": child.scorer.acquisition_id,
        "received_cursor": "cursor",
    }


def test_public_export_real_constructor_exact_weights_and_ack_carry(closed_package):
    folder, path, receipt = closed_package
    child = NativeStructuredAgent(folder, path)
    assert child.sampled and child.ready_summary_task
    assert child.scorer.model_id == receipt["package_model_id"]
    assert child.scorer.weights_sha256 == receipt["weights_sha256"]
    child.verify_weights()
    expected = child.scorer.model.initial_memory()
    memories = []
    for version, name in enumerate(("map_a", "inspect_b", "map_c"), 1):
        frame = copy.deepcopy(SHARED["frames"][name])
        encoded, _ = qualify_native(frame["observation"], frame["catalog"])
        before = child.scorer.memory.clone()
        with torch.inference_mode():
            expected = child.scorer.model.advance(child.scorer.model.encode(encoded), expected)
        report = child.consume(
            {
                "acquisition_id": "acq-" + name,
                "input_spec": INPUT_SPEC,
                "continuity_token": "segment-1",
                "previous_consumption_id": child.scorer.consumption_id,
                "observation": frame["observation"],
                "catalog": frame["catalog"],
            }
        )
        assert report["state_version"] == version and report["advanced"]
        assert torch.equal(child.scorer.memory, before)
        bad = acknowledgement(report) | {"acquisition_id": "wrong-original-basis"}
        with pytest.raises(BoundaryError, match="consume_ack_binding"):
            child.scorer.acknowledge(bad)
        assert torch.equal(child.scorer.memory, before)
        child.scorer.acknowledge(acknowledgement(report))
        assert torch.equal(child.scorer.memory, expected)
        output = child.next(next_input(child))["directive"]
        assert output["basis_acquisition_id"] == "acq-" + name
        assert output["selection"]["action_id"] in encoded.action_ids
        assert len(output["scores"]["values"]) == len(frame["catalog"])
        assert output["scores"]["catalog_digest"] == frame["observation"]["catalog"]["digest"]
        memories.append(child.scorer.memory.clone())
    assert not torch.equal(memories[0], memories[2])
    kind, directive = child.sampled_query_result(
        {
            "method": "current",
            "acquisition_id": "unused-map_c",
            "value": {
                **{
                    key: SHARED["frames"]["map_c"][key]
                    for key in ("capture", "observation", "catalog")
                },
                "catalog_materialized": True,
            },
        },
        next_input(child),
    )
    assert kind == "directive" and directive["directive"]["type"] == "await"
    assert child.scorer.state_version == 3 and torch.equal(child.scorer.memory, memories[-1])


def test_fresh_real_stdio_child_query_consume_ack_original_member_and_summary(closed_package):
    folder, path, _ = closed_package
    command = [
        sys.executable,
        "-c",
        (
            "import torch;torch.set_num_threads(2);torch.set_num_interop_threads(1);"
            "from stpd.policy.native_agent import main;raise SystemExit(main())"
        ),
        "--package",
        str(folder),
        "--manifest",
        str(path),
    ]
    child = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "python")},
    )
    assert child.stdin is not None and child.stdout is not None
    received: queue.Queue[str] = queue.Queue()

    def reader():
        assert child.stdout is not None
        for line in child.stdout:
            received.put(line)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def read():
        return json.loads(received.get(timeout=15))

    def send(kind, request, value):
        field = (
            "completion"
            if kind == "consume_ack"
            else "result"
            if kind == "query_result"
            else "input"
        )
        assert child.stdin is not None
        child.stdin.write(
            json_bytes(
                {
                    "schema": SESSION_SCHEMA,
                    "message_type": kind,
                    "session_id": "fresh-stdio",
                    "recovery_epoch": 0,
                    "request_id": request,
                    field: value,
                }
            ).decode()
        )
        child.stdin.flush()

    prefix = {
        "continuity_token": "segment-1",
        "consumption_id": None,
        "state_version": 0,
        "basis_acquisition_id": None,
        "received_cursor": "cursor",
    }
    advanced, directives = [], []
    try:
        ready = read()
        assert (
            ready["message_type"] == "ready"
            and ready["adapter"] == json.loads(path.read_bytes())["adapter"]
        )
        for serial, name in enumerate(
            ("map_a", "map_a", "inspect_b", "map_c", "map_c", "empty_wait", "ready_summary")
        ):
            request = "next-" + str(serial)
            send("next", request, prefix)
            query = read()
            assert query["message_type"] == "query" and query["request_id"].startswith(
                "child-query-"
            )
            assert query["input"] == {
                "method": "current",
                "arguments": {
                    "eager_scope": ["persistent", "interaction", "referents", "catalog"],
                    "expected_snapshot_id": None,
                },
            }
            frame = SHARED["frames"][name]
            send(
                "query_result",
                query["request_id"],
                {
                    "method": "current",
                    "acquisition_id": "fresh-" + str(serial),
                    "value": {
                        **{key: frame[key] for key in ("capture", "observation", "catalog")},
                        "catalog_materialized": True,
                    },
                },
            )
            response = read()
            if response["message_type"] == "consumed":
                report = response["completion"]
                assert report["state_version"] == prefix["state_version"] + 1
                assert report["previous_consumption_id"] == prefix["consumption_id"]
                assert report["acquisition_id"] == "fresh-" + str(serial)
                advanced.append(name)
                send("consume_ack", response["request_id"], acknowledgement(report))
                prefix.update({key: report[key] for key in ("consumption_id", "state_version")})
                prefix["basis_acquisition_id"] = report["acquisition_id"]
                response = read()
            assert response["message_type"] == "directive" and response["request_id"] == request
            directive = response["output"]["directive"]
            directives.append(directive["type"])
            if directive["type"] == "act":
                assert directive["selection"]["action_id"] in [
                    action["action_id"] for action in frame["catalog"]
                ]
                assert len(directive["scores"]["values"]) == len(frame["catalog"])
            if directive["type"] == "close":
                assert name == "ready_summary" and frame["catalog"] == []
                assert directive["reason"] == "native_ready_summary_task_complete"
        assert advanced == ["map_a", "inspect_b", "map_c", "ready_summary"]
        assert directives == ["act", "await", "act", "act", "await", "await", "close"]
        child.stdin.close()
        assert child.wait(timeout=10) == 0
        assert child.stderr is not None and child.stderr.read() == ""
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
