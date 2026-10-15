"""Daily runner with durable stages, heartbeats and bounded subprocesses.

The existing rhino_db.py supplies private database settings on the executor.
"""
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rhino_db
from rhino_events import PREFIX

HERE = Path(__file__).resolve().parent
PYTHON = HERE / '.venv/bin/python'
ROOT = rhino_db.ROOT
STATE_DIR = HERE / 'state/progress'
CHINA = timezone(timedelta(hours=8))
STAGES = [('check', '检查共享目录'), ('discover', '发现新视频'), ('download', '下载与合并'),
          ('transcribe', '语音转写'), ('summary_skill', '生成概要与检查分析框架'),
          ('sync', '同步分析框架'), ('save', '保存运行结果')]
LIMITS = {'check': 120, 'discover': 360, 'download': 5400, 'transcribe': 5400,
          'summary_skill': 7200, 'sync': 300, 'save': 120}
QUIET_LIMITS = {'download': 600, 'transcribe': 600}
TOTAL_LIMIT = 21600
PROGRESS_SQL = '''CREATE TABLE IF NOT EXISTS public.rhino_daily_progress (
    run_id uuid PRIMARY KEY REFERENCES public.rhino_daily_runs(run_id),
    progress jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())'''


def stamp():
    return datetime.now(timezone.utc).isoformat()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class TaskStopped(BaseException):
    pass


class TaskTimeout(Exception):
    pass


class Tracker:
    def __init__(self, payload):
        self.payload = payload
        self.started = self.stage_started = self.last_activity = time.monotonic()
        self.current = 'check'
        self.last_publish = 0
        self.data = dict(run_id=payload['run_id'], status='running', boot_id=boot_id(),
                         invocation_id=os.environ.get('INVOCATION_ID', ''), pid=os.getpid(),
                         started_at=payload['started_at'], heartbeat_at=stamp(), last_progress_at=stamp(),
                         current_stage='check', stages=[dict(id=k, label=v, status='pending',timeout_seconds=LIMITS[k]) for k,v in STAGES])
        self.event(dict(stage='check', state='running', message='正在准备任务'))

    def event(self, event):
        stage = event.get('stage')
        if stage not in LIMITS or event.get('state', 'running') not in {'running','success','failed','skipped'}:
            return
        if stage != self.current:
            previous = self.stage(self.current)
            if previous['status'] == 'running':
                previous.update(status='failed', finished_at=stamp(), message='阶段退出时未收到完成记录')
            self.current = stage
            self.stage_started = time.monotonic()
        row = self.stage(stage)
        if event.get('item') and event.get('item')!=row.get('item'):
            row.pop('percent',None)
        row.setdefault('started_at', stamp())
        row.update(status=event.get('state', 'running'), message=str(event.get('message', ''))[:500])
        for key in ('item', 'percent'):
            if key in event: row[key] = event[key]
        if row['status'] != 'running': row['finished_at'] = stamp()
        self.last_activity = time.monotonic()
        self.data.update(current_stage=stage, last_progress_at=stamp())
        self.publish(force=row['status'] != 'running')

    def stage(self, name):
        return next(row for row in self.data['stages'] if row['id'] == name)

    def publish(self, force=False):
        if not force and time.monotonic() - self.last_publish < 2: return
        self.last_publish = time.monotonic()
        self.data['heartbeat_at'] = stamp()
        STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in (self.payload['run_id']+'.json', 'current.json'):
            target = STATE_DIR / name
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(target)
        connection = None
        try:
            connection = rhino_db.connect()
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '5s'")
                cursor.execute(PROGRESS_SQL)
                cursor.execute('''INSERT INTO public.rhino_daily_progress (run_id, progress) VALUES (%s, %s::jsonb)
                    ON CONFLICT (run_id) DO UPDATE SET progress=EXCLUDED.progress, updated_at=now()''',
                    (self.payload['run_id'], json.dumps(self.data, ensure_ascii=False)))
            connection.commit()
        except Exception:
            print('PROGRESS_DB_UNAVAILABLE (local progress retained)', flush=True)
        finally:
            if connection is not None: connection.close()

    def check_deadlines(self):
        now = time.monotonic()
        if now-self.started >= TOTAL_LIMIT:
            raise TaskTimeout('任务超过总时限 6 小时')
        if now-self.stage_started >= LIMITS[self.current]:
            raise TaskTimeout(f'{self.stage(self.current)["label"]}超过阶段时限')
        if self.current in QUIET_LIMITS and now-self.last_activity >= QUIET_LIMITS[self.current]:
            raise TaskTimeout(f'{self.stage(self.current)["label"]}连续 10 分钟没有新进度')

    def finish(self, status, message=''):
        self.data.update(status=status, finished_at=stamp(), message=message)
        for row in self.data['stages']:
            if row['status']=='running': row.update(status=status if status!='success' else 'success', finished_at=stamp(), message=message or row.get('message',''))
            elif row['status']=='pending': row.update(status='skipped', message='本次执行未进入此阶段')
        self.publish(force=True)


