"""Real production Node pipe/session and public client; synthetic World only."""
from __future__ import annotations

import json
import shutil
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sts2_headless import DriverError, ReferencePlayerEnvironment

ROOT = Path(__file__).resolve().parents[3]


class ReferenceClientTests(unittest.TestCase):
    def command(self, root, **settings):
        log = Path(root)/"events.jsonl"
        config = Path(root)/"fixture.json"
        config.write_text(json.dumps({"log": str(log), **settings}))
        return [shutil.which("node") or "node", str(ROOT/"tools/test-fixtures/reference-episode.mjs"), "--fixture", str(config)], log

    def test_real_public_handoff_and_typed_close_are_distinct_from_driver_exit(self):
        with TemporaryDirectory() as root:
            command, log = self.command(root)
            client = ReferencePlayerEnvironment(command, response_timeout_seconds=2)
            client.reset("FIRST")
            client.release_bootstrap("fixture-runtime")
            with self.assertRaises(DriverError): client.reset("SECOND")
            with self.assertRaises(DriverError): client.step("action", "snapshot")
            client.close()
            receipt = client.close_receipt()
            self.assertEqual(receipt["close_result"]["type"], "close_result")
            self.assertEqual(receipt["close_result"]["exit"]["code"], 0)
            self.assertEqual(receipt["driver_exit_code"], 0)
            self.assertFalse(receipt["forced_fallback"])
            events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
            self.assertEqual(events.count("reset_started"), 1)
            self.assertEqual(events.count("released"), 1)
            self.assertEqual(events.count("step"), 0)

    def test_missing_capability_rejects_before_real_reset_offer(self):
        with TemporaryDirectory() as root:
            command, log = self.command(root, missingCapability=True)
            client = ReferencePlayerEnvironment(command, response_timeout_seconds=2)
            with self.assertRaises(DriverError): client.reset("FIRST")
            client.close()
            self.assertFalse(client.close_receipt()["world_mutation_offered"])
            self.assertNotIn("reset_started", log.read_text())

    def test_real_pipe_eof_interrupts_blocked_reset_without_waiting_read_timeout(self):
        with TemporaryDirectory() as root:
            command, log = self.command(root, blockReset=True)
            client = ReferencePlayerEnvironment(command, response_timeout_seconds=30)
            errors = []
            def reset():
                try: client.reset("FIRST")
                except Exception as error: errors.append(error)
            worker = threading.Thread(target=reset)
            worker.start()
            deadline = time.monotonic()+2
            while not log.exists() and time.monotonic() < deadline: time.sleep(.01)
            started = time.monotonic()
            client.close(force=True)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic()-started, 3)
            self.assertTrue(errors)
            self.assertIn("reset_aborted", log.read_text())
            self.assertIsNone(client.close_receipt()["close_result"])

    def test_started_handle_is_available_before_ready_wait(self):
        with TemporaryDirectory() as root:
            command, _ = self.command(root, readyDelay=1000)
            entered = threading.Event()
            def started(client):
                entered.set()
                client.close(force=True)
            with self.assertRaises(DriverError):
                ReferencePlayerEnvironment(command, response_timeout_seconds=3, on_started=started)
            self.assertTrue(entered.is_set())


if __name__ == "__main__": unittest.main()
