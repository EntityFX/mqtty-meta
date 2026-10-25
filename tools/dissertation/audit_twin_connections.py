"""Describe observed subscriber connectivity without inferring a subscription."""
import math


def subscriber_coverage(events, start, end):
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError('Expected finite 0 <= start < end')
    selected = []
    for event in events:
        if event.get('client') != 'subscriber':
            continue
        stamp = event['elapsedSeconds']
        state = event['state']
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp < 0:
            raise ValueError('Invalid subscriber event time')
        if state not in ('connected', 'disconnected'):
            raise ValueError('Unknown subscriber state')
        selected.append(event)
    selected.sort(key=lambda e: e['elapsedSeconds'])
    prior = [e for e in selected if e['elapsedSeconds'] <= start]
    state_at_start = prior[-1]['state'] if prior else None
    changes = [e for e in selected if start < e['elapsedSeconds'] < end]
    if state_at_start is None:
        status = 'unobserved'
    elif state_at_start == 'disconnected':
        status = 'disconnectedAtStart'
    elif any(e['state'] == 'disconnected' for e in changes):
        status = 'interrupted'
    else:
        status = 'continuousConnected'
    return dict(status=status, stateAtStart=state_at_start, changesInWindow=changes,
                subscriberEvents=selected, windowStart=start, windowEnd=end,
                scope='Observed connection events only; subscription and packet-level correctness not established')