def terminate(process):
    if process.poll() is None:
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=5)


def run_stage(name, tracker):
    env = dict(os.environ, PYTHONUNBUFFERED='1')
    process = subprocess.Popen([str(PYTHON), str(HERE/name)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, start_new_session=True, env=env)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b''
    heartbeat = time.monotonic()
    try:
        while selector.get_map():
            tracker.check_deadlines()
            if time.monotonic()-heartbeat >= 15:
                tracker.publish(force=True); heartbeat=time.monotonic()
            for key, _ in selector.select(timeout=1):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    break
                buffer += chunk
                lines = buffer.split(b'\n'); buffer = lines.pop()
                # Bound a malformed/no-newline child stream.
                if len(buffer)>65536: buffer=buffer[-65536:]
                for raw in lines:
                    line=raw.decode('utf-8', errors='replace')
                    print(line, flush=True)
                    if line.startswith(PREFIX):
                        try: tracker.event(json.loads(line[len(PREFIX):]))
                        except (ValueError, TypeError): pass
                    elif tracker.current=='download':
                        match=re.search(r'\[download\]\s+([0-9.]+)%', line)
                        if match:
                            tracker.event(dict(stage='download', message='正在下载视频或音频', percent=float(match[1])))
        code=process.wait(timeout=5)
        if code and tracker.stage(tracker.current)['status']=='running':
            tracker.event(dict(stage=tracker.current,state='failed',message='阶段执行失败，请检查执行端日志'))
        return code
    finally:
        terminate(process)
        selector.close()
        process.stdout.close()


def snapshot(folder, suffix):
    return {p.name:(p.stat().st_size,p.stat().st_mtime_ns) for p in folder.glob('*'+suffix) if p.is_file()}


def skill_hash():
    p=ROOT/'技能/rhino-finance-lens/SKILL.md'
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def save(payload):
    connection=rhino_db.connect()
    try:
        rhino_db.flush(connection)
        rhino_db.save(connection,payload)
    finally: connection.close()


def main():
    start=datetime.now(timezone.utc)
    payload=dict(run_id=str(uuid.uuid4()), run_date=start.astimezone(CHINA).date().isoformat(), started_at=start.isoformat(),status='running')
    save(payload)  # Fail before launching work if a new run cannot be recorded.
    tracker=Tracker(payload)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(TaskStopped()))
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(TaskStopped()))
    before={}; before_skill=None; failures=[]; status='failed'; message=''
    try:
        before={key:snapshot(ROOT/folder,suffix) for key,folder,suffix in [('video','视频','.mp4'),('transcript','原文','.txt'),('summary','概要','.md')]}
        before_skill=skill_hash()
        for filename,label in [('rhino-vm.py','media'),('rhino-postprocess.py','summary')]:
            code=run_stage(filename,tracker)
            payload[label+'_status']='failed' if code else 'success'
            save(payload)  # Publish coarse status after each major stage as well.
            if code: failures.append(label+' 阶段失败，请检查执行端日志')
        tracker.event(dict(stage='save',message='正在保存运行记录与概要索引'))
        for key,folder,suffix in [('video','视频','.mp4'),('transcript','原文','.txt'),('summary','概要','.md')]:
            after=snapshot(ROOT/folder,suffix)
            payload[key+'_files']=[name for name,meta in after.items() if before[key].get(name)!=meta]
        payload['summary_texts']={name:(ROOT/'概要'/name).read_text(encoding='utf-8') for name in payload['summary_files']}
        payload['skill_updated']=before_skill!=skill_hash()
        status='failed' if failures else 'success'
        message='；'.join(failures) if failures else '全部阶段已完成'
    except TaskTimeout as error:
        status='timed_out';message=str(error)
    except TaskStopped:
        status='interrupted';message='任务收到终止信号，已停止子进程'
    except Exception as error:
        status='failed';message='执行异常：'+type(error).__name__+'，请检查执行端日志'
    finally:
        # Ignore repeated stop signals while persisting the final state.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        if status in {'timed_out','interrupted'}:
            field='media_status' if tracker.current in {'check','discover','download','transcribe'} else 'summary_status'
            payload[field]=status
        payload.update(status=status,finished_at=stamp(),error_message=message if status!='success' else None,
                       run_summary=message)
        try:
            save(payload)
            if tracker.current=='save': tracker.event(dict(stage='save',state='success',message='运行记录与概要索引已保存'))
        except Exception:
            payload['status']='logging_failed' if status=='success' else status
            rhino_db.spool(payload)
            status=payload['status'];message='最终记录暂未入库，已保存本地重试记录'
        tracker.finish(status,message)
    if status!='success': raise SystemExit(1)


