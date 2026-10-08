"""Actual synthetic private-child application versions and independent producer identities."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import test_protocol_source as originals
import test_structured_s0 as fixtures

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.workbench.developer import ROOT
from spireagent.workbench.trusted_recipes import (
    DEFAULT_RECIPE,
    STRUCTURED_RECIPE,
    STRUCTURED_SCOPED_RECIPE,
    describe_recipe,
    structured_recipe_scope,
)
from stpd.policy.structured_export import export_structured_package
from stpd.structured_code_scope import (
    INFERENCE_SCOPE,
    LEGACY_SCOPE,
    SCOPED_MODEL_SCHEMA,
    STRUCTURED_MODEL_SCHEMAS,
    TRAINING_SCOPE,
    require_structured_model_package,
    structured_model_package_schema,
)
from stpd.workers.structured_evaluation import _model

cpu_threads = fixtures.cpu_threads
exported = fixtures.exported


def copied_source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for folder in ("stpd", "spireagent", "configs"):
        shutil.copytree(ROOT / folder, root / folder, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "uv.lock", root / "uv.lock")
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Synthetic Fixture"],
        ["config", "user.email", "fixture@example.test"],
        ["add", "."],
        ["commit", "-qm", "synthetic reviewed source copy"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


SUPERVISOR = r'''
import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.developer import ROOT, ProjectConfig, combination
from spireagent.workbench.local_training import LocalTrainingService, OPERATION_FILE
from spireagent.workbench.local_model_export import LocalModelExport
from spireagent.workbench.local_model_registration import LocalModelRegistration, VERBS
from spireagent.workbench.local_models import LocalModelService
from spireagent.workbench.local_memory_evaluation import LocalMemoryEvaluationService
from spireagent.workbench.local_evaluation import summary
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter
from spireagent.workbench import local_model_registration as registration
from spireagent.workbench.trusted_recipes import STRUCTURED_SCOPED_RECIPE
from stpd.structured_code_scope import TRAINING_SCOPE, INFERENCE_SCOPE, code_identity

state, train_id, dev_id, store_dir, recipe = sys.argv[2:]
config = ProjectConfig(Path(state), '', '', None, combination())
store = ManifestArtifactStore(LocalBlobStore(Path(store_dir), create=False))
service = LocalTrainingService(config)
request = TrainingRequest('1'*32, recipe, train_id, {'epochs':1}, limits={'wall_seconds':600})
scoped = recipe == STRUCTURED_SCOPED_RECIPE

def settle(target):
    thread = target._thread if hasattr(target, '_thread') else target.thread
    assert thread is not None
    thread.join(timeout=30)
    assert not thread.is_alive()
    operation = target.status()['operation']
    assert operation['status'] in {'paused','completed'}, operation
    return operation

def advance_application_source(label):
    path = ROOT/'spireagent/workbench/local_model_registration.py'
    path.write_bytes(path.read_bytes()+b'\n# synthetic unrelated Workbench change '+
                     label.encode()+b'\n')
    subprocess.run(['git','add',str(path)],cwd=ROOT,check=True,capture_output=True)
    subprocess.run(['git','commit','-qm','synthetic unrelated application '+label],
                   cwd=ROOT,check=True,capture_output=True)
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()

initial_head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
if scoped:
    identities = {scope:code_identity(scope) for scope in (TRAINING_SCOPE, INFERENCE_SCOPE)}
    original = adapter._private_child
    # The fixture waits on the real application pause after a publication-phase
    # checkpoint so creator A, exporter B and reconciler C are distinct facts.
    child_script = """
import sys,time
from spireagent.workbench.recipes import structured_child as child
original_emit=child.ChildReporter.emit
def emit(self,event):
    result=original_emit(self,event)
    info=event.parameters.value()
    if info.get('kind')=='checkpoint' and info.get('details',{}).get('phase')=='publication':
        deadline=time.monotonic()+5
        while self.fence.requested_action()=='continue':
            if time.monotonic()>deadline:
                raise RuntimeError('fixture pause not observed')
            time.sleep(0.005)
    return result
