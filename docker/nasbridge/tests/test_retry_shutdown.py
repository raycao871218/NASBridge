import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, Mock

OPS = Path(__file__).resolve().parents[1] / 'ops'
sys.path.insert(0, str(OPS / 'runner'))
import rhino_schedule as schedule


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RetryTests(unittest.TestCase):
    def test_zero_updates_only_at_ten_schedules_one_retry_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'retry.json'
            schedule.record_discovery('2026-10-23T02:00:00+00:00', 0, p)
            state = schedule.read_state(p)
            self.assertEqual(state['status'], 'waiting')
            self.assertFalse(schedule.retry_due(state, datetime.fromisoformat('2026-10-23T10:59:59+08:00')))
            self.assertTrue(schedule.retry_due(state, datetime.fromisoformat('2026-10-23T11:00:00+08:00')))
            self.assertTrue(schedule.shutdown_hold(state, datetime.now(timezone.utc)))
            # A reboot does not erase an overdue pending retry.
            self.assertTrue(schedule.retry_due(state, datetime.fromisoformat('2026-10-24T09:00:00+08:00')))
            schedule.write_state(dict(state, status='completed', attempted_at='2026-10-23T11:00:00+08:00'), p)
            schedule.record_discovery('2026-10-23T02:30:00+00:00', 0, p)
            self.assertEqual(schedule.read_state(p)['status'], 'completed')
            schedule.record_discovery('2026-10-24T02:00:00+00:00', 0, p)
            self.assertEqual(schedule.read_state(p)['status'], 'waiting')

    def test_found_updates_cancels_waiting_and_eleven_does_not_schedule_again(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'retry.json'
            schedule.record_discovery('2026-10-23T03:00:00+00:00', 0, p)
            self.assertFalse(p.exists())
            schedule.record_discovery('2026-10-23T02:00:00+00:00', 0, p)
            schedule.record_discovery('2026-10-23T02:20:00+00:00', 1, p)
            self.assertEqual(schedule.read_state(p)['status'], 'cancelled')
            self.assertFalse(schedule.shutdown_hold(schedule.read_state(p), datetime.now(timezone.utc)))

    @unittest.skipUnless(os.name == 'posix', 'Linux retry locking required')
    def test_real_state_allows_only_one_attempt_even_if_retry_fails(self):
        retry = load('retry_once_test', OPS / 'runner/rhino-retry.py')
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'retry.json'
            schedule.write_state(dict(date='2020-01-01', status='waiting', retry_at='2020-01-01T11:00:00+08:00'), p)
            with patch.object(retry, 'RETRY_FILE', p), \
                 patch.object(retry, 'daily_busy', return_value=False), \
                 patch.object(retry, 'read_state', side_effect=lambda: schedule.read_state(p)), \
                 patch.object(retry, 'write_state', side_effect=lambda value: schedule.write_state(value, p)), \
                 patch.object(retry.subprocess, 'run', return_value=Mock(returncode=1)) as start:
                with self.assertRaises(SystemExit): retry.main()
                retry.main()
                start.assert_called_once()
                self.assertTrue(schedule.read_state(p)['attempted_at'])
                self.assertEqual(schedule.read_state(p)['status'], 'completed')

    @unittest.skipUnless(os.name == 'posix', 'Linux retry locking required')
    def test_retry_waits_for_first_service_and_claims_before_start(self):
        retry = load('retry_test', OPS / 'runner/rhino-retry.py')
        with tempfile.TemporaryDirectory() as directory:
            pending = dict(date='2020-01-01', status='waiting', retry_at='2020-01-01T11:00:00+08:00')
            writes = []
            def start(*args, **kwargs):
                self.assertEqual(writes[-1]['status'], 'retrying')
                self.assertIn('attempted_at', writes[-1])
                return Mock(returncode=0)
            with patch.object(retry, 'RETRY_FILE', Path(directory) / 'retry.json'), \
                 patch.object(retry, 'daily_busy', side_effect=[True, False]) as busy, \
                 patch.object(retry.time, 'sleep') as sleep, \
                 patch.object(retry, 'read_state', return_value=pending), \
                 patch.object(retry, 'write_state', side_effect=lambda value: writes.append(value)), \
                 patch.object(retry.subprocess, 'run', side_effect=start):
                retry.main()
                self.assertEqual(busy.call_count, 2)
                sleep.assert_called_once_with(5)
                self.assertEqual(writes[-1]['status'], 'completed')


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.m = load('shutdown_test', OPS / 'shutdown/shutdown-readiness-monitor.py')

    def test_all_busy_states_including_stopping_and_retry_protect(self):
        for state in ('active', 'activating', 'reloading', 'deactivating'):
            with self.subTest(state=state), patch.object(self.m.subprocess, 'run', return_value=Mock(returncode=0, stdout=state)):
                self.assertIn('rhino-retry.service', self.m.active_work_services())

    def test_pending_and_corrupt_state_fail_closed(self):
        for value in ({'date':'2026-10-23', 'status':'waiting'}, {'status':'retrying'}, [], {'status':'bad'}):
            with self.subTest(value=value), patch.object(self.m, 'active_work_services', return_value=[]), \
                 patch.object(self.m.Path, 'read_text', return_value=json.dumps(value)):
                self.assertTrue(self.m.work_protection())
        with patch.object(self.m, 'active_work_services', side_effect=RuntimeError()):
            self.assertEqual(self.m.work_protection(), ['work_state_unavailable'])

    def test_protected_run_never_samples_or_calls_webhook(self):
        with patch.object(self.m, 'work_protection', return_value=['rhino_retry_waiting']), \
             patch.object(self.m, 'protected_exit') as skip, \
             patch.object(self.m, 'cpu_counters') as cpu, \
             patch.object(self.m, 'request_safe_shutdown') as send:
            self.m.main()
            skip.assert_called_once()
            cpu.assert_not_called()
            send.assert_not_called()

    def test_final_check_blocks_new_job_started_during_sampling(self):
        with patch.object(self.m, 'work_protection', side_effect=[[], ['active_work_service:rhino-daily.service']]), \
             patch.object(self.m, 'inside_detection_window', return_value=True), \
             patch.object(self.m, 'logged_in_users', return_value=[]), \
             patch.object(self.m, 'protected_processes', return_value=[]), \
             patch.object(self.m, 'codex_activity', return_value={'recently_active':False,'active_commands':[]}), \
             patch.object(self.m, 'docker_containers', return_value=[]), \
             patch.object(self.m, 'active_work_services', return_value=[]), \
             patch.object(self.m, 'cpu_counters', side_effect=[(100,100),(200,200)]), \
             patch.object(self.m, 'network_bytes', return_value=0), \
             patch.object(self.m, 'disk_sectors', return_value=0), \
             patch.object(self.m.time, 'sleep'), \
             patch.object(self.m.time, 'time', return_value=4000), \
             patch.object(self.m, 'load_state', return_value={'updated_at':3999,'idle_since':1}), \
             patch.object(self.m, 'protected_exit') as skip, \
             patch.object(self.m, 'request_safe_shutdown') as send:
            self.m.main()
            skip.assert_called_once()
            send.assert_not_called()
