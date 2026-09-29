import json
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from sts2_headless.client import (
    DriverCleanupError,
    DriverError,
    DriverInitializationError,
    FiniteActionView,
    ManagedPlayerEnvironment,
    ThreadedVectorPlayerEnvironment,
    canonicalize_episode_seed,
)

CONNECTOR_TEXT_ROOT = (Path(__file__).resolve().parents[5] / "components" / "connector"
                       / "sdk" / "typescript" / "test" / "fixtures" / "text-menu-root.json")


FAKE_DRIVER = r'''
import json, sys
snapshot = {
  "snapshot_id":"s1",
  "bound_actions":{"status":"complete","actions":[{"bound_action_id":"a1","verb":"activate"}]}
}
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    base = {"request_id":request["request_id"]}
    if request["command"] == "reset":
        print(json.dumps({**base,"type":"reset_result","snapshot":snapshot}), flush=True)
    elif request["command"] == "observe":
        print(json.dumps({**base,"type":"observe_result","snapshot":snapshot}), flush=True)
    elif request["command"] == "read":
        print(json.dumps({**base,"type":"read_result","read":{"kind":"detail"}}), flush=True)
    elif request["command"] == "step":
        print(json.dumps({**base,"type":"step_result","receipt":{"delivery":"delivered","successor":snapshot}}), flush=True)
    elif request["command"] == "text_observe":
        profile = request.get("input_profile", "text-menu-v1")
        version = "2" if profile == "text-menu-v2" else "1"
        reported = "text-menu-v1" if len(sys.argv) > 1 and sys.argv[1] == "wrong_profile" else profile
        context = {"schema":"sts2.player-environment/text-menu-observation-context-"+version,
                   "snapshot":{"schema":"sts2.player-environment/text-menu-snapshot-"+version,
                               "input_profile":reported, "snapshot_id":"text-s1",
                               "menu_actions":{"actions":[{"action_id":"text-a1"}]}},
                   "game_continuity_id":"managed_episode_test"}
        print(json.dumps({**base,"type":"text_observe_result","context":context}), flush=True)
    elif request["command"] == "text_submit":
        assert request["action_id"] == "text-a1"
        assert request["expected_snapshot_id"] == "text-s1"
        assert request["expected_game_continuity_id"] == "managed_episode_test"
        assert request["mutation_request_id"] == "text-mutation-1"
        profile = request.get("input_profile", "text-menu-v1")
        version = "2" if profile == "text-menu-v2" else "1"
        selection = profile == "text-menu-v2"
        action = {"action_id":"text-a1", "kind":"system_selection" if selection else "native_input",
                  "verb":"select_card" if selection else "activate",
                  "label":"Choose", "subject_referent_id":"card1" if selection else None, "arguments":[],
                  "effect_domain":"text_menu" if selection else "native_input"}
        successor = {"schema":"sts2.player-environment/text-menu-snapshot-2",
                     "input_profile":"text-menu-v2", "snapshot_id":"text-s2"} if selection else None
        print(json.dumps({**base,"type":"text_submit_result","result":{
            "protocol_version":"1.0.0",
            "schema":"sts2.player-environment/text-menu-action-result-"+version,
            "input_profile":"text-menu-v1" if len(sys.argv) > 1 and sys.argv[1] == "wrong_result_profile" else profile,
            "request_id":"text-mutation-1",
            "status":"applied", "effect_domain":"text_menu" if selection else "native_input",
            "native_delivery":None if selection else "delivered", "action":action,
            "reason_code":None, "detail":None, "retry":"never",
            "successor":successor, "attribution":None}}), flush=True)
    elif request["command"] == "episode_identity":
        print(json.dumps({**base,"type":"episode_identity_result","identity":{"episode_provenance":{"verdict":"provenance_pass","actual_seed":"SEED"}}}), flush=True)
    elif request["command"] == "close":
        print(json.dumps({**base,"type":"close_result","exit":{"code":0}}), flush=True)
        break
'''

HANG_DRIVER = r'''
import json, sys, time
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin:
    time.sleep(60)
'''

NOISY_DRIVER = r'''
import json, sys
for index in range(2000):
    print("diagnostic-" + str(index) + "-" + ("x" * 100), file=sys.stderr)
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({"request_id":request["request_id"],"type":"close_result","exit":{"code":0}}), flush=True)
    break
'''

