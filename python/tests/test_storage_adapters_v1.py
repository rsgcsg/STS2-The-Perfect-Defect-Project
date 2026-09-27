from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_artifact_store_v1 import PRODUCER, store

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError
from spireagent.storage.blobs import StoreError
from spireagent.storage.registry import SQLiteRegistry, sync_registry
from spireagent.storage.s3 import S3BlobStore, S3Config
from spireagent.storage.store import ManifestArtifactStore, copy_artifact


class FakeS3Error(Exception):
    def __init__(self, code: str) -> None:
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.conflicts = 0
        self.unsupported = False

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["IfNoneMatch"] == "*"
        assert kwargs["ContentLength"] == len(kwargs["Body"])
        self.puts += 1
        if self.unsupported:
            raise FakeS3Error("NotImplemented")
        if self.conflicts:
            self.conflicts -= 1
            raise FakeS3Error("ConditionalRequestConflict")
        if kwargs["Key"] in self.objects:
            raise FakeS3Error("PreconditionFailed")
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        try:
            value = self.objects[kwargs["Key"]]
        except KeyError:
            raise FakeS3Error("NoSuchKey") from None
        return {"Body": io.BytesIO(value), "ContentLength": len(value)}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        values = sorted(key for key in self.objects if key.startswith(kwargs["Prefix"]))
        index = int(kwargs.get("ContinuationToken", "0"))
        more = index + 1 < len(values)
        return {
            "Contents": [{"Key": key} for key in values[index : index + 1]],
            "IsTruncated": more,
            "NextContinuationToken": str(index + 1),
        }


def test_same_domain_flow_on_local_and_s3_contract(tmp_path: Path) -> None:
    local = store(tmp_path / "local")
    remote = ManifestArtifactStore(S3BlobStore(S3Config("bucket"), FakeS3()))
    data = local.put_bytes("data", b"dataset")
    dataset = Manifest("dataset", PRODUCER, payloads=(data,))
    local.publish(dataset)
    model = Manifest("model", PRODUCER, parents=(Parent("dataset", dataset.artifact_id),))
    local.publish(model)
    copy_artifact(local, remote, model.artifact_id)
    copy_artifact(local, remote, model.artifact_id)
    assert local.manifest_ids() == remote.manifest_ids()
    assert remote.get_manifest(dataset.artifact_id) == dataset
    assert remote.bytes(data) == b"dataset"


def test_s3_conditional_write_failures_never_fall_back_to_overwrite() -> None:
    fake = FakeS3()
    adapter = S3BlobStore(S3Config("bucket"), fake)
    fake.conflicts = 2
    assert adapter.put_if_absent("objects/a", b"x")
    assert not adapter.put_if_absent("objects/a", b"x")
    with pytest.raises(StoreError, match="collision"):
        adapter.put_if_absent("objects/a", b"y")
    assert adapter.get("objects/a") == b"x"
    fake.unsupported = True
    with pytest.raises(StoreError, match="not_supported"):
        adapter.put_if_absent("objects/b", b"x")
    assert "stpd/objects/b" not in fake.objects


@pytest.mark.parametrize(
    "endpoint",
    ["http://example.com", "https://key:secret@example.com", "https://example.com?token=abc"],
)
def test_secrets_and_unencrypted_remote_endpoints_rejected(endpoint: str) -> None:
    with pytest.raises(StoreError):
        S3Config("bucket", endpoint=endpoint)


