import ast
import unittest
from pathlib import Path
from unittest.mock import Mock
from fastapi import HTTPException
import re
import posixpath
import shlex
from typing import Any

# Extract exact production functions without running app startup/scheduled operations.
tree=ast.parse(Path('/app/app/main.py').read_text())
names={'validate_reverse_proxy_payload','add_remote_reverse_proxy'}
ns=dict(Any=Any,re=re,posixpath=posixpath,shlex=shlex,HTTPException=HTTPException)
exec(compile(ast.Module(body=[x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name in names],type_ignores=[]),'production-functions','exec'),ns)

class Tests(unittest.TestCase):
 def test_address_normalization(self):
  p=ns['validate_reverse_proxy_payload'](dict(name='nas18001',port=18001,server_name='dashboard.example.com',upstream='192.0.2.20：8001'),{})
  self.assertEqual(p['upstream'],'http://192.0.2.20:8001')
 def run_add(self,entries,listeners):
  settings=dict(sites_available_dir='/available',sites_enabled_dir='/enabled')
  client=Mock();sftp=client.open_sftp.return_value;sftp.lstat.side_effect=IOError
  out=Mock();out.channel.recv_exit_status.return_value=0;out.read.return_value=b''
  ss=Mock();ss.channel.recv_exit_status.return_value=0;ss.read.return_value=listeners
  err=Mock();err.read.return_value=b''
  client.exec_command.side_effect=[(None,out,err),(None,ss,err)]
  ns.update(remote_ssh_client=lambda s:client,read_remote_reverse_proxies=Mock(side_effect=[[],entries]),_sftp_write_temp=lambda *a:'/tmp/temp')
  return ns['add_remote_reverse_proxy'](dict(name='nas18001',port=18001,server_name='dashboard.example.com',upstream='192.0.2.20:8001',ssl=False),settings)
 def test_missing_readback_is_failure(self):
  with self.assertRaisesRegex(RuntimeError,'nginx -T'):self.run_add([],b'listener')
 def test_missing_listener_is_failure(self):
  with self.assertRaisesRegex(RuntimeError,'监听'):self.run_add(self.entries(),b'')
 def entries(self):return [dict(id='nasbridge-nas18001-18001.conf',listen=[18001],server_name=['dashboard.example.com'],proxy_pass=['http://192.0.2.20:8001'])]
 def test_verified_success(self):self.assertTrue(self.run_add(self.entries(),b'LISTEN 0 511 0.0.0.0:18001')['verified'])
if __name__=='__main__':unittest.main()