def finalize_service():
    """ExecStopPost also handles SIGKILL and systemd hard timeouts."""
    try: data=json.loads((STATE_DIR/'current.json').read_text())
    except (OSError,ValueError): return
    result=os.environ.get('SERVICE_RESULT','unknown')
    upgrade_timeout=result=='timeout' and data.get('status')=='interrupted'
    if data.get('invocation_id')!=os.environ.get('INVOCATION_ID') or (data.get('status')!='running' and not upgrade_timeout): return
    data.update(status='timed_out' if result=='timeout' else 'interrupted',finished_at=stamp(),
                message='服务超时终止' if result=='timeout' else '服务退出，未写入正常结束记录')
    for row in data.get('stages',[]):
        if row['status']=='running' or (upgrade_timeout and row['status']=='interrupted'):
            row.update(status=data['status'],finished_at=data['finished_at'])
        elif row['status']=='pending': row.update(status='skipped',message='本次执行未进入此阶段')
    connection=None
    try:
        connection=rhino_db.connect()
        with connection.cursor() as cursor:
            cursor.execute(PROGRESS_SQL)
            cursor.execute("UPDATE public.rhino_daily_runs SET status=%s, finished_at=%s, error_message=%s WHERE run_id=%s AND status IN ('running','interrupted')",
                           (data['status'],data['finished_at'],data['message'],data['run_id']))
            cursor.execute('UPDATE public.rhino_daily_progress SET progress=%s::jsonb, updated_at=now() WHERE run_id=%s',
                           (json.dumps(data),data['run_id']))
        connection.commit()
    except Exception: print('STOP_FINALIZE_DB_UNAVAILABLE',flush=True)
    finally:
        if connection is not None: connection.close()
        # Keep the final state available to the controller even if NAS was down.
        for name in ('current.json',data['run_id']+'.json'):
            target=STATE_DIR/name;temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(data),encoding='utf-8');temp.chmod(0o600);temp.replace(target)


if __name__=='__main__':
    finalize_service() if '--finalize-service' in sys.argv else main()
