"""Start the existing daily systemd service through a restricted SSH key."""
import json
from urllib.parse import urlsplit

import paramiko
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

router = APIRouter()
BUSY_STATES = {'active', 'activating', 'reloading', 'deactivating'}


def control_command(action):
    from .main import load_config
    settings = load_config().get('daily_task_control') or {}
    if not settings.get('ssh_host') or not settings.get('ssh_key_path'):
        raise HTTPException(503, '尚未配置每日任务执行连接')
    if action not in {'status', 'start'}:
        raise ValueError('Unsupported daily task action')
    client = paramiko.SSHClient()
    try:
        client.load_host_keys(settings.get('ssh_known_hosts', '/root/.ssh/known_hosts'))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        key = paramiko.Ed25519Key.from_private_key_file(settings['ssh_key_path'])
        client.connect(hostname=settings['ssh_host'], port=int(settings.get('ssh_port', 22)),
                       username=settings.get('ssh_user', 'caobowen'), pkey=key,
                       look_for_keys=False, allow_agent=False, timeout=4, banner_timeout=4, auth_timeout=4)
        stdin, stdout, stderr = client.exec_command(action, timeout=12)
        data = json.loads(stdout.read(65536))
        if stdout.channel.recv_exit_status() or not isinstance(data, dict) or not data.get('available'):
            raise ValueError('Controller unavailable')
        if data.get('active_state') not in BUSY_STATES | {'inactive', 'failed'}:
            raise ValueError('Unknown service state')
        stdin.close();stdout.close();stderr.close()
        return data
    except Exception:
        # Errors can contain private hostnames and key paths.
        raise HTTPException(503, '任务执行服务暂不可用，请刷新记录后重试') from None
    finally:
        client.close()


def manual_control_status(metrics, service=None):
    try:
        data = service if service is not None else control_command('status')
        if not data.get('available', True):
            raise HTTPException(503, '无法确认执行端状态，请稍后刷新')
        running = data['active_state'] in BUSY_STATES
        retry = data.get('retry') or {'available': False, 'status': 'unknown'}
        protected = True if running else None
        if retry.get('available'):
            protected = running or retry.get('service_active', False) or retry.get('status') in {'waiting','retrying'}
        return {'show': True, 'can_trigger': not running, 'running': running,
                'retry': retry, 'shutdown_protected': protected, 'observed_at': data.get('observed_at'),
                'message': '任务正在执行，请等待运行结果' if running else (
                    '今天的任务已成功，可再次手动执行' if metrics.get('today_success_runs', 0)
                    else '今天还没有成功记录，可手动执行一次每日任务')}
    except HTTPException as error:
        return {'show': True, 'can_trigger': False, 'message': error.detail,
                'retry': {'available': False, 'status': 'unknown'}, 'shutdown_protected': None}


def start_daily_task():
    from .main import postgres_connection
    connection = None
    try:
        connection = postgres_connection('nasbridge-daily-task-trigger')
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                # Serialize requests across workers; release on commit/rollback.
                cursor.execute('SELECT pg_try_advisory_xact_lock(731915001)')
                if not cursor.fetchone()[0]:
                    raise HTTPException(409, '已有手动请求正在处理，请刷新记录')
                result = control_command('start')
                if not result.get('accepted'):
                    raise HTTPException(409, '任务正在执行，请等待运行结果')
        return {'status': 'accepted', 'message': '已提交每日任务，请在运行记录中查看进度'}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, '无法确认任务记录，未提交执行请求，请刷新重试') from None
    finally:
        if connection is not None:
            connection.close()


class TriggerPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')


@router.post('/api/daily-tasks/run', status_code=202)
def trigger_daily_task(payload: TriggerPayload, request: Request):
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
        raise HTTPException(403, '不接受跨站请求')
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, '请使用 application/json')
    return JSONResponse(start_daily_task(), status_code=202, headers={'Cache-Control': 'no-store'})
