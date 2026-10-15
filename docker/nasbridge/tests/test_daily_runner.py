"""Executor tests run in an isolated temp workspace, with no NAS/private data."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT=Path(__file__).resolve().parents[1]/'ops/runner'


def load_runner():
    fake=types.ModuleType('rhino_db');fake.ROOT=Path('/unused');fake.connect=MagicMock()
    with patch.dict(sys.modules,{'rhino_db':fake}),patch.object(sys,'path',[str(ROOT)]+sys.path):
        spec=importlib.util.spec_from_file_location('tested_daily_runner',ROOT/'rhino-run.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.r=load_runner()
        self.payload=dict(run_id='run',status='running',started_at='2026-10-15T00:00:00+00:00')

    def tracker(self):
        with patch.object(self.r.Tracker,'publish'):
            t=self.r.Tracker(self.payload)
        t.publish=MagicMock()
        return t

    def test_events_completion_skips_and_heartbeats_not_progress(self):
        t=self.tracker();t.event(dict(stage='check',state='success',message='ok'))
        t.event(dict(stage='discover',state='success',message='none'))
        t.event(dict(stage='download',state='skipped',message='none'))
        t.event(dict(stage='transcribe',state='running',item='<video>',percent=40))
        self.assertEqual(t.stage('check')['status'],'success')
        self.assertEqual(t.stage('download')['status'],'skipped')
        t.finish('interrupted','stopped')
        self.assertEqual(t.stage('transcribe')['status'],'interrupted')
        self.assertEqual(t.stage('save')['status'],'skipped')

    def test_stage_total_and_no_progress_deadlines_are_independent_of_heartbeat(self):
        t=self.tracker();t.event(dict(stage='download',message='start'))
        t.last_activity=time.monotonic()-601
        with self.assertRaisesRegex(self.r.TaskTimeout,'10'):t.check_deadlines()
        t.last_activity=time.monotonic();t.stage_started=time.monotonic()-5401
        with self.assertRaisesRegex(self.r.TaskTimeout,'阶段时限'):t.check_deadlines()
        t.stage_started=time.monotonic();t.started=time.monotonic()-21601
        with self.assertRaisesRegex(self.r.TaskTimeout,'6'):t.check_deadlines()

    @unittest.skipUnless(os.name=='posix','Linux executor signals required')
    def test_real_sigterm_stops_child_and_records_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            (p/'worker.py').write_text('import os,time\nfrom pathlib import Path\nPath('+repr(str(p/'pid'))+').write_text(str(os.getpid()))\ntime.sleep(60)\n')
            harness='''import sys,types,importlib.util,signal
from pathlib import Path
sys.modules['rhino_db']=types.SimpleNamespace(ROOT=Path('/unused'))
sys.path.insert(0,SOURCE)
spec=importlib.util.spec_from_file_location('runner',Path(SOURCE)/'rhino-run.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
r.HERE=Path(TEMP);r.PYTHON=Path(sys.executable)
r.Tracker.publish=lambda *a,**k:None
t=r.Tracker(dict(run_id='test',started_at=r.stamp(),status='running'))
def stop(*args):raise r.TaskStopped()
signal.signal(signal.SIGTERM,stop)
try:r.run_stage('worker.py',t)
except r.TaskStopped:
 t.finish('interrupted','test stop')
 (Path(TEMP)/'result').write_text(t.data['status'])
'''.replace('SOURCE',repr(str(ROOT))).replace('TEMP',repr(folder))
            parent=subprocess.Popen([sys.executable,'-c',harness],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while not (p/'pid').exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue((p/'pid').exists())
                os.kill(parent.pid,signal.SIGTERM)
                out,err=parent.communicate(timeout=8)
                self.assertEqual(parent.returncode,0,err.decode())
                self.assertEqual((p/'result').read_text(),'interrupted')
                with self.assertRaises(ProcessLookupError):os.kill(int((p/'pid').read_text()),0)
            finally:
                if parent.poll() is None:parent.kill();parent.wait()

    def test_successful_run_records_every_completed_or_skipped_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            saved=[];trackers=[]
            def run_stage(name,tracker):
                if not trackers:trackers.append(tracker)
                for key in (['check','discover','download','transcribe'] if name=='rhino-vm.py' else ['summary_skill','sync']):
                    tracker.event(dict(stage=key,state='success' if key in {'check','discover'} else 'skipped'))
                return 0
            old_term=signal.getsignal(signal.SIGTERM);old_int=signal.getsignal(signal.SIGINT)
            try:
                with patch.object(self.r,'ROOT',Path(folder)),patch.object(self.r,'save',side_effect=lambda payload:saved.append(copy.deepcopy(payload))),patch.object(self.r.Tracker,'publish'),patch.object(self.r,'run_stage',side_effect=run_stage):
                    self.r.main()
                self.assertEqual(saved[-1]['status'],'success')
                self.assertEqual(trackers[0].stage('save')['status'],'success')
                self.assertTrue(all(row['status'] in {'success','skipped'} for row in trackers[0].data['stages']))
            finally:signal.signal(signal.SIGTERM,old_term);signal.signal(signal.SIGINT,old_int)

    @unittest.skipUnless(os.name=='posix','Linux executor process groups required')
    def test_real_hung_child_is_killed_on_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'worker.py').write_text('import os,time\nfrom pathlib import Path\nPath('+repr(str(p/'pid'))+').write_text(str(os.getpid()))\ntime.sleep(60)\n')
            t=self.tracker();t.current='check'
            with patch.object(self.r,'HERE',p),patch.object(self.r,'PYTHON',Path(sys.executable)),patch.dict(self.r.LIMITS,check=0.1):
                with self.assertRaises(self.r.TaskTimeout):self.r.run_stage('worker.py',t)
            pid=int((p/'pid').read_text())
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_stop_post_recovers_sigkill_but_preserves_finished_success(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(self.r,'STATE_DIR',Path(folder)),patch.dict(os.environ,INVOCATION_ID='test',SERVICE_RESULT='timeout'):
            data=dict(run_id='run',invocation_id='test',status='running',stages=[dict(id='download',status='running')])
            p=Path(folder)/'current.json';p.write_text(json.dumps(data))
            self.r.finalize_service()
            self.assertEqual(json.loads(p.read_text())['status'],'timed_out')
            connection=self.r.rhino_db.connect.return_value;connection.commit.assert_called_once()
            data['status']='success';p.write_text(json.dumps(data));self.r.rhino_db.connect.reset_mock()
            self.r.finalize_service();self.r.rhino_db.connect.assert_not_called()

    def test_main_records_timeout_and_interruption(self):
        for error,status in [(self.r.TaskTimeout('deadline'),'timed_out'),(self.r.TaskStopped(),'interrupted')]:
            with tempfile.TemporaryDirectory() as folder:
                saved=[]
                def save(payload):saved.append(copy.deepcopy(payload))
                old_term=signal.getsignal(signal.SIGTERM);old_int=signal.getsignal(signal.SIGINT)
                try:
                    with patch.object(self.r,'ROOT',Path(folder)),patch.object(self.r,'save',side_effect=save),patch.object(self.r.Tracker,'publish'),patch.object(self.r,'run_stage',side_effect=error):
                        with self.assertRaises(SystemExit):self.r.main()
                    self.assertEqual(saved[-1]['status'],status)
                    self.assertEqual(saved[-1]['media_status'],status)
                    self.assertIsNotNone(saved[-1]['finished_at'])
                finally:signal.signal(signal.SIGTERM,old_term);signal.signal(signal.SIGINT,old_int)
