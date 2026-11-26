import json
import base64
import zlib
import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import android_setup, geofence


class AndroidSetupTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = patch.object(android_setup, "profile_directory", return_value=Path(self.directory.name))
        self.storage.start(); self.addCleanup(self.storage.stop)
        app = FastAPI()
        app.include_router(android_setup.router)
        self.client = TestClient(app)
        self.data = {"network": {"ssid": "Home Wi-Fi", "local_url": "http://192.168.1.10:18080",
                                 "public_url": "https://nas.example.test:18099"},
                     "presence": {"url": "https://ha.example.test:8123", "entity": "input_boolean.home",
                                  "token": "fixture-secret", "test_mode": False}, "include_geofence": True}

    def post(self, data=None, **kwargs):
        with patch.object(geofence, "load_state", return_value=geofence.default_state()):
            return self.client.post("/api/android-setup/qr.svg", json=self.data if data is None else data, **kwargs)

    def test_round_trip_contract_and_svg_without_secret_echo(self):
        captured = []
        original = android_setup.qrcode.QRCode.add_data
        def add(qr, value, *args, **kwargs):
            captured.append(value)
            return original(qr, value, *args, **kwargs)
        with patch.object(android_setup.qrcode.QRCode, "add_data", new=add):
            result = self.post()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.headers["content-type"].startswith("image/svg+xml"))
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIn(b"<svg", result.content)
        self.assertNotIn(b"fixture-secret", result.content)
        self.assertTrue(captured[0].startswith("NASBRIDGE1:"))
        encoded = captured[0].split(":", 1)[1]
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))))
        self.assertEqual(data["type"], "nasbridge.android.config")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["network"]["local_url"], "http://192.168.1.10:18080/")
        self.assertEqual(data["presence"]["token"], "fixture-secret")
        self.assertEqual(data["geofence"]["schema_version"], 2)
        self.assertNotIn("max_detection_seconds", data["geofence"]["settings"])

    def test_network_only_never_reads_fence(self):
        data = {"network": self.data["network"], "include_geofence": False}
        with patch.object(geofence, "load_state", side_effect=AssertionError("Unexpected read")):
            result = self.client.post("/api/android-setup/qr.svg", json=data)
        self.assertEqual(result.status_code, 200)
        payload = android_setup.configuration(android_setup.Setup.model_validate(data))
        self.assertNotIn("presence", payload)
        self.assertNotIn("geofence", payload)

    def test_validation_errors_never_echo_credentials(self):
        cases = [dict(local_url="https://user:secret@nas.test"), dict(local_url="file:///etc/passwd"),
                 dict(local_url="http://nas.test:70000"), dict(public_url="https://nas.test/?token=fixture-secret"),
                 dict(ssid="x" * 33), dict(ssid="<unknown ssid>")]
        for change in cases:
            result = self.post({**self.data, "network": {**self.data["network"], **change}})
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("fixture-secret", result.text)
        for change in [dict(url="http://ha.test"), dict(entity="switch.home"), dict(token="fixture-secret\n"),
                       dict(test_mode="true"), dict(token="")]:
            result = self.post({**self.data, "presence": {**self.data["presence"], **change}})
            self.assertEqual(result.status_code, 422)
            self.assertNotIn("fixture-secret", result.text)
        self.assertNotIn("fixture-secret", repr(android_setup.Setup.model_validate(self.data)))

    def test_cross_origin_and_limits(self):
        self.assertEqual(self.post(headers={"Origin": "https://untrusted.test"}).status_code, 403)
        self.assertEqual(self.post(headers={"Origin": "http://testserver"}).status_code, 200)
        self.assertEqual(self.client.post("/api/android-setup/qr.svg", content="x", headers={"Content-Type": "text/plain"}).status_code, 415)
        result = self.client.post("/api/android-setup/qr.svg", content=b"x" * 8193, headers={"Content-Type": "application/json"})
        self.assertEqual(result.status_code, 413)
        data = {**self.data, "network": {**self.data["network"], "local_url": "https://nas.test/" + "a"*480,
                                        "public_url": "https://nas.test/" + "b"*480},
                "presence": {**self.data["presence"], "token": "s"*1024}}
        self.assertEqual(self.post(data).status_code, 422)

    def test_no_credential_storage_or_fetch_routes(self):
        self.assertEqual(self.client.get("/api/android-setup/qr.svg").status_code, 405)
        with patch.object(geofence, "load_state", side_effect=RuntimeError("fixture-secret")):
            result = self.client.post("/api/android-setup/qr.svg", json=self.data)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("fixture-secret", result.text)

    def save(self, changes=None):
        return self.client.put("/api/android-setup/config", json={**self.data, "expected_revision": 0, **(changes or {})})

    def test_ordered_local_addresses_saved_and_encoded(self):
        network = {"ssid": "Home", "local_urls": ["192.0.2.2:18080", "https://nas.test:8443", "192.0.2.2:18080/"],
                   "public_url": "public.test"}
        data = {**self.data, "network": network, "include_geofence": False}
        saved = self.save(data)
        self.assertEqual(saved.status_code, 200)
        expected = ["http://192.0.2.2:18080/", "https://nas.test:8443/"]
        self.assertEqual(saved.json()["settings"]["network"]["local_urls"], expected)
        self.assertEqual(saved.json()["settings"]["network"]["local_url"], expected[0])
        self.assertEqual(self.client.get("/api/android-setup/config").json(), saved.json())
        with patch.object(android_setup.qrcode.QRCode, "add_data") as capture:
            self.assertEqual(self.post(data).status_code, 200)
        encoded = capture.call_args.args[0].removeprefix("NASBRIDGE1:")
        payload = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))))
        self.assertEqual(payload["network"], saved.json()["settings"]["network"])

    def test_invalid_local_address_lists(self):
        for values in ([], None, ["http://nas.test"] * 9, [1], [""], ["file:///etc/passwd"],
                       ["http://user:secret@nas.test"], ["nas.test:70000"], ["nas.test/?key=secret"]):
            with self.subTest(values=values):
                response = self.post({**self.data, "network": {**self.data["network"], "local_urls": values}})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("secret", response.text)

    def test_legacy_network_has_no_null_list(self):
        data = {**self.data, "include_geofence": False}
        payload = android_setup.configuration(android_setup.Setup.model_validate(data))
        self.assertNotIn("local_urls", payload["network"])
        self.assertEqual(self.save().status_code, 200)
        self.assertNotIn("local_urls", self.client.get("/api/android-setup/config").json()["settings"]["network"])

    def test_encrypted_profile_survives_reload_and_redacts_token(self):
        initial = self.client.get("/api/android-setup/config")
        self.assertIsNone(initial.json()["settings"])
        saved = self.save()
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["revision"], 1)
        self.assertTrue(saved.json()["has_saved_token"])
        self.assertNotIn("fixture-secret", saved.text)
        self.assertNotIn("token", saved.json()["settings"]["presence"])
        root = Path(self.directory.name)
        self.assertNotIn(b"fixture-secret", (root / "android-setup.enc").read_bytes())
        if os.name != "nt":
            self.assertEqual((root / "android-setup.key").stat().st_mode & 0o777, 0o600)
            self.assertEqual((root / "android-setup.enc").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.client.get("/api/android-setup/config").json(), saved.json())
        self.assertEqual(self.client.get("/api/android-setup/config").headers["cache-control"], "no-store")

    def test_blank_token_reuses_saved_secret_for_save_and_qr(self):
        self.assertEqual(self.save().status_code, 200)
        presence = {**self.data["presence"], "token": "", "test_mode": True}
        saved = self.save({"presence": presence, "expected_revision": 1})
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["settings"]["presence"]["test_mode"])
        request = {**self.data, "presence": presence}
        result = self.post(request)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(android_setup._load_profile()["settings"]["presence"]["token"], "fixture-secret")
        self.assertEqual(self.save().status_code, 409)

    def test_first_save_requires_secret_and_network_only_clears_saved_ha(self):
        presence = {**self.data["presence"], "token": ""}
        self.assertEqual(self.save({"presence": presence}).status_code, 422)
        self.assertEqual(self.save().status_code, 200)
        result = self.save({"presence": None, "expected_revision": 1})
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["has_saved_token"])
        self.assertIsNone(result.json()["settings"]["presence"])
        self.assertEqual(self.post({**self.data, "presence": presence}).status_code, 422)

    def test_corruption_and_write_failure_do_not_overwrite_saved_profile(self):
        self.assertEqual(self.save().status_code, 200)
        path = Path(self.directory.name) / "android-setup.enc"
        original = path.read_bytes()
        with patch.object(android_setup.os, "replace", side_effect=OSError("fixture-secret")):
            result = self.save({"expected_revision": 1})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("fixture-secret", result.text)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(Path(self.directory.name).glob(".android-setup-*")), [])
        path.write_bytes(b"corrupted")
        self.assertEqual(self.client.get("/api/android-setup/config").status_code, 503)
        self.assertEqual(self.save({"expected_revision": 1}).status_code, 503)
        self.assertEqual(path.read_bytes(), b"corrupted")

    def test_saved_profile_rejects_cross_origin_and_stale_revisions(self):
        result = self.client.put("/api/android-setup/config", json={**self.data, "expected_revision": 0}, headers={"Origin": "https://other.test"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.save({"expected_revision": True}).status_code, 422)
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.save().status_code, 409)

    def test_wifi_can_be_left_for_phone_to_read(self):
        data = {**self.data, "network": {**self.data["network"], "ssid": ""}}
        self.assertEqual(self.post(data).status_code, 200)
        result = self.save({"network": data["network"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["settings"]["network"]["ssid"], "")
        data["network"].pop("ssid")
        self.assertEqual(android_setup.Setup.model_validate(data).network.ssid, "")

    def test_addresses_without_protocol_are_normalized_for_save_and_qr(self):
        data = {**self.data, "network": {"ssid": "", "local_url": " 192.168.1.10:18080/base ",
                                       "public_url": "nas.example.test:18099"},
                "presence": {**self.data["presence"], "url": "ha.example.test:8123"}, "include_geofence": False}
        saved = self.save(data)
        self.assertEqual(saved.status_code, 200)
        settings = saved.json()["settings"]
        self.assertEqual(settings["network"]["local_url"], "http://192.168.1.10:18080/base/")
        self.assertEqual(settings["network"]["public_url"], "https://nas.example.test:18099/")
        self.assertEqual(settings["presence"]["url"], "https://ha.example.test:8123/")
        self.assertEqual(self.client.get("/api/android-setup/config").json(), saved.json())
        self.assertEqual(self.post(data).status_code, 200)
        payload = android_setup.configuration(android_setup.Setup.model_validate(data))
        self.assertEqual(payload["network"], settings["network"])
        self.assertEqual(payload["presence"]["url"], settings["presence"]["url"])

    def test_explicit_protocols_and_invalid_bare_addresses(self):
        for url in ("https://nas.test:18080/base", "http://nas.test:18080/base"):
            with self.subTest(url=url):
                network = android_setup.Network(ssid="", local_url=url, public_url=url)
                self.assertEqual(network.local_url, url + "/")
                self.assertEqual(network.public_url, url + "/")
        for url in ("nas.test:70000", "user:secret@nas.test", "nas.test/?token=secret", "nas.test/#fragment", "nas test", " ", "ftp://nas.test"):
            with self.subTest(url=url):
                self.assertEqual(self.post({**self.data, "network": {**self.data["network"], "local_url": url}}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
