import sys
import unittest

from sts2_headless.client import (
    DriverError,
    FiniteActionView,
    ManagedPlayerEnvironment,
    ThreadedVectorPlayerEnvironment,
    canonicalize_episode_seed,
)


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
        print(json.dumps({**base,"type":"text_submit_result","result":{
            "schema":"sts2.player-environment/text-menu-action-result-1",
            "status":"applied", "request_id":"text-mutation-1"}}), flush=True)
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
            with self.assertRaises(ValueError):
                environment.submit_text_menu(action, "", context["game_continuity_id"])

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
