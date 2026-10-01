from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store

import spireagent.workbench.local_workspace as local_workspace_module
from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.workbench import managed_local_workspace as managed
from spireagent.workbench.developer import LocalResearchWorkspaceConfig
from spireagent.workbench.local_workspace import LocalWorkspace, open_registered_workspace
from spireagent.workbench.memory_recipe import (
    M2_K1_RECIPE,
    RESET_K1_RECIPE,
    V2_M2_K1_RECIPE,
    V2_RESET_K1_RECIPE,
    recipe_for_memory_config,
)
from stpd.fullrun.memory_sequence_bridge import (
    MemoryEpisodeProjectionConfig,
    v2_episode_projection_config,
)
from stpd.workers.memory_ranking import MemoryConfig


def fixture(tmp_path: Path):
    artifact_store = store(tmp_path / "store")
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    payload = artifact_store.put_bytes("dataset", b"private dataset bytes")
    dataset = Manifest(
        "dataset",
        PRODUCER,
        payloads=(payload,),
        parameters=FrozenObject.of(
            {"name": "alpha sample", "private_path": "/private/raw/session.json"}
        ),
    )
    artifact_store.publish(dataset)
    model = Manifest(
        "model",
        PRODUCER,
        parents=(Parent("dataset", dataset.artifact_id),),
        parameters=FrozenObject.of({"name": "beta model"}),
    )
    artifact_store.publish(model)
    sync_registry(artifact_store, registry, frozenset({dataset.artifact_id}))
    return artifact_store, registry, dataset, model


def test_inventory_pages_and_searches_local_metadata_only(tmp_path: Path) -> None:
    artifact_store, registry, dataset, model = fixture(tmp_path)

    def forbidden_payload_read(_payload):
        raise AssertionError("inventory must not open payload bytes")

    artifact_store.read_payload = forbidden_payload_read
    browser = LocalWorkspace(registry, artifact_store)
    first = browser.inventory(limit=1)
    second = browser.inventory(limit=1, offset=1)
    searched = browser.inventory(kind="dataset", query="ALPHA")

    assert first["source"] == "configured_local_artifact_store"
    assert first["total"] == second["total"] == 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["artifact_id"] != second["items"][0]["artifact_id"]
    assert searched["total"] == 1
    item = searched["items"][0]
    assert item["artifact_id"] == dataset.artifact_id
    assert item["registry_indexed"] is True
    assert item["registry_cached"] is True
    assert item["payloads"] == [
        {
            "role": "dataset",
            "sha256": dataset.payloads[0].sha256,
            "size": dataset.payloads[0].size,
            "media_type": "application/json",
        }
    ]
    assert "/private/raw/session.json" not in str(item)
    model_view = browser.artifact(model.artifact_id)
    assert model_view["artifact_id"] == model.artifact_id
    assert model_view["parents"] == [{"role": "dataset", "artifact_id": dataset.artifact_id}]


def test_exact_artifact_view_uses_manifest_identity_and_safe_descriptors(tmp_path: Path) -> None:
    artifact_store, registry, dataset, model = fixture(tmp_path)
    artifact_store.read_payload = lambda _payload: (_ for _ in ()).throw(
        AssertionError("artifact view must not open payload bytes")
    )
    value = LocalWorkspace(registry, artifact_store).artifact(model.artifact_id)

    assert value["artifact_id"] == model.artifact_id
    assert value["source"] == "configured_local_artifact_store"
    assert value["producer"]["source_revision"] == PRODUCER.source_revision
    assert value["parents"] == [{"role": "dataset", "artifact_id": dataset.artifact_id}]
    assert value["payloads"] == []
    assert value["registry_cached"] is False
    assert "workbench_memory_recipe" not in value