UNCERTAIN_DRIVER = r'''
import json, sys
mode, record = sys.argv[1:3]
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with open(record, "a", encoding="utf-8") as handle:
        handle.write(request["command"] + "\n")
    base = {"request_id":request["request_id"]}
    if request["command"] == "reset":
        print(json.dumps({**base,"type":"reset_result","snapshot":{"snapshot_id":"s1"}}), flush=True)
    elif request["command"] == "text_submit":
        if mode == "bad_json":
            print("{not-json", flush=True)
        elif mode == "eof":
            sys.exit(0)
        elif mode == "error":
            print(json.dumps({**base,"type":"error","message":"driver_request_failed"}), flush=True)
        else:
            navigation = mode == "system_navigation"
            uncertain = mode in ("unknown", "bad_unknown_retry")
            rejected = mode == "not_applied"
            effect = "text_menu" if navigation else "native_input"
            action = {"action_id":request["action_id"],
                      "kind":"system_navigation" if navigation else "native_input",
                      "verb":"open_information" if navigation else "activate",
                      "label":"Information" if navigation else "Choose",
                      "subject_referent_id":None, "arguments":[], "effect_domain":effect}
            successor = None
            if navigation:
                with open(sys.argv[3], encoding="utf-8") as handle:
                    successor = json.load(handle)
                successor["snapshot_id"] = "text-runtime-1-native-1-u1"
                successor["menu"]["cursor"] = "information"
                successor["menu"]["revision"] = 1
                successor["menu_actions"]["actions"][0]["verb"] = "back"
            result = {"protocol_version":"1.0.0",
                      "schema":"bad-after-action" if mode == "bad_schema" else
                      "sts2.player-environment/text-menu-action-result-1",
                      "input_profile":"text-menu-v1", "request_id":request["mutation_request_id"],
                      "status":"not_applied" if rejected else "unknown" if uncertain else "applied",
                      "effect_domain":None if rejected else effect,
                      "native_delivery":None if mode in ("bad_delivery", "not_applied", "system_navigation")
                      else "unknown" if uncertain else "delivered",
                      "action":None if rejected else action,
                      "reason_code":"stale_snapshot" if rejected else None,
                      "detail":None,
                      "retry":"reobserve" if mode in ("not_applied", "bad_unknown_retry", "bad_applied_retry") else "never",
                      "successor":successor,
                      "attribution":None}
            if mode == "bad_status_type":
                result["status"] = []
            if mode == "bad_effect_type":
                result["effect_domain"] = []
            if mode == "bad_retry_type":
                result["retry"] = []
            print(json.dumps({**base,"type":"text_submit_result",
                              "result":result if mode != "bad_type" else []}), flush=True)
    elif request["command"] == "step":
        print(json.dumps({**base,"type":"step_result","receipt":{"delivery":"delivered"}}), flush=True)
    elif request["command"] == "close":
        print(json.dumps({**base,"type":"close_result","exit":{"code":0}}), flush=True)
        break
'''

UNKNOWN_IGNORES_EOF_DRIVER = r'''
import json, sys, time
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    base = {"request_id":request["request_id"]}
    if request["command"] == "text_submit":
        action = {"action_id":request["action_id"], "kind":"native_input", "verb":"play",
                  "label":"Play", "subject_referent_id":"card1", "arguments":[],
                  "effect_domain":"native_input"}
        result = {"protocol_version":"1.0.0",
                  "schema":"sts2.player-environment/text-menu-action-result-2",
                  "input_profile":"text-menu-v2", "request_id":request["mutation_request_id"],
                  "status":"unknown", "effect_domain":"native_input",
                  "native_delivery":"unknown", "action":action,
                  "reason_code":None, "detail":None, "retry":"never",
                  "successor":None, "attribution":None}
        print(json.dumps({**base,"type":"text_submit_result","result":result}), flush=True)
        time.sleep(60)
'''

HELD_EOF_DRIVER = r'''
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
print(json.dumps({"type":"ready","protocol":"test"}), flush=True)
for line in sys.stdin: pass
(root / "eof-entered").write_text("entered")
while not (root / "release").exists(): time.sleep(0.005)
'''


