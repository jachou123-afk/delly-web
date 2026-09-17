"""Session-local frozen save operations. No recalculation or approval on retry."""
from copy import deepcopy
import time
import uuid

from audit_persistence import result_payload
from dispatch_manager import DispatchError

PENDING_KEY = 'dispatch_pending_audit_save'
MAX_ATTEMPTS = 4  # Initial attempt plus at most three automatic retries.


def pending_save(store, batch, result):
    return dict(payload=result_payload(batch, result), result=deepcopy(result),
                expected_revision=batch.get('_revision', ''), record_id=uuid.uuid4().hex,
                spreadsheet_id=getattr(store.spreadsheet, 'id', ''), attempts=0,
                not_before=0, rate_limited=False, error='')


def rate_limit_delay(exc):
    """Recognize HTTP 429 through wrapped storage exceptions, not message guesses."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        response = getattr(exc, 'response', None)
        if getattr(response, 'status_code', None) == 429 or getattr(exc, 'code', None) == 429:
            try:
                retry_after = float(getattr(response, 'headers', {}).get('Retry-After', 60))
            except (ValueError, TypeError):
                retry_after = 60
            return max(60, retry_after)
        exc = exc.__cause__ or exc.__context__
    return None


def attempt_save(store_factory, pending, *, clock=time.monotonic):
    """Check existing cloud revision before appending the exact same operation ID."""
    if clock() < pending['not_before']:
        return None  # No authentication, metadata, reads, or writes during cooldown.
    pending['attempts'] += 1
    try:
        store = store_factory()
        if getattr(store.spreadsheet, 'id', '') != pending['spreadsheet_id']:
            raise DispatchError('雲表已切換；不會將待保存結果寫入其他雲表')
        return store.save_batch(pending['payload'], expected_revision=pending['expected_revision'],
                                record_id=pending['record_id'])
    except Exception as exc:
        delay = rate_limit_delay(exc)
        pending['rate_limited'] = delay is not None
        pending['not_before'] = clock() + (max(delay, 60 * 2 ** min(pending['attempts'] - 1, 4)) if delay else 5)
        pending['error'] = str(exc)
        return None


def auto_retry_due(pending, *, clock=time.monotonic):
    return (pending['rate_limited'] and pending['attempts'] < MAX_ATTEMPTS
            and clock() >= pending['not_before'])
