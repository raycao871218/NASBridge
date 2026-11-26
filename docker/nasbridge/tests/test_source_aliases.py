import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app import source_aliases as aliases


class SourceAliasesTest(unittest.TestCase):
    def test_specific_port_wins_and_other_ports_use_device(self):
        rules = [dict(id="phone", ip="::1", port=None, name="手机"),
                 dict(id="app", ip="::1", port=7890, name="应用")]
        items = [dict(source_ip="0:0:0:0:0:0:0:1", source_port="7890"),
                 dict(source_ip="::1", source_port=1234),
                 dict(source_ip="192.0.2.1", source_port=7890)]
        aliases.label_connections(items, rules)
        self.assertEqual([c["source_alias"] for c in items], ["应用", "手机", ""])
        self.assertEqual([c["source_key"] for c in items], ["alias:app", "alias:phone", "ip:192.0.2.1"])

    def test_persistence_update_and_delete(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(aliases, "storage_path", return_value=Path(directory) / "aliases.json"):
            first = aliases.save_alias(dict(ip="192.0.2.1", port=None, name="手机"))["items"][0]
            result = aliases.save_alias(dict(ip="192.0.2.1", port=None, name="新手机"))["items"]
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["id"], first["id"])
            self.assertEqual(aliases.get_aliases()["items"][0]["name"], "新手机")
            self.assertEqual(aliases.delete_alias(first["id"]), {"items": []})

    def test_validation(self):
        for change in [dict(ip="invalid"), dict(port=True), dict(port=0), dict(port=65536), dict(port="80"), dict(name=" ")]:
            with self.assertRaises(HTTPException):
                aliases.validate_rule(dict(dict(ip="192.0.2.1", port=None, name="手机"), **change))
