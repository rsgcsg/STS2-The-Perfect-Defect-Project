"""The existing consume/ACK/next/await boundary under deferred capacity admission."""
from __future__ import annotations

import io
import json

import pytest

from stpd.p1.agent_driver import SCHEMA, serve_session


@pytest.mark.parametrize('stop_waiting', [False, True])
@pytest.mark.parametrize('keyed', [False, True])
def test_acknowledged_capacity_wait_then_next_without_reconsume(stop_waiting, keyed):
    destination = io.StringIO()
    ready = stopped = False
    draws = []
    spec = {'id': 'synthetic-input', 'version': '1', 'sha256': 'a'*64}
    catalog = [{'choice_id': 'first'}, {'choice_id': 'second'}]
    value = {'input_spec': spec, 'previous_consumption_id': None,
             'continuity_token': 'continuity', 'acquisition_id': 'actual-acknowledged-basis',
             'consumption_unit': {'id': 'unit'}, 'catalog': catalog,
             'observation': {'status': 'decision', 'catalog': catalog}}
    manifest = {'adapter': {}, 'artifact': {'sha256': 'b'*64},
                'input': {'input_spec': spec}}

    def message(kind, request, **fields):
        return json.dumps({'schema': SCHEMA, 'message_type': kind, 'request_id': request,
                           'session_id': 'session', 'recovery_epoch': 0, **fields})+'\n'

    def last():
        return json.loads(destination.getvalue().splitlines()[-1])

    def requests():
        nonlocal ready, stopped
        yield message('consume', 'consume', input=value)
        completion = last()['completion']
        yield message('consume_ack', 'consume', completion=completion)
        next_input = {'continuity_token': 'continuity', 'received_cursor': 'known-cursor'}
        yield message('next', 'wait-1', input=next_input)
        assert last()['output']['directive'] == {
            'type': 'await', 'after_cursor': 'known-cursor',
            'condition': 'any_event', 'timeout_ms': 1000}
        assert draws == []
        yield message('next', 'wait-2', input=next_input)
        assert last()['output']['directive']['type'] == 'await'
        assert draws == []
        # Runtime can reuse the same occurrence after await without consume/ACK.
        stopped = stop_waiting
        ready = not stop_waiting
        yield message('next', 'ready', input=next_input)
        directive = last()['output']['directive']
        if stopped:
            assert directive['type'] == 'close' and draws == []
        else:
            assert directive['type'] == 'act'
            assert directive['basis_acquisition_id'] == value['acquisition_id']
            assert directive['selection']['action_id'] == 'second'
            assert draws == ([completion['consumption_id']] if keyed else [])
            yield message('next', 'repeat', input=next_input)
            assert last()['output']['directive'] == directive
            assert draws == ([completion['consumption_id']] if keyed else [])

    def choose(scores, *args):
        assert scores == [1.0, 2.0]
        if keyed:
            assert len(args) == 1
            draws.append(args[0])
        else:
            assert not args  # Preserve the original one-argument argmax caller.
        return 1

    serve_session(manifest, lambda _: pytest.fail('ordinary scorer is not this producer'),
                  choose, requests(), destination, reuse_selection=keyed, keyed_selection=keyed,
                  score_input=lambda _: [1.0, 2.0] if ready else None,
                  close_requested=lambda: stopped)


def test_same_unit_ready_reconsume_ack_draws_once_after_pending_ack():
    destination = io.StringIO()
    ready = False
    draws = []
    spec = {'id': 'synthetic-input', 'version': '1', 'sha256': 'a'*64}
    catalog = [{'choice_id': 'first'}, {'choice_id': 'second'}]
    value = {'input_spec': spec, 'previous_consumption_id': None,
             'continuity_token': 'continuity', 'acquisition_id': 'first-basis',
             'consumption_unit': {'id': 'unit'}, 'catalog': catalog,
             'observation': {'status': 'decision', 'catalog': catalog}}
    manifest = {'adapter': {}, 'artifact': {'sha256': 'b'*64},
                'input': {'input_spec': spec}}

    def message(kind, request, **fields):
        return json.dumps({'schema': SCHEMA, 'message_type': kind, 'request_id': request,
                           'session_id': 'session', 'recovery_epoch': 0, **fields})+'\n'

    def last():
        return json.loads(destination.getvalue().splitlines()[-1])

    def requests():
        nonlocal ready
        yield message('consume', 'first', input=value)
        first = last()['completion']
        yield message('consume_ack', 'first', completion=first)
        assert draws == []
        ready = True
        reobserved = {**value, 'acquisition_id': 'current-acknowledged-basis',
                      'previous_consumption_id': first['consumption_id']}
        yield message('consume', 'second', input=reobserved)
        second = last()['completion']
        assert second['consumption_id'] == first['consumption_id']
        assert second['advanced'] is False
        yield message('consume_ack', 'second', completion=second)
        assert draws == [first['consumption_id']]
        next_input = {'continuity_token': 'continuity', 'received_cursor': 'known-cursor'}
        yield message('next', 'ready', input=next_input)
        directive = last()['output']['directive']
        assert directive['basis_acquisition_id'] == 'current-acknowledged-basis'
        assert directive['selection']['action_id'] == 'second'
        yield message('next', 'repeat', input=next_input)
        assert last()['output']['directive'] == directive
        assert draws == [first['consumption_id']]

    def choose(scores, consumption):
        draws.append(consumption)
        return 1

    serve_session(manifest, lambda _: pytest.fail('ordinary scorer is not this producer'),
                  choose, requests(), destination, reuse_selection=True, keyed_selection=True,
                  score_input=lambda _: [1.0, 2.0] if ready else None)
