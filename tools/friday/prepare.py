#!/usr/bin/env python3
"""Freeze a Friday engineering seed pool and its non-secret deployment inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from batch import POOL_SCHEMA, SCHEMA, hashed, pool_groups, save


def prepare(output: Path, execution: Path, *, namespace: str, groups: int, per_group: int,
            cells: list[str], persistent: Path) -> dict:
    from stpd.evaluation.suites import diagnostic_reference_manifest

    if not namespace or not 1 <= groups <= 1000 or not 1 <= per_group <= 10:
        raise ValueError('bounded_namespace_groups_and_count_required')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifests = []
    for ordinal in range(groups):
        identity = hashlib.sha256(f'{namespace}\0{ordinal}'.encode()).hexdigest()[:32]
        manifest = diagnostic_reference_manifest('v0.111.0', identity, per_group)
        manifest['host'] = 'Managed'
        manifests.append(manifest)
    pool = {'schema': POOL_SCHEMA, 'purpose': 'engineering_only',
            'namespace': namespace, 'comparison_cells': cells, 'groups': manifests,
            'qualification': 'none', 'training_admission': False}
    pool_groups(pool)
    pool_path = output / 'pool.json'
    save(pool_path, pool)
    configuration = {'schema': SCHEMA,
                     'execution': {'path': str(execution.resolve(strict=True)),
                                   'sha256': hashed(execution)},
                     'pool': {'path': str(pool_path.resolve()), 'sha256': hashed(pool_path)},
                     'persistent_root': str(persistent)}
    save(output / 'batch.json', configuration)
    return {'configuration': str(output / 'batch.json'), 'pool_sha256': hashed(pool_path),
            'planned_seeds_per_cell': groups * per_group, 'cells': cells}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--groups', type=int, default=1)
    parser.add_argument('--per-group', type=int, default=8)
    parser.add_argument('--cell', action='append', required=True)
    parser.add_argument('--persistent', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(prepare(args.output.resolve(), args.execution, namespace=args.namespace,
                            groups=args.groups, per_group=args.per_group, cells=args.cell,
                            persistent=args.persistent), indent=2))


if __name__ == '__main__':
    main()
