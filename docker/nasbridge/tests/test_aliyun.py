import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import aliyun

app=FastAPI()
app.include_router(aliyun.router)
client=TestClient(app)
BASE=dict(region='cn-hangzhou',group='sg-test',ports='443,8000-8010',source='192.0.2.1/32')

class AliyunTests(unittest.TestCase):
    @patch.dict('os.environ', {k:'secret-sentinel' for k in aliyun.KEYS})
    def test_status_redaction(self):
        r=client.get('/api/aliyun/status')
        self.assertEqual(r.json(),{'configured':True})
        self.assertNotIn('secret-sentinel',r.text)

    @patch('app.aliyun.call_api')
    def test_preview_no_cloud_writes(self,api):
        r=client.post('/api/aliyun/open-ports',json=BASE)
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json()['items']),2)
        api.assert_not_called()

    @patch('app.aliyun.call_api',return_value={'RequestId':'test'})
    def test_confirm_batch(self,api):
        r=client.post('/api/aliyun/open-ports',json=dict(BASE,confirm=True))
        self.assertEqual(r.status_code,200)
        action,region,params=api.call_args.args
        self.assertEqual(action,'AuthorizeSecurityGroup')
        self.assertEqual(params['Permissions.2.PortRange'],'8000/8010')
        self.assertEqual(params['Permissions.1.SourceCidrIp'],'192.0.2.1/32')

    @patch('app.aliyun.call_api')
    def test_bad_inputs_never_write(self,api):
        for change in [dict(ports='0'),dict(ports='65536'),dict(ports='90-80'),dict(ports='443,'),dict(source='192.0.2.1/24'),dict(protocol='all'),dict(priority=101),dict(group='bad'),dict(ports=','.join(map(str,range(1,102))))]:
            r=client.post('/api/aliyun/open-ports',json=dict(BASE,confirm=True,**change))
            self.assertEqual(r.status_code,422,change)
        api.assert_not_called()

    @patch('app.aliyun.call_api')
    def test_cross_origin_rejected(self,api):
        r=client.post('/api/aliyun/open-ports',json=dict(BASE,confirm=True),headers={'Origin':'https://other.example'})
        self.assertEqual(r.status_code,403)
        api.assert_not_called()

    def test_ipv6_dedup(self):
        rules=aliyun.permissions(aliyun.OpenPorts(**dict(BASE,source='::/0',ports='443,443/443',protocol='udp')))
        self.assertEqual(len(rules),1)
        self.assertEqual(rules[0]['Ipv6SourceCidrIp'],'::/0')
        self.assertNotIn('SourceCidrIp',rules[0])

    @patch('app.aliyun.call_api',side_effect=[{'Permissions':{'Permission':[{'PortRange':'443/443'}]},'NextToken':'next'},{'Permissions':{'Permission':[{'PortRange':'80/80'}]}}])
    def test_pagination(self,api):
        r=client.get('/api/aliyun/rules?region=cn-hangzhou&group=sg-test')
        self.assertEqual(len(r.json()['items']),2)
        self.assertEqual(api.call_count,2)

if __name__=='__main__':unittest.main()
