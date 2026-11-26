"""Historical discovery and one-shot handoff without touching production media."""
from datetime import date
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


OPS = Path(__file__).resolve().parents[1] / 'ops'


def load(name, path):
    with patch.object(sys, 'path', [str(OPS / 'runner')] + sys.path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.media = load('backfill_media_test', OPS / 'runner/rhino-vm.py')
        self.target = date(2026, 10, 20)

    def test_exact_date_metadata_and_missing_date_fail_closed(self):
        listing = subprocess.CompletedProcess([], 0, '\n'.join([
            'newer000001\tpublic', 'target00001\tpublic', 'target00002\tsubscriber_only',
            'older000001\tpublic', 'unread00001\tpublic',
        ]))
        indexed = {'newer000001': (date(2026, 10, 21), None), 'older000001': (date(2026, 10, 19), None)}
        with patch.object(self.media.subprocess, 'run') as run:
            run.side_effect = [listing, subprocess.CompletedProcess([], 0, '20261020\tpublic')]
            self.assertEqual(self.media.historical_entries(self.target, indexed), ['target00001'])
            self.assertEqual(run.call_count, 2)
            self.assertIn('--flat-playlist', run.call_args_list[0].args[0])
            run.side_effect = [listing, subprocess.CompletedProcess([], 0, 'NA\tpublic')]
            with self.assertRaises(RuntimeError):
                self.media.historical_entries(self.target, indexed)

    def test_backfill_keeps_existing_video_and_recovers_missing_archived_video(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / '视频'
            videos.mkdir()
            (videos / '20261020-present.mp4').touch()
            (videos / '.rhino-finance-archive.txt').write_text('youtube present0001\nyoutube missing0001\n')
            (root / '.rhino-finance-video-index.tsv').write_text(
                'present0001\t20261020-present.mp4\nmissing0001\t20261020-missing.mp4\n')
            with patch.object(self.media, 'ROOT', root), patch.object(self.media, 'event') as event, \
                 patch.object(self.media, 'historical_entries', return_value=['present0001', 'missing0001']), \
                 patch.object(self.media.subprocess, 'run') as run:
                self.media.download(self.target)
            run.assert_called_once()
            self.assertIn('--no-download-archive', run.call_args.args[0])
            self.assertTrue(run.call_args.args[0][-1].endswith('missing0001'))
            self.assertEqual(event.call_args_list[1].kwargs, {'matched_count': 2, 'pending_count': 1})

    @unittest.skipUnless(os.name == 'posix', 'executor uses flock')
    def test_fixed_service_handoff_consumes_date_once_and_clears_failed_start(self):
        controller = load('backfill_controller_test', OPS / 'daily-task-control.py')
        handoff = sys.modules['rhino_backfill']
        with tempfile.TemporaryDirectory() as temp, patch.object(handoff, 'STATE', Path(temp)), \
             patch.object(controller, 'RUNNER', OPS / 'runner'), \
             patch.object(handoff, 'parse_target', return_value=self.target), \
             patch.object(controller, 'retry_status', return_value={}), \
             patch.object(controller.subprocess, 'run') as run:
            run.return_value.stdout = 'LoadState=loaded\nActiveState=inactive\n'
            result = controller.control('start 2026-10-20')
            self.assertTrue(result['accepted'])
            self.assertEqual(run.call_args.args[0][-1], controller.UNIT)
            self.assertEqual(handoff.take_target(), self.target)
            self.assertIsNone(handoff.take_target())
            run.side_effect = [run.return_value, subprocess.CalledProcessError(1, 'systemctl')]
            with self.assertRaises(subprocess.CalledProcessError):
                controller.control('start 2026-10-20')
            self.assertIsNone(handoff.take_target())
