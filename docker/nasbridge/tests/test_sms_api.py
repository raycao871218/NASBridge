import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import sms_api, main

class SmsTest(unittest.TestCase):
    def setUp(self):
        self.connection=MagicMock();self.cursor=self.connection.cursor.return_value.__enter__.return_value
        self.patch=patch.object(main,'postgres_connection',return_value=self.connection);self.patch.start()
        app=FastAPI();app.include_router(sms_api.router);self.client=TestClient(app)
    def tearDown(self):self.patch.stop()
    def test_page_readonly_and_no_store(self):
        now=datetime(2026,10,14,tzinfo=timezone.utc)
        self.cursor.fetchall.return_value=[('device','id'+str(i),'<sender>','<script>alert(1)</script>',now,now,'android',now,False) for i in range(3)]
        response=self.client.get('/api/sms?limit=2&offset=4');data=response.json()
        self.assertEqual(response.status_code,200);self.assertEqual(len(data['items']),2);self.assertTrue(data['has_more'])
        self.assertEqual(data['items'][0]['body'],'<script>alert(1)</script>')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertIn('T00:00:00',data['items'][0]['sent_at'])
        self.connection.set_session.assert_called_once_with(readonly=True)
        self.assertEqual(self.cursor.execute.call_args.args[1],(3,4));self.connection.close.assert_called_once()
    def test_search_bound_and_wildcards_literal(self):
        self.cursor.fetchall.return_value=[]
        response=self.client.get('/api/sms',params={'q':"a%_' OR 1=1--"})
        self.assertEqual(response.status_code,200)
        sql,args=self.cursor.execute.call_args.args
        self.assertNotIn('1=1',sql);self.assertEqual(args[:2],("%a\\%\\_' OR 1=1--%",)*2)
    def test_bounds(self):
        for query in ('limit=0','limit=101','offset=-1','q='+'a'*101):self.assertEqual(self.client.get('/api/sms?'+query).status_code,422)
        self.assertFalse(self.connection.cursor.called)
    def test_error_closes_and_sanitizes(self):
        self.cursor.execute.side_effect=RuntimeError('private db password')
        response=self.client.get('/api/sms');self.assertEqual(response.status_code,503)
        self.assertNotIn('password',response.text);self.connection.close.assert_called_once()

class VerificationCodeTest(unittest.TestCase):
    def test_context_and_leading_zero(self):
        cases = {
            '【银行】您的验证码是 001234，5 分钟内有效。':'001234',
            '校验码：5678，请勿泄露':'5678',
            '动态密码为87654321':'87654321',
            '短信验证码１２３４５６，勿泄露':'123456',
            'Your verification code is: 123456.':'123456',
            'OTP: 098765':'098765',
            '123456 is your verification code.':'123456',
            '654321为您的登录验证码':'654321',
            '订单123456，验证码987654':'987654',
            '验证码123456，再次确认验证码123456':'123456',
            '快递取件号123456':'',
            '订单号123456，金额1234.56元':'',
            '验证码123456789':'',
            '验证码 ABC123456':'',
            '验证码123456，另一验证码654321':'',
            '请不要泄露验证码，客服电话13800138000':'',
            '':'',
        }
        for text, expected in cases.items():
            with self.subTest(text=text): self.assertEqual(sms_api.verification_code(text),expected)
