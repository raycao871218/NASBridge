import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import home, home_preview


class PreviewCacheTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.patch = patch.object(home_preview, 'cache_directory', return_value=self.folder)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        home_preview._capture_lock = asyncio.Lock()

    async def test_cache_deduplicates_and_caches_full_url(self):
        calls = []
        async def capture(url, output):
            calls.append(url)
            await asyncio.sleep(.01)
            output.write_bytes(b'jpeg fixture')
        with patch.object(home_preview, 'capture', side_effect=capture):
            paths = await asyncio.gather(*(home_preview.screenshot('https://photos.example/a') for _ in range(3)))
            self.assertEqual(len(calls), 1)
            self.assertEqual(paths[0].read_bytes(), b'jpeg fixture')
            self.assertNotEqual(await home_preview.screenshot('https://photos.example/b'), paths[0])
            self.assertEqual(len(calls), 2)
            self.assertFalse(list(self.folder.glob('*.pending.jpg')))

    async def test_refresh_failure_preserves_last_success(self):
        async def capture(url, output):
            output.write_bytes(b'old screenshot')
        with patch.object(home_preview, 'capture', side_effect=capture):
            path = await home_preview.screenshot('https://photos.example/a')
        os.utime(path, (time.time() - 30000, time.time() - 30000))
        with patch.object(home_preview, 'capture', side_effect=RuntimeError('browser failed')):
            self.assertEqual(await home_preview.screenshot('https://photos.example/a'), path)
            with self.assertRaises(HTTPException) as raised:
                await home_preview.screenshot('https://photos.example/unknown')
            self.assertEqual(raised.exception.status_code, 503)
        self.assertFalse(list(self.folder.glob('*.pending.jpg')))


class PreviewRouteTest(unittest.TestCase):
    def test_only_saved_slots_are_captured(self):
        app = FastAPI()
        app.include_router(home.router)
        client = TestClient(app)
        state = {'revision': 0, 'sites': [{'name': 'Photos', 'url': 'https://photos.example'}]+[None]*5}
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'preview.jpg'
            image.write_bytes(b'jpeg fixture')
            with patch.object(home, 'load_state', return_value=state), patch.object(home_preview, 'screenshot', return_value=image) as capture:
                response = client.get('/api/home/sites/0/preview')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['content-type'], 'image/jpeg')
                capture.assert_called_once_with('https://photos.example')
                for slot in [-1, 1, 6]:
                    self.assertEqual(client.get(f'/api/home/sites/{slot}/preview').status_code, 404)
                self.assertEqual(capture.call_count, 1)
