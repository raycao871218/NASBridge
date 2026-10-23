import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import main, daily_task_control as control


class DailyTaskControlTest(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.db = patch.object(main, 'postgres_connection', return_value=self.connection)
        self.db.start()
        app = FastAPI()
        app.include_router(control.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.db.stop()

    def test_start_and_connection_closed(self):
        self.cursor.fetchone.side_effect = [(True,)]
        with patch.object(control, 'control_command', return_value={'accepted': True}) as remote:
            response = self.client.post('/api/daily-tasks/run', json={})
        self.assertEqual(response.status_code, 202)
        remote.assert_called_once_with('start')
        self.assertIn('pg_try_advisory_xact_lock', self.cursor.execute.call_args.args[0])
        self.connection.close.assert_called_once()

    def test_rerun_does_not_gate_on_previous_success(self):
        self.cursor.fetchone.side_effect = [(True,)]
        with patch.object(control, 'control_command', return_value={'accepted': True}) as remote:
            response = self.client.post('/api/daily-tasks/run', json={})
        self.assertEqual(response.status_code, 202)
        remote.assert_called_once_with('start')
        self.assertFalse(any('rhino_daily_runs' in call.args[0] for call in self.cursor.execute.call_args_list))

    def test_busy_request_or_running_service_blocks(self):
        for rows in [[(False,)], [(True,)]]:
            self.cursor.fetchone.side_effect = rows
            with patch.object(control, 'control_command', return_value={'accepted': False}):
                self.assertEqual(self.client.post('/api/daily-tasks/run', json={}).status_code, 409)

    def test_database_failure_never_starts_or_leaks(self):
        self.cursor.execute.side_effect = RuntimeError('secret-password')
        with patch.object(control, 'control_command') as remote:
            response = self.client.post('/api/daily-tasks/run', json={})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret-password', response.text)
        remote.assert_not_called()
        self.connection.close.assert_called_once()

    def test_cross_origin_and_extra_parameters_blocked(self):
        for headers in [{'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.client.post('/api/daily-tasks/run', json={}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/daily-tasks/run', json={'command': 'arbitrary'}).status_code, 422)
        main.postgres_connection.assert_not_called()

    def test_controls_success_running_idle_and_disconnected(self):
        for state, can_trigger in [('activating', False), ('active', False), ('deactivating', False), ('inactive', True), ('failed', True)]:
            with patch.object(control, 'control_command', return_value={'active_state': state}):
                for successes in [0, 1]:
                    status = control.manual_control_status({'today_success_runs': successes})
                    self.assertTrue(status['show'])
                    self.assertEqual(status['can_trigger'], can_trigger)
        with patch.object(control, 'control_command', side_effect=HTTPException(503, '不可用')):
            self.assertFalse(control.manual_control_status({})['can_trigger'])

    def test_retry_and_shutdown_protection_are_not_confused_with_run_success(self):
        for state, retry, expected in [
            ('inactive', {'available':True,'status':'waiting'}, True),
            ('inactive', {'available':True,'status':'retrying'}, True),
            ('inactive', {'available':True,'status':'completed','service_active':True}, True),
            ('inactive', {'available':True,'status':'completed'}, False),
            ('active', {'available':True,'status':'none'}, True),
            ('inactive', {'available':False,'status':'unknown'}, None),
            ('active', {'available':False,'status':'unknown'}, True),
        ]:
            with self.subTest(state=state,retry=retry):
                result=control.manual_control_status({'today_success_runs':1},
                    {'active_state':state,'retry':retry,'observed_at':'2026-10-24T02:30:00Z'})
                self.assertIs(result['shutdown_protected'],expected)
                self.assertEqual(result['retry'],retry)


class ForcedCommandTest(unittest.TestCase):
    def test_only_fixed_service_can_start_and_busy_service_is_not_restarted(self):
        path = Path(__file__).resolve().parents[1] / 'ops/daily-task-control.py'
        spec = importlib.util.spec_from_file_location('forced_daily_control', path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        with patch.object(helper.subprocess, 'run') as run, patch.object(helper.Path,'read_text',return_value='test-boot'), \
             patch.object(helper,'retry_status',return_value={'available':False}):
            self.assertFalse(helper.control('start; other command')['available'])
            run.assert_not_called()
            run.return_value.stdout = 'LoadState=loaded\nActiveState=activating\n'
            self.assertFalse(helper.control('start')['accepted'])
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            run.return_value.stdout = 'LoadState=loaded\nActiveState=failed\n'
            self.assertTrue(helper.control('start')['accepted'])
            self.assertEqual(run.call_args.args[0], ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'start', '--no-block', 'rhino-daily.service'])

    def test_retry_metadata_is_bounded_and_private_fields_are_not_exposed(self):
        import json
        path = Path(__file__).resolve().parents[1] / 'ops/daily-task-control.py'
        spec = importlib.util.spec_from_file_location('forced_retry_control', path)
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        with patch.object(helper.subprocess,'run') as run, patch.object(helper.Path,'stat') as stat, \
             patch.object(helper.Path,'read_text') as read:
            run.return_value.stdout='LoadState=loaded\nActiveState=inactive\n'
            stat.return_value.st_size=100
            read.return_value=json.dumps({'status':'waiting','retry_at':'2026-10-24T11:00:00+08:00','token':'secret'})
            result=helper.retry_status()
            self.assertTrue(result['available']); self.assertEqual(result['status'],'waiting')
            self.assertNotIn('token',result)
            read.side_effect=FileNotFoundError()
            self.assertEqual(helper.retry_status()['status'],'none')
            read.side_effect=None; read.return_value='invalid json'
            self.assertFalse(helper.retry_status()['available'])
            stat.return_value.st_size=5000
            read.reset_mock()
            self.assertFalse(helper.retry_status()['available']); read.assert_not_called()
