import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import proxy_groups as pg

PROXIES = {'PROXY / 中文': {'type': 'Selector', 'now': 'AUTO', 'all': ['AUTO', 'JP']},
           'AUTO': {'type': 'URLTest', 'now': 'JP', 'all': ['JP'], 'fixed': ''},
           'BALANCE': {'type': 'LoadBalance', 'all': ['JP']},
           'JP': {'type': 'Vless', 'history': [{'delay': 123, 'time': 'fixture'}], 'secret': 'not-public'}}


class ProxyGroupsTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI(); app.include_router(pg.router)
        self.client = TestClient(app)
        self.controller = AsyncMock(return_value={'proxies': PROXIES})
        self.patch = patch.object(pg, 'controller', self.controller); self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_groups_show_nested_choice_and_only_public_fields(self):
        data = self.client.get('/api/mihomo/proxies').json()['items']
        self.assertEqual(data[0]['members'][0]['now'], 'JP')
        self.assertTrue(data[0]['selectable']); self.assertFalse(data[2]['selectable'])
        self.assertEqual(data[0]['members'][1]['delay'], 123)
        self.assertNotIn('secret', str(data))

    def test_selection_encodes_group_and_verifies_readback(self):
        self.controller.side_effect = [{'proxies': PROXIES}, {}, {'now': 'JP'}]
        r = self.client.put('/api/mihomo/proxies/selection', json={'group': 'PROXY / 中文', 'name': 'JP'})
        self.assertEqual(r.status_code, 200)
        call = self.controller.call_args_list[1]
        self.assertEqual(call.args, ('PUT', '/proxies/PROXY%20%2F%20%E4%B8%AD%E6%96%87'))
        self.assertEqual(call.kwargs['json'], {'name': 'JP'})

    def test_rejects_nonmembers_and_unsupported_groups_without_writes(self):
        for group, name in [('PROXY / 中文', 'unknown'), ('BALANCE', 'JP')]:
            self.controller.reset_mock()
            self.assertEqual(self.client.put('/api/mihomo/proxies/selection', json={'group': group, 'name': name}).status_code, 400)
            self.assertEqual(self.controller.call_count, 1)

    def test_cross_site_and_invalid_payload_never_reach_controller(self):
        for body, headers, code in [({'group': 'AUTO', 'name': 'JP'}, {'Origin': 'https://evil.invalid'}, 403),
                                    ({'group': '', 'name': 'JP'}, {}, 422)]:
            self.assertEqual(self.client.put('/api/mihomo/proxies/selection', json=body, headers=headers).status_code, code)
        self.controller.assert_not_called()

    def test_unconfirmed_switch_does_not_retry(self):
        self.controller.side_effect = [{'proxies': PROXIES}, {}, {'now': 'AUTO'}]
        self.assertEqual(self.client.put('/api/mihomo/proxies/selection', json={'group': 'PROXY / 中文', 'name': 'JP'}).status_code, 409)
        self.assertEqual(self.controller.call_count, 3)

    def test_restore_auto_uses_delete_and_checks_fixed(self):
        self.controller.side_effect = [{'proxies': PROXIES}, {}, {'now': 'JP', 'fixed': ''}]
        self.assertEqual(self.client.post('/api/mihomo/proxies/automatic', json={'group': 'AUTO'}).status_code, 200)
        self.assertEqual(self.controller.call_args_list[1].args, ('DELETE', '/proxies/AUTO'))

    def test_latency_uses_fixed_test_url_and_does_not_clear_group_pin(self):
        self.controller.side_effect = [{'proxies': PROXIES}, {'delay': 150}]
        self.assertEqual(self.client.post('/api/mihomo/proxies/delay', json={'name': 'JP'}).json()['delay'], 150)
        self.assertEqual(self.controller.call_args.args, ('GET', '/proxies/JP/delay'))
        self.assertEqual(self.controller.call_args.kwargs['params']['url'], pg.TEST_URL)

if __name__ == '__main__': unittest.main()