child.ChildReporter.emit=emit
raise SystemExit(child.main(sys.argv[1:]))
"""
    def pause_at_publication(command,*args,**kwargs):
        callback=kwargs['on_stdout_line']
        def line(raw):
            callback(raw)
            message=json.loads(raw)
            if message['kind']=='event':
                event=store.get_manifest(message['details']['event_id'])
                info=event.parameters.value()
                if (info.get('kind')=='checkpoint' and
                        info.get('details',{}).get('phase')=='publication'):
                    service.pause(message['operation_id'],message['attempt_id'])
        return original([command[0],'-c',child_script,*command[3:]],*args,
                        **{**kwargs,'on_stdout_line':line})
    adapter._private_child=pause_at_publication
    service.start(request)
    paused=settle(service)
    assert paused['status']=='paused'
    checkpoint_a=store.get_manifest(paused['checkpoint_id'])
    assert checkpoint_a.producer.source_revision==initial_head
    assert checkpoint_a.parameters.value()['schema']==(
        'stpd/structured-m2-training-checkpoint-v3')
    assert checkpoint_a.parameters.value()['phase']=='publication'
    run=store.get_manifest(paused['run_id'])
    assert run.producer==checkpoint_a.producer
    # Mixing an old checkpoint into the new recipe cannot create a new attempt.
    from spireagent.artifact_contracts import Manifest
    from spireagent.json_boundary import FrozenObject
    bad=Manifest(checkpoint_a.kind,checkpoint_a.producer,checkpoint_a.parents,checkpoint_a.payloads,
                 FrozenObject.of({**checkpoint_a.parameters.value(),
                                  'schema':'stpd/structured-m2-training-checkpoint-v2'}))
    store.publish(bad)
    owner,_,_=service._selected()
    path=owner.path.parent/OPERATION_FILE
    before=path.read_bytes()
    try:
        service.resume(paused['operation_id'],paused['attempt_id'],bad.artifact_id,'2'*32,request.limits)
        raise AssertionError('mixed checkpoint admitted')
    except BoundaryError as error:
        assert error.code=='checkpoint_run_identity_mismatch'
    assert path.read_bytes()==before
    exporter_head=advance_application_source('B')
    assert identities=={scope:code_identity(scope) for scope in identities}
    adapter._private_child=original
    service.resume(paused['operation_id'],paused['attempt_id'],paused['checkpoint_id'],'2'*32,request.limits)
    completed=settle(service)
    assert completed['status']=='completed',completed
    model=store.get_manifest(completed['model_id'])
    result=store.get_manifest(completed['result_id'])
    assert model.producer.source_revision==exporter_head
    assert result.producer==model.producer
    assert model.parent('checkpoint')==checkpoint_a.artifact_id
    assert store.get_manifest(model.parent('checkpoint')).producer.source_revision==initial_head
    assert completed['attempt_producer']==model.producer.to_dict()
    before_ids=store.manifest_ids()
    reconciler_head=advance_application_source('C')
    service.reconcile(completed['operation_id'],completed['attempt_id'])
    reconciled=settle(service)
    assert reconciled['status']=='completed',reconciled
    assert reconciled['attempt_producer']['source_revision']==reconciler_head
    assert reconciled['model_id']==model.artifact_id and reconciled['result_id']==result.artifact_id
    assert store.manifest_ids()==before_ids
else:
    service.start(request)
    completed=settle(service)
    assert completed['status']=='completed',completed
    model=store.get_manifest(completed['model_id'])
    run=store.get_manifest(completed['run_id'])
    assert model.producer==run.producer
    assert 'attempt_producer' not in completed
    exporter_head=initial_head
    reconciler_head=initial_head

expected_model='stpd/structured-m2-model-v3' if scoped else 'stpd/structured-m2-model-v2'
expected_package='stpd/structured-m2-package-v2' if scoped else 'stpd/structured-m2-package-v1'
assert model.parameters.value()['schema']==expected_model
package=json.loads(store.bytes(model.payload('package_manifest')))
assert package['schema']==expected_package
if scoped:
    assert package['provenance']['training_producer']==run.producer.to_dict()
    assert package['provenance']['export_producer']==model.producer.to_dict()
    assert package['provenance']['checkpoint_id']==checkpoint_a.artifact_id

export=LocalModelExport(config)
export.start(model.artifact_id)
assert settle(export)['status']=='completed'
models=LocalModelService(config)
models.private_root.mkdir(parents=True,exist_ok=True)
runtime=models.private_root/'runtime-fixture'
runtime.mkdir()
pin={'package':'@rsgcsg/sts2-policy-runtime'}
models.text_runtime_profile=lambda profile:(runtime,pin)
registration.validate_runtime_install=lambda *args:{}
registration._v2_sdk_available=lambda *args:True
bind=LocalModelRegistration(config,export,models)
capabilities={'input_profile':'text-menu-v2',
 'snapshot_schema':'sts2.player-environment/text-menu-snapshot-2',
 'receipt_schema':'sts2.player-environment/text-menu-action-result-2',
 'protocol_version':'1.0.0','execution_available':True,'single_controller':True,
 'verbs':[*VERBS,'select_card','select_target','cancel_selection'],
 'host':{'host_kind':'test','version':'synthetic-connector','implementation':{
    'source_revision':'test-source','artifact_sha256':'a'*64,'module_version_id':'test-mvid'}},
 'game':{'version':'test-game','commit':'test-commit','modset':{
    'status':'exact','fingerprint':'b'*64,'loaded_mod_ids':[]}}}
bind._capabilities=lambda *args,**kwargs:capabilities
bind._context_available=lambda *args,**kwargs:None
bind._m2_runtime_manifest_compatible=lambda *args,**kwargs:None
registered=bind.register(model.artifact_id)
assert registered['status']=='registered'
assert bind.status(model.artifact_id)==registered
if scoped:
    advance_application_source('D')
    assert identities=={scope:code_identity(scope) for scope in identities}
assert bind.register(model.artifact_id)==registered
assert bind.status(model.artifact_id)==registered
entry=models.selection(registered['selection_id'])
manifest=json.loads(models.entry_path(entry,'manifest').read_bytes())
assert manifest['adapter']['version']==('1.1.0' if scoped else '1.0.0')
assert manifest['adapter_config']['stage1a']['config']['schema']==(
    'stpd/structured-policy-config-v2' if scoped else 'stpd/structured-policy-config-v1')
models._runtime_package=lambda *args:{'verified':True}
models._public_manifest_contract=lambda *args:None
assert models.readiness(entry['id'])['status']=='ready_to_load'

evaluation=LocalMemoryEvaluationService(config)
evaluation.start(model.artifact_id,dev_id)
observed=settle(evaluation)
assert observed['status']=='completed',observed
report=summary(store,observed['evaluation_id'])
assert report['optimizer_updates']==0 and report['partition']=='dev'
assert store.get_manifest(observed['evaluation_id']).parent('model')==model.artifact_id
if scoped:
    cache=config.state_dir/'downloads'/model.artifact_id
    cache.mkdir(parents=True)
    (cache/'manifest.json').write_bytes(model.to_bytes())
    (cache/'download.json').write_text(json.dumps({
        'schema':'stpd/result-download-v1','artifact_id':model.artifact_id}))
    for payload in model.payloads:
        (cache/(payload.sha256+'.bin')).write_bytes(store.bytes(payload))
    (store.blobs.root/('manifests/'+model.parent('training_input')+'.json')).unlink()
    export.start(model.artifact_id)
    assert settle(export)['status']=='completed'
    assert bind.register(model.artifact_id)==registered
print(json.dumps({'model_schema':expected_model,'package_schema':expected_package,
    'run_schema':run.parameters.value()['schema'], 'initial_head':initial_head,
    'exporter_head':exporter_head,'reconciler_head':reconciler_head,
    'evaluation_id':observed['evaluation_id'],
    'source_schema':package['projection']['source_schema']}))
'''


@pytest.mark.parametrize("recipe", [STRUCTURED_RECIPE, STRUCTURED_SCOPED_RECIPE])
def test_actual_service_versions_export_register_evaluate(tmp_path, recipe):
    root = copied_source(tmp_path)
    store, owner = originals.setup_store(tmp_path)
    partitions = {}
    for seed, split in (("1", "train"), ("2", "dev")):
        ref, _, _ = originals.publish_run(store, tmp_path, seed, split)
        source = originals.sources.publish_protocol_source_partition(
            store, (ref,), split, originals.PROJECTION
        )
        owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
        partitions[split] = source.manifest.artifact_id
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "STPD_HUB_ADMIN_TOKEN"}
    }
    environment.update(OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            SUPERVISOR,
            str(root),
            str(tmp_path / "state"),
            partitions["train"],
            partitions["dev"],
            str(owner.store_dir),
            recipe,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["model_schema"] in STRUCTURED_MODEL_SCHEMAS
    if recipe == STRUCTURED_SCOPED_RECIPE:
        assert (
            len({observed[key] for key in ("initial_head", "exporter_head", "reconciler_head")})
            == 3
        )
        assert observed["run_schema"] == "stpd/structured-m2-run-v3"
    else:
        assert observed["run_schema"] == "stpd/structured-m2-run-v2"


def test_closed_recipe_opt_in_keeps_legacy_default():
    assert DEFAULT_RECIPE == "stage1a.dsimple.s.v1"
    assert structured_recipe_scope(STRUCTURED_RECIPE) == LEGACY_SCOPE
    assert structured_recipe_scope(STRUCTURED_SCOPED_RECIPE) == TRAINING_SCOPE
    assert describe_recipe(STRUCTURED_RECIPE)["scoped_attempt_producer"] is False
    assert describe_recipe(STRUCTURED_SCOPED_RECIPE)["scoped_attempt_producer"] is True
    with pytest.raises(BoundaryError, match="unsupported_structured_recipe"):
        structured_recipe_scope("structured-m2-cpu-v4")


@pytest.mark.parametrize("schema", [SCOPED_MODEL_SCHEMA, "stpd/structured-m2-model-v999"])
def test_domain_rejects_legacy_package_with_scoped_or_unknown_model(tmp_path, exported, schema):
    store, _ = originals.setup_store(tmp_path)
    package, metadata, _ = exported
    payloads = tuple(
        store.put_payload(role, io.BytesIO((package / name).read_bytes()), media)
        for role, name, media in (
            ("package_manifest", "model.json", "application/json"),
            ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
        )
    )
    model = Manifest(
        "model",
        originals.PROJECTION,
        payloads=payloads,
        parameters=FrozenObject.of({"schema": schema, "model_id": metadata["model_id"]}),
    )
    store.publish(model)
    with pytest.raises(
        BoundaryError, match="structured_model_required|model_package_binding_mismatch"
    ):
        _model(store, model.artifact_id)


def test_closed_binding_rejects_reverse_version_and_exporter_relabel():
    origin = originals.PROJECTION
    parents = tuple(
        Parent(role, char * 64)
        for role, char in (("run", "1"), ("training_input", "2"), ("checkpoint", "3"))
    )
    legacy = Manifest(
        "model",
        origin,
        parents,
        parameters=FrozenObject.of({"schema": "stpd/structured-m2-model-v2", "model_id": "4" * 64}),
    )
    scoped_package = {
        "schema": "stpd/structured-m2-package-v2",
        "model_id": "4" * 64,
        "graph": {"id": "fixture-graph"},
        "qualification": "engineering_only",
        "provenance": {
            "export_producer": origin.to_dict(),
            "run_id": "1" * 64,
            "training_input_id": "2" * 64,
            "checkpoint_id": "3" * 64,
        },
    }
    with pytest.raises(BoundaryError, match="model_package_binding_mismatch"):
        require_structured_model_package(legacy, scoped_package)
    scoped = Manifest(
        "model",
        origin,
        parents,
        parameters=FrozenObject.of(
            {
                "schema": SCOPED_MODEL_SCHEMA,
                "model_id": "4" * 64,
                "graph_id": "fixture-graph",
                "qualification": "engineering_only",
                "attempt": "a" * 32,
            }
        ),
    )
    require_structured_model_package(scoped, scoped_package)
    with pytest.raises(BoundaryError, match="model_export_provenance_mismatch"):
        require_structured_model_package(
            scoped,
            {
                **scoped_package,
                "provenance": {**scoped_package["provenance"], "export_producer": {}},
            },
        )
    for schema in ("stpd/structured-m2-model-v1", "stpd/structured-m2-model-v2"):
        assert structured_model_package_schema(schema) == "stpd/structured-m2-package-v1"


@pytest.mark.parametrize("schema", ["stpd/structured-m2-model-v1", "stpd/structured-m2-model-v2"])
def test_domain_rejects_scoped_package_with_legacy_artifact(tmp_path, exported, schema):
    store, _ = originals.setup_store(tmp_path)
    origin = Producer(
        "fixture://scoped-package",
        "a" * 40,
        hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
    )
    package = tmp_path / "scoped-package"
    metadata = export_structured_package(
        exported[2],
        package,
        source_revision="a" * 40,
        data_sha256="b" * 64,
        source_kind="synthetic",
        teacher_sha256="c" * 64,
        training={"fixture": True},
        code_scope=INFERENCE_SCOPE,
        provenance={
            "training_producer": origin.to_dict(),
            "export_producer": origin.to_dict(),
            "run_id": "1" * 64,
            "training_input_id": "2" * 64,
            "checkpoint_id": "3" * 64,
        },
    )
    payloads = tuple(
        store.put_payload(role, io.BytesIO((package / name).read_bytes()), media)
        for role, name, media in (
            ("package_manifest", "model.json", "application/json"),
            ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
        )
    )
    model = Manifest(
        "model",
        origin,
        payloads=payloads,
        parameters=FrozenObject.of({"schema": schema, "model_id": metadata["model_id"]}),
    )
    store.publish(model)
    with pytest.raises(BoundaryError, match="model_package_binding_mismatch"):
        _model(store, model.artifact_id)


@pytest.mark.parametrize("schema", [None, [], {}, 3, "stpd/structured-m2-model-v4"])
def test_non_string_and_unknown_schema_cannot_acquire_structured_dispatch(schema):
    from stpd.structured_code_scope import is_structured_model_schema

    assert is_structured_model_schema(schema) is False
    with pytest.raises(BoundaryError, match="unsupported_model_schema"):
        structured_model_package_schema(schema)


def test_scoped_current_event_cannot_use_a_coherent_foreign_producer(tmp_path):
    root = copied_source(tmp_path)
    store, owner = originals.setup_store(tmp_path)
    ref, _, _ = originals.publish_run(store, tmp_path)
    source = originals.sources.publish_protocol_source_partition(
        store, (ref,), "train", originals.PROJECTION
    )
    owner.reserve_verified_protocol_source(store, source.manifest.artifact_id)
    script = r"""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from spireagent.artifact_contracts import Manifest,Producer
