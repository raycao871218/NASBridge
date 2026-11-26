import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import codex_accounts as api


spec = importlib.util.spec_from_file_location("controller", Path(__file__).resolve().parents[1] / "ops/codex-account.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
FIRST = "first@example.test"
SECOND = "second@example.test"


def token(email, account):
    data = {"email": email, controller.AUTH_CLAIM: {"chatgpt_account_id": account}}
    return "header." + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=") + ".signature"


class ControllerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.auth = Path(tmp.name) / "auth.json"
        self.store = Path(tmp.name) / "store.json"
        self.auth.write_text(json.dumps({"auth_mode": "chatgpt", "last_refresh": "2026-11-12T00:00:00Z",
            "tokens": {"id_token": token(FIRST, "a"), "refresh_token": "newer-a", "account_id": "a"}}))
        self.store.write_text(json.dumps({"accounts": {
            key: {"email": email, "id_token": token(email, key), "chatgpt_account_id": key,
                  "refresh_token": "old-" + key} for key, email in [("a", FIRST), ("b", SECOND)]
        }}))

    def test_switch_rotates_token_and_preserves_outgoing_login(self):
        with patch.object(controller, "refresh", return_value={"access_token": "test", "refresh_token": "new-b"}) as refresh:
            result = controller.manage(self.auth, self.store, SECOND, FIRST)
        self.assertEqual(result, {"current": SECOND, "accounts": [FIRST, SECOND], "changed": True})
        refresh.assert_called_once()
        accounts = json.loads(self.store.read_text())["accounts"]
        self.assertEqual((accounts["a"]["refresh_token"], accounts["b"]["refresh_token"]), ("newer-a", "new-b"))
        self.assertEqual(controller.manage(self.auth, self.store)["current"], SECOND)
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o600)

    def test_stale_unknown_and_noop_do_not_refresh(self):
        with patch.object(controller, "refresh") as refresh:
            with self.assertRaisesRegex(controller.Rejected, "stale"):
                controller.manage(self.auth, self.store, SECOND, SECOND)
            with self.assertRaisesRegex(controller.Rejected, "unknown_account"):
                controller.manage(self.auth, self.store, "other@example.test", FIRST)
            self.assertFalse(controller.manage(self.auth, self.store, FIRST, FIRST)["changed"])
        refresh.assert_not_called()

    def test_wrong_refreshed_identity_never_replaces_login(self):
        auth, store = self.auth.read_bytes(), self.store.read_bytes()
        with patch.object(controller, "refresh", return_value={"id_token": token(FIRST, "a"), "access_token": "test"}):
            with self.assertRaisesRegex(controller.Rejected, "identity_mismatch"):
                controller.manage(self.auth, self.store, SECOND, FIRST)
        self.assertEqual((self.auth.read_bytes(), self.store.read_bytes()), (auth, store))


class ApiTest(unittest.TestCase):
    def test_lan_direct_access_and_same_origin(self):
        app = FastAPI()
        app.include_router(api.router)
        client = TestClient(app, base_url="http://10.10.0.66:18080")
        url = "/api/codex-accounts"
        config = {"codex_accounts": {"lan_hosts": ["10.10.0.66:18080"], "ssh_host": "ubuntu", "ssh_key_path": "test"}}
        with patch("app.main.load_config", return_value=config), patch.object(api, "control") as control:
            self.assertEqual(client.get(url, headers={"Host": "public.example"}).status_code, 403)
            self.assertEqual(client.get(url, headers={"X-Forwarded-For": "10.10.0.100"}).status_code, 403)
            payload = {"target": SECOND, "expected_current": FIRST}
            self.assertEqual(client.post(url + "/switch", json=payload, headers={"Origin": "https://unrelated.test"}).status_code, 403)
            self.assertEqual(client.post(url + "/switch", json={"target": SECOND}).status_code, 422)
            control.assert_not_called()
            control.return_value = {"current": FIRST, "accounts": [FIRST, SECOND]}
            self.assertEqual(client.get(url).status_code, 200)
            control.assert_called_once_with(config["codex_accounts"])
            control.reset_mock()
            control.return_value = {"current": SECOND, "accounts": [FIRST, SECOND], "changed": True}
            response = client.post(url + "/switch", json=payload, headers={"Origin": "http://10.10.0.66:18080"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            control.assert_called_once_with(config["codex_accounts"], SECOND, FIRST)


if __name__ == "__main__":
    unittest.main()
