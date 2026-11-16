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


def revisions(old='7' * 64, head='2' * 64):
    """at_revision stand-in: the previous commit computes `old`, HEAD computes `head`."""
    return lambda rev: {'revision': rev, 'code_sha256': head if rev == 'HEAD' else old,
                        'closure_files_changed_since': [] if rev == 'HEAD' else
                        ['python/stpd/p1/inference_service.py']}


def run(tool, old, out, **options):
    return tool.refreeze(old, out, **{'previous_revision': 'abc', 'reason': 'PR #592 review',
                                      'identities': NEW, 'revision': revisions(), **options})


def test_refreeze_changes_only_adapter_and_producer_and_records_both(tool, tmp_path):
    old = tmp_path / 'template.json'
    old.write_text(json.dumps(TEMPLATE))
    out = tmp_path / 'new/template.json'
    record = run(tool, old, out)
    written = json.loads(out.read_bytes())
    marker = written.pop('refrozen_from')
    assert written == {**TEMPLATE, **NEW}
    assert marker == {'template_sha256': tool.hashlib.sha256(old.read_bytes()).hexdigest(),
                      'record': 'template.json.refreeze.json',
                      'agent_adapter_code_sha256': '7' * 64, 'reason': 'PR #592 review'}
    assert record['agent_adapter'] == {'old': TEMPLATE['agent_adapter'],
                                       'new': NEW['agent_adapter']}
    assert record['reason'] == 'PR #592 review' and record['head'] == 'HEAD'
    assert record['previous']['reproduces_old_pin'] is True
    assert record['previous']['closure_files_changed_since'] == [
        'python/stpd/p1/inference_service.py']
    assert record['template_out']['sha256'] == tool.hashlib.sha256(out.read_bytes()).hexdigest()
    assert json.loads(Path(str(out) + '.refreeze.json').read_bytes()) == record
    assert 'host_pin' in record['unchanged'] and 'models' in record['unchanged']
    assert oct(out.stat().st_mode & 0o777) == '0o600'
    with pytest.raises(ValueError, match='never_overwritten'):
        run(tool, old, out)
    with pytest.raises(ValueError, match='from_an_original_template_required'):
        run(tool, out, tmp_path / 'again.json')
    with pytest.raises(ValueError, match='already_current'):
        run(tool, old, tmp_path / 'same.json',
            identities={**NEW, 'agent_adapter': TEMPLATE['agent_adapter']})
    with pytest.raises(ValueError, match='reason_required'):
        run(tool, old, tmp_path / 'why.json', reason=' ')
    other = tmp_path / 'other-schema.json'
    other.write_text(json.dumps({**TEMPLATE, 'schema': 'other'}))
    with pytest.raises(ValueError, match='execution_template_required'):
        run(tool, other, tmp_path / 'other.json')


def test_refreeze_refuses_a_record_that_could_lie(tool, tmp_path):
    """The previous commit must reproduce the old pin; HEAD must be the imported source."""
    old = tmp_path / 'template.json'
    old.write_text(json.dumps(TEMPLATE))
    with pytest.raises(ValueError, match='does_not_reproduce_the_old_pin'):
        run(tool, old, tmp_path / 'a.json', revision=revisions(old='9' * 64))
    with pytest.raises(ValueError, match='checkout_head_is_not_the_imported_source'):
        run(tool, old, tmp_path / 'b.json', revision=revisions(head='3' * 64))
    assert not list(tmp_path.glob('[ab].json*'))  # refused before anything is written


def test_refreeze_refuses_when_any_other_key_would_change(tool, tmp_path):
    """The guard compares every key but adapter/producer (a 3-pin guard never fired)."""
    old = tmp_path / 'template.json'
    old.write_text(json.dumps(TEMPLATE))
    import release_swap

    for key, value in (('models', {'m': 1}), ('store', '/q'), ('extra', 1)):
        def build(old_value, new, key=key, value=value):
            return {**old_value, **new, key: value}

        with (patch.object(release_swap, 'build_execution', build),
              pytest.raises(ValueError, match=f'changed_other_keys: {key}$')):
            run(tool, old, tmp_path / f'{key}.json')


def test_previous_revision_names_the_pinned_commit_and_changed_closure_files(tool):
    """at_revision on the real checkout: HEAD computes the live identity, nothing changed."""
    if not (ROOT / '.git').exists():
        pytest.skip('needs the git checkout')
    head = tool.at_revision('HEAD')
    assert head['closure_files_changed_since'] == [] and len(head['code_sha256']) == 64
    assert head['code_sha256'] == tool.closure_code_sha256(
        lambda path: (ROOT / path).read_bytes())  # a clean checkout: HEAD is what is imported