from spireagent.json_boundary import BoundaryError,FrozenObject,json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from spireagent.workbench.developer import ProjectConfig,combination
from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as adapter
store=ManifestArtifactStore(LocalBlobStore(Path(sys.argv[4]),create=False))
service=LocalTrainingService(ProjectConfig(Path(sys.argv[2]),'', '',None,combination()))
original=adapter._private_child
replaced=False
def launch(command,*args,**kwargs):
    callback=kwargs['on_stdout_line']
    def line(raw):
        global replaced
        value=json.loads(raw)
        if value['kind']=='event' and not replaced:
            event=store.get_manifest(value['details']['event_id'])
            producer=Producer(event.producer.repository,'f'*40,event.producer.uv_lock_sha256)
            info=event.parameters.value()
            forged=Manifest(event.kind,producer,event.parents,event.payloads,FrozenObject.of({
                **info,'details':{**info.get('details',{}),'attempt_producer':producer.to_dict()}}))
            store.publish(forged)
            value['details']['event_id']=forged.artifact_id
            raw=json_bytes(value)
            replaced=True
        callback(raw)
    return original(command,*args,**{**kwargs,'on_stdout_line':line})
adapter._private_child=launch
service.start(TrainingRequest('1'*32,'structured-m2-cpu-v3',sys.argv[3],{'epochs':1}))
service._thread.join(timeout=30)
assert not service._thread.is_alive()
operation=service.status()['operation']
assert replaced and operation['status']=='interrupted_unknown',operation
assert operation['error']['code']=='child_event_invalid',operation
assert operation['child_exit']['forced'] is True
assert operation['worker_state']=='terminal'
before=store.manifest_ids()
repeated=service.start(TrainingRequest('1'*32,'structured-m2-cpu-v3',sys.argv[3],{'epochs':1}))
assert repeated['operation']['attempt_id']==operation['attempt_id']
assert repeated['operation']['status']=='interrupted_unknown'
assert store.manifest_ids()==before
try:
    service.start(TrainingRequest('2'*32,'structured-m2-cpu-v3',sys.argv[3],{'epochs':1}))
    raise AssertionError('unknown writer retried')
except BoundaryError as error:
    assert error.code=='previous_training_outcome_unknown'
print(json.dumps({'code':operation['error']['code'],'forced':operation['child_exit']['forced']}))
"""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "STPD_HUB_ADMIN_TOKEN"}
    }
    environment.update(OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            str(tmp_path / "state"),
            source.manifest.artifact_id,
            str(owner.store_dir),
        ],
        capture_output=True,
        text=True,
        timeout=45,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"code": "child_event_invalid", "forced": True}
