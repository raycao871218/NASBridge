import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import geofence


class GeofenceTest(unittest.TestCase):
    def setUp(self):
        self.state = geofence.default_state()
        self.read = patch.object(geofence, 'load_state', side_effect=lambda: copy.deepcopy(self.state))
        self.write = patch.object(geofence, 'save_state', side_effect=self.save)
        self.read.start(); self.write.start()
        geofence._search_cache.clear(); geofence._last_search=0
        app = FastAPI(); app.include_router(geofence.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.read.stop(); self.write.stop()

    def save(self, state):
        self.state = copy.deepcopy(state)

    def payload(self, **changes):
        return {**geofence.Settings().model_dump(), 'center_latitude': 31.62, 'center_longitude': 104.46,
                'expected_revision': self.state['revision'], **changes}

    def test_default_contract_no_location_invented(self):
        r = self.client.get('/api/geofence/config'); self.assertEqual(r.status_code, 200)
        d = r.json(); self.assertEqual(d['schema_version'], 2); self.assertEqual(d['revision'], 0)
        self.assertFalse(d['settings']['enabled']); self.assertFalse(d['settings']['test_mode'])
        self.assertEqual(d['fence']['type'], 'circle'); self.assertEqual(d['fence']['coordinate_system'], 'WGS-84')
        self.assertIsNone(d['fence']['center'])
        self.assertEqual(d['effective'], {'check_interval_seconds': 3600, 'max_detection_seconds': 14400,
                                          'max_checks': 4, 'first_check_after_seconds': 3600})
        self.assertEqual(d['rules']['ha_reporter'], 'android')
        self.assertEqual(d['rules']['inside_fence'], 'keep_previous_state')
        self.assertEqual(d['rules']['window_expired'], 'away_if_still_public')
        self.assertEqual(r.headers['cache-control'], 'no-store'); geofence.save_state.assert_not_called()

    def test_circle_round_trip_and_revision(self):
        r = self.client.put('/api/geofence/config', json=self.payload(enabled=True, name='生活圈',radius_meters=12345))
        self.assertEqual(r.status_code, 200); d = r.json(); self.assertEqual(d['revision'], 1)
        self.assertEqual(d['fence'], {'type':'circle','name':'生活圈','coordinate_system':'WGS-84',
                                    'center':{'latitude':31.62,'longitude':104.46},'radius_meters':12345})
        self.assertEqual(self.client.get('/api/geofence/config').json(), d)
        r = self.client.put('/api/geofence/config', json=self.payload(expected_revision=0, enabled=False))
        self.assertEqual(r.status_code, 409); self.assertTrue(self.state['settings']['enabled'])

    def test_concurrent_edit_only_one_wins(self):
        body = self.payload(enabled=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.client.put('/api/geofence/config', json=body).status_code, range(2)))
        self.assertEqual(sorted(results), [200, 409]); self.assertEqual(self.state['revision'], 1)

    def test_test_mode_preserves_normal_times(self):
        d = self.client.put('/api/geofence/config', json=self.payload(test_mode=True)).json()
        self.assertEqual(d['effective']['check_interval_seconds'], 15)
        self.assertEqual(d['effective']['max_detection_seconds'], 60)
        self.assertEqual(d['settings']['check_interval_seconds'], 3600)
        d = self.client.put('/api/geofence/config', json=self.payload(test_mode=False)).json()
        self.assertEqual(d['effective']['max_detection_seconds'], 14400)

    def test_invalid_input_never_saved(self):
        cases = [dict(enabled='true'),dict(test_mode=1),dict(region_id='510726'),dict(fence_type='polygon'),
                 dict(center_latitude=91),dict(center_longitude=181),dict(center_latitude='31'),
                 dict(center_latitude=True),dict(center_latitude=None),dict(enabled=True,center_latitude=None,center_longitude=None),
                 dict(radius_meters=99),dict(radius_meters=500001),dict(radius_meters=True),dict(radius_meters=1.5),
                 dict(name=' '),dict(name='x'*101),dict(check_interval_seconds=True),dict(check_interval_seconds=30),
                 dict(max_detection_seconds=59),dict(max_detection_seconds=604801),dict(max_detection_seconds=3601),
                 dict(max_detection_seconds=1800),dict(check_interval_seconds=60,max_detection_seconds=3600),
                 dict(boundary_buffer_meters=-1),dict(boundary_buffer_meters=10001),dict(max_location_age_seconds=0),
                 dict(max_location_accuracy_meters=99),dict(expected_revision=-1),dict(ha_token='no'),dict(boundary_url='https://evil.test')]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.client.put('/api/geofence/config',json=self.payload(**changes)).status_code,422)
        geofence.save_state.assert_not_called()
        for v in [float('nan'),float('inf'),-float('inf')]:
            with self.assertRaises(ValueError):geofence.Settings(center_latitude=v,center_longitude=1)

    def test_center_and_radius_limits(self):
        for lat,lon,radius in [(0,0,100),(-85,-180,500000),(85,180,10000)]:
            r=self.client.put('/api/geofence/config',json=self.payload(enabled=True,center_latitude=lat,center_longitude=lon,radius_meters=radius))
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()['fence']['radius_meters'],radius)

    def test_legacy_migration_requires_new_choice_preserves_time(self):
        legacy={'revision':8,'updated_at':'2026-10-17T00:00:00Z','settings':{
            'enabled':True,'region_id':'510726','check_interval_seconds':7200,'max_detection_seconds':14400,
            'boundary_buffer_meters':500,'max_location_age_seconds':600,'max_location_accuracy_meters':2000,'test_mode':True}}
        d=geofence.document(legacy)
        self.assertTrue(d['migration_required']);self.assertFalse(d['settings']['enabled']);self.assertIsNone(d['fence']['center'])
        self.assertEqual(d['settings']['check_interval_seconds'],7200);self.assertEqual(d['revision'],8)
        self.assertTrue(legacy['settings']['enabled'])
        self.state=legacy
        r=self.client.put('/api/geofence/config',json=self.payload(enabled=True))
        self.assertEqual(r.status_code,200);self.assertFalse(r.json()['migration_required'])

    def test_errors_do_not_leak_secrets(self):
        with patch.object(geofence,'load_state',side_effect=RuntimeError('secret DB password')):
            r=self.client.get('/api/geofence/config');self.assertEqual(r.status_code,503);self.assertNotIn('password',r.text)
        with patch.object(geofence,'save_state',side_effect=RuntimeError('secret DB password')):
            r=self.client.put('/api/geofence/config',json=self.payload());self.assertEqual(r.status_code,503);self.assertNotIn('password',r.text)

    def test_cross_origin_put_rejected(self):
        r=self.client.put('/api/geofence/config',json=self.payload(),headers={'Origin':'https://evil.test'})
        self.assertEqual(r.status_code,403);geofence.save_state.assert_not_called()
        self.assertEqual(self.client.put('/api/geofence/config',json=self.payload(),headers={'Origin':'http://testserver'}).status_code,200)

    def test_search_is_explicit_cached_and_bounded(self):
        items=[{'name':'北川','latitude':31.97,'longitude':104.29}]
        with patch.object(geofence,'geocode_query',new_callable=AsyncMock,return_value=items) as lookup:
            r=self.client.get('/api/geofence/search',params={'q':'北川'});self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['items'],items)
            self.assertEqual(self.client.get('/api/geofence/search',params={'q':'北川'}).status_code,200)
            lookup.assert_awaited_once_with('北川')
            self.assertEqual(self.client.get('/api/geofence/search',params={'q':'绵阳'}).status_code,429)
        self.assertEqual(self.client.get('/api/geofence/search',params={'q':' '} ).status_code,422)
        self.assertEqual(self.client.get('/api/geofence/search',params={'q':'x'*121}).status_code,422)

    def test_search_failure_safe_fallback(self):
        with patch.object(geofence,'geocode_query',new_callable=AsyncMock,side_effect=RuntimeError('proxy private password')):
            r=self.client.get('/api/geofence/search',params={'q':'北川'})
            self.assertEqual(r.status_code,502);self.assertNotIn('password',r.text);self.assertIn('点击地图',r.json()['detail'])


class PersistenceIntegrationTest(unittest.TestCase):
    def test_existing_state_store_and_corrupt_state(self):
        from app import main
        state={'revision':7,'updated_at':None,'settings':geofence.Settings().model_dump()}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(main,'DATA_DIR',root),patch.object(main,'save_persisted_state') as save:
                geofence.save_state(state);save.assert_called_once_with('geofence_config',root/'geofence.yaml',state)
            with patch.object(main,'DATA_DIR',root),patch.object(main,'load_persisted_state',return_value=state):
                self.assertEqual(geofence.load_state()['settings'],state['settings'])
            with patch.object(main,'load_persisted_state',return_value={'revision':-1,'settings':{}}):
                with self.assertRaises(ValueError):geofence.load_state()
