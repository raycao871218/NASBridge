import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main


class ContainerOriginTests(unittest.TestCase):
    def test_cross_site_forms_cannot_control_container(self):
        client = TestClient(main.app)
        with patch.object(main, "control_mihomo_container", new_callable=AsyncMock) as control:
            for headers in (
                {"Origin": "https://external.example"},
                {"Sec-Fetch-Site": "cross-site"},
                {"Origin": "null"},
            ):
                response = client.post("/api/mihomo/container/stop", headers=headers)
                self.assertEqual(response.status_code, 403)
            control.assert_not_awaited()

    def test_same_origin_and_native_clients_still_work(self):
        client = TestClient(main.app)
        with patch.object(main, "control_mihomo_container", new_callable=AsyncMock,
                          return_value={"status": "ok"}) as control:
            for headers in ({"Origin": "http://testserver"}, {}):
                self.assertEqual(client.post("/api/mihomo/container/restart", headers=headers).status_code, 200)
            self.assertEqual(control.await_count, 2)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistence_outage_does_not_stop_scheduler(self):
        iterations = 0

        async def next_cycle(_):
            nonlocal iterations
            iterations += 1
            if iterations == 2:
                raise asyncio.CancelledError()

        with patch.object(main, "load_schedule", side_effect=lambda: {}), \
             patch.object(main, "scheduled_slot", return_value="slot"), \
             patch.object(main, "sync_all_configured_certificates", new_callable=AsyncMock, return_value=[]), \
             patch.object(main, "save_schedule", side_effect=OSError("offline")) as save, \
             patch.object(main.asyncio, "sleep", side_effect=next_cycle), \
             patch.object(main.logging, "exception"):
            with self.assertRaises(asyncio.CancelledError):
                await main.certificate_scheduler()
            self.assertEqual(iterations, 2)
            self.assertEqual(save.call_count, 4)


class OverviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_certificate_inspection_leaves_event_loop_responsive(self):
        started, release = threading.Event(), threading.Event()

        def inspect(_):
            started.set()
            release.wait(2)
            return {"kind": "remote"}

        with patch.object(main, "certificate_inspection_slots", asyncio.Semaphore(4)), \
             patch.object(main, "load_config", return_value={"certificates": {"remote": [{}]}}), \
             patch.object(main, "build_mihomo_snapshot", new_callable=AsyncMock, return_value={}), \
             patch.object(main, "inspect_remote_cert", side_effect=inspect), \
             patch.object(main, "schedule_state", return_value={}):
            task = asyncio.create_task(main.build_overview())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                self.assertFalse(task.done(), "inspection must still be waiting while the event loop responds")
            finally:
                release.set()
                await task


class MihomoConfigTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.yaml"
        self.path.write_text("external-controller: original\n", encoding="utf-8")
        for name, value in (
            ("DATA_DIR", Path(self.directory.name)),
            ("mihomo_config_path", Mock(return_value=self.path)),
            ("load_config", Mock(return_value={})),
            ("mihomo_config_lock", asyncio.Lock()),
        ):
            replacement = patch.object(main, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)

    async def test_same_second_backups_preserve_both_versions(self):
        with patch.object(main, "utc_now", return_value=datetime(2026, 11, 3, tzinfo=timezone.utc)):
            first = main.write_mihomo_config_file(self.path, "change A")
            second = main.write_mihomo_config_file(self.path, "change B")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), "external-controller: original\n")
        self.assertEqual(Path(second).read_text(), "change A")

    async def test_stale_editor_does_not_write_or_reload(self):
        with patch.object(main, "mihomo_reload", new_callable=AsyncMock) as reload:
            with self.assertRaises(HTTPException) as error:
                await main.update_mihomo_config({"content": "external-controller: new", "expected_revision": "stale"})
            self.assertEqual(error.exception.status_code, 409)
            reload.assert_not_awaited()
        self.assertEqual(self.path.read_text(), "external-controller: original\n")

    async def test_failed_reload_rolls_back_before_waiting_writer_proceeds(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def reload(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                raise RuntimeError("reload failed")

        original = main.read_mihomo_config_file(self.path)["revision"]
        with patch.object(main, "mihomo_reload", side_effect=reload):
            first = asyncio.create_task(main.update_mihomo_config({"content": "external-controller: A"}))
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(main.update_mihomo_config({"content": "external-controller: B", "expected_revision": original}))
            try:
                await asyncio.sleep(.02)
                self.assertFalse(second.done())
                self.assertEqual(calls, 1)
            finally:
                release.set()
                results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], HTTPException)
        self.assertEqual(results[1]["status"], "applied")
        self.assertEqual(self.path.read_text(), "external-controller: B")
        self.assertEqual(results[1]["revision"], hashlib.sha256(b"external-controller: B").hexdigest())


class CertificateScriptTests(unittest.TestCase):
    def test_failed_issue_or_deploy_never_advances_success_marker(self):
        script = Path(__file__).resolve().parents[3] / "src" / "nas_cert_update.sh"
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        if not script.exists() or not bash or not Path(bash).exists():
            self.skipTest("requires repository certificate script and Bash")
        harness = '''
docker() {
    printf '%s\\n' "$*" >> "$CALLS"
    if [[ "$1" == ps ]]; then echo acme-test; return 0; fi
    if [[ "$*" == *--issue* && "$FAIL_STAGE" == issue ]]; then return 11; fi
    if [[ "$*" == *--deploy* && "$FAIL_STAGE" == deploy ]]; then return 12; fi
    return 0
}
export -f docker
bash "$SCRIPT"
'''
        for stage in ("issue", "deploy", "success"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / ".env").write_text('DOMAIN_NAME="example.com" # inline comment\nCERT_DNS_TYPE=dns_test\nCERT_DNS_SLEEP=1\nCERT_SERVER=test\nCERT_DOCKER_CONTAINER=acme-test\n')
                marker = root / "log" / "last_cert_issue_at"
                marker.parent.mkdir()
                marker.write_text("old-marker")
                calls = root / "calls"
                env = dict(os.environ, SCRIPT=script.as_posix(), FAIL_STAGE=stage, CALLS=calls.as_posix())
                result = subprocess.run([bash, "-c", harness], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
                if stage == "success":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(marker.read_text().strip().isdigit())
                else:
                    self.assertEqual(result.returncode, 11 if stage == "issue" else 12, result.stderr)
                    self.assertEqual(marker.read_text(), "old-marker")
                if stage == "issue":
                    self.assertNotIn("--deploy", calls.read_text())
                self.assertIn("-d example.com -d *.example.com", calls.read_text())
