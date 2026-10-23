#!/usr/bin/env python3
"""SSH forced command: only inspect/start the existing rhino-daily service."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import re

UNIT = 'rhino-daily.service'
BUSY = {'active', 'activating', 'reloading', 'deactivating'}


def retry_status():
    """Expose only scheduling metadata; never return arbitrary executor file contents."""
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'show', 'rhino-retry.service',
                                 '-p', 'LoadState', '-p', 'ActiveState'],
                                capture_output=True, text=True, timeout=3, check=True)
        unit = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if unit.get('LoadState') != 'loaded' or unit.get('ActiveState') not in BUSY | {'inactive', 'failed'}:
            raise ValueError('Unknown retry service')
        data = {'available': True, 'status': 'none', 'service_active': unit['ActiveState'] in BUSY}
        path = Path.home()/'rhino-finance/state/retry.json'
        try:
            if path.stat().st_size > 4096: raise ValueError('Retry state too large')
            state = json.loads(path.read_text())
        except FileNotFoundError:
            return data
        if not isinstance(state, dict) or state.get('status') not in {'waiting','retrying','completed','cancelled'}:
            raise ValueError('Invalid retry state')
        for key in ('status','date','retry_at','attempted_at','finished_at','service_result'):
            if isinstance(state.get(key), str): data[key] = state[key][:100]
        if type(state.get('returncode')) is int: data['returncode'] = state['returncode']
        return data
    except Exception:
        return {'available': False, 'status': 'unknown'}


def control(action):
    if action not in {'status', 'start'}:
        return {'available': False}
    observed_at=datetime.now(timezone.utc).isoformat()
    result = subprocess.run(['/usr/bin/systemctl', 'show', UNIT, '-p', 'LoadState', '-p', 'ActiveState',
                             '-p', 'Result', '-p', 'MainPID', '-p', 'InvocationID', '-p', 'ExecMainStartTimestamp'],
                            capture_output=True, text=True, timeout=5, check=True)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if state.get('LoadState') != 'loaded' or state.get('ActiveState') not in BUSY | {'inactive', 'failed'}:
        return {'available': False}
    data = {'available': True, 'active_state': state['ActiveState'], 'observed_at':observed_at}
    data.update(result=state.get('Result'), main_pid=int(state.get('MainPID', '0')),
                invocation_id=state.get('InvocationID', ''), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    data['retry'] = retry_status()
    # systemd prints executor-local UTC timestamps on the configured runner host.
    match=re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',state.get('ExecMainStartTimestamp',''))
    if match and state['ExecMainStartTimestamp'].endswith(' UTC'):
        data['started_at']=datetime.fromisoformat(match[0]).replace(tzinfo=timezone.utc).isoformat()
    try:
        path=Path.home()/'rhino-finance/state/progress/current.json'
        if path.stat().st_size<=32768: data['progress']=json.loads(path.read_text())
    except (OSError,ValueError): pass
    if action == 'start':
        data['accepted'] = state['ActiveState'] not in BUSY
        if data['accepted']:
            # systemd also coalesces a concurrent timer/manual start into one job.
            subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', UNIT],
                           capture_output=True, timeout=5, check=True)
            data['active_state'] = 'activating'
    return data


if __name__ == '__main__':
    try:
        print(json.dumps(control(os.environ.get('SSH_ORIGINAL_COMMAND', ''))))
    except Exception:
        print(json.dumps({'available': False}))
