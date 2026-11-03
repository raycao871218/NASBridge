import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import wechat


class WechatTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(wechat.router)
        self.client = TestClient(app)
        self.url = 'https://mp.weixin.qq.com/s?__biz=abc%3D%3D&mid=123&idx=1&sn=xyz'

    def call(self, handler, *, health=False, images=True):
        client_type = httpx.AsyncClient
        with patch.object(wechat, 'reader_url', return_value='http://reader.test:18765'), patch.object(
                wechat.httpx, 'AsyncClient', side_effect=lambda **kwargs: client_type(
                    transport=httpx.MockTransport(handler), **kwargs)):
            if health:
                return self.client.get('/api/wechat/health')
            return self.client.post('/api/wechat/read', json={'url': self.url, 'images': images})

    def test_long_url_and_image_parameter(self):
        def handler(request):
            self.assertEqual(request.url.path, '/read')
            self.assertEqual(request.url.params['url'], self.url)
            self.assertEqual(request.url.params['format'], 'json')
            self.assertEqual(request.url.params['browser'], 'never')
            self.assertEqual(request.url.params['images'], '0')
            return httpx.Response(200, json={'title': '文章', 'markdown': '正文', 'images': ['https://image.test/a']})
        response = self.call(handler, images=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['images'], ['https://image.test/a'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_health(self):
        response = self.call(lambda r: httpx.Response(200, json={'ok': True, 'version': '0.1.0'}), health=True)
        self.assertEqual(response.json(), {'ok': True, 'version': '0.1.0'})
        self.assertEqual(self.call(lambda r: httpx.Response(200, json={'ok': False}), health=True).status_code, 502)

    def test_errors_and_malformed_responses(self):
        for code in ['verify_required', 'deleted', 'blocked', 'expired', 'invalid', 'migrated', 'unavailable', 'fetch_failed']:
            count = []
            def handler(request):
                count.append(request)
                return httpx.Response(502, json={'error': code, 'message': '原因'})
            response = self.call(handler)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()['error'], code)
            self.assertEqual(len(count), 1)  # No automatic retries, including verification pages.
        for response in [httpx.Response(200, text='<html>error</html>'), httpx.Response(200, json=[]),
                         httpx.Response(200, json={'title': None, 'markdown': []}), httpx.Response(302)]:
            self.assertEqual(self.call(lambda r: response).status_code, 502)
        # Incomplete parsing remains inspectable rather than masquerading as a transport failure.
        self.assertEqual(self.call(lambda r: httpx.Response(200, json={'title': '', 'markdown': ''})).status_code, 200)

    def test_timeout_and_connection_failure(self):
        for exception, status in [(httpx.ReadTimeout('slow'), 504), (httpx.ConnectError('offline'), 502)]:
            def handler(request):
                raise exception
            self.assertEqual(self.call(handler).status_code, status)

    def test_validation_origin_and_concurrency(self):
        for url in ['https://example.com/s/abc', 'https://mp.weixin.qq.com.evil.test/s/abc',
                    'https://user@mp.weixin.qq.com/s/abc', 'file:///etc/passwd',
                    'https://mp.weixin.qq.com:9999/s/a', 'https://mp.weixin.qq.com/sneaky',
                    'https://mp.weixin.qq.com/s/a\\b']:
            self.assertEqual(self.client.post('/api/wechat/read', json={'url': url}).status_code, 422)
        for headers in [{'Origin': 'https://evil.test'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.client.post('/api/wechat/read', json={'url': self.url}, headers=headers).status_code, 403)
        asyncio.run(wechat._read_lock.acquire())
        try:
            self.assertEqual(self.client.post('/api/wechat/read', json={'url': self.url}).status_code, 409)
        finally:
            wechat._read_lock.release()


if __name__ == '__main__':
    unittest.main()
