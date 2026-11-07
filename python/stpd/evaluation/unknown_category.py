"""Typed category of one unknown/save_failed close, from facts already recorded.

Read-only and deterministic (card U4 A): the close's status and reason codes plus the
U3 failure facts (last undelivered receipt, Runtime last result/taint, handoff reason,
save error). Exactly one category's facts give that category; none, or facts of two
categories, give ``unclassified`` with the reason. Never a retry, label or training input.

- ``environment_gap``: the Managed environment could not settle or witness an action.
- ``infrastructure``: process, allocation, inference or node failure; says nothing about
  the model, so model statistics leave it out and per-node isolation counts it.
- ``save_ledger``: the game ran, but saving, verifying or accounting its original failed.
- ``wall_limit``: the per-game wall limit stopped a running game (``wall_limit_reached``):
  a censored game, not an unknown of the game. Never rerun; scored at its reached floor
  by the conservative score and listed on its own.
"""
from __future__ import annotations

from typing import Any

CATEGORIES = ('environment_gap', 'infrastructure', 'save_ledger', 'wall_limit',
              'unclassified')

SAVE_LEDGER_CODES = frozenset({
    'segment_delivery_incomplete', 'preservation_failed', 'behavior_preservation_failed',
    'preservation_unknown_or_failed', 'report_persistence_failed',
    'publication_unknown_requires_explicit_seal', 'typed_original_verification_failed',
    'post_verification_processing_failed'})
INFRASTRUCTURE_CODES = frozenset({
    'runtime_exited_before_startup', 'runtime_startup_missing', 'runtime_startup_too_large',
    'host_cleanup_unconfirmed', 'abandoned_attempt_requires_owner_read',
    'reserved_attempt_report_unavailable', 'service_status_unavailable_before_offer',
    'actor_terminal_before_close', 'TimeoutExpired', 'MemoryError', 'ConnectionError',
    'ConnectionResetError', 'BrokenPipeError'})
INFRASTRUCTURE_HANDOFFS = frozenset({'agent_call_timeout'})
ENVIRONMENT_RECEIPT_CODES = frozenset({'managed_execution_unknown'})
SETTLING = 'managed_settling_stalled'
WALL_LIMIT = 'episode_wall_limit_reached'  # U4-1 executor reason for the same fact


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def classify_unknown(status: str, reason: Any = None,
                     failure: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{'category', 'rule', 'detail'}`` for one close; ``detail`` is public codes only."""
    failure = failure if isinstance(failure, dict) else {}
    receipt = failure.get('last_unknown_receipt')
    receipt = receipt if isinstance(receipt, dict) else {}
    last = failure.get('last_result')
    last = last if isinstance(last, dict) else {}
    codes = {name: _text(value) for name, value in (
        ('reason', reason), ('execution_reason', failure.get('execution_reason')),
        ('save_error', failure.get('save_error')), ('taint_reason', failure.get('taint_reason')),
        ('handoff_reason', failure.get('handoff_reason')),
        ('last_result_reason', last.get('reason')),
        ('receipt_reason_code', receipt.get('reason_code')))}
    signals: dict[str, list[str]] = {}

    def signal(category: str, rule: str) -> None:
        signals.setdefault(category, []).append(rule)

    if status == 'save_failed':
        signal('save_ledger', 'save_failed_after_retry')
    if (failure.get('wall_limit_reached') is True
            or WALL_LIMIT in (codes['reason'], codes['execution_reason'])):
        signal('wall_limit', WALL_LIMIT)
    for name in ('reason', 'execution_reason'):
        code = codes[name]
        if code in SAVE_LEDGER_CODES:
            signal('save_ledger', code)
        if code in INFRASTRUCTURE_CODES:
            signal('infrastructure', code)
    handoff = codes['handoff_reason']
    if handoff is not None and handoff in INFRASTRUCTURE_HANDOFFS:
        signal('infrastructure', handoff)
    if any(SETTLING in (codes[name] or '') for name in (
            'reason', 'execution_reason', 'taint_reason', 'last_result_reason')):
        signal('environment_gap', SETTLING)
    if (codes['receipt_reason_code'] in ENVIRONMENT_RECEIPT_CODES
            or codes['last_result_reason'] in ENVIRONMENT_RECEIPT_CODES):
        signal('environment_gap', 'managed_execution_not_witnessed')
    floor = failure.get('max_observed_floor')
    detail = {'floor': floor if type(floor) is int else None,
              'step': _text(last.get('action_id')),
              'action': _text(receipt.get('label')),
              'native_error': (codes['receipt_reason_code'] or codes['taint_reason']
                               or codes['handoff_reason'] or codes['save_error']
                               or codes['last_result_reason']),
              'native_detail': _text(receipt.get('detail')),
              'codes': {k: v for k, v in codes.items() if v is not None},
              'wall_limit_reached': failure.get('wall_limit_reached') is True}
    if len(signals) == 1:
        (category, rules), = signals.items()
        return {'category': category, 'rule': rules[0], 'detail': detail}
    reason_text = ('conflicting_facts:' + ','.join(sorted(signals)) if signals
                   else 'no_classifying_fact')
    return {'category': 'unclassified', 'rule': reason_text, 'detail': detail}
