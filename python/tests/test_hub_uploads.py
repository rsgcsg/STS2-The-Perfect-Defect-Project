from __future__ import annotations

import concurrent.futures
import hashlib
import io
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sts2_platform_evidence import DirectoryTransferManifest

from spireagent.artifact_contracts import Producer
from spireagent.hub.database import Operations
from spireagent.hub.uploads import LocalStaging, UploadService
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore


def fixture(tmp_path: Path) -> tuple[UploadService, dict, bytes]:
    ops = Operations(tmp_path / "ops.sqlite")
    stage = LocalStaging(tmp_path / "stage", "http://127.0.0.1:1")
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    service = UploadService(ops, stage, store, Producer("test", "a" * 40, "b" * 64))
    directory = tmp_path / "source"
    directory.mkdir()
    (directory / "evidence.json").write_bytes(b"immutable test fixture")
    transfer = DirectoryTransferManifest.from_directory(
        directory, content_id="a" * 64, artifact_type="human-session-bundle"
    )
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        archive.add(directory / "evidence.json", arcname="evidence.json")
    data = output.getvalue()
    intent = {
        "schema": "stpd/upload-intent-v1",
        "transfer_manifest": transfer.to_dict(),
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_bytes": len(data),
    }
    return service, intent, data


def test_concurrent_local_publication_same_bytes_and_collision(tmp_path: Path) -> None:
    stage = LocalStaging(tmp_path, "http://127.0.0.1:1")
    data = b"some immutable bytes" * 1000
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(
            pool.map(lambda _: stage.write("upload", io.BytesIO(data), len(data)), range(4))
        )
    assert results == [None] * 4
    assert (tmp_path / "upload").read_bytes() == data
    with pytest.raises(BoundaryError, match="staging_collision"):
        stage.write("upload", io.BytesIO(b"other"), 5)
    assert (tmp_path / "upload").read_bytes() == data
    assert list(tmp_path.iterdir()) == [tmp_path / "upload"]


def test_missing_object_does_not_starve_and_owner_findings_are_preserved(tmp_path: Path) -> None:
    service, intent, data = fixture(tmp_path)
    missing = service.intent("missing", intent)["upload_id"]
    present = service.intent("present", intent)["upload_id"]
    assert isinstance(service.staging, LocalStaging)
    service.staging.write(present, io.BytesIO(data), len(data))
    service.operations.request_verification(missing)
    service.operations.request_verification(present)
    owner_result = SimpleNamespace(
        passed=False, findings=[SimpleNamespace(code="exact_owner_failure")]
    )
    clock = SimpleNamespace(now=100.0)
    with patch("spireagent.hub.uploads.time", SimpleNamespace(time=lambda: clock.now)):
        with patch("spireagent.hub.uploads.verify_human_session_bundle", return_value=owner_result):
            assert service.verify_pending() == 2
        assert service.operations.upload(missing)["status"] == "verification_pending"
        assert service.operations.upload(missing)["verify_attempts"] == 1
        receipt = service.operations.upload(present)["receipt"]
        assert "exact_owner_failure" in receipt
        assert service.operations.upload(present)["status"] == "quarantined"
        assert service.verify_pending() == 0
    for _attempt in range(4):
        service.operations.verification_failure(missing, "missing", now=time.time())
    assert service.operations.upload(missing)["status"] == "transfer_failed"


def test_pending_upload_can_be_retried_when_backoff_expires_during_drain(
    tmp_path: Path,
) -> None:
    service, intent, data = fixture(tmp_path)
    missing = service.intent("missing", intent)["upload_id"]
    present = service.intent("present", intent)["upload_id"]
    assert isinstance(service.staging, LocalStaging)
    service.staging.write(present, io.BytesIO(data), len(data))
    service.operations.request_verification(missing)
    service.operations.request_verification(present)
    clock = SimpleNamespace(now=100.0)

    def slow_owner_failure(_directory: Path) -> SimpleNamespace:
        clock.now += 2.1
        return SimpleNamespace(passed=False, findings=[SimpleNamespace(code="exact_owner_failure")])

    with patch("spireagent.hub.uploads.time", SimpleNamespace(time=lambda: clock.now)):
        with patch(
            "spireagent.hub.uploads.verify_human_session_bundle",
            side_effect=slow_owner_failure,
        ):
            assert service.verify_pending() == 3
        missing_row = service.operations.upload(missing)
        assert missing_row["status"] == "verification_pending"
        assert missing_row["verify_attempts"] == 2
        assert missing_row["retry_at"] == 106.1
        assert service.operations.upload(present)["status"] == "quarantined"
        assert service.verify_pending() == 0


