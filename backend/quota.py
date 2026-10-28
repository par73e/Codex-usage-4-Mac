"""Observed account quota snapshots. Never infer quota from token counts."""
from datetime import datetime, timezone
import hashlib
import json
import math

GAP_MS = 15 * 60 * 1000


def parse_quota(event):
    payload = event.get('payload')
    if event.get('type') != 'event_msg' or not isinstance(payload, dict) or payload.get('type') != 'token_count':
        return []
    limits = payload.get('rate_limits')
    if not isinstance(limits, dict):
        return []
    try:
        timestamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            return []
        timestamp = timestamp.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    result = []
    limit_id = limits.get('limit_id') or 'legacy'
    if not isinstance(limit_id, str):
        return []
    for slot in ('primary', 'secondary'):
        window = limits.get(slot)
        if not isinstance(window, dict):
            continue
        used, minutes, reset = (window.get(k) for k in ('used_percent', 'window_minutes', 'resets_at'))
        if (isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used)
                or not 0 <= used <= 100 or isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0):
            continue
        # Some records omit the reset deadline. Missing is not epoch zero.
        if reset is not None and (isinstance(reset, bool) or not isinstance(reset, (int, float)) or not math.isfinite(reset)):
            continue
        try:
            reset_at = datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset is not None else None
        except (ValueError, OverflowError, OSError):
            continue
        row = {'timestamp': timestamp.isoformat(), 'timestamp_ms': int(timestamp.timestamp() * 1000),
               'limit_id': limit_id, 'slot': slot, 'window_minutes': minutes,
               'window_key': f'{limit_id}:{slot}:{minutes}', 'used_percent': used,
               'remaining_percent': 100 - used, 'resets_at': reset_at}
        row['snapshot_id'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:24]
        result.append(row)
    return result


def deduplicate_quota(rows):
    unique = {row['snapshot_id']: row for row in rows}
    groups = {}
    for row in unique.values():
        groups.setdefault((row['window_key'], row['timestamp_ms']), []).append(row)
    conflicts = {key for key, values in groups.items() if len({r['used_percent'] for r in values}) > 1}
    # Equal timestamps with conflicting percentages cannot establish an order.
    clean = [r for r in unique.values() if (r['window_key'], r['timestamp_ms']) not in conflicts]
    return sorted(clean, key=lambda r: (r['timestamp_ms'], r['window_key'], r['snapshot_id'])), len(conflicts)


def query_quota(rows, start_ms, end_ms):
    groups = {}
    for row in rows:
        groups.setdefault(row['window_key'], []).append(row)
    windows = []
    for key, history in groups.items():
        points = [r for r in history if start_ms <= r['timestamp_ms'] < end_ms]
        before = next((r for r in reversed(history) if r['timestamp_ms'] < start_ms), None)
        if not points and before is None:
            continue
        events = []
        previous = before
        for point in points:
            gain = previous['used_percent'] - point['used_percent'] if previous else 0
            if previous and (gain >= 10 or gain >= 5 and point['used_percent'] == 0):
                events.append({'id': previous['snapshot_id'] + '-' + point['snapshot_id'],
                               'kind': 'full_recovery' if point['used_percent'] == 0 else 'recovery',
                               'lower_timestamp': previous['timestamp'], 'upper_timestamp': point['timestamp'],
                               'lower_ms': previous['timestamp_ms'], 'upper_ms': point['timestamp_ms'],
                               'remaining_before': previous['remaining_percent'], 'remaining_after': point['remaining_percent'],
                               'scheduled_reset_changed': previous['resets_at'] != point['resets_at']})
            previous = point
        example = points[-1] if points else before
        windows.append({'key': key, 'limit_id': example['limit_id'], 'slot': example['slot'],
                        'window_minutes': example['window_minutes'], 'points': points,
                        'context_before': before, 'events': events})
    return {'windows': sorted(windows, key=lambda w: w['key']), 'gap_threshold_ms': GAP_MS,
            'scope': 'account', 'sampling': 'log_snapshots'}
