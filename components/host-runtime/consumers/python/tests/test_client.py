import sys
from pathlib import Path
import tempfile
import unittest

from sts2_headless.client import (
    DriverError,
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
        context = {"schema":"sts2.player-environment/text-menu-observation-context-1",
                   "snapshot":{"schema":"sts2.player-environment/text-menu-snapshot-1",
                               "input_profile":"text-menu-v1", "snapshot_id":"text-s1",
                               "menu_actions":{"actions":[{"action_id":"text-a1"}]}},
                   "game_continuity_id":"managed_episode_test"}
        print(json.dumps({**base,"type":"text_observe_result","context":context}), flush=True)
    elif request["command"] == "text_submit":
        assert request["action_id"] == "text-a1"
        assert request["expected_snapshot_id"] == "text-s1"
        assert request["expected_game_continuity_id"] == "managed_episode_test"
        assert request["mutation_request_id"] == "text-mutation-1"
        action = {"action_id":"text-a1", "kind":"native_input", "verb":"activate",
                  "label":"Choose", "subject_referent_id":None, "arguments":[],
                  "effect_domain":"native_input"}
        print(json.dumps({**base,"type":"text_submit_result","result":{
            "protocol_version":"1.0.0",
            "schema":"sts2.player-environment/text-menu-action-result-1",
            "input_profile":"text-menu-v1", "request_id":"text-mutation-1",
            "status":"applied", "effect_domain":"native_input",
            "native_delivery":"delivered", "action":action,
            "reason_code":None, "detail":None, "retry":"never",
            "successor":None, "attribution":None}}), flush=True)
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


class ClientTest(unittest.TestCase):
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
