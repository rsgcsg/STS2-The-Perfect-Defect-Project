"""Real completed M2 recipes, transferred into independent test workspaces.

Only immutable manifests/payloads and verified typed projections are shared.
Each clone creates its own owner, registry and ledger through production APIs;
no database, owner identity, export receipt or worker completion is fabricated.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_local_training import _human_ready

from spireagent.storage.local import LocalBlobStore
from spireagent.storage.registry import SQLiteRegistry
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.developer import LocalResearchWorkspaceConfig, ProjectConfig, atomic_json
from spireagent.workbench.inplace_curation import InplaceCurationPreparation, configured_owner
from spireagent.workbench.local_curation import LocalCurationOwner
from spireagent.workbench.memory_training import prepare_workbench_memory
from stpd.fullrun.contracts import SourceProjection
from stpd.fullrun.decision_dataset import SelectionRules
from stpd.fullrun.decision_store import preview
from stpd.fullrun.text_menu_human_import import load_verified_human_text_bundle
from stpd.workers.memory_run import execute_memory_run

M2Fixture = tuple[ProjectConfig, LocalCurationOwner, ManifestArtifactStore, str,
                  list[str], set[str], str, str]


@dataclass(frozen=True)
class CompletedM2:
    config: ProjectConfig
    store: ManifestArtifactStore
    dataset_id: str
    sources: tuple[str, ...]
    runs: frozenset[str]
    run_id: str
    model_id: str
    claim_id: str
    projections: tuple[SourceProjection, ...]


def _produce(root: Path, recipe: str) -> CompletedM2:
    with pytest.MonkeyPatch.context() as patcher:
        config, dataset_id, sources, store = _human_ready(root, patcher)
        owner = configured_owner(config)
        runs: set[str] = set()
        projections: list[SourceProjection] = []
        for source in sources:
            _, bundle, _ = load_verified_human_text_bundle(store, source)
            runs.update(bundle.session_id + "/" + run for run in bundle.run_ids)
            selected = preview(store, (store.get_manifest(source),), SelectionRules(),
                               on_projection=projections.append)
            if close := getattr(selected, "close", None):
                close()
        operation_id = "a" * 32
        for source in sources:
            owner.ledger.use_source(source, "training", operation_id)
        owner.ledger.use(runs, "training", operation_id)
        producer = store.get_manifest(dataset_id).producer
        run_id, _ = prepare_workbench_memory(store, dataset_id, producer, operation_id, recipe)
        outcome = execute_memory_run(
            store, ObjectStoreRunReporter(store, store.blobs), run_id, producer)
        assert outcome.state == "completed" and outcome.result_id is not None
        model_id = store.get_manifest(outcome.result_id).parent("model")
        with owner.transaction() as db:
            claim_id, = db.execute("SELECT id FROM curation_claims WHERE artifact=?",
                                   (dataset_id,)).fetchone()
        assert len(projections) == len(sources)
    assert isinstance(store.blobs, LocalBlobStore)
    readonly = ManifestArtifactStore(LocalBlobStore(store.blobs.root, create=False, readonly=True))
    return CompletedM2(config, readonly, dataset_id, tuple(sources), frozenset(runs),
                       run_id, model_id, claim_id, tuple(projections))


class CompletedM2Recipes:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._recipes: dict[str, CompletedM2] = {}

    def get(self, recipe: str) -> CompletedM2:
        if recipe not in self._recipes:
            root = self.root / str(len(self._recipes))
            root.mkdir()
            self._recipes[recipe] = _produce(root, recipe)
        return self._recipes[recipe]

    def clone(self, recipe: str, root: Path) -> M2Fixture:
        return clone_completed_m2(self.get(recipe), root)


@pytest.fixture(scope="session")
def completed_m2_recipes(tmp_path_factory: pytest.TempPathFactory) -> CompletedM2Recipes:
    return CompletedM2Recipes(tmp_path_factory.mktemp("completed-m2-recipes"))


def clone_completed_m2(source: CompletedM2, root: Path) -> M2Fixture:
    library = root / "library"
    store = ManifestArtifactStore(LocalBlobStore(library / "store"))
    registry = SQLiteRegistry(library / "registry.sqlite")
    state = root / "state"
    state.mkdir(parents=True)
    config = replace(source.config, state_dir=state,
                     research_workspace=LocalResearchWorkspaceConfig(
                         store.blobs.root, registry.path))
    # Preserve the original pre-owner evidence and historical-use classification.
    for identity in source.store.manifest_ids():
        manifest = source.store.get_manifest(identity)
        if manifest.kind == "evidence" and identity not in source.sources:
            copy_artifact(source.store, store, identity)
    registry.rebuild(store.get_manifest(identity) for identity in store.manifest_ids())
    preparation = InplaceCurationPreparation(config)
    preparation.start()
    assert preparation.thread is not None
    preparation.thread.join(timeout=30)
    assert not preparation.thread.is_alive()
    assert preparation.status()["status"] == "ready", preparation.status()
    owner = configured_owner(config)
    # Transfer the full store, including the completed result/reporter pointers.
    for identity in source.store.manifest_ids():
        copy_artifact(source.store, store, identity)
    for prefix in ("run-events/", "run-completions/"):
        for key in source.store.blobs.keys(prefix):
            store.blobs.put_if_absent(key, source.store.blobs.get(key))
    assert ObjectStoreRunReporter(store, store.blobs).completed(source.run_id) == (
        ObjectStoreRunReporter(source.store, source.store.blobs).completed(source.run_id))
    registry.rebuild(store.get_manifest(identity) for identity in store.manifest_ids())
    for identity, projection in zip(source.sources, source.projections, strict=True):
        candidate = store.get_manifest(identity).parameters.value()["candidate_id"]
        owner.begin_source(candidate)
        owner.published_source(candidate, identity)
        owner.ledger.index_source(identity, projection)
        owner.index_human_runs(store, identity)
        owner.complete_index(candidate, identity)
    owner.ledger.claim(source.claim_id, "training", source.runs)
    owner.ledger.bind(source.claim_id, source.dataset_id)
    for identity in source.sources:
        owner.ledger.use_source(identity, "training", "a" * 32)
    owner.ledger.use(source.runs, "training", "a" * 32)
    # The only changed application-state field is its operational owner context.
    # Immutable producer IDs, payloads, manifest bytes and learned weights stay exact.
    shutil.copytree(source.config.state_dir, state, dirs_exist_ok=True)
    operation = state / "local-dataset-operation.json"
    value = json.loads(operation.read_bytes())
    value["_owner"] = list(owner.identity)
    atomic_json(operation, value)
    return (config, owner, store, source.dataset_id, list(source.sources), set(source.runs),
            source.run_id, source.model_id)