class ClientTest(unittest.TestCase):
    def test_concurrent_close_waits_for_same_real_eof_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", HELD_EOF_DRIVER, temporary],
                response_timeout_seconds=2,
            )
            first_errors = []
            first = threading.Thread(target=lambda: self._capture_close(environment, first_errors))
            first.start()
            try:
                deadline = time.monotonic() + 2
                while not (root / "eof-entered").exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue((root / "eof-entered").exists(), "real driver must enter EOF gate")
                released = threading.Event()

                def release():
                    time.sleep(0.15)
                    (root / "release").write_text("go")
                    released.set()

                gate = threading.Thread(target=release)
                gate.start()
                try:
                    environment.close(force=True)
                    self.assertTrue(released.is_set(), "second close returned before cleanup gate opened")
                finally:
                    gate.join(timeout=2)
                first.join(timeout=2)
                self.assertFalse(first.is_alive())
                self.assertEqual(first_errors, [])
                self.assertIsNotNone(environment._process.poll())
            finally:
                (root / "release").write_text("go")
                first.join(timeout=2)
                if environment._process.poll() is None:
                    environment._process.kill()
                    environment._process.wait(timeout=2)

    @staticmethod
    def _capture_close(environment, errors):
        try:
            environment.close(force=True)
        except Exception as error:
            errors.append(error)

    def test_force_close_reports_unconfirmed_child_cleanup(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is required for the public Host consumer")
        fixture = (Path(__file__).resolve().parents[3] / "tools" / "test-fixtures"
                   / "managed-driver-shutdown.mjs")
        with tempfile.TemporaryDirectory() as temporary:
            environment = ManagedPlayerEnvironment(
                [node, str(fixture), str(Path(temporary) / "entered"), "--ignore-eof"],
                response_timeout_seconds=5,
            )
            native_pid = environment.ready["native_pid"]
            try:
                with self.assertRaisesRegex(DriverCleanupError, "unconfirmed"):
                    environment.close(force=True)
                with self.assertRaises(DriverCleanupError):
                    environment.close()
            finally:
                subprocess.run([node, "-e", "try { process.kill(Number(process.argv[1]), 'SIGKILL'); } "
                                "catch (error) { if (error.code !== 'ESRCH') throw error; }",
                                str(native_pid)], check=True)

    def test_constructor_malformed_ready_preserves_cause_and_closes_child(self):
        with self.assertRaises(DriverInitializationError) as captured:
            ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", "import sys; print('{bad', flush=True); sys.exit(1)"],
                response_timeout_seconds=2,
            )
        self.assertIsInstance(captured.exception.__cause__, json.JSONDecodeError)
        self.assertFalse(captured.exception.cleanup_confirmed)
        self.assertIsInstance(captured.exception.cleanup_error, DriverCleanupError)
        with self.assertRaises(DriverInitializationError) as confirmed:
            ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", "print('{bad', flush=True)"],
                response_timeout_seconds=2,
            )
        self.assertIsInstance(confirmed.exception.__cause__, json.JSONDecodeError)
        self.assertTrue(confirmed.exception.cleanup_confirmed)

    def test_correlated_v2_unknown_survives_cleanup_failure(self):
        environment = ManagedPlayerEnvironment(
            [sys.executable, "-u", "-c", UNKNOWN_IGNORES_EOF_DRIVER],
            response_timeout_seconds=5,
        )
        result = environment.submit_text_menu(
            "play-card1", "text-s1", "managed_episode_test", request_id="unknown-v2-1",
            input_profile="text-menu-v2",
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["native_delivery"], "unknown")
        self.assertEqual(result["retry"], "never")
        with self.assertRaises(DriverCleanupError):
            environment.close()

    def test_force_close_uses_eof_to_reap_pending_native_child(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is required for the public Host consumer")
        fixture = (Path(__file__).resolve().parents[3] / "tools" / "test-fixtures"
                   / "managed-driver-shutdown.mjs")
        with tempfile.TemporaryDirectory() as temporary:
            environment = ManagedPlayerEnvironment(
                [node, str(fixture), str(Path(temporary) / "entered")],
                response_timeout_seconds=5,
            )
            native_pid = environment.ready["native_pid"]
            environment.close(force=True)
            self.assertIsNotNone(environment._process.poll())
            self.assertEqual(subprocess.run(
                [node, "-e", "try { process.kill(Number(process.argv[1]), 0); process.exit(1); } "
                 "catch (error) { process.exit(error.code === 'ESRCH' ? 0 : 2); }",
                 str(native_pid)], check=False,
            ).returncode, 0, "force-close left the synthetic native child alive")

    def test_episode_seed_uses_the_game_canonical_form(self):
        self.assertEqual(canonicalize_episode_seed(" oiAbc123 "), "01ABC123")
        with self.assertRaises(ValueError):
            canonicalize_episode_seed("bad-seed")

    def test_round_trip_and_finite_projection(self):
        with ManagedPlayerEnvironment([sys.executable, "-u", "-c", FAKE_DRIVER]) as environment:
            snapshot = environment.reset("SEED")
            view = FiniteActionView.from_snapshot(snapshot)
            self.assertEqual(view.action_ids, ("a1",))
            receipt = environment.step(view.action_ids[0], view.snapshot_id)
            self.assertEqual(receipt["delivery"], "delivered")
            self.assertEqual(environment.observe()["snapshot_id"], "s1")
            self.assertEqual(environment.episode_identity()["episode_provenance"]["actual_seed"], "SEED")

    def test_public_text_menu_context_and_exact_submission(self):
        with ManagedPlayerEnvironment([sys.executable, "-u", "-c", FAKE_DRIVER]) as environment:
            environment.reset("SEED")
            context = environment.observe_text_menu()
            self.assertEqual(context["snapshot"]["input_profile"], "text-menu-v1")
            action = context["snapshot"]["menu_actions"]["actions"][0]["action_id"]
            result = environment.submit_text_menu(
                action, context["snapshot"]["snapshot_id"],
                context["game_continuity_id"], request_id="text-mutation-1",
            )
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["effect_domain"], "native_input")
            self.assertEqual(result["native_delivery"], "delivered")
            self.assertEqual(result["retry"], "never")
            with self.assertRaises(ValueError):
                environment.submit_text_menu(action, "", context["game_continuity_id"])

    def test_public_v2_profile_selection_and_v1_isolation(self):
        with ManagedPlayerEnvironment([sys.executable, "-u", "-c", FAKE_DRIVER]) as environment:
            environment.reset("SEED")
            v1 = environment.observe_text_menu()
            v2 = environment.observe_text_menu(input_profile="text-menu-v2")
            self.assertEqual(v1["schema"], "sts2.player-environment/text-menu-observation-context-1")
            self.assertEqual(v2["schema"], "sts2.player-environment/text-menu-observation-context-2")
            self.assertEqual(v2["snapshot"]["input_profile"], "text-menu-v2")
            result = environment.submit_text_menu(
                "text-a1", "text-s1", v2["game_continuity_id"],
                request_id="text-mutation-1", input_profile="text-menu-v2",
            )
            self.assertEqual(result["action"]["kind"], "system_selection")
            self.assertEqual(result["effect_domain"], "text_menu")
            self.assertIsNone(result["native_delivery"])
            with self.assertRaises(ValueError):
                environment.observe_text_menu(input_profile="text-menu-v3")
        with ManagedPlayerEnvironment(
            [sys.executable, "-u", "-c", FAKE_DRIVER, "wrong_profile"]
        ) as environment:
            with self.assertRaisesRegex(DriverError, "observation context is invalid"):
                environment.observe_text_menu(input_profile="text-menu-v2")
            self.assertTrue(environment._closed)
        with ManagedPlayerEnvironment(
            [sys.executable, "-u", "-c", FAKE_DRIVER, "wrong_result_profile"]
        ) as environment:
            with self.assertRaisesRegex(DriverError, "action result is invalid"):
                environment.submit_text_menu(
                    "text-a1", "text-s1", "managed_episode_test",
                    request_id="text-mutation-1", input_profile="text-menu-v2",
                )
            self.assertTrue(environment._closed)

    def test_post_offer_invalid_text_result_quarantines_before_another_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            for mode in ("bad_schema", "bad_type", "bad_delivery", "bad_unknown_retry",
                         "bad_applied_retry", "bad_status_type", "bad_effect_type",
                         "bad_retry_type", "bad_json", "eof", "error"):
                with self.subTest(mode=mode):
                    record = Path(temporary) / f"{mode}.txt"
                    with ManagedPlayerEnvironment(
                        [sys.executable, "-u", "-c", UNCERTAIN_DRIVER, mode, str(record)]
                    ) as environment:
                        environment.reset("SEED")
                        with self.assertRaises(DriverError):
                            environment.submit_text_menu(
                                "text-a1", "text-s1", "managed_episode_test", request_id="mutation-1"
                            )
                        self.assertTrue(environment._closed)
                        with self.assertRaisesRegex(DriverError, "closed"):
                            environment.step("a1", "s1")
                    self.assertEqual(record.read_text().splitlines(), ["reset", "text_submit"])

    def test_correlated_not_applied_result_remains_an_explicit_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "requests.txt"
            with ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", UNCERTAIN_DRIVER, "not_applied", str(record)]
            ) as environment:
                environment.reset("SEED")
                result = environment.submit_text_menu(
                    "text-a1", "text-s1", "managed_episode_test", request_id="mutation-1"
                )
                self.assertEqual(result["status"], "not_applied")
                self.assertIsNone(result["native_delivery"])
                self.assertEqual(result["retry"], "reobserve")
                self.assertFalse(environment._closed)
                self.assertEqual(environment.step("a1", "s1")["delivery"], "delivered")
            self.assertEqual(record.read_text().splitlines(), ["reset", "text_submit", "step", "close"])

    def test_system_navigation_applied_has_no_native_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "requests.txt"
            with ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", UNCERTAIN_DRIVER, "system_navigation",
                 str(record), str(CONNECTOR_TEXT_ROOT)]
            ) as environment:
                environment.reset("SEED")
                result = environment.submit_text_menu(
                    "menu-open_information-1", "text-s1", "managed_episode_test",
                    request_id="navigation-1",
                )
                self.assertEqual(result["status"], "applied")
                self.assertEqual(result["effect_domain"], "text_menu")
                self.assertIsNone(result["native_delivery"])
                self.assertEqual(result["successor"]["menu"]["cursor"], "information")
                self.assertFalse(environment._closed)
            self.assertEqual(record.read_text().splitlines(), ["reset", "text_submit", "close"])

    def test_valid_unknown_result_is_returned_then_closes_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "requests.txt"
            with ManagedPlayerEnvironment(
                [sys.executable, "-u", "-c", UNCERTAIN_DRIVER, "unknown", str(record)]
            ) as environment:
                environment.reset("SEED")
                result = environment.submit_text_menu(
                    "text-a1", "text-s1", "managed_episode_test", request_id="unknown-1"
                )
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["native_delivery"], "unknown")
                self.assertEqual(result["retry"], "never")
                self.assertTrue(environment._closed)
                with self.assertRaisesRegex(DriverError, "closed"):
                    environment.step("a1", "s1")
            self.assertEqual(record.read_text().splitlines(), ["reset", "text_submit"])

    def test_rejects_incomplete_action_projection(self):
        with self.assertRaises(DriverError):
            FiniteActionView.from_snapshot({"snapshot_id": "s", "bound_actions": {"status": "partial"}})
        with self.assertRaises(DriverError):
            FiniteActionView.from_snapshot({
                "snapshot_id": "s",
                "bound_actions": {"status": "complete", "actions": [{"bound_action_id": ""}]},
            })
        with self.assertRaises(DriverError):
            FiniteActionView.from_snapshot({
                "snapshot_id": "s",
                "bound_actions": {"status": "complete", "actions": [{}]},
            })

    def test_driver_response_timeout_quarantines_process(self):
        environment = ManagedPlayerEnvironment(
            [sys.executable, "-u", "-c", HANG_DRIVER],
            response_timeout_seconds=0.1,
        )
        with self.assertRaisesRegex(DriverError, "response timed out"):
            environment.reset("SEED")
        self.assertIsNotNone(environment._process.poll())

    def test_stderr_is_drained_without_blocking_ready(self):
        with ManagedPlayerEnvironment(
            [sys.executable, "-u", "-c", NOISY_DRIVER],
            response_timeout_seconds=5,
        ) as environment:
            self.assertEqual(environment.ready["type"], "ready")

    def test_threaded_vector_preserves_environment_order_and_exact_bindings(self):
        class RecordingEnvironment:
            def __init__(self, name):
                self.name = name
                self.calls = []

            def reset(self, seed):
                self.calls.append(("reset", seed))
                return {"environment": self.name, "seed": seed}

            def step(self, action_id, snapshot_id):
                self.calls.append(("step", action_id, snapshot_id))
                return {
                    "environment": self.name,
                    "action_id": action_id,
                    "snapshot_id": snapshot_id,
                }

            def close(self):
                self.calls.append(("close",))

        first = RecordingEnvironment("first")
        second = RecordingEnvironment("second")
        vector = ThreadedVectorPlayerEnvironment((first, second))

        self.assertEqual(
            vector.reset(("seed-a", "seed-b")),
            (
                {"environment": "first", "seed": "seed-a"},
                {"environment": "second", "seed": "seed-b"},
            ),
        )
        self.assertEqual(
            vector.step((("action-a", "snapshot-a"), ("action-b", "snapshot-b"))),
            (
                {
                    "environment": "first",
                    "action_id": "action-a",
                    "snapshot_id": "snapshot-a",
                },
                {
                    "environment": "second",
                    "action_id": "action-b",
                    "snapshot_id": "snapshot-b",
                },
            ),
        )
        vector.close()
        self.assertEqual(first.calls[-1], ("close",))
        self.assertEqual(second.calls[-1], ("close",))


if __name__ == "__main__":
    unittest.main()
