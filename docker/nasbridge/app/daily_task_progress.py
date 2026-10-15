"""Reconcile durable run records against the executor, without guessing death from network loss."""
import copy
import json
from datetime import datetime, timezone

BUSY = {'active','activating','reloading','deactivating'}
TERMINAL = {'success','failed','logging_failed','interrupted','timed_out'}


def parsed(value):
    if isinstance(value,datetime): return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (AttributeError,TypeError,ValueError): return None


def classify_run(row, service, now=None):
    """Return display status, explanation and whether a terminal write is justified."""
    if row['status']!='running': return row['status'], '', False
    now=now or datetime.now(timezone.utc)
    progress=row.get('progress') or {}
    if not service or not service.get('available'):
        return 'unknown','无法连接执行端，暂时无法确认任务状态',False
    observed=parsed(service.get('observed_at'));started=parsed(row.get('started_at'))
    if observed and started and (observed-started).total_seconds()<5:
        return 'unknown','新任务刚启动，正在确认执行状态',False
    remote=service.get('progress') or {}
    if remote.get('run_id')==row['run_id'] and remote.get('status') in TERMINAL:
        return remote['status'], remote.get('message','执行端已记录结束状态'),True
    if remote.get('run_id')==row['run_id'] and remote.get('status')=='running':
        progress=remote
    if progress.get('boot_id') and progress['boot_id']!=service.get('boot_id'):
        return 'interrupted','执行服务器已重启，原任务已中断',True
    if progress.get('invocation_id') and progress['invocation_id']!=service.get('invocation_id'):
        return 'interrupted','该次服务实例已结束或已被新的执行替代',True
    if service['active_state'] not in BUSY or not service.get('main_pid'):
        status='timed_out' if service.get('result')=='timeout' else 'interrupted'
        return status,'服务超时终止' if status=='timed_out' else '执行进程已退出，未收到正常结束记录',True
    if not progress.get('invocation_id'):
        started=parsed(row.get('started_at'));active_start=parsed(service.get('started_at'))
        if active_start and started and (active_start-started).total_seconds()>5:
            return 'interrupted','旧任务已退出，执行端当前运行的是另一轮任务',True
        return 'unknown','旧版任务缺少执行标识，无法确认其当前阶段',False
    heartbeat=parsed(progress.get('heartbeat_at'))
    if not heartbeat or (now-heartbeat).total_seconds()>90:
        return 'unresponsive','服务进程仍存在，但心跳已超过 90 秒未更新；暂不允许重复执行',False
    return 'running','执行进程及心跳正常',False


def terminal_progress(progress,status,message,now):
    result=copy.deepcopy(progress or {})
    result.update(status=status,message=message,finished_at=now.isoformat())
    for stage in result.get('stages',[]):
        if stage['status']=='running': stage.update(status=status,finished_at=now.isoformat(),message=message)
        elif stage['status']=='pending': stage.update(status='skipped',message='本次执行未进入此阶段')
    return result


def reconcile(connection,service):
    """Repair ALL stale running records, including hidden/off-page ones."""
    from psycopg2.extras import RealDictCursor
    now=datetime.now(timezone.utc)
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute('''SELECT r.run_id::text, r.status, r.started_at, p.progress
            FROM public.rhino_daily_runs r LEFT JOIN public.rhino_daily_progress p USING(run_id)
            WHERE r.status='running' ''')
        for row in cursor.fetchall():
            status,message,terminal=classify_run(dict(row),service,now)
            if not terminal: continue
            # Only change still-running rows: never overwrite a concurrent final success.
            cursor.execute("""UPDATE public.rhino_daily_runs SET status=%s, finished_at=%s,
                error_message=%s, run_summary=%s WHERE run_id=%s AND status='running' RETURNING run_id""",
                (status,now,message,message,row['run_id']))
            if cursor.fetchone() and row.get('progress'):
                remote=service.get('progress') or {}
                progress=remote if remote.get('run_id')==row['run_id'] and remote.get('status') in TERMINAL else terminal_progress(row['progress'],status,message,now)
                cursor.execute('UPDATE public.rhino_daily_progress SET progress=%s::jsonb,updated_at=now() WHERE run_id=%s',
                               (json.dumps(progress),row['run_id']))
    connection.commit()


def display_run(row,service):
    row=dict(row)
    remote=(service or {}).get('progress') or {}
    if row['status']=='running' and remote.get('run_id')==row['run_id']:
        row['progress']=remote
    if row['status']=='running':
        status,message,_=classify_run(row,service)
        row.update(status=status,status_message=message)
    return row
