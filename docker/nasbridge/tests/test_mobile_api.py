import importlib
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
from fastapi import HTTPException
from app import mobile_api


class MobileApiTest(unittest.IsolatedAsyncioTestCase):
    def test_port_validation(self):
        for port in (0, -1, 65536, True, "7890", None):
            with self.assertRaises(ValueError):
                mobile_api.proxy_address("http://192.0.2.10:9090", {"mixed-port": port})
        proxy = mobile_api.proxy_address("http://[::1]:9090", {"mixed-port": 7890, "authentication": ["name:pass:part"]})
        self.assertEqual(proxy.url.port, 7890)
        self.assertEqual(proxy.auth, ("name", "pass:part"))

    async def test_success_uses_proxy_and_fixed_target(self):
        main = importlib.import_module("app.main")
        response = MagicMock(status_code=204)
        stream = MagicMock()
        stream.__aenter__ = AsyncMock(return_value=response)
        stream.__aexit__ = AsyncMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = stream
        factory = MagicMock()
        factory.__aenter__ = AsyncMock(return_value=client)
        factory.__aexit__ = AsyncMock(return_value=False)
        with patch.object(main, "mihomo_get", AsyncMock(return_value={"mixed-port": 7890})), patch.object(main, "mihomo_base_url", return_value="http://nas:9090"), patch.object(mobile_api.httpx, "AsyncClient", return_value=factory) as create:
            result = await mobile_api.run_google_check({})
        self.assertTrue(result["success"])
        client.stream.assert_called_once_with("GET", mobile_api.GOOGLE_TARGET)
        self.assertFalse(create.call_args.kwargs["trust_env"])
        self.assertEqual(create.call_args.kwargs["proxy"].url.host, "nas")

    async def test_timeout_and_errors_do_not_leak(self):
        main = importlib.import_module("app.main")
        for error in (httpx.ReadTimeout("timeout"), RuntimeError("secret-password-must-not-leak")):
            with patch.object(main, "load_config", return_value={}), patch.object(mobile_api, "run_google_check", AsyncMock(side_effect=error)):
                result = await mobile_api.google_check()
            self.assertFalse(result["success"])
            self.assertNotIn("secret-password", str(result))

    def test_article_id_is_bound_sql_and_connection_closes(self):
        main = importlib.import_module("app.main")
        cursor = MagicMock()
        cursor.fetchone.return_value = ("one.md", "vid", "https://example.com", None, "# Full article")
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        with patch.object(main, "postgres_connection", return_value=connection), patch.object(main, "load_daily_task_state", return_value={"read_summaries": {"one.md"}}):
            row = mobile_api.read_article("one.md")
        self.assertEqual(row["summary_markdown"], "# Full article")
        self.assertTrue(row["is_read"])
        self.assertEqual(cursor.execute.call_args.args[1], ("one.md",))
        connection.close.assert_called_once()
        with self.assertRaises(HTTPException): mobile_api.read_article("")


