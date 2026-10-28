"""Cheap resource-adapter regressions; no invented gameplay/typed evidence."""
from __future__ import annotations

import importlib.util
import multiprocessing
import os
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

fcntl = pytest.importorskip("fcntl", reason="Friday is a POSIX Slurm resource adapter")
FRIDAY = Path(__file__).resolve().parents[2] / "tools/friday"

SPEC = importlib.util.spec_from_file_location('friday_batch', FRIDAY / 'batch.py')
assert SPEC and SPEC.loader
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)


def pool(count=2):
    from stpd.evaluation.suites import diagnostic_reference_manifest
    groups = []
    for index in range(count):
        group = diagnostic_reference_manifest('v0.111.0', f'{index + 1:032x}', 2)
        group['host'] = 'Managed'
        groups.append(group)
    return {'schema': batch.POOL_SCHEMA, 'purpose': 'engineering_only',
            'comparison_cells': ['c6220', 'fc430', 'trial'], 'groups': groups}


def concurrent_claim(directory, output):
    claimed = batch.claim_group(Path(directory), pool(1), 'trial', {'worker_id': 'test'})
    output.put(None if claimed is None else claimed['group_id'])


class FridayBatchTests(unittest.TestCase):
    def test_capacity_uses_idle_and_queue_not_total_cores(self):
        self.assertEqual(batch.capacity(395, 285, 32), 32)
        self.assertEqual(batch.capacity(100, 285, 32), 25)
        self.assertEqual(batch.capacity(1000, 301, 100), 32)
        self.assertEqual(batch.capacity(3, 0, 32), 0)
        with self.assertRaises(ValueError):
            batch.capacity(1000, 0, 201)

    def test_pool_rejects_reserved_evaluation_and_duplicates(self):
        value = pool()
        batch.pool_groups(value)
        value['groups'][1] = value['groups'][0]
        with self.assertRaisesRegex(ValueError, 'duplicate_seed'):
            batch.pool_groups(value)
        value = pool()
        value['groups'][0]['purpose'] = 'fixed_evaluation_only'
        with self.assertRaises(ValueError):
            batch.pool_groups(value)

    def test_leases_are_exclusive_and_never_expire_or_reoffer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            value = pool(1)
            one = batch.claim_group(root, value, 'trial', {'worker_id': 'one'})
            self.assertIsNotNone(one)
            self.assertIsNone(batch.claim_group(root, value, 'trial', {'worker_id': 'two'}))
            # Paired hardware comparisons must have an explicitly declared separate cell.
            self.assertIsNotNone(batch.claim_group(root, value, 'fc430', {'worker_id': 'two'}))
            with self.assertRaises(ValueError):
                batch.claim_group(root, value, '../other', {'worker_id': 'two'})

    def test_concurrent_workers_cannot_claim_same_seed(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = multiprocessing.get_context('fork')
            queue = context.Queue()
            processes = [context.Process(target=concurrent_claim, args=(temporary, queue))
                         for _ in range(3)]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(item is not None for item in results), 1)

    def test_explicit_remote_store_uses_existing_pinned_peer(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary).resolve()
            remote = {"store": "s3", "peer": {"kind": "s3", "bucket": "test"},
                      "producer": {"source_revision": "unchanged"}}
            prepared = batch.execution_on_node(remote, scratch)
            self.assertEqual(prepared["store"], "s3")
            self.assertEqual(prepared["peer"], remote["peer"])
            self.assertEqual(prepared["producer"], remote["producer"])
            self.assertNotIn("pipeline", remote)
            local = {"store": "/old", "peer": {"kind": "local", "directory": "/peer"}}
            self.assertEqual(batch.execution_on_node(local, scratch)["store"],
                             str(scratch / "store"))
            with self.assertRaises(ValueError):
                batch.execution_on_node({"store": "s3", "peer": {"kind": "local"}}, scratch)

    def test_snapshot_copies_and_reads_back_real_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = root / 'original'
            original.mkdir()
            (original / 'record.json').write_bytes(b'{"unknown":true}\n')
            receipt = batch.snapshot(original, root / 'persistent')
            self.assertTrue(receipt['full_readback'])
            self.assertEqual(receipt['files']['record.json'],
                             batch.hashed(root / 'persistent' / 'record.json'))
            (original / 'record.json').write_bytes(b'{"unknown":false}\n')
            second = batch.snapshot(original, root / 'persistent')
            self.assertNotEqual(receipt['files'], second['files'])

    def test_submission_is_finite_one_core_and_never_requeues(self):
        spec = importlib.util.spec_from_file_location(
            'friday_submit', FRIDAY / 'submit.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'batch': batch}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            configuration = root / 'batch.json'
            batch.save(configuration, {'persistent_root': str(root / 'persistent')})
            with (patch.object(module, 'submission_lock_path',
                               return_value=root / 'config/friday-submit.lock'),
                  patch.object(module, 'output', side_effect=['', '297/100/0/397', '1\n2'])):
                result = module.submit(configuration, 'trial', 32, 'fc430', dry_run=True)
            self.assertEqual(result['concurrency'], 25)
            self.assertIn('--array=0-31%25', result['command'])
            self.assertIn('--cpus-per-task=1', result['command'])
            self.assertIn('--no-requeue', result['command'])
            self.assertIn('--time=01:05:00', result['command'])
            self.assertIn('--constraint=fc430', result['command'])

    def test_existing_pending_running_or_completing_array_blocks_new_submit(self):
        spec = importlib.util.spec_from_file_location(
            'friday_submit_existing', FRIDAY / 'submit.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'batch': batch}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with (patch.object(module, 'submission_lock_path',
                               return_value=root / 'config/friday-submit.lock'),
                  patch.object(module, 'username', return_value='s4961270'),
                  patch.object(module, 'output', return_value='1394000_[0-31]') as observed,
                  self.assertRaisesRegex(ValueError, 'must_drain')):
                module.submit(root / 'unread-config.json', 'trial', 200, None, dry_run=False)
            observed.assert_called_once_with(
                ['squeue', '-h', '-u', 's4961270', '-n', 'spireagent-f2', '-o', '%i'])

    def test_one_user_lock_covers_scheduler_query_through_sbatch(self):
        spec = importlib.util.spec_from_file_location(
            'friday_submit_locked', FRIDAY / 'submit.py')
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'batch': batch}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            configuration = root / 'batch.json'
            lock = root / 'config/friday-submit.lock'
            batch.save(configuration, {'persistent_root': str(root / 'persistent')})
            commands = []

            def scheduler(command):
                commands.append(command)
                # A second submission cannot acquire this user's lock during ANY query,
                # including the accepted sbatch call. This tests the real OS-held lease.
                with lock.open('a+b') as contender, self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if command[0] == 'sinfo':
                    return '0/1000/0/1000'
                if command[0] == 'sbatch':
                    return '1394001'
                return ''

            with (patch.object(module, 'submission_lock_path', return_value=lock),
                  patch.object(module, 'output', side_effect=scheduler)):
                result = module.submit(configuration, 'trial', 200, None, dry_run=False)
            self.assertEqual(result['job_id'], '1394001')
            self.assertEqual(result['concurrency'], 200)
            self.assertEqual(commands[0][0], 'squeue')
            self.assertEqual(commands[-1][0], 'sbatch')
            with lock.open('a+b') as available:
                fcntl.flock(available, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(available, fcntl.LOCK_UN)

    def test_pinned_configuration_and_private_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            file = root / 'input.json'
            batch.save(file, {'one': 1})
            reference = {'path': str(file), 'sha256': batch.hashed(file)}
            self.assertEqual(batch.read_pinned(reference), {'one': 1})
            file.write_text('{}')
            with self.assertRaises(ValueError):
                batch.read_pinned(reference)
            root.chmod(0o755)
            with self.assertRaises(ValueError):
                batch.private_root(root)
            root.chmod(0o700)
            self.assertEqual(batch.private_root(root), root)


@pytest.fixture
def profiler_stub(tmp_path):
    """Stub only Agent serving/model calls; prove process/signal metrics, never gameplay."""
    stubs = tmp_path / "stub-modules"
    package = stubs / "stpd/p1"
    package.mkdir(parents=True)
    (stubs / "stpd/__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (stubs / "torch.py").write_text(textwrap.dedent("""\
        __version__ = 'stub-not-game-proof'
        threads = 0
        def set_num_threads(value):
            global threads
            threads = value
        def get_num_threads(): return threads
        def get_num_interop_threads(): return 1
        """), encoding="utf-8")
    (package / "agent.py").write_text(textwrap.dedent("""\
        import json
        import sys
        def project(value): return value + 1
        class MStd:
            def forward(self, value): return value * 2
        def serve(config, manifest, owner, *, local_inference=None):
            assert project(20) == 21
            assert MStd().forward(21) == 42
            if json.loads(config.read_bytes()).get('return_normally'):
                return
            print('stub-agent-serving', flush=True)
            sys.stdin.read()
        """), encoding="utf-8")
    binding = tmp_path / "binding"
    install = binding / "installation"
    install.mkdir(parents=True)
    config = install / "config.json"
    config.write_text("{}", encoding="utf-8")
    manifest = install / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    command = [sys.executable, str(FRIDAY / "profile-python"), "-m", "stpd.p1.agent",
               "--config", str(config), "--manifest", str(manifest),
               "--binding-root", str(binding)]
    # The test process receives only these nonsensitive settings, not a copied account env.
    environment = {"PATH": os.defpath, "PYTHONPATH": str(stubs),
                   "FRIDAY_INFERENCE_THREADS": "1"}
    return command, environment, config, binding / "agent-performance.json"


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT, signal.SIGKILL])
def test_profiler_completed_checkpoints_survive_signals_with_original_exit(profiler_stub, number):
    import hashlib
    import json

    command, environment, _config, output = profiler_stub
    with subprocess.Popen(command, env=environment, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        try:
            readable, _, _ = select.select([process.stdout], [], [], 15)
            assert readable, "stub Agent did not enter its blocking serve"
            assert process.stdout.readline() == "stub-agent-serving\n"
            completed = json.loads(output.read_bytes())
            assert completed["project_calls"] == completed["forward_calls"] == 1
            assert completed["checkpoint_scope"] == "completed_calls_only"
            assert completed["observer_prior_checkpoint_writes"] == 1
            assert completed["observer_prior_checkpoint_seconds"] >= 0
            process.send_signal(number)
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == -number
            assert stdout == "" and stderr == ""
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    metrics = json.loads(output.read_bytes())
    if number == signal.SIGKILL:
        assert "termination_signal" not in metrics
    else:
        assert metrics["termination_signal"] == number
    assert metrics["project_calls"] == metrics["forward_calls"] == 1
    assert metrics["project_seconds"] >= 0 and metrics["forward_seconds"] >= 0
    assert metrics["threads"] == metrics["actual_threads"] == metrics["interop_threads"] == 1
    assert metrics["torch_version"] == "stub-not-game-proof"
    assert metrics["python_version"] == sys.version.split()[0]
    assert metrics["profiler_sha256"] == hashlib.sha256((FRIDAY / "profile-python").read_bytes()
                                                       ).hexdigest()
    if sys.platform == "linux":
        assert metrics["linux_cpu_affinity"] == sorted(os.sched_getaffinity(0))
    else:
        assert metrics["linux_cpu_affinity"] is None


def test_profiler_optional_metrics_io_failure_preserves_original_success(profiler_stub):
    command, environment, config, output = profiler_stub
    config.write_text('{"return_normally":true}', encoding="utf-8")
    output.mkdir()  # A real filesystem failure at the optional output boundary.
    result = subprocess.run(command, env=environment, capture_output=True, text=True,
                            timeout=15, check=False)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.startswith("friday profiler: optional metrics unavailable ")
    assert result.stderr.count("friday profiler:") == 1
    assert len(result.stderr) < 160
    assert str(output) not in result.stderr
