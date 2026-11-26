import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.daily_task_progress import display_run
from test_daily_runner import load_runner


class SkillUpdateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runner = load_runner()
        self.runner.ROOT = self.root
        self.skill = self.root / '技能/rhino-finance-lens'
        (self.skill / 'references').mkdir(parents=True)
        (self.skill / 'SKILL.md').write_text('framework', encoding='utf-8')
        (self.skill / 'references/observations.md').write_text('old observations', encoding='utf-8')

    def tracker(self, state='success'):
        return types.SimpleNamespace(data={}, payload={}, stage=lambda _: {'status': state})

    def test_new_observation_is_separate_from_unchanged_framework(self):
        before = self.runner.skill_snapshot()
        (self.skill / 'references/observations.md').write_text('new observations', encoding='utf-8')
        tracker = self.tracker()
        tracker.data['skill_reason'] = '新增证据支持已有方法，没有形成新的重复规律'
        self.runner.record_skill_update(before, tracker)
        self.assertEqual(tracker.data['skill_update']['observations_status'], 'updated')
        self.assertEqual(tracker.data['skill_update']['framework_status'], 'unchanged')
        self.assertEqual(tracker.data['skill_update']['reason'], tracker.data['skill_reason'])
        self.assertFalse(tracker.payload['skill_updated'])

    def test_framework_update_preserves_legacy_flag(self):
        before = self.runner.skill_snapshot()
        (self.skill / 'SKILL.md').write_text('new framework', encoding='utf-8')
        tracker = self.tracker()
        self.runner.record_skill_update(before, tracker)
        self.assertEqual(tracker.data['skill_update']['framework_status'], 'updated')
        self.assertTrue(tracker.payload['skill_updated'])

    def test_skipped_failed_and_missing_files_do_not_claim_review(self):
        for stage, review in [('skipped', 'skipped'), ('failed', 'incomplete'), ('pending', 'incomplete')]:
            with self.subTest(stage=stage):
                tracker = self.tracker(stage)
                self.runner.record_skill_update(self.runner.skill_snapshot(), tracker)
                self.assertEqual(tracker.data['skill_update']['review_status'], review)
        (self.skill / 'SKILL.md').unlink()
        tracker = self.tracker()
        self.runner.record_skill_update(self.runner.skill_snapshot(), tracker)
        self.assertEqual(tracker.data['skill_update']['framework_status'], 'unrecorded')

    def test_api_exposes_stored_detail_without_inventing_history(self):
        detail = {'observations_status': 'updated', 'framework_status': 'unchanged', 'reason': 'evidence'}
        row = dict(run_id='run', status='success', skill_updated=False, progress={'skill_update': detail})
        self.assertEqual(display_run(row, {})['skill_update'], detail)
        row['progress'] = None
        self.assertIsNone(display_run(row, {})['skill_update'])

    def test_tracker_persists_bounded_reason_from_child_event(self):
        with patch.object(self.runner, 'boot_id', return_value='test'), patch.object(self.runner.Tracker, 'publish'):
            tracker = self.runner.Tracker(dict(run_id='run', started_at=self.runner.stamp()))
            tracker.event(dict(stage='summary_skill', state='success', skill_reason='reason\n' * 200))
        self.assertLessEqual(len(tracker.data['skill_reason']), 500)
        self.assertNotIn('\n', tracker.data['skill_reason'])

    def test_completed_runner_publishes_observation_detail_with_final_state(self):
        trackers = []
        def run_stage(name, tracker):
            if name == 'rhino-postprocess.py':
                trackers.append(tracker)
                (self.skill/'references/observations.md').write_text('new evidence', encoding='utf-8')
                tracker.event(dict(stage='summary_skill', state='success', skill_reason='证据支持现有框架'))
                tracker.event(dict(stage='sync', state='success'))
            else:
                tracker.event(dict(stage='check', state='success'))
            return 0
        with patch.object(self.runner, 'boot_id', return_value='test'), \
             patch.object(self.runner.signal, 'signal'), patch.object(self.runner, 'save') as save, \
             patch.object(self.runner.Tracker, 'publish'), patch.object(self.runner, 'run_stage', side_effect=run_stage):
            self.runner.main()
        self.assertEqual(trackers[0].data['status'], 'success')
        self.assertEqual(trackers[0].data['skill_update']['observations_status'], 'updated')
        self.assertEqual(trackers[0].data['skill_update']['reason'], '证据支持现有框架')
        self.assertFalse(save.call_args.args[0]['skill_updated'])


class PostprocessReviewTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'ops/runner'
        with patch.object(sys, 'path', [str(source)] + sys.path):
            spec = importlib.util.spec_from_file_location('tested_postprocess_review', source / 'rhino-postprocess.py')
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)

    def test_reason_validation_and_missing_report_are_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'report.json'
            for value in (None, [], {'reason': 10}, {'reason': ''}):
                report.write_text(json.dumps(value), encoding='utf-8')
                self.assertIn('未提供有效', self.module.read_review_reason(report))
            report.write_text(json.dumps({'reason': '具体证据\n与框架判断'}), encoding='utf-8')
            self.assertEqual(self.module.read_review_reason(report), '具体证据 与框架判断')
            report.unlink()
            self.assertIn('未提供有效', self.module.read_review_reason(report))

    def test_new_summary_records_reason_and_removes_temporary_report(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            text, summary, skill = root/'原文', root/'概要', root/'local-skill'
            for path in (text, summary, skill): path.mkdir()
            (text/'today.txt').write_text('transcript')
            (skill/'SKILL.md').write_text('framework')
            report = root/'.rhino-skill-review-test.json'

            def generate(command, **kwargs):
                self.assertIn(str(report), command[-1])
                (summary/'today.md').write_text('summary')
                report.write_text(json.dumps({'reason': '新材料延续原有方法，暂不修改主框架'}), encoding='utf-8')

            with patch.multiple(self.module, ROOT=root, TEXT=text, SUMMARY=summary, SKILL=skill, MIRROR=root/'mirror'), \
                 patch.object(self.module.os.path, 'ismount', return_value=True), \
                 patch.object(self.module.uuid, 'uuid4', return_value=types.SimpleNamespace(hex='test')), \
                 patch.object(self.module.subprocess, 'run', side_effect=generate), \
                 patch.object(self.module, 'event') as event:
                self.module.main()
            self.assertFalse(report.exists())
            self.assertTrue((root/'mirror/SKILL.md').is_file())
            self.assertTrue(any(call.kwargs.get('skill_reason') == '新材料延续原有方法，暂不修改主框架'
                                for call in event.call_args_list))

    def test_no_new_transcripts_skips_review_without_running_ai(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.multiple(self.module, TEXT=Path(folder), SUMMARY=Path(folder)), \
             patch.object(self.module.os.path, 'ismount', return_value=True), \
             patch.object(self.module.subprocess, 'run') as run, patch.object(self.module, 'event') as event:
            self.module.main()
            run.assert_not_called()
            self.assertIn('未进行框架复核', event.call_args_list[1].kwargs['skill_reason'])
