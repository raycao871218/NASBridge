import copy
import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import geofence


class GeofenceTest(unittest.TestCase):
    def setUp(self):
        self.state = geofence.default_state()
        self.read = patch.object(geofence, 'load_state', side_effect=lambda: copy.deepcopy(self.state))
        self.write = patch.object(geofence, 'save_state', side_effect=self.save)
        self.read.start(); self.write.start()
        app = FastAPI(); app.include_router(geofence.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.read.stop(); self.write.stop()

    def save(self, state):
        self.state = copy.deepcopy(state)

    def payload(self, **changes):
        return {**geofence.Settings().model_dump(), 'expected_revision': self.state['revision'], **changes}

    def test_default_contract_and_no_side_effects(self):
        r = self.client.get('/api/geofence/config'); self.assertEqual(r.status_code, 200)
        d = r.json(); self.assertEqual(d['schema_version'], 1); self.assertEqual(d['revision'], 0)
        self.assertFalse(d['settings']['enabled']); self.assertFalse(d['settings']['test_mode'])
        self.assertEqual(d['effective'], {'check_interval_seconds': 3600, 'max_detection_seconds': 14400,
                                          'max_checks': 4, 'first_check_after_seconds': 3600})
        self.assertEqual(d['rules']['ha_reporter'], 'android')
        self.assertEqual(d['rules']['inside_region'], 'keep_previous_state')
        self.assertEqual(d['rules']['unknown_location'], 'keep_previous_state')
        self.assertEqual(d['rules']['window_expired'], 'away_if_still_public')
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.assertNotIn('ha_token', r.text)
        geofence.save_state.assert_not_called()

    def test_round_trip_and_revision(self):
        r = self.client.put('/api/geofence/config', json=self.payload(enabled=True, boundary_buffer_meters=2000))
        self.assertEqual(r.status_code, 200); d = r.json(); self.assertEqual(d['revision'], 1)
        self.assertIsNotNone(d['updated_at']); self.assertEqual(d['settings']['boundary_buffer_meters'], 2000)
        self.assertEqual(self.client.get('/api/geofence/config').json(), d)
        r = self.client.put('/api/geofence/config', json=self.payload(expected_revision=0, enabled=False))
        self.assertEqual(r.status_code, 409); self.assertTrue(self.state['settings']['enabled'])

    def test_concurrent_edit_only_one_wins(self):
        body = self.payload(enabled=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.client.put('/api/geofence/config', json=body).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 409]); self.assertEqual(self.state['revision'], 1)

    def test_test_mode_does_not_overwrite_normal_settings(self):
        d = self.client.put('/api/geofence/config', json=self.payload(test_mode=True)).json()
        self.assertEqual(d['effective']['check_interval_seconds'], 15)
        self.assertEqual(d['effective']['max_detection_seconds'], 60)
        self.assertEqual(d['effective']['max_checks'], 4)
        self.assertEqual(d['settings']['check_interval_seconds'], 3600)
        d = self.client.put('/api/geofence/config', json=self.payload(test_mode=False)).json()
        self.assertEqual(d['effective']['max_detection_seconds'], 14400)

    def test_invalid_input_never_saved(self):
        cases = [dict(enabled='true'), dict(test_mode=1), dict(region_id='other'),
                 dict(check_interval_seconds=True), dict(check_interval_seconds=0),
                 dict(check_interval_seconds=60.1), dict(check_interval_seconds=30),
                 dict(max_detection_seconds=59), dict(max_detection_seconds=604801),
                 dict(max_detection_seconds=3601), dict(max_detection_seconds=1800),
                 dict(check_interval_seconds=60, max_detection_seconds=3600),
                 dict(boundary_buffer_meters=-1), dict(boundary_buffer_meters=10001),
                 dict(max_location_age_seconds=0), dict(max_location_accuracy_meters=99),
                 dict(max_location_accuracy_meters=10001), dict(expected_revision=-1),
                 dict(ha_token='must not be accepted'), dict(boundary_url='https://evil.test')]
        for changes in cases:
            with self.subTest(changes=changes):
                r = self.client.put('/api/geofence/config', json=self.payload(**changes))
                self.assertEqual(r.status_code, 422)
        geofence.save_state.assert_not_called()

    def test_boundary_matches_download_hash(self):
        r = self.client.get('/api/geofence/boundary'); self.assertEqual(r.status_code, 200)
        d = r.json(); props = d['feature']['properties']
        self.assertEqual(props['adcode'], '510726'); self.assertEqual(d['coordinate_system'], 'GCJ-02')
        self.assertEqual(d['sha256'], hashlib.sha256((geofence.GEODATA/'beichuan.geojson').read_bytes()).hexdigest())
        self.assertEqual(d['sha256'], self.client.get('/api/geofence/config').json()['region']['boundary_sha256'])
        self.assertIn(d['feature']['geometry']['type'], ['Polygon', 'MultiPolygon'])
        preview = self.client.get('/api/geofence/preview').json()
        county = next(f for f in preview['features'] if f['properties']['adcode'] == 510726)
        self.assertEqual(county['geometry'], d['feature']['geometry'])
        self.assertEqual(r.headers['cache-control'], 'no-store')

    def test_errors_do_not_leak_secrets(self):
        with patch.object(geofence, 'load_state', side_effect=RuntimeError('secret DB password')):
            r = self.client.get('/api/geofence/config'); self.assertEqual(r.status_code, 503)
            self.assertNotIn('password', r.text)
        with patch.object(geofence, 'save_state', side_effect=RuntimeError('secret DB password')):
            r = self.client.put('/api/geofence/config', json=self.payload())
            self.assertEqual(r.status_code, 503); self.assertNotIn('password', r.text)
            self.assertEqual(self.state['revision'], 0)

    def test_cross_origin_put_rejected(self):
        r = self.client.put('/api/geofence/config', json=self.payload(), headers={'Origin': 'https://evil.test'})
        self.assertEqual(r.status_code, 403); geofence.save_state.assert_not_called()
        r = self.client.put('/api/geofence/config', json=self.payload(), headers={'Origin': 'http://testserver'})
        self.assertEqual(r.status_code, 200)


class PersistenceIntegrationTest(unittest.TestCase):
    def test_uses_existing_state_store_with_local_backup(self):
        from app import main
        state = {'revision': 7, 'updated_at': None, 'settings': geofence.Settings(enabled=True).model_dump()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(main, 'DATA_DIR', root), patch.object(main, 'save_persisted_state') as save:
                geofence.save_state(state)
                save.assert_called_once_with('geofence_config', root/'geofence.yaml', state)
            with patch.object(main, 'DATA_DIR', root), patch.object(main, 'load_persisted_state', return_value=state):
                self.assertEqual(geofence.load_state(), state)
            with patch.object(main, 'load_persisted_state', return_value={'revision': -1, 'settings': {}}):
                with self.assertRaises(ValueError): geofence.load_state()
