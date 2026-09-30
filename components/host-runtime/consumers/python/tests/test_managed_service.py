from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from sts2_headless.managed_service import (
    ManagedHostServiceClient,
    ManagedHostServiceError,
    ManagedHostServiceManager,
    ManagedHostUncertainError,
)


SERVICE_ID = "managed_service_test"
HOST_IDENTITY = {"package_name": "@rsgcsg/sts2-host-runtime",
                 "version": "test", "source_digest_sha256": "a" * 64}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/ready":
            self.reply({"schema": "sts2.host-runtime/managed-service-ready-1",
                        "service_instance_id": SERVICE_ID,
                        "host_identity": HOST_IDENTITY,
                        "episode": {"game_continuity_id": "episode-one"}})
        elif self.path == "/v1/admin/status":
            self.reply({"schema": "sts2.host-runtime/managed-service-ready-1",
                        "service_instance_id": SERVICE_ID,
                        "status": {"control_held": False}})
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/v1/command" and self.headers.get("X-STS2-Managed-Service-ID") != SERVICE_ID:
            self.send_error(409)
            return
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if data.get("command") == "drop_reply":
            self.connection.close()
            return
        if self.path == "/v1/command":
            result = {"type": "text_observe_result", "request_id": data["request_id"],
                      "context": {"game_continuity_id": "episode-one"}}
        else:
            result = {"type": "reset_result", "request_id": data["request_id"]}
        self.reply({"schema": "sts2.host-runtime/managed-service-result-1",
                    "service_instance_id": SERVICE_ID, "result": result})

    def reply(self, value):
        data = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format, *_args):
        pass


class ManagedServiceClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def attachment(self, role):
        path = self.directory / f"{role}.json"
        path.write_text(json.dumps({
            "schema": "sts2.host-runtime/managed-service-attachment-1",
            "role": role, "endpoint": self.endpoint,
            "service_instance_id": SERVICE_ID,
            "host_identity": HOST_IDENTITY,
            "token": f"private-{role}-token",
        }), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        return path

    def test_client_attaches_and_correlates_a_command(self):
        client = ManagedHostServiceClient.from_attachment(self.attachment("client"))
        self.assertEqual(client.ready()["episode"]["game_continuity_id"], "episode-one")
        response = client.request({"command": "text_observe"})
        self.assertEqual(response["result"]["type"], "text_observe_result")
        self.assertTrue(response["result"]["request_id"])
        manager = ManagedHostServiceManager.from_attachment(self.attachment("manager"))
        self.assertFalse(manager.status()["status"]["control_held"])
        self.assertEqual(manager.reset("SEED", expected_runtime_instance_id="runtime-one",
                                       expected_game_continuity_id=None)["result"]["type"], "reset_result")

    def test_wrong_role_and_insecure_attachment_fail(self):
        path = self.attachment("manager")
        with self.assertRaises(ManagedHostServiceError):
            ManagedHostServiceClient.from_attachment(path)
        if os.name != "nt":
            path.chmod(0o644)
            with self.assertRaisesRegex(ManagedHostServiceError, "not_private"):
                ManagedHostServiceManager.from_attachment(path)

    def test_lost_post_reply_is_uncertain_and_never_retried(self):
        client = ManagedHostServiceClient.from_attachment(self.attachment("client"))
        with self.assertRaises(ManagedHostUncertainError):
            client.request({"command": "drop_reply", "request_id": "offered-once"})


if __name__ == "__main__":
    unittest.main()
