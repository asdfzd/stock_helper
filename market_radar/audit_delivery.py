"""Disabled delivery/AI boundaries. No network client or Codex execution here.

A future private-destination adapter requires separate approval and verification.
The queue is restart-safe; only fixed enums, dates and counts may be exported.
"""
from datetime import datetime, timezone
import threading

from .postrun_audit import read_json, write_output, enum, stamp, number, SEVERITIES, failure_code, confined

FIELDS = {
    'collector_status': {'PASS', 'FAIL', 'UNKNOWN'},
    'research_status': {'VERIFIED', 'FAILED', 'UNKNOWN'},
    'preservation_status': {'VERIFIED', 'BLOCKED', 'INCOMPLETE', 'UNKNOWN'},
    'paper_status': {'VERIFIED', 'FAILED', 'UNKNOWN'},
    'rotation_status': {'VERIFIED', 'FAILED', 'UNKNOWN'},
    'opening_status': {'COMPLETE', 'PARTIAL', 'EMPTY', 'INCOMPLETE', 'UNKNOWN'},
    'severity': SEVERITIES,
    'ws_status': {'DISCONNECTED_OBSERVED', 'RECEIPT_GAP_OBSERVED', 'STICKY_GAP_WITH_RECENT_RECEIPT', 'RECENT_RECEIPT_OBSERVED', 'UNKNOWN'},
    'archive_status': {'RECORDED_VERIFIED_PRESENT', 'MISSING', 'NOT_VERIFIED'},
    'shutdown_request_status': {'DISABLED', 'REQUESTING', 'SCHEDULED', 'CANCELLED', 'CANCEL_FAILED', 'WAITING_SHUTDOWN',
                                'REQUEST_ACCEPTED_POWER_OFF_NOT_VERIFIED', 'UNKNOWN'},
}


def external_summary(report):
    # Not a recursive redactor: raw evidence, arbitrary strings, paths, hashes,
    # account fields and exception text are never copied into this new object.
    result = {key: enum(report.get(key), choices) for key, choices in FIELDS.items()}
    import re
    day = report.get('et_date')
    result.update(schema=1, et_date=day if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(day)) else None,
        detected_at_utc=stamp(report.get('detected_at_utc')), actual_power_off='NOT_VERIFIED')
    result['counts'] = {key: number((report.get('execution') or report.get('counts') or {}).get(key))
                        for key in ('batch_count', 'snapshot_count', 'candidate_count')}
    first = report.get('first_failure') or {}
    result['first_failure'] = dict(code=failure_code(first.get('code')), occurred_at=stamp(first.get('occurred_at'))) if first else None
    if first and first.get('primary_reason'):
        result['first_failure'].update(primary_reason=failure_code(first['primary_reason']),
            primary_reason_recorded_at=stamp(first.get('primary_reason_recorded_at')))
    return result


class DeliveryQueue:
    def __init__(self, folder):
        self.folder = folder

    def prepare(self, report):
        import uuid
        if confined(self.folder, 'delivery.json').exists(): return
        write_output(self.folder, 'outbox.json', external_summary(report))
        write_output(self.folder, 'delivery.json', dict(status='DISABLED', attempts=0, delivery_id=uuid.uuid4().hex))

    def attempt(self, *, approved_adapter=None, timeout=1.0):
        # No installed transport exists by default. A future adapter must be
        # restricted to the separately approved private destination and scope.
        if approved_adapter is None:
            return 'DISABLED'
        if (not getattr(approved_adapter, 'user_approved', False) or
                not getattr(approved_adapter, 'private_destination_verified', False) or
                not getattr(approved_adapter, 'destination', None)):
            return 'DISABLED'
        prior = read_json(self.folder, 'delivery.json')
        if prior.get('status') == 'SENT': return 'SENT'
        attempts = int(prior.get('attempts', 0))
        if attempts >= 3: return 'FAILED'
        payload = external_summary(read_json(self.folder, 'outbox.json'))
        done, result = threading.Event(), []
        def deliver():
            try:
                result.append(approved_adapter.send(payload, timeout=min(1.0, timeout), idempotency_key=prior['delivery_id']))
            except Exception:
                result.append(False)
            finally:
                done.set()
        worker = threading.Thread(target=deliver, daemon=True, name='audit-summary-delivery')
        worker.start()
        done.wait(max(0, min(1.0, timeout)))
        attempts += 1
        status = 'SENT' if done.is_set() and result == [True] else 'FAILED' if attempts >= 3 else 'PENDING_RETRY'
        write_output(self.folder, 'delivery.json', dict(status=status, attempts=attempts,
            delivery_id=prior['delivery_id'], updated_at=datetime.now(timezone.utc).isoformat()))
        return status


def ai_review_request(report, *, enabled=False, environment_verified=False, usage_limit_verified=False):
    if not enabled:
        return dict(status='DISABLED')
    if report.get('severity') == 'PASS':
        return dict(status='NOT_REQUIRED')
    if not environment_verified or not usage_limit_verified:
        return dict(status='BLOCKED_UNVERIFIED_ENVIRONMENT_OR_BUDGET')
    return dict(status='READY_FOR_SEPARATELY_APPROVED_READ_ONLY_WORKER',
        input=external_summary(report), allowlist=['sanitized_summary.json'],
        timeout_seconds=60, code_changes_allowed=False, subprocess_actions_allowed=False,
        execution='not_implemented_in_collector',
        required_sections=['failure', 'first_error', 'evidence', 'cause_fact_or_hypothesis',
                           'affected_scope', 'checks_before_next_run', 'proposed_change_only'])
