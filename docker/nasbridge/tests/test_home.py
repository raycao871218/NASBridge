import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import home


class HomeTest(unittest.TestCase):
    def setUp(self):
        self.state = {"revision": 0, "sites": [None] * 6}
        self.reader = patch.object(home, "load_state", side_effect=lambda: copy.deepcopy(self.state))
        self.writer = patch.object(home, "save_state", side_effect=self.save)
        self.reader.start()
        self.writer.start()
        self.addCleanup(self.reader.stop)
        self.addCleanup(self.writer.stop)
        app = FastAPI()
        app.include_router(home.router)
        self.client = TestClient(app)

    def save(self, state):
        self.state = copy.deepcopy(state)

    def payload(self, url="nas.local:8080/photos"):
        return {"expected_revision": 0, "sites": [{"name": " 相册 ", "url": url}] + [None] * 5}

    def test_defaults_add_reload_edit_remove(self):
        response = self.client.get("/api/home/sites")
        self.assertEqual(response.json(), self.state)
        self.assertEqual(response.headers["cache-control"], "no-store")
        response = self.client.put("/api/home/sites", json=self.payload())
        self.assertEqual(response.status_code, 200)
        state = self.client.get("/api/home/sites").json()
        self.assertEqual(state["sites"][0], {"name": "相册", "url": "https://nas.local:8080/photos"})
        body = {"expected_revision": state["revision"], "sites": state["sites"]}
        body["sites"][0] = {"name": "NAS", "url": "http://192.168.1.2:8080"}
        self.assertEqual(self.client.put("/api/home/sites", json=body).status_code, 200)
        body["expected_revision"] = 2
        body["sites"][0] = None
        self.assertEqual(self.client.put("/api/home/sites", json=body).json()["sites"], [None] * 6)

    def test_invalid_urls_and_site_counts(self):
        for url in ["javascript:alert(1)", "data:text/html,test", "ftp://nas.local", "https://user:pass@nas.local", "https://", "https://nas.local:bad", "https://nas.local/a b", "https://nas.local\\evil"]:
            with self.subTest(url=url):
                self.assertEqual(self.client.put("/api/home/sites", json=self.payload(url)).status_code, 422)
        for count in [0, 5, 7]:
            self.assertEqual(self.client.put("/api/home/sites", json={"expected_revision": 0, "sites": [None] * count}).status_code, 422)
        body = self.payload()
        body["sites"][0]["name"] = " "
        self.assertEqual(self.client.put("/api/home/sites", json=body).status_code, 422)
        self.assertEqual(self.state["revision"], 0)

    def test_concurrent_edits_reject_stale_revision(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(lambda _: self.client.put("/api/home/sites", json=self.payload()).status_code, range(2)))
        self.assertEqual(sorted(codes), [200, 409])
        self.assertEqual(self.state["revision"], 1)

    def test_save_failure_does_not_claim_success(self):
        with patch.object(home, "save_state", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.put("/api/home/sites", json=self.payload())
        self.assertEqual(self.state["revision"], 0)


if __name__ == "__main__":
    unittest.main()
