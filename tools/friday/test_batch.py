"""Cheap resource-adapter regressions; no invented gameplay/typed evidence."""
from __future__ import annotations

import importlib.util
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('friday_batch', Path(__file__).with_name('batch.py'))
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
            'friday_submit', Path(__file__).with_name('submit.py'))
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            configuration = root / 'batch.json'
            batch.save(configuration, {'persistent_root': str(root / 'persistent')})
            with patch.object(module, 'output', side_effect=['297/100/0/397', '1\n2']):
                result = module.submit(configuration, 'trial', 32, 'fc430', dry_run=True)
            self.assertEqual(result['concurrency'], 25)
            self.assertIn('--array=0-31%25', result['command'])
            self.assertIn('--cpus-per-task=1', result['command'])
            self.assertIn('--no-requeue', result['command'])
            self.assertIn('--time=01:05:00', result['command'])
            self.assertIn('--constraint=fc430', result['command'])

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


if __name__ == '__main__':
    unittest.main()
