import ast
import posixpath
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from unittest.mock import Mock
import shlex
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[1] / "app" / "main.py"
NAMES = {"parse_nginx_reverse_proxies", "label_reverse_proxies", "update_remote_reverse_proxy"}
ns = dict(Any=Any, re=re, posixpath=posixpath, urlparse=urlparse,
          shlex=shlex, uuid=uuid, HTTPException=HTTPException,
          utc_now=lambda: datetime.now(timezone.utc))
exec(compile(ast.Module(body=[n for n in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                              if isinstance(n, ast.FunctionDef) and n.name in NAMES],
                        type_ignores=[]), str(SOURCE), "exec"), ns)

CONTENT = "server {\n    listen 443 ssl;\n    server_name example.com;\n    location / {\n        proxy_pass http://192.0.2.1:8000;\n    }\n}\n"

class ProxyRenameTests(unittest.TestCase):
    def parse(self, content):
        return ns["label_reverse_proxies"](ns["parse_nginx_reverse_proxies"](
            "# configuration file /enabled/example.conf:\n" + content),
            {"services": [{"name": "catalog-name", "port": 443, "target": "192.0.2.1"}]})

    def update(self, content, name):
        client = Mock()
        sftp = client.open_sftp.return_value
        sftp.normalize.return_value = "/available/example.conf"
        sftp.open.return_value.__enter__ = Mock(return_value=Mock(read=lambda: content.encode()))
        sftp.open.return_value.__exit__ = Mock(return_value=False)
        output = Mock()
        output.read.return_value = b""
        output.channel.recv_exit_status.return_value = 0
        client.exec_command.return_value = (None, output, output)
        written = []
        def write(sftp, path, data, mode):
            written.append(data.decode())
            return "/available/temp"
        ns.update(remote_ssh_client=lambda settings: client, _sftp_write_temp=write,
                  read_remote_reverse_proxies=lambda settings: self.parse(written[-1] if written else content))
        result = ns["update_remote_reverse_proxy"]("example.conf", {"name": name},
                    {"sites_enabled_dir": "/enabled", "nginx_backup_dir": "/backups"})
        return result, written

    def test_rename_roundtrip_and_catalog_override(self):
        result, written = self.update(CONTENT, "New name {test}")
        self.assertTrue(result["verified"])
        self.assertEqual(self.parse(written[0])[0]["service_name"], "New name {test}")
        self.assertIn("    listen 443 ssl;", written[0])
        self.assertEqual(self.parse(written[0])[0]["id"], "example.conf")

    def test_repeated_rename_replaces_marker(self):
        _, first = self.update(CONTENT, "First")
        _, second = self.update(first[0], "Second")
        self.assertEqual(second[0].count("# nasbridge-service-name:"), 1)
        self.assertEqual(self.parse(second[0])[0]["service_name"], "Second")
        result, unchanged = self.update(second[0], "Second")
        self.assertTrue(result["verified"])
        self.assertEqual(unchanged, [])

    def test_reject_empty_long_and_multiline_names(self):
        for name in ["", "x" * 101, "name\nserver {}", "name\x00"]:
            with self.subTest(name=name), self.assertRaises(HTTPException) as error:
                self.update(CONTENT, name)
            self.assertEqual(error.exception.status_code, 400)

if __name__ == "__main__":
    unittest.main()