class VideoApiTest(unittest.TestCase):
    def test_publication_date_comes_from_valid_filename_not_file_mtime(self):
        for name,expected in [('20261015-title.mp4','2026-10-15'),('20240229-title.mp4','2024-02-29'),('20260230-title.mp4',None),('sample.mp4',None)]:
            path=MagicMock();path.stem=name[:-4];path.stat.return_value.st_size=100
            with patch.object(mobile_api,'resolve_video',return_value=path),patch.object(mobile_api,'video_revision',return_value='v'):
                self.assertEqual(mobile_api.describe_video('safe-id')['published_date'],expected)

    def test_only_indexed_files_and_ranges(self):
        import tempfile
        from pathlib import Path
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'videos'
            root.mkdir()
            (root / 'sample.mp4').write_bytes(bytes(range(256)) * 100)
            outside = Path(temp) / 'outside.mp4'
            outside.write_bytes(b'private')
            (root / 'escape.mp4').symlink_to(outside)
            index = Path(temp) / 'index.tsv'
            index.write_text('valid-id\t/mnt/share/sample.mp4\nescape\t/escape.mp4\n', encoding='utf-8')
            app = FastAPI()
            app.include_router(mobile_api.router)
            with patch.object(mobile_api, 'VIDEO_ROOT', root), patch.object(mobile_api, 'VIDEO_INDEX', index), TestClient(app) as client:
                result = client.get('/api/videos/valid-id', headers={'Range': 'bytes=100-199'})
                self.assertEqual(result.status_code, 206)
                self.assertEqual(result.headers['content-range'], 'bytes 100-199/25600')
                self.assertEqual(result.content, bytes(range(100, 200)))
                listed = client.get('/api/videos').json()['items']
                self.assertEqual([item['video_id'] for item in listed], ['valid-id'])
                self.assertNotIn(str(root), str(listed))
                self.assertEqual(client.get('/api/videos/valid-id/info').json()['size'], 25600)
                self.assertEqual(client.head('/api/videos/valid-id').headers['content-length'], '25600')
                self.assertEqual(client.get('/api/videos/valid-id', headers={'Range': 'bytes=999999-'}).status_code, 416)
                self.assertEqual(client.get('/api/videos/unknown').status_code, 404)
                self.assertEqual(client.get('/api/videos/escape').status_code, 404)
                for video_id in ('../outside', '/outside', 'bad?query'):
                    with self.assertRaises(HTTPException): mobile_api.resolve_video(video_id)

    def test_article_video_mapping_and_windows_index(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sample.mp4').write_bytes(b'video')
            index = root / 'index.tsv'
            index.write_text('video-id\t\\\\NAS\\home\\sample.mp4\n', encoding='utf-8')
            with patch.object(mobile_api, 'VIDEO_ROOT', root), patch.object(mobile_api, 'VIDEO_INDEX', index), patch.object(mobile_api, 'article', AsyncMock(return_value={'video_id': 'video-id'})):
                import asyncio
                info = asyncio.run(mobile_api.video_info('article.md'))
                self.assertEqual(info['stream_path'], 'api/videos/video-id')
                self.assertEqual(info['size'], 5)


class ThumbnailTest(unittest.TestCase):
    def test_generation_is_cached_bounded_and_invalidated_by_file_change(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            video=root / 'local.mp4'
            video.write_bytes(b'video')
            index=root / 'index.tsv'
            index.write_text('safe-id\t/mnt/local.mp4\n',encoding='utf-8')
            def generate(command, **kwargs):
                self.assertEqual(command[0], 'ffmpeg')
                self.assertIn(str(video), command)
                self.assertEqual(kwargs['timeout'], 30)
                self.assertNotIn('shell', kwargs)
                Path(command[-1]).write_bytes(b'jpeg-fixture')
                return MagicMock(returncode=0)
            with patch.object(mobile_api,'VIDEO_ROOT',root), patch.object(mobile_api,'VIDEO_INDEX',index), patch.object(mobile_api,'THUMBNAIL_ROOT',root/'cache'), patch.object(mobile_api.subprocess,'run',side_effect=generate) as run:
                first=mobile_api.make_thumbnail('safe-id')
                self.assertEqual(first.read_bytes(),b'jpeg-fixture')
                self.assertEqual(mobile_api.make_thumbnail('safe-id'),first)
                self.assertEqual(run.call_count,1)
                video.write_bytes(b'changed-video')
                self.assertNotEqual(mobile_api.make_thumbnail('safe-id'),first)
                self.assertEqual(run.call_count,2)
                with self.assertRaises(HTTPException): mobile_api.make_thumbnail('../outside')
                self.assertEqual(run.call_count,2)
                with patch.object(mobile_api.subprocess,'run',side_effect=OSError('secret path')):
                    video.write_bytes(b'another-video-change')
                    with self.assertRaises(HTTPException) as failed: mobile_api.make_thumbnail('safe-id')
                    self.assertEqual(failed.exception.status_code,503)
                    self.assertNotIn('secret',failed.exception.detail)
                    self.assertEqual(len(list((root/'cache').glob('*.jpg'))),2)


class ArticleListTest(unittest.TestCase):
    def test_paging_preview_read_status_and_bound_query(self):
        main=importlib.import_module("app.main")
        cursor=MagicMock()
        cursor.fetchall.return_value=[('one.md','video-1',None,'short preview'),('two.md','video-2',None,'second')]
        connection=MagicMock()
        connection.cursor.return_value.__enter__.return_value=cursor
        with patch.object(main,'postgres_connection',return_value=connection), patch.object(main,'load_daily_task_state',return_value={'read_summaries':{'one.md'}}):
            page=mobile_api.read_articles(4,1)
        self.assertTrue(page['has_more'])
        self.assertEqual(len(page['items']),1)
        self.assertEqual(page['items'][0]['preview'],'short preview')
        self.assertTrue(page['items'][0]['is_read'])
        self.assertEqual(cursor.execute.call_args.args[1],(2,4))
        connection.close.assert_called_once()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI(); app.include_router(mobile_api.router)
        with TestClient(app) as client:
            for query in ('offset=-1','limit=0','limit=101'):
                self.assertEqual(client.get('/api/articles?'+query).status_code,422)


if __name__ == "__main__":
    unittest.main()
