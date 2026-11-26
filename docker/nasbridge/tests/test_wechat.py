import asyncio
import io
import json
import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import psycopg2
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import wechat


class WechatTest(unittest.TestCase):
    def setUp(self):
        writer = patch.object(wechat, 'save_article', side_effect=lambda payload, url: {
            **payload, 'url': url, 'history_id': wechat.article_id(url)})
        writer.start()
        self.addCleanup(writer.stop)
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
        self.assertEqual(response.json()['history_id'], wechat.article_id(self.url))

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

    def test_failed_save_keeps_downloadable_result(self):
        with patch.object(wechat, 'save_article', side_effect=psycopg2.OperationalError('offline')):
            response = self.call(lambda r: httpx.Response(200, json={'title': '文章', 'markdown': '正文'}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('保存失败', response.json()['history_error'])
        self.assertEqual(response.json()['markdown'], '正文')


class WechatHistoryTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(wechat.router)
        self.client = TestClient(app)
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        fixture = patch.object(wechat, 'history_connection', return_value=self.connection)
        fixture.start()
        self.addCleanup(fixture.stop)

    def test_upsert_and_identity(self):
        first = 'https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&sn=token&scene=3'
        second = 'https://mp.weixin.qq.com/s?idx=1&mid=1&__biz=abc&sn=other'
        self.assertEqual(wechat.article_id(first), wechat.article_id(second))
        self.assertEqual(wechat.article_id('http://mp.weixin.qq.com/s/abc?scene=1'), wechat.article_id('https://mp.weixin.qq.com/s/abc'))
        self.assertNotEqual(wechat.article_id(first), wechat.article_id(second.replace('idx=1', 'idx=2')))
        result = wechat.save_article({'title': '标题', 'markdown': '正文', 'account': '公众号'}, first)
        sql, params = self.cursor.execute.call_args.args
        self.assertIn('ON CONFLICT(id) DO UPDATE', sql)
        self.assertIn('public.wechat_articles', sql)
        self.assertEqual(params[0], result['history_id'])
        self.assertEqual(params[2], '标题')
        self.assertIn('正文', params[-1])
        self.connection.close.assert_called_once()

    def test_list_search_and_pagination(self):
        now = datetime.now(timezone.utc)
        self.cursor.fetchone.return_value = {'total': 25}
        self.cursor.fetchall.return_value = [{'id': 'a' * 64, 'title': '文章', 'fetched_at': now}]
        response = self.client.get('/api/wechat/articles', params={'q': "%' OR 1=1 --", 'page': 2, 'limit': 20})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total'], 25)
        self.assertEqual(response.json()['items'][0]['fetched_at'], now.isoformat())
        sql, params = self.cursor.execute.call_args.args
        self.assertNotIn("OR 1=1", sql)
        self.assertEqual(params, ("%' or 1=1 --", 20, 20))
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/api/wechat/articles?page=0').status_code, 422)
        self.assertEqual(self.client.get('/api/wechat/articles?limit=101').status_code, 422)

    def test_reopen_missing_and_unavailable(self):
        now = datetime.now(timezone.utc)
        self.cursor.fetchone.return_value = {'payload': {'title': '标题', 'markdown': '持久正文'}, 'fetched_at': now}
        response = self.client.get('/api/wechat/articles/' + 'a' * 64)
        self.assertEqual(response.json()['markdown'], '持久正文')
        self.cursor.fetchone.return_value = None
        self.assertEqual(self.client.get('/api/wechat/articles/' + 'b' * 64).status_code, 404)
        self.assertEqual(self.client.get('/api/wechat/articles/invalid').status_code, 404)
        self.connection.cursor.side_effect = psycopg2.OperationalError('secret database details')
        for path in ['/api/wechat/articles', '/api/wechat/articles/' + 'a' * 64]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('secret', response.text)

    def test_export_zip_search_and_selection(self):
        now = datetime.now(timezone.utc)
        self.cursor.__iter__.side_effect = lambda: iter([
            {'id': key * 64, 'payload': {'title': '../同名文章', 'account': '公众号', 'markdown': '正文', 'url': 'https://mp.weixin.qq.com/s/a'}, 'fetched_at': now}
            for key in ['a', 'b']])
        response = self.client.post('/api/wechat/export', json={'q': "%' OR 1=1 --"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            self.assertEqual(len(bundle.namelist()), 4)
            for name in bundle.namelist():
                self.assertNotIn('/', name)
                self.assertNotIn('\\', name)
                if name.endswith('.md'):
                    self.assertIn('公众号：公众号', bundle.read(name).decode())
                else:
                    self.assertEqual(json.loads(bundle.read(name))['markdown'], '正文')
        sql, params = self.cursor.execute.call_args.args
        self.assertNotIn('OR 1=1', sql)
        self.assertEqual(params, ("%' or 1=1 --",))
        response = self.client.post('/api/wechat/export', json={'ids': ['a' * 64]})
        self.assertEqual(response.status_code, 200)
        self.assertIn('ANY(%s)', self.cursor.execute.call_args.args[0])
        self.assertEqual(self.cursor.execute.call_args.args[1], (['a' * 64],))

    def test_export_empty_invalid_and_cross_origin(self):
        self.cursor.__iter__.return_value = iter([])
        self.assertEqual(self.client.post('/api/wechat/export', json={}).status_code, 404)
        for body in [{'ids': []}, {'ids': ['../bad']}, {'q': 'x' * 201}]:
            self.assertEqual(self.client.post('/api/wechat/export', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/wechat/export', json={}, headers={'Origin': 'https://evil.test'}).status_code, 403)
        self.connection.cursor.side_effect = psycopg2.OperationalError('secret')
        response = self.client.post('/api/wechat/export', json={})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.text)


if __name__ == '__main__':
    unittest.main()
