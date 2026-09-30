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
        mode = self.server.reply_mode
        if mode == "known_reject":
            self.reply({"schema": "sts2.host-runtime/managed-service-error-1",
                        "service_instance_id": SERVICE_ID,
                        "error": "managed_service_episode_precondition_required"}, 428)
            return
        if mode == "stale_reject":
            self.reply({"schema": "sts2.host-runtime/managed-service-error-1",
                        "service_instance_id": SERVICE_ID,
                        "error": "stale_game_continuity"}, 409)
            return
        self.server.applied_count += 1
        if mode == "malformed_200":
            self.raw_reply(200, b'{"schema":', len(b'{"schema":'))
            return
        if mode == "truncated_200":
            self.raw_reply(200, b'{"schema":', 200)
            self.close_connection = True
            return
        if mode in {"generic_409", "unconfirmed_500", "malformed_409", "wrong_service_409"}:
            if mode == "malformed_409":
                self.raw_reply(409, b'{"error":', len(b'{"error":'))
            else:
                self.reply({"schema": "sts2.host-runtime/managed-service-error-1",
                            "service_instance_id": "other-service" if mode == "wrong_service_409" else SERVICE_ID,
                            "error": "managed_service_host_close_unconfirmed" if mode == "unconfirmed_500"
                            else "managed_service_request_failed"},
                           500 if mode == "unconfirmed_500" else 409)
            return
        if self.path == "/v1/command":
            result = {"type": "text_observe_result", "request_id": data["request_id"],
                      "context": {"game_continuity_id": "episode-one"}}
        else:
            result = {"type": "reset_result", "request_id": data["request_id"]}
        if mode == "wrong_request_id":
            result["request_id"] = "other-request"
        self.reply({"schema": "wrong-schema" if mode == "wrong_schema" else
                    "sts2.host-runtime/managed-service-result-1",
                    "service_instance_id": "other-service" if mode == "wrong_service" else SERVICE_ID,
                    "result": result})

    def raw_reply(self, status, data, length):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.write(data)

    def reply(self, value, status=200):
        data = json.dumps(value).encode("utf-8")
        self.raw_reply(status, data, len(data))

    def log_message(self, _format, *_args):
        pass


class ManagedServiceClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        if os.name != "nt":
            self.directory.chmod(0o700)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.reply_mode = "normal"
        self.server.applied_count = 0
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

    def test_applied_post_with_invalid_success_reply_is_uncertain_and_not_retried(self):
        client = ManagedHostServiceClient.from_attachment(self.attachment("client"))
        for mode in ("malformed_200", "truncated_200", "wrong_service",
                     "wrong_schema", "wrong_request_id"):
            with self.subTest(mode=mode):
                self.server.reply_mode = mode
                before = self.server.applied_count
                with self.assertRaises(ManagedHostUncertainError):
                    client.request({"command": "text_submit", "request_id": f"offered-{mode}"})
                self.assertEqual(self.server.applied_count, before + 1)

    def test_applied_post_with_invalid_error_reply_is_uncertain_and_not_retried(self):
        client = ManagedHostServiceClient.from_attachment(self.attachment("client"))
        for mode in ("malformed_409", "wrong_service_409", "generic_409", "unconfirmed_500"):
            with self.subTest(mode=mode):
                self.server.reply_mode = mode
                before = self.server.applied_count
                with self.assertRaises(ManagedHostUncertainError):
                    client.request({"command": "release_control", "request_id": f"offered-{mode}"})
                self.assertEqual(self.server.applied_count, before + 1)

    def test_manager_post_routes_preserve_uncertain_reply_classification(self):
        manager = ManagedHostServiceManager.from_attachment(self.attachment("manager"))
        for mode, submit in (
            ("malformed_200", lambda: manager.reset("SEED",
                expected_runtime_instance_id="runtime-one", expected_game_continuity_id=None)),
            ("generic_409", lambda: manager.recover_control(
                expected_control_epoch="epoch-one", expected_runtime_instance_id="runtime-one",
                expected_game_continuity_id="episode-one")),
            ("wrong_request_id", manager.close_host),
        ):
            with self.subTest(mode=mode):
                self.server.reply_mode = mode
                before = self.server.applied_count
                with self.assertRaises(ManagedHostUncertainError):
                    submit()
                self.assertEqual(self.server.applied_count, before + 1)

    def test_validated_explicit_rejection_is_known_not_applied(self):
        client = ManagedHostServiceClient.from_attachment(self.attachment("client"))
        for mode, code in (("known_reject", "managed_service_episode_precondition_required"),
                           ("stale_reject", "stale_game_continuity")):
            with self.subTest(mode=mode):
                self.server.reply_mode = mode
                before = self.server.applied_count
                with self.assertRaises(ManagedHostServiceError) as caught:
                    client.request({"command": "claim_control", "request_id": f"rejected-{mode}"})
                self.assertNotIsInstance(caught.exception, ManagedHostUncertainError)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.server.applied_count, before)


if __name__ == "__main__":
    unittest.main()