def test_data_facts_are_bounded_metadata_and_read_only_with_duplicate_run_labels(
    tmp_path: Path, monkeypatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registration = managed.create_managed_workspace(state_dir)
    workspace_dir = state_dir / managed.ROOT_NAME / registration["workspace_id"]
    artifact_store = store(workspace_dir / "store")
    owner = registration["curation_owner"]
    source_ids = []
    source_archives = []
    run_ids = []
    evidence = []
    for number, session in enumerate(("session-a", "session-b")):
        content_id = ("a" if number == 0 else "b") * 64
        source = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
            "schema": "stpd/local-verified-bundle-v1", "content_id": content_id,
        }))
        artifact_store.publish(source)
        evidence.append(source)
        source_ids.append(source.artifact_id)
        source_archives.append(content_id)
        run_ids.append(f"{session}/run-0001")

    original = Manifest("dataset", PRODUCER, parents=tuple(
        Parent(f"source_{item.artifact_id}", item.artifact_id) for item in evidence
    ), parameters=FrozenObject.of({
        "schema": "stpd/fullrun-dataset-v1", "logical_id": "d" * 64,
        "records": 814, "runs": 2,
    }))
    artifact_store.publish(original)
    curated = Manifest("dataset", PRODUCER,
        parents=(Parent("dataset_" + original.artifact_id, original.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/curated-decision-dataset-v1", "logical_id": "e" * 64,
            "purpose": "training", "records": 814, "runs": 2,
            "split_status": "insufficient_independent_run_components",
        }))
    artifact_store.publish(curated)
    model = Manifest("model", PRODUCER,
        parents=(Parent("training_input", curated.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/stage1a-model-v1", "steps": 10}))
    artifact_store.publish(model)
    evaluation = Manifest("offline_evaluation", PRODUCER,
        parents=(Parent("model", model.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/stage1a-ranking-evaluation-v1", "partition": "dev",
        }))
    artifact_store.publish(evaluation)
    run_result = Manifest("run_result", PRODUCER,
        parents=(Parent("model", model.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/run-result-v1",
                                    "state": "completed", "steps": 10}))
    artifact_store.publish(run_result)
    with owner.transaction() as db:
        for source_id, run_id, archive in zip(
            source_ids, run_ids, source_archives, strict=True
        ):
            db.execute("INSERT INTO curation_sources VALUES(?,?,1)", (source_id, archive))
            db.execute("INSERT INTO curation_exact_source_index VALUES(?)", (source_id,))
            db.execute("INSERT INTO curation_source_runs VALUES(?,?)", (source_id, run_id))

    registry = SQLiteRegistry(workspace_dir / "registry.sqlite")
    sync_registry(artifact_store, registry)
    artifact_store.read_payload = lambda _payload: (_ for _ in ()).throw(
        AssertionError("data facts must never read payload bytes")
    )
    registry_before = registry.path.read_bytes()
    ledger_before = owner.path.read_bytes()
    real_connect = local_workspace_module.sqlite3.connect
    readonly_ledger_connections = []

    def checked_connect(database, *args, **kwargs):
        if str(database).startswith(owner.path.resolve().as_uri()):
            assert "mode=ro" in str(database)
            assert kwargs.get("uri") is True
            readonly_ledger_connections.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(local_workspace_module.sqlite3, "connect", checked_connect)
    monkeypatch.setattr(
        owner, "transaction", lambda: pytest.fail("data facts must not call owner writers")
    )
    workspace = LocalWorkspace(registry, artifact_store)
    workspace.curation_owner = owner
    facts = workspace.artifact(curated.artifact_id)["data_facts"]

    assert facts["dataset"]["samples"] == {
        "selected": 814, "excluded": None, "excluded_known": False,
    }
    assert facts["recording"]["bundle_count"] == 2
    assert facts["recording"]["session_count"] == 2
    assert facts["recording"]["qualified_run_occurrence_count"] == 2
    assert facts["recording"]["native_starts"] == {"known": False, "value": None}
    assert facts["recording"]["native_ends"] == {"known": False, "value": None}
    assert facts["recording"]["physical_game_independence"] == "unresolved"
    assert [row["artifact_id"] for row in facts["descendants"]["models"]] == [model.artifact_id]
    assert facts["descendants"]["models"][0]["producer_completion_status"] == "completed"
    assert [row["artifact_id"] for row in facts["descendants"]["evaluations"]] == [
        evaluation.artifact_id
    ]
    assert facts["descendants"]["run_results"][0]["state"] == "completed"
    assert facts["ledger"]["coverage"] == "incomplete"
    assert facts["ledger"]["label"] == "历史使用记录不完整"
    assert facts["ledger"]["uses"] == []
    assert "never" not in facts["ledger"]["label"].lower()
    assert facts["ledger"]["historical_manual_exposure"] == "unknown"
    assert facts["user_declaration"]["status"] == "not_durably_registered"
    assert facts["eligibility"]["clean_dev_test_claim"] is False
    assert facts["eligibility"]["gold_claim"] is False
    assert registry.path.read_bytes() == registry_before
    assert owner.path.read_bytes() == ledger_before
    assert readonly_ledger_connections


def test_data_facts_report_truncation_when_manifest_inventory_is_capped(
    tmp_path: Path, monkeypatch,
) -> None:
    artifact_store, registry, dataset, _model = fixture(tmp_path)
    monkeypatch.setattr(local_workspace_module, "DATA_FACT_MANIFEST_LIMIT", 1)

    facts = LocalWorkspace(registry, artifact_store).artifact(dataset.artifact_id)["data_facts"]

    assert facts["status"] == "partial"
    assert facts["lineage"]["truncated"] is True
    assert len(facts["lineage"]["datasets"]) <= 1


def test_dataset_data_facts_stay_on_selected_manifest_ancestry(tmp_path: Path) -> None:
    artifact_store = store(tmp_path / "store")
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    first_source = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/local-verified-bundle-v1", "content_id": "a" * 64,
    }))
    artifact_store.publish(first_source)
    selected = Manifest("dataset", PRODUCER,
        parents=(Parent("source", first_source.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/fullrun-dataset-v1", "records": 100, "purpose": "source",
        }))
    artifact_store.publish(selected)
    curated_child = Manifest("dataset", PRODUCER,
        parents=(Parent("dataset", selected.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/curated-decision-dataset-v1", "records": 2,
            "purpose": "training",
        }))
    artifact_store.publish(curated_child)
    second_source = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/local-verified-bundle-v1", "content_id": "b" * 64,
    }))
    artifact_store.publish(second_source)
    other_dataset = Manifest("dataset", PRODUCER,
        parents=(Parent("source", second_source.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/fullrun-dataset-v1", "records": 50}))
    artifact_store.publish(other_dataset)
    model = Manifest("model", PRODUCER,
        parents=(Parent("dataset_a", selected.artifact_id),
                 Parent("dataset_b", other_dataset.artifact_id)),
        parameters=FrozenObject.of({"schema": "model/fixture"}))
    artifact_store.publish(model)
    sync_registry(artifact_store, registry)

    facts = LocalWorkspace(registry, artifact_store).artifact(selected.artifact_id)["data_facts"]

    assert facts["dataset"]["artifact_id"] == selected.artifact_id
    assert facts["dataset"]["samples"]["selected"] == 100
    assert facts["dataset"]["purpose"] == "source"
    assert [item["artifact_id"] for item in facts["lineage"]["datasets"]] == [
        selected.artifact_id
    ]
    assert facts["recording"]["bundle_count"] == 1
    assert [item["artifact_id"] for item in facts["lineage"]["recording_bundles"]] == [
        first_source.artifact_id
    ]


def test_unverified_source_index_keeps_run_and_session_counts_unknown(tmp_path: Path) -> None:
    artifact_store = store(tmp_path / "store")
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    source = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/local-verified-bundle-v1", "content_id": "c" * 64,
    }))
    artifact_store.publish(source)
    dataset = Manifest("dataset", PRODUCER,
        parents=(Parent("source", source.artifact_id),),
        parameters=FrozenObject.of({"schema": "stpd/fullrun-dataset-v1", "records": 4}))
    artifact_store.publish(dataset)
    sync_registry(artifact_store, registry)

    facts = LocalWorkspace(registry, artifact_store).artifact(dataset.artifact_id)["data_facts"]

    assert facts["recording"]["bundle_count"] == 1
    assert facts["recording"]["session_count"] is None
    assert facts["recording"]["qualified_run_occurrence_count"] is None
    assert facts["ledger"]["source_index_status"] == "missing_or_unverified"
    assert facts["ledger"]["status"] == "not_available"


