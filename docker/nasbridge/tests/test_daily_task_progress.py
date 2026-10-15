from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import MagicMock
from app.daily_task_progress import classify_run, reconcile, display_run

NOW=datetime(2026,10,15,3,0,tzinfo=timezone.utc)


def running():
    return dict(run_id='run', status='running', started_at=NOW-timedelta(minutes=10),
                progress=dict(boot_id='boot',invocation_id='invocation',heartbeat_at=NOW.isoformat(),
                              stages=[dict(id='download',status='running'),dict(id='transcribe',status='pending')]))


def service(**values):
    return dict(dict(available=True,boot_id='boot',invocation_id='invocation',active_state='activating',main_pid=100),**values)


class ProgressTests(unittest.TestCase):
    def test_healthy_and_stale_heartbeat(self):
        row=running()
        self.assertEqual(classify_run(row,service(),NOW)[0],'running')
        row['progress']['heartbeat_at']=(NOW-timedelta(seconds=91)).isoformat()
        status,_,terminal=classify_run(row,service(),NOW)
        self.assertEqual(status,'unresponsive');self.assertFalse(terminal)

    def test_unreachable_never_claims_alive_or_dead(self):
        status,_,terminal=classify_run(running(),{'available':False},NOW)
        self.assertEqual(status,'unknown');self.assertFalse(terminal)

    def test_new_run_after_service_snapshot_is_never_marked_interrupted(self):
        row=running();row['started_at']=NOW
        status,_,terminal=classify_run(row,service(active_state='inactive',main_pid=0,observed_at=(NOW-timedelta(seconds=1)).isoformat()),NOW)
        self.assertEqual(status,'unknown');self.assertFalse(terminal)

    def test_reboot_replacement_exit_and_systemd_timeout(self):
        for executor,status in [(service(boot_id='new'),'interrupted'),
                                (service(invocation_id='new'),'interrupted'),
                                (service(active_state='inactive',main_pid=0),'interrupted'),
                                (service(active_state='failed',main_pid=0,result='timeout'),'timed_out')]:
            actual,_,terminal=classify_run(running(),executor,NOW)
            self.assertEqual(actual,status);self.assertTrue(terminal)

    def test_legacy_older_run_is_not_confused_with_new_active_run(self):
        row=running();row['progress']=None
        self.assertEqual(classify_run(row,service(started_at=NOW.isoformat()),NOW)[0],'interrupted')
        self.assertEqual(classify_run(row,service(),NOW)[0],'unknown')

    def test_recover_local_terminal_state_and_never_overwrite_finished_row(self):
        s=service(progress={'run_id':'run','status':'timed_out','message':'deadline'})
        self.assertEqual(classify_run(running(),s,NOW),('timed_out','deadline',True))
        row=running();row['status']='success'
        self.assertEqual(classify_run(row,service(active_state='inactive'),NOW),('success','',False))

    def test_repairs_all_running_and_uses_guarded_update(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[running()];cursor.fetchone.return_value=('run',)
        reconcile(connection,service(active_state='failed',main_pid=0,result='timeout'))
        calls=cursor.execute.call_args_list
        self.assertNotIn('LIMIT',calls[0].args[0])
        self.assertIn("AND status='running' RETURNING",calls[1].args[0])
        progress=json.loads(calls[2].args[1][0])
        self.assertEqual(progress['stages'][0]['status'],'timed_out')
        self.assertEqual(progress['stages'][1]['status'],'skipped')
        connection.commit.assert_called_once()

    def test_concurrent_success_does_not_overwrite_progress(self):
        connection=MagicMock();cursor=connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[running()];cursor.fetchone.return_value=None
        reconcile(connection,service(active_state='inactive',main_pid=0))
        self.assertEqual(cursor.execute.call_count,2)

    def test_display_can_recover_without_mutating_running_record(self):
        row=running()
        result=display_run(row,{'available':False})
        self.assertEqual(result['status'],'unknown');self.assertEqual(row['status'],'running')
