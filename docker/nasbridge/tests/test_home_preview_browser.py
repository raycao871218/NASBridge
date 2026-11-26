"""Optional real Chromium check; uses only a loopback fixture, no saved websites."""
import asyncio
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import home_preview


@unittest.skipUnless(os.getenv('NASBRIDGE_BROWSER_TESTS') == '1', 'set NASBRIDGE_BROWSER_TESTS=1 to run Chromium')
class RealBrowserTest(unittest.TestCase):
    def test_first_screen_capture_and_no_post(self):
        writes = []
        class Fixture(BaseHTTPRequestHandler):
            def do_GET(self):
                body = ('<!doctype html><meta charset="utf-8"><body style="background:#bacceb;font:40px sans-serif;padding:60px">'
                        '<h1>网页首屏 · 真实截图</h1><p>截图测试页面</p><script>fetch("/write",{method:"POST"}).catch(()=>{});</script>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                writes.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(home_preview, 'browser_proxy', return_value=None):
                image = Path(folder) / 'page.jpg'
                asyncio.run(home_preview.capture(f'http://127.0.0.1:{server.server_port}', image))
                data = image.read_bytes()
                self.assertTrue(data.startswith(b'\xff\xd8'))
                self.assertGreater(len(data), 5000)
                self.assertEqual(writes, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