def test_unbound_training_use_does_not_claim_complete_model_history(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    registration = managed.create_managed_workspace(state_dir)
    workspace_dir = state_dir / managed.ROOT_NAME / registration["workspace_id"]
    artifact_store = store(workspace_dir / "store")
    owner = registration["curation_owner"]
    source = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/local-verified-bundle-v1", "content_id": "d" * 64,
    }))
    artifact_store.publish(source)
    dataset = Manifest("dataset", PRODUCER,
        parents=(Parent("source", source.artifact_id),),
        parameters=FrozenObject.of({
            "schema": "stpd/curated-decision-dataset-v1", "records": 4,
            "purpose": "training",
        }))
    artifact_store.publish(dataset)
    for number in (1, 2):
        model = Manifest("model", PRODUCER,
            parents=(Parent("training_input", dataset.artifact_id),),
            parameters=FrozenObject.of({"schema": "model/fixture", "iteration": number}))
        artifact_store.publish(model)
        artifact_store.publish(Manifest("run_result", PRODUCER,
            parents=(Parent("model", model.artifact_id),),
            parameters=FrozenObject.of({"state": "completed", "iteration": number})))
    with owner.transaction() as db:
        db.execute("INSERT INTO curation_sources VALUES(?,?,1)", (source.artifact_id, "d" * 64))
        db.execute("INSERT INTO curation_exact_source_index VALUES(?)", (source.artifact_id,))
        db.execute(
            "INSERT INTO curation_source_runs VALUES(?,?)",
            (source.artifact_id, "session-a/run-0001"),
        )
        db.execute(
            "INSERT INTO curation_source_uses VALUES(?,?,?)",
            (source.artifact_id, "training", "unrelated-operation"),
        )
    registry = SQLiteRegistry(workspace_dir / "registry.sqlite")
    sync_registry(artifact_store, registry)
    workspace = LocalWorkspace(registry, artifact_store)
    workspace.curation_owner = owner

    facts = workspace.artifact(dataset.artifact_id)["data_facts"]

    assert len(facts["descendants"]["models"]) == 2
    assert facts["ledger"]["uses"] == [{
        "scope": "source", "identity": source.artifact_id,
        "kind": "training", "reference": "unrelated-operation",
    }]
    assert facts["ledger"]["coverage"] == "incomplete"
    assert facts["ledger"]["historical_manual_exposure"] == "unknown"
    assert facts["status"] == "partial"


