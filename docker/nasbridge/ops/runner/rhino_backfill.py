"""One-shot target handoff to the existing systemd service."""
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
import time

from rhino_schedule import CHINA

FIRST_DATE = date(2026, 10, 8)
STATE = Path(__file__).resolve().parent / 'state'
REQUEST_MAX_AGE = 300


def parse_target(value):
    target = date.fromisoformat(value)
    if value != target.isoformat() or not FIRST_DATE <= target < datetime.now(CHINA).date():
        raise ValueError('Invalid historical date')
    return target


@contextmanager
def locked_request():
    import fcntl

    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (STATE / 'backfill.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield STATE / 'backfill.date'


def take_target():
    if not STATE.is_dir():
        return None
    with locked_request() as request:
        try:
            age = time.time() - request.stat().st_mtime
            value = request.read_text(encoding='ascii')
        except FileNotFoundError:
            return None
        finally:
            request.unlink(missing_ok=True)
        if 0 <= age <= REQUEST_MAX_AGE:
            return parse_target(value)
    return None


def for_target(path, target):
    return target is None or (path.name[:8] == target.strftime('%Y%m%d') and path.name[8:9] in ('-', '_', ' '))
