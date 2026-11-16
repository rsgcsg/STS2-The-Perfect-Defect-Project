"""Re-freezing a Managed evaluation template: only adapter and producer change, recorded."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

FRIDAY = Path(__file__).resolve().parents[2] / 'tools/friday'
ROOT = FRIDAY.parents[1]


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location('friday_eval_template',
                                                  FRIDAY / 'eval_template.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch.object(sys, 'path', [str(FRIDAY), *sys.path]):
        yield module


ADAPTER = {'id': 'stpd-p1-mstd-agent', 'version': '1',
           'protocol': 'sts2.policy-runtime/agent-session-ndjson-1'}
TEMPLATE = {'schema': 'spireagent/managed-evaluation-execution-v1',
            'agent_adapter': {**ADAPTER, 'code_sha256': '7' * 64},
            'producer': {'repository': 'r', 'source_revision': 'a' * 40, 'uv_lock_sha256': 'u'},
            'host_pin': {'version': 'rc.66'}, 'runtime_pin': {'bundle': 'r1'},
            'initial_progress': {'progress_json': '{}'}, 'models': {}, 'store': '/p'}
NEW = {'agent_adapter': {**ADAPTER, 'code_sha256': '2' * 64},
       'producer': {'repository': 'r', 'source_revision': 'b' * 40, 'uv_lock_sha256': 'u'}}


def test_closure_list_reproduces_the_executor_adapter_identity(tool):
    """The record's per-file listing is only true while ADAPTER_CLOSURE is export's list."""
    pytest.importorskip('torch')
    from stpd.p1 import export

    assert tool.closure_code_sha256(lambda path: (ROOT / path).read_bytes()) == \
        export.adapter_identity()['code_sha256']


def test_refreeze_changes_only_adapter_and_producer_and_records_both(tool, tmp_path):
    old = tmp_path / 'template.json'
    old.write_text(json.dumps(TEMPLATE))
    out = tmp_path / 'new/template.json'
    record = tool.refreeze(old, out, identities=NEW)
    written = json.loads(out.read_bytes())
    assert written == {**TEMPLATE, **NEW}
    assert {k for k in TEMPLATE if written[k] != TEMPLATE[k]} == {'agent_adapter', 'producer'}
    assert record['agent_adapter'] == {'old': TEMPLATE['agent_adapter'],
                                       'new': NEW['agent_adapter']}
    assert record['template_out']['sha256'] == tool.hashlib.sha256(out.read_bytes()).hexdigest()
    assert json.loads(Path(str(out) + '.refreeze.json').read_bytes()) == record
    assert 'host_pin' in record['unchanged'] and 'previous' not in record
    assert oct(out.stat().st_mode & 0o777) == '0o600'
    with pytest.raises(ValueError, match='never_overwritten'):
        tool.refreeze(old, out, identities=NEW)
    with pytest.raises(ValueError, match='already_current'):
        tool.refreeze(out, tmp_path / 'again.json', identities=NEW)
    old.write_text(json.dumps({**TEMPLATE, 'schema': 'other'}))
    with pytest.raises(ValueError, match='execution_template_required'):
        tool.refreeze(old, tmp_path / 'other.json', identities=NEW)


def test_previous_revision_names_the_pinned_commit_and_changed_closure_files(tool, tmp_path):
    """--previous-revision: does that commit reproduce the old pin, and what changed since."""
    if not (ROOT / '.git').exists():
        pytest.skip('needs the git checkout')
    head = tool.at_revision('HEAD')
    assert head['closure_files_changed_since'] == [] and len(head['code_sha256']) == 64
    old = tmp_path / 'template.json'
    old.write_text(json.dumps({**TEMPLATE, 'agent_adapter': {
        **ADAPTER, 'code_sha256': head['code_sha256']}}))
    record = tool.refreeze(old, tmp_path / 'new.json', previous_revision='HEAD',
                           identities=NEW)
    assert record['previous']['reproduces_old_pin'] is True
    record = tool.refreeze(tmp_path / 'template.json', tmp_path / 'stale.json',
                           previous_revision='HEAD',
                           identities={**NEW, 'agent_adapter': {**ADAPTER,
                                                                'code_sha256': '3' * 64}})
    assert record['previous']['revision'] == head['revision']
