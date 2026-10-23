"""Durable once-per-day retry decision, independent of database availability."""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

CHINA = timezone(timedelta(hours=8))
RETRY_FILE = Path('/home/caobowen/rhino-finance/state/retry.json')


def read_state(path=RETRY_FILE):
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict): raise ValueError('Invalid retry state')
        return value
    except FileNotFoundError:
        return {}


def write_state(value, path=RETRY_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.retry-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def record_discovery(started_at, count, path=RETRY_FILE):
    started = datetime.fromisoformat(started_at).astimezone(CHINA)
    if started.weekday() not in range(1, 6): return
    day = started.date().isoformat()
    state = read_state(path)
    if state.get('date') == day and state.get('attempted_at'): return
    if count == 0 and started.hour == 10:
        write_state(dict(date=day, status='waiting', retry_at=started.replace(hour=11, minute=0, second=0, microsecond=0).isoformat()), path)
    elif count > 0 and state.get('date') == day and state.get('status') == 'waiting':
        write_state(dict(state, status='cancelled', reason='new_videos_found'), path)


def retry_due(state, now):
    now = now.astimezone(CHINA)
    if state.get('status') != 'waiting' or state.get('attempted_at'): return False
    return now >= datetime.fromisoformat(state['retry_at'])


def shutdown_hold(state, now):
    # Keep overdue pending retries protected too; never expire the protection at 11:00.
    return bool(state.get('date') and state.get('status') in {'waiting', 'retrying'})
