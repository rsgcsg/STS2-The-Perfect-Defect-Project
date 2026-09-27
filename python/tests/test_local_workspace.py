from __future__ import annotations

from pathlib import Path

import pytest
from test_artifact_store_v1 import PRODUCER, store

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.workbench.developer import LocalResearchWorkspaceConfig
from spireagent.workbench.local_workspace import LocalWorkspace, open_registered_workspace


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
