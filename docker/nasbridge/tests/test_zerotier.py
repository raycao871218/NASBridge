import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.zerotier import router
app=FastAPI();app.include_router(router);client=TestClient(app)
URL='/api/zerotier/networks/0123456789abcdef/members/0123456789'
class Tests(unittest.TestCase):
 @patch('app.zerotier.remote',return_value={'items':[]})
 def test_read_only(self,r):
  self.assertEqual(client.get('/api/zerotier/networks').status_code,200)
  self.assertEqual(r.call_args.args[0],{'mode':'networks'})
 @patch('app.zerotier.remote')
 def test_reject_bad_writes(self,r):
  for body in [{'name':'test'},{'confirm':True,'ipAssignments':['1.2.3.4/24']},{'confirm':True,'authorized':'yes'},{'confirm':True}]:
   self.assertIn(client.post(URL,json=body).status_code,[400,422])
  self.assertEqual(client.post(URL,json={'confirm':True,'name':'x'},headers={'Origin':'https://other.example'}).status_code,403)
  self.assertEqual(client.get('/api/zerotier/networks/invalid/members').status_code,422)
  r.assert_not_called()
 @patch('app.zerotier.remote',return_value={'verified':True})
 def test_patch_only_changed_fields(self,r):
  self.assertEqual(client.post(URL,json={'confirm':True,'name':'NAS'}).status_code,200)
  self.assertEqual(r.call_args.args[0]['patch'],{'name':'NAS'})
 @patch('app.zerotier.remote',return_value={'verified':True})
 def test_normalize_ip(self,r):
  self.assertEqual(client.post(URL,json={'confirm':True,'ipAssignments':[' 192.0.2.20 ','192.0.2.20']}).status_code,200)
  self.assertEqual(r.call_args.args[0]['patch'],{'ipAssignments':['192.0.2.20']})
if __name__=='__main__':unittest.main()

class AdminLinkTests(unittest.TestCase):
 def test_link_comes_from_local_config(self):
  with patch('app.main.load_config',return_value={'zerotier':{'admin_url':'https://controller.example.com/'}}):
   response=client.get('/api/zerotier/admin',follow_redirects=False)
   self.assertEqual(response.status_code,307)
   self.assertEqual(response.headers['location'],'https://controller.example.com/')
   self.assertEqual(response.headers['cache-control'],'no-store')
 def test_missing_and_unsafe_links(self):
  for url,status in [('',404),('javascript:alert(1)',400),('https://user:pass@example.com/',400)]:
   with patch('app.main.load_config',return_value={'zerotier':{'admin_url':url}}):
    self.assertEqual(client.get('/api/zerotier/admin',follow_redirects=False).status_code,status)