def test_disk_failure_is_not_semantic_quarantine(tmp_path: Path) -> None:
    service, intent, data = fixture(tmp_path)
    upload_id = service.intent("one", intent)["upload_id"]
    assert isinstance(service.staging, LocalStaging)
    service.staging.write(upload_id, io.BytesIO(data), len(data))
    service.operations.request_verification(upload_id)
    with patch("spireagent.hub.uploads.unpack", side_effect=OSError("disk full")):
        service.verify_pending()
    row = service.operations.upload(upload_id)
    assert row["status"] == "verification_pending" and row["receipt"] is None
    assert row["last_error"] == "transport_or_storage_unavailable"


@pytest.mark.parametrize("bad", [0.4, True, float("nan"), float("inf")])
def test_noninteger_budget_and_time_rejected(tmp_path: Path, bad: object) -> None:
    ops = Operations(tmp_path / "ops.sqlite")
    with pytest.raises(BoundaryError):
        ops.enqueue("training", "a" * 64, "job", max_seconds=10, reserved_units=bad, budget_limit=1)  # type: ignore[arg-type]
    with pytest.raises(BoundaryError):
        ops.enqueue("training", "a" * 64, "job", max_seconds=bad, reserved_units=1, budget_limit=1)  # type: ignore[arg-type]
    assert ops.jobs() == []


def test_expanded_headers_and_corrupt_gzip_are_quarantined(tmp_path: Path) -> None:
    import gzip

    service, intent, _ = fixture(tmp_path)
    # gzip header is valid, tar long-header data exceeds the whole expanded budget.
    data = gzip.compress(b"x" * 8192)
    intent.update(archive_sha256=hashlib.sha256(data).hexdigest(), archive_bytes=len(data))
    identity = service.intent("device", intent)["upload_id"]
    assert isinstance(service.staging, LocalStaging)
    service.staging.write(identity, io.BytesIO(data), len(data))
    service.operations.request_verification(identity)
    with patch("spireagent.hub.uploads.MAX_EXPANDED", 4096):
        service.verify_pending()
    row = service.operations.upload(identity)
    assert row["status"] == "quarantined"
    assert "expanded_stream_size_limit" in row["receipt"]


def test_verified_source_pipeline_and_true_platform_client_wait(tmp_path: Path) -> None:
    import json
    import threading
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    from platform_bundle3_fixture import bundle3
    from sts2_platform_evidence.delivery import ReceiverVerificationPending
    from sts2_platform_evidence.delivery_http import HubTransport

    from spireagent.hub.application import HubApplication
    from spireagent.hub.pipeline import build_dataset

    class Quiet(WSGIRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

    service, _, _ = fixture(tmp_path)
    token = "collector" * 8
    service.operations.register("device", token)
    app = HubApplication(service, "admin" * 8)
    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    url = f"http://127.0.0.1:{server.server_port}"
    assert isinstance(service.staging, LocalStaging)
    service.staging.public_url = url
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bundle = bundle3(tmp_path / "bundle-fixture")
        from sts2_platform_evidence import verify_human_session_bundle

        identity = verify_human_session_bundle(bundle).require_value().bundle_content_id
        transfer = DirectoryTransferManifest.from_directory(
            bundle, content_id=identity, artifact_type="human-session-bundle"
        )
        client = HubTransport(
            url,
            token,
            tmp_path / "archives",
            allowed_upload_hosts=["127.0.0.1"],
            allow_loopback_http=True,
        )
        with pytest.raises(ReceiverVerificationPending):
            client(bundle, transfer, {})
        with pytest.raises(ReceiverVerificationPending):
            client(bundle, transfer, {})
        assert len(service.operations.uploads()) == 1
        assert len(list(service.staging.root.iterdir())) == 1
        assert service.verify_pending() == 1
        receipt = client(bundle, transfer, {})
        assert receipt["status"] == "verified"
        recovered = HubTransport(
            url,
            token,
            tmp_path / "replacement-archives",
            allowed_upload_hosts=["127.0.0.1"],
            allow_loopback_http=True,
        )
        assert recovered(bundle, transfer, {}) == receipt
        result = build_dataset(service.store, (receipt["evidence_id"],), service.producer)
        assert result["records"] == 6 and len(result["splits"]) == 3
        assert json.loads(service.operations.uploads()[0]["receipt"])["verifier"]["version"]
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
