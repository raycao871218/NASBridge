"""11:00 retry controller. Only starts the existing, coalesced daily service."""
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from rhino_schedule import CHINA, RETRY_FILE, read_state, write_state, retry_due

BUSY = {'active', 'activating', 'reloading', 'deactivating'}


def daily_busy():
    result = subprocess.run(['systemctl', 'show', 'rhino-daily.service', '-p', 'ActiveState', '--value'],
                            capture_output=True, text=True, timeout=10, check=True)
    value = result.stdout.strip()
    if value not in BUSY | {'inactive', 'failed'}: raise RuntimeError('Unknown daily service state')
    return value in BUSY


def finalize():
    state = read_state()
    if state.get('status') == 'retrying':
        write_state(dict(state, status='completed', finished_at=datetime.now(CHINA).isoformat(),
                         service_result=os.environ.get('SERVICE_RESULT', 'unknown')))


def main():
    RETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with (RETRY_FILE.parent / 'retry.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        # A 10:00 run can still be finishing at 11:00. Do not lose the retry by coalescing it.
        until = time.monotonic() + 21660
        while daily_busy():
            if time.monotonic() >= until: raise TimeoutError('Daily service did not finish')
            time.sleep(5)
        state = read_state()
        if not retry_due(state, datetime.now(CHINA)): return
        write_state(dict(state, status='retrying', attempted_at=datetime.now(CHINA).isoformat()))
        result = subprocess.run(['sudo', '-n', '/usr/bin/systemctl', 'start', 'rhino-daily.service'], timeout=21660)
        state = read_state()
        write_state(dict(state, status='completed', finished_at=datetime.now(CHINA).isoformat(), returncode=result.returncode))
        if result.returncode: raise SystemExit(result.returncode)


if __name__ == '__main__':
    finalize() if '--finalize-service' in sys.argv else main()