@pytest.mark.parametrize(("reset", "v2", "expected"), [
    (False, False, M2_K1_RECIPE),
    (True, False, RESET_K1_RECIPE),
    (False, True, V2_M2_K1_RECIPE),
    (True, True, V2_RESET_K1_RECIPE),
])
def test_memory_artifact_view_derives_only_supported_workbench_recipe(
        tmp_path: Path, reset: bool, v2: bool, expected: str) -> None:
    artifact_store = store(tmp_path / "store")
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    config = asdict(MemoryConfig(vocab_size=32, episode_count=2,
                                 width=48, layers=1, heads=2,
                                 feedforward=96, max_tokens=16384,
                                 max_total_input_tokens=4_194_304,
                                 max_episode_observations=768,
                                 max_episode_input_tokens=4_194_304,
                                 max_chunk_steps=2,
                                 max_chunk_input_tokens=24_576,
                                 max_actions_per_step=256,
                                 reset_each_step=reset))
    source = Manifest("dataset", PRODUCER, parameters=FrozenObject.of({
        "schema": ("stpd/managed-text-menu-observed-source-v1" if v2
                   else "stpd/human-text-input-source-v1")}))
    artifact_store.publish(source)
    projection = (v2_episode_projection_config() if v2 else
                  MemoryEpisodeProjectionConfig(
                      "stpd/memory-episode-projection-config-v1", 64))
    training_input = Manifest("training_input", PRODUCER,
                              parents=(Parent("source", source.artifact_id),),
                              parameters=FrozenObject.of({
                                  "schema": "stpd/experimental-m2-training-input-v2",
                                  "projection_config": asdict(projection)}))
    artifact_store.publish(training_input)
    run = Manifest("run", PRODUCER,
                   parents=(Parent("training_input", training_input.artifact_id),),
                   parameters=FrozenObject.of({"config": config}))
    artifact_store.publish(run)
    model = Manifest("model", PRODUCER,
                     parents=(Parent("run", run.artifact_id),
                              Parent("training_input", training_input.artifact_id)),
                     parameters=FrozenObject.of({
        "schema": "stpd/experimental-m2-model-v1", "config": config,
        "episodes": 2, "partition": "train", "qualification": "engineering_only",
    }))
    artifact_store.publish(model)
    artifact_store.read_payload = lambda _payload: (_ for _ in ()).throw(
        AssertionError("model detail must not read payload bytes")
    )
    value = LocalWorkspace(registry, artifact_store).artifact(model.artifact_id)
    assert value["workbench_memory_recipe"] == expected