def test_registry_rebuild_after_deletion_and_transactional_rejection(tmp_path: Path) -> None:
    source = store(tmp_path / "source")
    dataset = Manifest("dataset", PRODUCER)
    source.publish(dataset)
    model = Manifest("model", PRODUCER, parents=(Parent("dataset", dataset.artifact_id),))
    source.publish(model)
    path = tmp_path / "registry.sqlite"
    index = SQLiteRegistry(path)
    assert sync_registry(source, index, frozenset({dataset.artifact_id})) == 2
    assert len(index.lineage(model.artifact_id)) == 2
    assert index.is_cached(dataset.artifact_id)
    assert not index.is_cached(model.artifact_id)
    with pytest.raises(BoundaryError, match="dangling_parent"):
        index.rebuild([model])
    assert index.get(dataset.artifact_id) == dataset
    path.unlink()
    index = SQLiteRegistry(path)
    assert sync_registry(source, index) == 2
    assert index.get(model.artifact_id) == model
    assert len(index.manifests("dataset")) == 1


def test_registry_rejects_future_schema_and_cached_tamper(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite"
    index = SQLiteRegistry(path)
    manifest = Manifest("dataset", PRODUCER)
    index.rebuild([manifest])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE artifacts SET manifest=?", (b"{}",))
    with pytest.raises(BoundaryError):
        index.get(manifest.artifact_id)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(BoundaryError, match="unsupported_cache_schema"):
        SQLiteRegistry(path)


def test_read_only_registry_requires_existing_schema_and_rejects_rebuild(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "registry.sqlite"
    with pytest.raises(BoundaryError, match="not_configured"):
        SQLiteRegistry(missing, readonly=True)
    assert not missing.parent.exists()

    uninitialized = tmp_path / "uninitialized.sqlite"
    uninitialized.touch()
    with pytest.raises(BoundaryError, match="unsupported_or_uninitialized_cache"):
        SQLiteRegistry(uninitialized, readonly=True)
    assert uninitialized.stat().st_size == 0

    writable = SQLiteRegistry(tmp_path / "ready.sqlite")
    manifest = Manifest("dataset", PRODUCER)
    writable.rebuild([manifest])
    readonly = SQLiteRegistry(writable.path, readonly=True)
    assert readonly.get(manifest.artifact_id) == manifest
    with pytest.raises(BoundaryError, match="read_only"):
        readonly.rebuild([manifest])


def test_s3_wire_contract_uses_real_sdk_validation_without_network() -> None:
    import base64
    import hashlib

    import boto3
    from botocore.stub import Stubber

    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="https://example.invalid",
        aws_access_key_id="fixture",
        aws_secret_access_key="fixture",
    )
    adapter = S3BlobStore(S3Config("bucket"), client)
    raw = b"wire-contract"
    expected = {
        "Bucket": "bucket",
        "Key": "stpd/objects/a",
        "Body": raw,
        "ContentLength": len(raw),
        "IfNoneMatch": "*",
        "ContentMD5": base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode(
            "ascii"
        ),
        "Metadata": {"sha256": hashlib.sha256(raw).hexdigest()},
    }
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, expected)
        stub.add_client_error(
            "put_object",
            service_error_code="PreconditionFailed",
            http_status_code=412,
            expected_params=expected,
        )
        stub.add_response(
            "get_object",
            {"Body": io.BytesIO(raw), "ContentLength": len(raw)},
            {"Bucket": "bucket", "Key": "stpd/objects/a"},
        )
        assert adapter.put_if_absent("objects/a", raw)
        assert not adapter.put_if_absent("objects/a", raw)
        stub.assert_no_pending_responses()


def test_retry_copy_does_not_redownload_existing_verified_payloads(tmp_path: Path) -> None:
    class Source(ManifestArtifactStore):
        reads = 0

        def read_payload(self, payload):
            self.reads += 1
            yield from super().read_payload(payload)

    from spireagent.storage.local import LocalBlobStore

    source = Source(LocalBlobStore(tmp_path / "source"))
    target = store(tmp_path / "target")
    payload = source.put_bytes("data", b"data")
    manifest = Manifest("dataset", PRODUCER, payloads=(payload,))
    source.publish(manifest)
    copy_artifact(source, target, manifest.artifact_id)
    previous = source.reads
    copy_artifact(source, target, manifest.artifact_id)
    assert source.reads == previous
