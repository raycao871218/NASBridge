"""Opt-in integration against an isolated PostgreSQL database, never production."""
import os
import io
import unittest
import zipfile
from unittest.mock import AsyncMock, patch

import psycopg2
from psycopg2.extensions import parse_dsn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import wechat


@unittest.skipUnless(os.getenv('NASBRIDGE_WECHAT_TEST_DSN'), 'isolated PostgreSQL not configured')
class WechatPostgresTest(unittest.TestCase):
    def test_archive_survives_new_connections_and_failed_reads(self):
        from app import main
        dsn = os.environ['NASBRIDGE_WECHAT_TEST_DSN']
        self.assertEqual(parse_dsn(dsn)['dbname'], 'nasbridge_wechat_test')
        app = FastAPI()
        app.include_router(wechat.router)
        client = TestClient(app)
        wechat._schema_ready = False
        try:
            with patch.object(main, 'postgres_connection', side_effect=lambda *args: psycopg2.connect(dsn)):
                url = 'https://mp.weixin.qq.com/s?__biz=test&mid=123&idx=1&sn=first'
                payload = {'title': '历史文章', 'markdown': '完整正文', 'account': '测试公众号', 'images': ['https://image.test/a']}
                with patch.object(wechat, 'upstream', new=AsyncMock(return_value=payload)):
                    response = client.post('/api/wechat/read', json={'url': url})
                self.assertEqual(response.status_code, 200)
                key = response.json()['history_id']
                self.assertEqual(client.get('/api/wechat/articles').json()['total'], 1)
                # Every endpoint opens a new PostgreSQL connection; stored JSONB must round-trip.
                reopened = client.get('/api/wechat/articles/' + key).json()
                self.assertEqual(reopened['markdown'], '完整正文')
                self.assertEqual(reopened['images'], payload['images'])
                with patch.object(wechat, 'upstream', new=AsyncMock(return_value={**payload, 'markdown': '更新正文'})):
                    client.post('/api/wechat/read', json={'url': url.replace('sn=first', 'sn=second&scene=1')})
                self.assertEqual(client.get('/api/wechat/articles').json()['total'], 1)
                self.assertEqual(client.get('/api/wechat/articles/' + key).json()['markdown'], '更新正文')
                with patch.object(wechat, 'upstream', new=AsyncMock(return_value={'title': '', 'markdown': ''})):
                    client.post('/api/wechat/read', json={'url': url})
                self.assertEqual(client.get('/api/wechat/articles/' + key).json()['markdown'], '更新正文')
                for index in range(22):
                    wechat.save_article({**payload, 'title': '额外文章 ' + str(index)}, 'https://mp.weixin.qq.com/s/item' + str(index))
                first = client.get('/api/wechat/articles?limit=20').json()
                second = client.get('/api/wechat/articles?limit=20&page=2').json()
                self.assertEqual(first['total'], 23)
                self.assertEqual(len(first['items']), 20)
                self.assertEqual(len(second['items']), 3)
                self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
                self.assertEqual(client.get('/api/wechat/articles', params={'q': '历史文章'}).json()['total'], 1)
                self.assertEqual(client.get('/api/wechat/articles', params={'q': "%' OR 1=1 --"}).json()['total'], 0)
                response = client.post('/api/wechat/export', json={'q': '历史文章'})
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
                    self.assertEqual(len(bundle.namelist()), 2)
                    self.assertIn('更新正文', bundle.read(next(name for name in bundle.namelist() if name.endswith('.md'))).decode())
                response = client.post('/api/wechat/export', json={'ids': [key]})
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
                    self.assertEqual(len(bundle.namelist()), 2)
        finally:
            wechat._schema_ready = False


if __name__ == '__main__':
    unittest.main()