@pytest.mark.parametrize("change", [
    "width", "missing_config_key", "dev_partition", "missing_qualification",
    "episode_count_mismatch",
])
def test_memory_artifact_view_does_not_guess_unsupported_recipe(
        tmp_path: Path, change: str) -> None:
    artifact_store = store(tmp_path / "store")
    registry = SQLiteRegistry(tmp_path / "registry.sqlite")
    config = asdict(MemoryConfig(vocab_size=32, episode_count=2,
                                 width=48, layers=1, heads=2,
                                 feedforward=96, max_tokens=16384,
                                 max_total_input_tokens=4_194_304,
                                 max_episode_observations=768,
                                 max_episode_input_tokens=4_194_304,
                                 max_chunk_steps=2,
                                 max_chunk_input_tokens=24_576,
                                 max_actions_per_step=256))
    assert recipe_for_memory_config(config) == M2_K1_RECIPE
    if change == "width":
        config["width"] = 96
    elif change == "missing_config_key":
        del config["reset_each_step"]
    parameters = {"schema": "stpd/experimental-m2-model-v1", "config": config,
                  "episodes": 2, "partition": "train",
                  "qualification": "engineering_only"}
    if change == "dev_partition":
        parameters["partition"] = "dev"
    elif change == "missing_qualification":
        del parameters["qualification"]
    elif change == "episode_count_mismatch":
        parameters["episodes"] = 3
    model = Manifest("model", PRODUCER, parameters=FrozenObject.of(parameters))
    artifact_store.publish(model)
    value = LocalWorkspace(registry, artifact_store).artifact(model.artifact_id)
    assert "workbench_memory_recipe" in value
    assert value["workbench_memory_recipe"] is None


def test_inventory_shows_store_records_missing_from_rebuildable_registry(tmp_path: Path) -> None:
    artifact_store, registry, dataset, _ = fixture(tmp_path)
    registry.rebuild([])
    value = LocalWorkspace(registry, artifact_store).inventory(kind="dataset")

    assert value["total"] == 1
    assert value["items"][0]["artifact_id"] == dataset.artifact_id
    assert value["items"][0]["registry_indexed"] is False
    assert value["items"][0]["registry_cached"] is None


def test_category_filters_exact_recording_schemas_before_paging_and_search(
    tmp_path: Path,
) -> None:
    artifact_store, registry, dataset, model = fixture(tmp_path)
    recordings = []
    for schema, disposition in (
        ("stpd/local-verified-bundle-v1", "locally_verified"),
        ("stpd/received-bundle-v1", "verified"),
        ("stpd/received-bundle-v1", "quarantined"),
    ):
        item = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
            "schema": schema, "disposition": disposition,
        }))
        artifact_store.publish(item)
        recordings.append(item.artifact_id)
    unrelated = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/verified-text-menu-agent-run-v1",
    }))
    artifact_store.publish(unrelated)
    report = Manifest("offline_evaluation", PRODUCER,
                      parameters=FrozenObject.of({"partition": "dev"}))
    artifact_store.publish(report)
    artifact_store.read_payload = lambda _payload: (_ for _ in ()).throw(
        AssertionError("category inventory must not read payload"))
    before_registry = registry.path.read_bytes()
    browser = LocalWorkspace(registry, artifact_store)

    recording_page = browser.inventory(category="recordings", limit=1, offset=1)
    assert recording_page["total"] == 3
    assert recording_page["category"] == "recordings"
    assert recording_page["items"][0]["artifact_id"] == sorted(recordings)[1]
    assert {item["artifact_id"] for item in browser.inventory(
        category="recordings", kind="evidence", limit=10)["items"]} == set(recordings)
    assert browser.inventory(category="recordings", kind="model")["total"] == 0
    assert browser.inventory(category="recordings", query="quarantined")["total"] == 1
    assert browser.inventory(category="datasets")["items"][0]["artifact_id"] == dataset.artifact_id
    assert browser.inventory(category="models")["items"][0]["artifact_id"] == model.artifact_id
    assert browser.inventory(category="reports")["items"][0]["artifact_id"] == report.artifact_id
    assert browser.inventory(category="all")["total"] == browser.inventory()["total"] == 7
    assert "category" not in browser.inventory()
    assert registry.path.read_bytes() == before_registry


