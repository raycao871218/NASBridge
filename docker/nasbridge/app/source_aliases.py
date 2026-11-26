"""Shared, persistent labels for Mihomo connection sources."""
import ipaddress
import json
import os
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

router = APIRouter()
lock = threading.Lock()


def storage_path():
    from .main import DATA_DIR
    return DATA_DIR / "mihomo-source-aliases.json"


def read_rules():
    path = storage_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def write_rules(rules):
    path = storage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def validate_rule(payload):
    try:
        ip = str(ipaddress.ip_address(str(payload.get("ip", "")).strip()))
    except ValueError:
        raise HTTPException(422, "请输入有效的 IPv4 或 IPv6 地址") from None
    port = payload.get("port")
    if port in (None, ""):
        port = None
    elif type(port) is not int or not 1 <= port <= 65535:
        raise HTTPException(422, "端口须为 1–65535 的整数，或留空匹配整个 IP")
    name = payload.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise HTTPException(422, "别名须为 1–80 个字符")
    return {"ip": ip, "port": port, "name": name.strip()}


def label_connections(items, rules):
    lookup = {(r["ip"], r["port"]): r for r in rules}
    for item in items:
        try:
            ip = str(ipaddress.ip_address(item["source_ip"]))
        except ValueError:
            ip = item["source_ip"]
        try:
            port = int(item["source_port"])
        except (ValueError, TypeError):
            port = None
        rule = lookup.get((ip, port)) or lookup.get((ip, None))
        item["source_alias"] = rule["name"] if rule else ""
        item["source_key"] = "alias:" + rule["id"] if rule else "ip:" + ip
    return items


@router.get("/api/mihomo/source-aliases")
def get_aliases():
    with lock:
        return {"items": read_rules()}


@router.put("/api/mihomo/source-aliases")
def save_alias(payload: dict = Body(...)):
    rule = validate_rule(payload)
    with lock:
        rules = read_rules()
        existing = next((r for r in rules if r["ip"] == rule["ip"] and r["port"] == rule["port"]), None)
        rule["id"] = existing["id"] if existing else uuid.uuid4().hex
        rules = [r for r in rules if r["id"] != rule["id"]] + [rule]
        write_rules(rules)
        return {"items": rules}


@router.delete("/api/mihomo/source-aliases/{rule_id}")
def delete_alias(rule_id: str):
    with lock:
        rules = [r for r in read_rules() if r["id"] != rule_id]
        write_rules(rules)
        return {"items": rules}
