import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import home, home_metadata


class MetadataTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(home.router)
        self.client = TestClient(app)

    def test_title_and_relative_image(self):
        result = home_metadata.extract_metadata('<title>Fallback</title><meta property="og:title" content=" 我的 &amp; 相册 "><meta property="og:image" content="/cover.png">', 'https://photos.example/a')
        self.assertEqual(result, {"name": "我的 & 相册", "image_url": "https://photos.example/cover.png"})
        result = home_metadata.extract_metadata('<title>网站标题</title><meta name="twitter:image" content="javascript:alert(1)">', 'https://photos.example/a')
        self.assertEqual(result, {"name": "网站标题", "image_url": None})
        self.assertEqual(home_metadata.extract_metadata('', 'https://nas.local/')["name"], 'nas.local')

    def call_with_transport(self, handler):
        client_type = httpx.AsyncClient
        with patch.object(home_metadata.httpx, 'AsyncClient', side_effect=lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs)):
            return self.client.post('/api/home/metadata', json={"url": "https://photos.example"})

    def test_redirect_and_streamed_html(self):
        def handler(request):
            if request.url.path == '/':
                return httpx.Response(302, headers={'location': '/photos'})
            return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'}, text='<title>相册</title><meta property="og:image" content="cover.jpg">')
        response = self.call_with_transport(handler)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'name': '相册', 'image_url': 'https://photos.example/cover.jpg'})
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_limits_failures_and_non_html(self):
        for handler in [lambda r: httpx.Response(302, headers={'location': '/again'}),
                        lambda r: httpx.Response(200, headers={'content-type': 'application/pdf'}),
                        lambda r: httpx.Response(503)]:
            self.assertEqual(self.call_with_transport(handler).status_code, 502)
        result = self.call_with_transport(lambda r: httpx.Response(200, headers={'content-type': 'text/html'}, text='<title>Bounded</title>'+'x'*600000))
        self.assertEqual(result.json()['name'], 'Bounded')

    def test_origin_and_invalid_url(self):
        self.assertEqual(self.client.post('/api/home/metadata', json={'url': 'https://photos.example'}, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/home/metadata', json={'url': 'file:///etc/passwd'}).status_code, 422)
        with self.assertRaises(ValueError):
            home.Website(name='Photo', url='https://photos.example', image_url='data:image/svg+xml,test')


if __name__ == '__main__':
    unittest.main()