@pytest.mark.parametrize("schema", [[], {}])
def test_recording_category_ignores_nonstring_schema_without_hiding_all(
    tmp_path: Path, schema: object,
) -> None:
    artifact_store, registry, _, _ = fixture(tmp_path)
    malformed = Manifest("evidence", PRODUCER,
                         parameters=FrozenObject.of({"schema": schema}))
    artifact_store.publish(malformed)
    recording = Manifest("evidence", PRODUCER, parameters=FrozenObject.of({
        "schema": "stpd/received-bundle-v1", "disposition": "verified",
    }))
    artifact_store.publish(recording)
    browser = LocalWorkspace(registry, artifact_store)

    assert [item["artifact_id"] for item in browser.inventory(category="recordings")["items"]] \
        == [recording.artifact_id]
    assert {item["artifact_id"] for item in browser.inventory(category="all")["items"]} \
        >= {malformed.artifact_id, recording.artifact_id}
    assert {item["artifact_id"] for item in browser.inventory()["items"]} \
        >= {malformed.artifact_id, recording.artifact_id}


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"kind": "unknown"}, "unknown_artifact_kind"),
        ({"category": "unknown"}, "unknown_category"),
        ({"category": ""}, "unknown_category"),
        ({"limit": 0}, "invalid_page_size"),
        ({"limit": 101}, "invalid_page_size"),
        ({"offset": -1}, "invalid_page_offset"),
        ({"query": "  "}, "invalid_search_query"),
        ({"query": "x\nsecret"}, "invalid_search_query"),
    ],
)
def test_inventory_rejects_unbounded_or_malformed_queries(
    tmp_path: Path, arguments: dict[str, object], code: str
) -> None:
    artifact_store, registry, _, _ = fixture(tmp_path)
    with pytest.raises(BoundaryError, match=code):
        LocalWorkspace(registry, artifact_store).inventory(**arguments)


def test_artifact_requires_exact_content_address(tmp_path: Path) -> None:
    artifact_store, registry, _, _ = fixture(tmp_path)
    with pytest.raises(BoundaryError, match="invalid_digest"):
        LocalWorkspace(registry, artifact_store).artifact("not-an-artifact-id")


def test_registered_workspace_is_read_only_and_never_bootstraps_missing_paths(
    tmp_path: Path,
) -> None:
    artifact_store, registry, dataset, _ = fixture(tmp_path)
    assert open_registered_workspace(None) is None
    registered = LocalResearchWorkspaceConfig(artifact_store.blobs.root, registry.path)
    readonly = open_registered_workspace(registered)
    assert readonly is not None
    assert readonly.artifact(dataset.artifact_id)["artifact_id"] == dataset.artifact_id

    missing_store = tmp_path / "missing-store"
    missing_registry = tmp_path / "missing-registry.sqlite"
    with pytest.raises(BoundaryError, match="store_not_found"):
        open_registered_workspace(LocalResearchWorkspaceConfig(missing_store, registry.path))
    with pytest.raises(BoundaryError, match="registry_not_found"):
        open_registered_workspace(
            LocalResearchWorkspaceConfig(artifact_store.blobs.root, missing_registry)
        )
    assert not missing_store.exists()
    assert not missing_registry.exists()
    with pytest.raises(BoundaryError, match="read_only_store"):
        readonly.store.blobs.put_if_absent("objects/new", b"forbidden")
