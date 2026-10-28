"""Cumulative-only history, kept distinct from response-level measurements."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json


def instant(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return int(date.timestamp() * 1000)
    except (AttributeError, ValueError, TypeError):
        return None


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def snapshot(event, thread_id, created_ms, inherited_counter=False):
    info = (event.get('payload') or {}).get('info') or {}
    usage = info.get('total_token_usage') if isinstance(info, dict) else None
    timestamp = instant(event.get('timestamp'))
    if not isinstance(usage, dict) or timestamp is None or not thread_id:
        return None
    values = [usage.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens')]
    if any(type(n) is not int or n < 0 for n in values) or values[0] + values[1] != values[2]:
        return None
    last = info.get('last_token_usage')
    last_total = last.get('total_tokens') if isinstance(last, dict) else None
    inherited = bool(inherited_counter and created_ms is not None and 0 <= timestamp - created_ms <= 1000
                     and type(last_total) is int and 0 <= last_total < values[2])
    row = dict(thread_id=thread_id, timestamp_ms=timestamp, total_tokens=values[2],
               created_ms=created_ms if created_ms is not None and created_ms <= timestamp else None,
               inherited_counter=inherited)
    row['snapshot_id'] = hashlib.sha256(json.dumps([thread_id, timestamp, values[2]]).encode()).hexdigest()[:24]
    return row


def build_intervals(snapshots, responses):
    first_response = {}
    for row in responses:
        key = row['thread_id']
        first_response[key] = min(first_response.get(key, row['timestamp_ms']), row['timestamp_ms'])
    grouped = defaultdict(dict)
    conflicts = set()
    for point in snapshots:
        key = (point['thread_id'], point['timestamp_ms'])
        if point['timestamp_ms'] >= first_response.get(point['thread_id'], float('inf')):
            continue  # A response measurement takes priority over cumulative telemetry.
        old = grouped[key[0]].get(key[1])
        if old and old['total_tokens'] != point['total_tokens']:
            conflicts.add(key)
        # Some old archives assign one timestamp to many cumulative updates.
        # Keep the largest observed reading as a coarse anchor, never sum them.
        if old is None or point['total_tokens'] > old['total_tokens'] or (point['total_tokens'] == old['total_tokens'] and old.get('created_ms') is None):
            grouped[key[0]][key[1]] = point
    result = []
    for thread, points in grouped.items():
        previous = None
        for timestamp, point in sorted(points.items()):
            coarse = (thread, timestamp) in conflicts
            value = point['total_tokens']
            if previous is None and point.get('inherited_counter'):
                previous = point
                continue  # A child may start with its parent's existing counter.
            reset = bool(previous and value < previous['total_tokens'])
            total = value if previous is None or reset else value - previous['total_tokens']
            lower = previous['timestamp_ms'] if previous else point.get('created_ms')
            unknown = lower is None or lower == timestamp
            lower = timestamp if unknown else min(lower, timestamp)
            if total > 0:
                rid = hashlib.sha256(json.dumps([thread, lower, timestamp, total]).encode()).hexdigest()[:24]
                result.append(dict(interval_id=rid, thread_id=thread, start_ms=lower, end_ms=timestamp,
                                   start=iso(lower), end=iso(timestamp), total_tokens=total,
                                   start_unknown=unknown, counter_reset=reset, coarse_timestamp=coarse, estimated=True))
            previous = point
    return sorted(result, key=lambda row: (row['end_ms'], row['interval_id'])), len(conflicts)


def allocated(row, start, end):
    """Integer cumulative apportionment: adjacent query/bin totals conserve exactly."""
    a, b, tokens = row['start_ms'], row['end_ms'], row['total_tokens']
    if a == b:
        return tokens if start <= b < end else 0
    def at(time):
        return tokens * max(0, min(b - a, time - a)) // (b - a)
    return at(end) - at(start)


def overlaps(row, start, end):
    if row['start_ms'] == row['end_ms']:
        return start <= row['end_ms'] < end
    return row['start_ms'] < end and row['end_ms'] > start
