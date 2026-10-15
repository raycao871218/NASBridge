#!/usr/bin/env python3
"""SSH forced command: only inspect/start the existing rhino-daily service."""
import json
import os
import subprocess

UNIT = 'rhino-daily.service'
BUSY = {'active', 'activating', 'reloading', 'deactivating'}


def control(action):
    if action not in {'status', 'start'}:
        return {'available': False}
    result = subprocess.run(['/usr/bin/systemctl', 'show', UNIT, '-p', 'LoadState', '-p', 'ActiveState'],
                            capture_output=True, text=True, timeout=5, check=True)
    state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if state.get('LoadState') != 'loaded' or state.get('ActiveState') not in BUSY | {'inactive', 'failed'}:
        return {'available': False}
    data = {'available': True, 'active_state': state['ActiveState']}
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
