from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import posixpath
import re
import shlex
import socket
import stat
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import paramiko
import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("NASBRIDGE_CONFIG", "/app/config/config.yaml"))
DATA_DIR = Path(os.getenv("NASBRIDGE_DATA_DIR", "/app/data"))
SCHEDULE_PATH = DATA_DIR / "certificate-schedule.yaml"
APPLICATIONS_PATH = DATA_DIR / "applications.yaml"
DAILY_TASK_STATE_PATH = DATA_DIR / "daily-task-state.yaml"
daily_task_state_lock = threading.Lock()
state_table_lock = threading.Lock()
state_table_ready = False
LOCAL_TIMEZONE = os.getenv("TZ", "Asia/Shanghai")
MIHOMO_CONTROL_URL = os.getenv("MIHOMO_CONTROL_URL", "http://mihomo-control:8001").rstrip("/")

from .aliyun import router as aliyun_router

app = FastAPI(title="NASBridge", version="0.1.0")
app.include_router(aliyun_router)
from .zerotier import router as zerotier_router
app.include_router(zerotier_router)
from .mobile_api import router as mobile_router
app.include_router(mobile_router)
from .android_setup import router as android_setup_router
app.include_router(android_setup_router)
from .daily_task_control import router as daily_task_control_router, manual_control_status
app.include_router(daily_task_control_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
certificate_sync_lock = asyncio.Lock()
scheduler_task: asyncio.Task[None] | None = None

STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.nasbridge_state (
    state_key TEXT PRIMARY KEY,
    state_value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def ensure_state_table() -> None:
    global state_table_ready
    if state_table_ready:
        return
    with state_table_lock:
        if state_table_ready:
            return
        connection = postgres_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(STATE_TABLE_SQL)
        finally:
            connection.close()
        state_table_ready = True

def postgres_connection(application_name: str = "nasbridge-state"):
    return psycopg2.connect(
        host=os.getenv("NASBRIDGE_POSTGRES_HOST", "host.docker.internal"),
        port=int(os.getenv("NASBRIDGE_POSTGRES_PORT", "5432")),
        dbname=os.getenv("NASBRIDGE_POSTGRES_DATABASE", "nasbridge"),
        user=os.getenv("NASBRIDGE_POSTGRES_USER", "nasbridge"),
        password=os.getenv("NASBRIDGE_POSTGRES_PASSWORD", ""),
        connect_timeout=4,
        application_name=application_name,
    )


def json_compatible(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_compatible(item) for item in value]
    return value

def read_legacy_state(path: Path, default: Any) -> Any:
    try:
        stored = yaml.safe_load(path.read_text(encoding="utf-8"))
        return default if stored is None else json_compatible(stored)
    except FileNotFoundError:
        return default
    except Exception as exc:
        logging.warning("读取本地状态备份 %s 失败：%s: %s", path, type(exc).__name__, exc)
        return default


def write_legacy_state(path: Path, value: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def load_persisted_state(key: str, path: Path, default: Any) -> Any:
    try:
        ensure_state_table()
        connection = postgres_connection()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT state_value FROM public.nasbridge_state WHERE state_key = %s", (key,))
                    row = cursor.fetchone()
                    if row is not None:
                        return row[0]
                    legacy = read_legacy_state(path, default)
                    cursor.execute(
                        "INSERT INTO public.nasbridge_state (state_key, state_value) VALUES (%s, %s::jsonb) ON CONFLICT (state_key) DO NOTHING",
                        (key, json.dumps(legacy, ensure_ascii=False)),
                    )
                    return legacy
        finally:
            connection.close()
    except Exception as exc:
        logging.warning("读取 NASBridge 状态 %s 失败，使用本地备份：%s: %s", key, type(exc).__name__, exc)
        return read_legacy_state(path, default)


def save_persisted_state(key: str, path: Path, value: Any) -> None:
    ensure_state_table()
    connection = postgres_connection()
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO public.nasbridge_state (state_key, state_value, updated_at) VALUES (%s, %s::jsonb, now()) ON CONFLICT (state_key) DO UPDATE SET state_value = EXCLUDED.state_value, updated_at = now()",
                    (key, json.dumps(json_compatible(value), ensure_ascii=False)),
                )
    finally:
        connection.close()
    try:
        write_legacy_state(path, value)
    except Exception as exc:
        logging.warning("已保存状态到 PostgreSQL，但更新本地备份失败：%s: %s", type(exc).__name__, exc)


def initialize_persisted_state() -> None:
    try:
        ensure_state_table()
    except Exception as exc:
        logging.warning("PostgreSQL 状态表暂不可用，先从本地备份启动：%s: %s", type(exc).__name__, exc)
        return
    for key, path, default in (
        ("certificate_schedule", SCHEDULE_PATH, default_schedule()),
        ("applications", APPLICATIONS_PATH, {"scanned_at": None, "applications": []}),
        ("daily_task_state", DAILY_TASK_STATE_PATH, {"hidden_runs": [], "read_summaries": []}),
    ):
        load_persisted_state(key, path, default)




def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def default_schedule() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "daily",
        "time": "03:30",
        "days": [0, 1, 2, 3, 4, 5, 6],
        "month_day": 1,
        "last_run_slot": None,
        "last_run_at": None,
        "last_result": None,
        "last_error": None,
    }


def load_schedule() -> dict[str, Any]:
    schedule = default_schedule()
    stored = load_persisted_state("certificate_schedule", SCHEDULE_PATH, schedule)
    if isinstance(stored, dict):
        schedule.update(stored)
    return schedule


def save_schedule(schedule: dict[str, Any]) -> None:
    save_persisted_state("certificate_schedule", SCHEDULE_PATH, schedule)


def normalize_schedule(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    schedule = default_schedule()
    schedule.update(existing or {})
    mode = str(payload.get("mode", schedule["mode"]))
    if mode not in {"daily", "weekdays", "weekends", "custom", "monthly"}:
        raise HTTPException(status_code=400, detail="计划类型无效")
    time_text = str(payload.get("time", schedule["time"]))
    try:
        parsed_time = datetime.strptime(time_text, "%H:%M")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="时间必须是 HH:MM，例如 03:30") from exc
    try:
        month_day = int(payload.get("month_day", schedule.get("month_day", 1)))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="每月日期必须是 1 到 28") from exc
    if not 1 <= month_day <= 28:
        raise HTTPException(status_code=400, detail="每月日期必须是 1 到 28")
    days_value = payload.get("days", schedule.get("days", []))
    try:
        days = sorted({int(day) for day in days_value})
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="星期设置无效") from exc
    if any(day < 0 or day > 6 for day in days):
        raise HTTPException(status_code=400, detail="星期必须使用 0 到 6，周一为 0")
    if mode == "daily":
        days = list(range(7))
    elif mode == "weekdays":
        days = list(range(5))
    elif mode == "weekends":
        days = [5, 6]
    elif mode == "custom" and not days:
        raise HTTPException(status_code=400, detail="自定义计划至少选择一天")
    schedule.update({"enabled": bool(payload.get("enabled", schedule["enabled"])), "mode": mode, "time": parsed_time.strftime("%H:%M"), "days": days, "month_day": month_day})
    return schedule
def schedule_description(schedule: dict[str, Any]) -> str:
    labels = ["一", "二", "三", "四", "五", "六", "日"]
    mode = schedule.get("mode", "daily")
    time_text = str(schedule.get("time", "03:30"))
    if mode == "daily":
        return f"每天 {time_text}"
    if mode == "weekdays":
        return f"工作日 {time_text}"
    if mode == "weekends":
        return f"周末 {time_text}"
    if mode == "monthly":
        return f"每月 {int(schedule.get('month_day', 1))} 日 {time_text}"
    days = schedule.get("days", [])
    return f"每周{'、'.join(labels[day] for day in days if 0 <= day < 7)} {time_text}"
def schedule_cron(schedule: dict[str, Any]) -> str:
    hour, minute = str(schedule.get("time", "03:30")).split(":")
    if schedule.get("mode") == "monthly":
        return f"{int(minute)} {int(hour)} {int(schedule.get('month_day', 1))} * *"
    days = schedule.get("days", list(range(7)))
    day_field = "*" if len(days) == 7 else ",".join(str((day + 1) % 7) for day in days)
    return f"{int(minute)} {int(hour)} * * {day_field}"
def next_schedule_run(schedule: dict[str, Any]) -> str | None:
    if not schedule.get("enabled"):
        return None
    try:
        timezone_info = ZoneInfo(LOCAL_TIMEZONE)
    except Exception:
        timezone_info = datetime.now().astimezone().tzinfo
    now = datetime.now(timezone_info)
    hour, minute = (int(part) for part in str(schedule.get("time", "03:30")).split(":"))
    if schedule.get("mode") == "monthly":
        day = int(schedule.get("month_day", 1))
        candidate = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            year, month = now.year, now.month + 1
            if month > 12:
                year, month = year + 1, 1
            candidate = candidate.replace(year=year, month=month, day=day)
        return candidate.isoformat()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    allowed_days = set(schedule.get("days", list(range(7))))
    for _ in range(8):
        if candidate.weekday() in allowed_days:
            return candidate.isoformat()
        candidate += timedelta(days=1)
    return None
def schedule_state() -> dict[str, Any]:
    schedule = load_schedule()
    schedule["description"] = schedule_description(schedule)
    schedule["cron"] = schedule_cron(schedule)
    schedule["next_run"] = next_schedule_run(schedule)
    return schedule


def load_applications(config: dict[str, Any]) -> list[dict[str, Any]]:
    stored = load_persisted_state("applications", APPLICATIONS_PATH, {})
    if isinstance(stored, dict) and isinstance(stored.get("applications"), list) and stored.get("scanned_at"):
        return stored["applications"]
    return list(config.get("applications", []))


def save_applications(applications: list[dict[str, Any]], scanned_at: str) -> None:
    save_persisted_state("applications", APPLICATIONS_PATH, {"scanned_at": scanned_at, "applications": applications})


def application_registry_state() -> dict[str, Any]:
    stored = load_persisted_state("applications", APPLICATIONS_PATH, {})
    if isinstance(stored, dict) and stored.get("scanned_at"):
        return {"source": "saved_scan", "scanned_at": stored.get("scanned_at")}
    return {"source": "config", "scanned_at": None}


def discovered_application_records(snapshot: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    applications = [dict(item) for item in existing]
    grouped: dict[str, dict[str, Any]] = {}
    for connection in snapshot.get("connections", []):
        process = str(connection.get("process") or "").strip()
        source_ip = str(connection.get("source_ip") or "").strip()
        if process:
            process_name = Path(process).name or process
            key = f"process:{process.lower()}"
            name = process_name
            process_terms = [process_name.lower()]
        elif source_ip:
            key = f"source:{source_ip}"
            name = f"来源 {source_ip}"
            process_terms = []
        else:
            continue
        item = grouped.setdefault(key, {"name": name, "source_ips": set(), "process_contains": set(), "routes": set()})
        if source_ip:
            item["source_ips"].add(source_ip)
        item["process_contains"].update(process_terms)
        item["routes"].add(str(connection.get("route") or "unknown"))

    for discovered in grouped.values():
        process_terms = sorted(discovered["process_contains"])
        source_ips = sorted(discovered["source_ips"])
        match = next((item for item in applications if any(term in {str(value).lower() for value in item.get("process_contains", [])} for term in process_terms) or set(source_ips) & {str(value) for value in item.get("source_ips", [])}), None)
        if match is None:
            slug_source = process_terms[0] if process_terms else (source_ips[0] if source_ips else discovered["name"])
            slug = re.sub(r"[^a-z0-9]+", "-", slug_source.lower()).strip("-") or "app"
            base_id = f"discovered-{slug}"
            app_id = base_id
            index = 2
            while any(item.get("id") == app_id for item in applications):
                app_id = f"{base_id}-{index}"
                index += 1
            match = {
                "id": app_id,
                "name": discovered["name"],
                "expected_mode": "unknown",
                "source_ips": [],
                "process_contains": [],
                "note": "由 Mihomo 活动连接扫描记录",
                "discovered": True,
            }
            applications.append(match)
        match["source_ips"] = sorted(set(str(value) for value in match.get("source_ips", [])) | set(source_ips))
        match["process_contains"] = sorted(set(str(value).lower() for value in match.get("process_contains", [])) | set(process_terms))
        match["last_seen_routes"] = sorted(discovered["routes"])
    return applications


def mihomo_config_path(config: dict[str, Any]) -> Path:
    path = Path(str(config.get("mihomo", {}).get("config_path", "/host/mihomo/config.yaml"))).resolve()
    allowed = Path("/host/mihomo/config.yaml").resolve()
    if path != allowed:
        raise RuntimeError("Mihomo 配置路径被限制为 /host/mihomo/config.yaml")
    return path


def reverse_proxy_settings(config: dict[str, Any]) -> dict[str, Any]:
    remote_items = config.get("certificates", {}).get("remote", [])
    remote = next((item for item in remote_items if item.get("ssh_host")), {})
    settings = dict(config.get("reverse_proxy", {}))
    settings.setdefault("ssh_host", remote.get("ssh_host"))
    settings.setdefault("ssh_user", remote.get("ssh_user", "root"))
    settings.setdefault("ssh_port", remote.get("ssh_port", 22))
    settings.setdefault("ssh_key_path", remote.get("ssh_key_path", "/root/.ssh/id_rsa"))
    settings.setdefault("ssh_known_hosts", remote.get("ssh_known_hosts", "/root/.ssh/known_hosts"))
    settings.setdefault("sites_available_dir", "/etc/nginx/sites-available")
    settings.setdefault("sites_enabled_dir", "/etc/nginx/sites-enabled")
    settings.setdefault("nginx_backup_dir", "/root/certs/backups/nasbridge/nginx")
    settings["local_certificates"] = list(config.get("certificates", {}).get("local", []))
    configured_remote = [
        item for item in remote_items
        if item.get("ssh_host") == settings.get("ssh_host")
    ]
    extra_remote = list(settings.get("remote_certificates", []))
    settings["remote_certificates"] = [
        {"ssh_host": settings.get("ssh_host"), **item} for item in configured_remote + extra_remote
    ]
    return settings


def certificate_domains(item: dict[str, Any]) -> list[str]:
    path = Path(str(item.get("path") or ""))
    certificate = x509.load_pem_x509_certificate(path.read_bytes(), default_backend())
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if not names:
        names = [attribute.value for attribute in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    return sorted({str(name).lower().rstrip(".") for name in names if name})


def dns_name_matches(pattern: str, hostname: str) -> bool:
    pattern = pattern.lower().rstrip(".")
    hostname = hostname.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname.count(".") == suffix.count(".")
    return pattern == hostname


def certificate_details_from_bytes(data: bytes) -> tuple[Any, list[str], str, str]:
    certificate = x509.load_pem_x509_certificate(data, default_backend())
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    if not names:
        names = [attribute.value for attribute in certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    domains = sorted({str(name).lower().rstrip(".") for name in names if name})
    expires = getattr(certificate, "not_valid_after_utc", None)
    if expires is None:
        expires = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    starts = getattr(certificate, "not_valid_before_utc", None)
    if starts is None:
        starts = certificate.not_valid_before.replace(tzinfo=timezone.utc)
    if starts > now:
        status = "not_yet_valid"
    elif expires <= now:
        status = "expired"
    elif (expires - now).total_seconds() <= 30 * 86400:
        status = "expiring"
    else:
        status = "valid"
    fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
    return certificate, domains, expires.isoformat(), status


def certificate_domains(item: dict[str, Any]) -> list[str]:
    path = Path(str(item.get("path") or ""))
    return certificate_details_from_bytes(path.read_bytes())[1]


def dns_name_matches(pattern: str, hostname: str) -> bool:
    pattern = pattern.lower().rstrip(".")
    hostname = hostname.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return hostname.endswith(suffix) and hostname.count(".") == suffix.count(".")
    return pattern == hostname


def reverse_proxy_certificate_options(settings: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    local_by_id = {str(item.get("id")): item for item in settings.get("local_certificates", [])}
    paired_local_ids: set[str] = set()
    client = remote_ssh_client(settings)
    sftp = client.open_sftp()
    try:
        for item in settings.get("remote_certificates", []):
            cert_path = str(item.get("remote_cert_path") or "")
            key_path = str(item.get("remote_key_path") or "")
            cert_id = str(item.get("id") or item.get("local_ref") or cert_path)
            name = str(item.get("name") or item.get("id") or cert_path)
            domains = [str(item.get("domain"))] if item.get("domain") else []
            expires_at = None
            status = "unavailable"
            key_matches = False
            fingerprint = None
            try:
                with sftp.open(cert_path, "rb") as cert_file:
                    cert_data = cert_file.read()
                _certificate, domains, expires_at, status = certificate_details_from_bytes(cert_data)
                sftp.stat(key_path)
                cert_pub = shlex.quote(cert_path)
                key_pub = shlex.quote(key_path)
                check_command = (
                    f"cert_pub=$(openssl x509 -in {cert_pub} -pubkey -noout 2>/dev/null | "
                    "openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}'); "
                    f"key_pub=$(openssl pkey -in {key_pub} -pubout -outform DER 2>/dev/null | "
                    "sha256sum | awk '{print $1}'); test -n \"$cert_pub\" && test \"$cert_pub\" = \"$key_pub\""
                )
                _stdin, stdout, _stderr = client.exec_command(check_command, timeout=12)
                key_matches = stdout.channel.recv_exit_status() == 0
                fingerprint = _certificate.fingerprint(hashes.SHA256()).hex()
            except Exception:
                pass

            local = local_by_id.get(str(item.get("local_ref") or ""))
            source = "远端服务器"
            if local:
                try:
                    local_certificate = x509.load_pem_x509_certificate(Path(str(local.get("path"))).read_bytes(), default_backend())
                    if fingerprint and local_certificate.fingerprint(hashes.SHA256()).hex() == fingerprint:
                        source = "NAS 本地 + 远端服务器"
                        paired_local_ids.add(str(local.get("id")))
                except Exception:
                    pass
            available = bool(cert_path and key_path and key_matches and status in {"valid", "expiring"})
            options.append({
                "id": cert_id,
                "name": name,
                "domains": domains,
                "expires_at": expires_at,
                "status": status,
                "available": available,
                "key_matches": key_matches,
                "source": source,
                "fingerprint": fingerprint,
            })

        for item in settings.get("local_certificates", []):
            cert_id = str(item.get("id") or item.get("name") or "")
            if cert_id in paired_local_ids:
                continue
            try:
                certificate, domains, expires_at, status = certificate_details_from_bytes(Path(str(item.get("path") or "")).read_bytes())
                fingerprint = certificate.fingerprint(hashes.SHA256()).hex()
            except Exception:
                domains, expires_at, status, fingerprint = [], None, "unavailable", None
            options.append({
                "id": f"local:{cert_id}",
                "name": str(item.get("name") or cert_id),
                "domains": domains,
                "expires_at": expires_at,
                "status": status,
                "available": False,
                "key_matches": False,
                "source": "仅 NAS 本地，未配置远端副本",
                "fingerprint": fingerprint,
            })
    finally:
        sftp.close()
        client.close()

    # A current fullchain and its leaf certificate may be stored under two names.
    # Keep one selector row per actual certificate and list all known locations.
    grouped: dict[str, dict[str, Any]] = {}
    for option in options:
        key = option.get("fingerprint") or f"{option['id']}:{option['name']}"
        if key not in grouped:
            grouped[key] = option
            continue
        current = grouped[key]
        sources = {current.get("source"), option.get("source")}
        current["source"] = " + ".join(sorted(value for value in sources if value))
        current["available"] = current["available"] or option["available"]
        if not current.get("expires_at") or (option.get("expires_at") and option["expires_at"] > current["expires_at"]):
            current["expires_at"] = option["expires_at"]
            current["status"] = option["status"]
        if not current["available"] and option["available"]:
            current["id"] = option["id"]
            current["name"] = option["name"]
    return sorted(grouped.values(), key=lambda option: (option.get("domains", [""])[0] if option.get("domains") else "", option["name"]))


def select_reverse_proxy_certificate(server_name: str, settings: dict[str, Any], certificate_id: str | None = None) -> dict[str, Any]:
    options = reverse_proxy_certificate_options(settings)
    sources = {str(item.get("id")): item for item in settings.get("remote_certificates", [])}
    if certificate_id:
        option = next((item for item in options if item["id"] == certificate_id), None)
        if not option:
            raise HTTPException(status_code=400, detail="所选证书不存在")
        if not option["available"]:
            raise HTTPException(status_code=400, detail=f"证书 {option['name']} 不可用、已过期或私钥不匹配")
        if not any(dns_name_matches(domain, server_name) for domain in option["domains"]):
            raise HTTPException(status_code=400, detail=f"证书 {option['name']} 不包含域名 {server_name}")
        source = sources.get(option["id"])
        if not source:
            raise HTTPException(status_code=400, detail="证书没有远端 Nginx 路径")
        return {**source, "path": source["remote_cert_path"], "key_path": source["remote_key_path"]}

    matches = []
    for option in options:
        if not option["available"] or not any(dns_name_matches(domain, server_name) for domain in option["domains"]):
            continue
        source = sources.get(option["id"])
        if not source:
            continue
        score = 2 if server_name.lower().rstrip(".") in option["domains"] else 1
        matches.append((score, source, option))
    if not matches:
        raise HTTPException(status_code=400, detail=f"没有有效证书匹配域名 {server_name}；请检查证书和私钥，或关闭 HTTPS")
    best_score = max(match[0] for match in matches)
    best = [match for match in matches if match[0] == best_score]
    if len(best) > 1:
        names = "、".join(match[2]["name"] for match in best)
        raise HTTPException(status_code=409, detail=f"域名 {server_name} 匹配多个证书，请在表单中明确选择：{names}")
    source = best[0][1]
    return {**source, "path": source["remote_cert_path"], "key_path": source["remote_key_path"]}


def parse_nginx_reverse_proxies(nginx_dump: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    current_file: str | None = None
    block: dict[str, Any] | None = None
    depth = 0
    for raw_line in nginx_dump.splitlines():
        header = re.match(r"# configuration file (.+):$", raw_line.strip())
        if header:
            current_file = header.group(1)
        line = raw_line.strip()
        if block is None and re.match(r"server\s*\{", line):
            block = {"file": current_file, "listen": [], "server_name": [], "proxy_pass": [], "ssl": False}
            depth = line.count("{") - line.count("}")
            continue
        if block is None:
            continue
        listen_match = re.match(r"listen\s+([^;]+);", line)
        if listen_match:
            value = listen_match.group(1).strip()
            token = value.split()[0]
            port_text = token.rsplit(":", 1)[-1]
            if port_text.isdigit():
                block["listen"].append(int(port_text))
            block["ssl"] = block["ssl"] or " ssl" in f" {value} "
        name_match = re.match(r"server_name\s+([^;]+);", line)
        if name_match:
            block["server_name"].extend(name_match.group(1).split())
        proxy_match = re.match(r"proxy_pass\s+([^;]+);", line)
        if proxy_match:
            block["proxy_pass"].append(proxy_match.group(1).strip())
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            if block["proxy_pass"]:
                block["id"] = posixpath.basename(str(block.get("file") or ""))
                block["remote_port"] = block["listen"][0] if block["listen"] else None
                local_ports = []
                for upstream in block["proxy_pass"]:
                    try:
                        parsed = urlparse(upstream)
                        local_ports.append(parsed.port or (443 if parsed.scheme == "https" else 80))
                    except ValueError:
                        local_ports.append(None)
                block["local_port"] = local_ports[0] if local_ports else None
                block["local_ports"] = local_ports
                block.pop("file", None)
                results.append(block)
            block = None
            depth = 0
    return results


def remote_ssh_client(settings: dict[str, Any]) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    known_hosts = str(settings.get("ssh_known_hosts", "/root/.ssh/known_hosts"))
    if Path(known_hosts).exists():
        client.load_host_keys(known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(
        hostname=str(settings["ssh_host"]),
        port=int(settings.get("ssh_port", 22)),
        username=str(settings.get("ssh_user", "root")),
        key_filename=str(settings.get("ssh_key_path", "/root/.ssh/id_rsa")),
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    return client


def label_reverse_proxies(items: list[dict[str, Any]], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    hosts = {str(item.get('name', '')).lower(): str(item.get('ip', '')) for item in catalog.get('zt_ip', [])}
    for item in items:
        port = item.get('remote_port')
        upstream_hosts = {urlparse(url).hostname for url in item.get('proxy_pass', [])}
        names = set()
        for service in catalog.get('services', []):
            if str(service.get('port')) != str(port) or not service.get('name'):
                continue
            target = str(service.get('target', ''))
            resolved = hosts.get(target.lower().removesuffix('_ip'), target)
            if resolved and resolved not in upstream_hosts:
                continue
            names.add(str(service['name']))
        if len(names) == 1:
            item['service_name'] = names.pop()
            item['service_name_source'] = 'catalog'
            continue
        # Existing generator: <domain-prefix>-<service>-<port>.conf.
        stem = str(item.get('id', '')).removesuffix('.conf')
        suffix = f'-{port}'
        prefixes = ['nasbridge'] + [domain.split('.')[0] for domain in item.get('server_name', [])]
        name = next((stem[len(prefix)+1:-len(suffix)] for prefix in prefixes
                     if stem.startswith(prefix + '-') and stem.endswith(suffix)), '')
        item['service_name'] = name or '未命名服务'
        item['service_name_source'] = 'entry' if name else 'unknown'
    return items


def read_remote_reverse_proxies(settings: dict[str, Any]) -> list[dict[str, Any]]:
    client = remote_ssh_client(settings)
    try:
        _stdin, stdout, stderr = client.exec_command("nginx -T 2>&1", timeout=20)
        exit_code = stdout.channel.recv_exit_status()
        output = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")
        if exit_code != 0:
            raise RuntimeError(output[-4000:])
        catalog_path = Path(str(settings.get('services_catalog_path', '/app/services/domains_config.yaml')))
        catalog = yaml.safe_load(catalog_path.read_text(encoding='utf-8')) if catalog_path.exists() else {}
        return label_reverse_proxies(parse_nginx_reverse_proxies(output), catalog or {})
    finally:
        client.close()


def validate_reverse_proxy_payload(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,50}", name):
        raise HTTPException(status_code=400, detail="名称只能包含字母、数字、下划线和短横线")
    try:
        port = int(payload.get("port"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="监听端口必须是数字") from exc
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="监听端口必须在 1 到 65535 之间")
    server_name = str(payload.get("server_name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}", server_name):
        raise HTTPException(status_code=400, detail="域名格式无效")
    upstream = str(payload.get("upstream", "")).strip().replace("：", ":")
    if upstream and "://" not in upstream:
        upstream = "http://" + upstream
    if re.search(r"[\r\n;`$]", upstream) or not re.match(r"^https?://[^\s]+$", upstream):
        raise HTTPException(status_code=400, detail="后端地址必须是 http:// 或 https:// URL，且不能包含特殊命令字符")
    return {"name": name, "port": port, "server_name": server_name, "upstream": upstream, "ssl": bool(payload.get("ssl", True))}


def add_remote_reverse_proxy(payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    item = validate_reverse_proxy_payload(payload, settings)
    current = read_remote_reverse_proxies(settings)
    if any(item["port"] in entry.get("listen", []) for entry in current):
        raise HTTPException(status_code=409, detail=f"远端 Nginx 已经使用监听端口 {item['port']}")
    sites_available = str(settings["sites_available_dir"])
    sites_enabled = str(settings["sites_enabled_dir"])
    filename = f"nasbridge-{item['name']}-{item['port']}.conf"
    remote_file = posixpath.join(sites_available, filename)
    remote_link = posixpath.join(sites_enabled, filename)
    ssl_lines = ""
    if item["ssl"]:
        certificate_item = select_reverse_proxy_certificate(
            item["server_name"], settings, str(payload.get("certificate_id") or "") or None
        )
        certificate = str(certificate_item.get("path") or "")
        certificate_key = str(certificate_item.get("key_path") or "")
        ssl_lines = f"    ssl_certificate     {certificate};\n    ssl_certificate_key {certificate_key};\n    ssl_protocols       TLSv1.2 TLSv1.3;\n"
    listen = f"listen {item['port']}{' ssl' if item['ssl'] else ''};"
    content = f"""# Managed by NASBridge\nserver {{\n    {listen}\n    server_name {item['server_name']};\n\n{ssl_lines}\n    location / {{\n        proxy_pass {item['upstream']};\n        proxy_set_header Host $host;\n        proxy_set_header X-Real-IP $remote_addr;\n        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n        proxy_set_header X-Forwarded-Proto $scheme;\n    }}\n}}\n"""

    client = remote_ssh_client(settings)
    sftp = client.open_sftp()
    temporary: str | None = None
    stale_managed_file = False
    stale_backup_file: str | None = None
    try:
        try:
            sftp.lstat(remote_link)
            raise HTTPException(status_code=409, detail=f"远端启用入口已存在：{remote_link}")
        except IOError:
            pass
        try:
            sftp.lstat(remote_file)
            with sftp.open(remote_file, "rb") as existing_file:
                managed_marker = existing_file.readline(256).decode("utf-8", errors="replace").strip()
            if managed_marker != "# Managed by NASBridge":
                raise HTTPException(status_code=409, detail=f"同名远端配置已存在且不是 NASBridge 管理的文件：{remote_file}")
            resolved_file = posixpath.normpath(sftp.normalize(remote_file))
            for enabled_name in sftp.listdir(sites_enabled):
                enabled_path = posixpath.join(sites_enabled, enabled_name)
                try:
                    enabled_target = posixpath.normpath(sftp.normalize(enabled_path))
                except IOError:
                    continue
                if enabled_target == resolved_file:
                    raise HTTPException(status_code=409, detail="同名配置仍被 Nginx 启用入口引用，拒绝覆盖")
            stale_managed_file = True
            stamp = utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
            stale_backup_file = posixpath.join(str(settings["nginx_backup_dir"]), f"{filename}.replaced-{stamp}.bak")
        except IOError:
            pass
        temporary = _sftp_write_temp(sftp, remote_file, content.encode("utf-8"), 0o644)
        command_parts = []
        if stale_managed_file and stale_backup_file:
            command_parts.extend([
                f"mkdir -p -- {shlex.quote(str(settings['nginx_backup_dir']))}",
                f"cp -a -- {shlex.quote(remote_file)} {shlex.quote(stale_backup_file)}",
            ])
        command_parts.extend([
            f"mv -f -- {shlex.quote(temporary)} {shlex.quote(remote_file)}",
            f"ln -s -- {shlex.quote(remote_file)} {shlex.quote(remote_link)}",
            "nginx -t",
            "systemctl reload nginx",
        ])
        command = " && ".join(command_parts)
        _stdin, stdout, stderr = client.exec_command(command, timeout=30)
        exit_code = stdout.channel.recv_exit_status()
        output = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")[-4000:]
        if exit_code != 0:
            if stale_managed_file and stale_backup_file:
                cleanup = " && ".join([
                    f"rm -f -- {shlex.quote(remote_link)}",
                    f"if [ -e {shlex.quote(stale_backup_file)} ]; then cp -a -- {shlex.quote(stale_backup_file)} {shlex.quote(remote_file)}; fi",
                ])
            else:
                cleanup = f"rm -f -- {shlex.quote(remote_file)} {shlex.quote(remote_link)}"
            client.exec_command(cleanup, timeout=10)[1].channel.recv_exit_status()
            raise RuntimeError(f"nginx -t/reload 失败（退出码 {exit_code}）：{output}")
        verified = read_remote_reverse_proxies(settings)
        if not any(entry.get("id") == filename and item["port"] in entry.get("listen", [])
                   and item["server_name"] in entry.get("server_name", [])
                   and item["upstream"] in entry.get("proxy_pass", []) for entry in verified):
            raise RuntimeError("reload 后未在 nginx -T 中找到匹配规则；请检查远端 include 配置，不能认定创建成功")
        _stdin, stdout, stderr = client.exec_command(
            f"ss -H -ltn 'sport = :{item['port']}'", timeout=10)
        listeners = stdout.read().decode("utf-8", errors="replace").strip()
        if stdout.channel.recv_exit_status() != 0 or not listeners:
            raise RuntimeError("配置已写入，但未确认端口监听；请读取远端配置核对，不要重复创建")
        return {"status": "created", "verified": True, "message": "反向代理已创建，配置回读与端口监听验证通过", "file": remote_file, "listen": item["port"], "server_name": item["server_name"], "upstream": item["upstream"], "remote_output": output}
    finally:
        if temporary:
            try:
                sftp.remove(temporary)
            except IOError:
                pass
        sftp.close()
        client.close()


def update_remote_reverse_proxy(proxy_id: str, payload: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}\.conf", proxy_id):
        raise HTTPException(status_code=400, detail="反代标识无效")
    entries = [item for item in read_remote_reverse_proxies(settings) if item["id"] == proxy_id]
    if not entries:
        raise HTTPException(status_code=404, detail=f"反代入口不存在，请刷新列表：{proxy_id}")
    if len(entries) != 1 or len(entries[0].get("server_name", [])) != 1 or len(entries[0].get("proxy_pass", [])) != 1:
        raise HTTPException(status_code=409, detail="此配置包含多个域名或后端，暂不支持单独编辑")
    entry = entries[0]
    server_name = str(payload.get("server_name", entry["server_name"][0])).strip()
    upstream = str(payload.get("upstream", entry["proxy_pass"][0])).strip().replace("：", ":")
    if upstream and "://" not in upstream:
        upstream = "http://" + upstream
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}", server_name):
        raise HTTPException(status_code=400, detail="域名格式无效")
    if re.search(r"[\r\n;`$]", upstream) or not re.match(r"^https?://[^\s]+$", upstream):
        raise HTTPException(status_code=400, detail="后端地址必须是 http:// 或 https:// URL，且不能包含特殊命令字符")
    enabled_file = posixpath.join(posixpath.normpath(str(settings["sites_enabled_dir"])), proxy_id)
    backup_dir = str(settings["nginx_backup_dir"])
    stamp = utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_file = posixpath.join(backup_dir, f"{proxy_id}.{stamp}.bak")
    client = remote_ssh_client(settings)
    sftp = client.open_sftp()
    temporary = None
    try:
        try:
            remote_file = posixpath.normpath(sftp.normalize(enabled_file))
            with sftp.open(remote_file, "rb") as stream:
                content = stream.read().decode("utf-8")
        except (IOError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=404, detail="无法读取反代配置文件") from exc
        name_pattern = re.compile(r"(?m)^(\s*server_name\s+)[^;]+;")
        upstream_pattern = re.compile(r"(?m)^(\s*proxy_pass\s+)[^;]+;")
        if len(name_pattern.findall(content)) != 1 or len(upstream_pattern.findall(content)) != 1:
            raise HTTPException(status_code=409, detail="配置文件包含多个域名或后端指令，拒绝整体替换")
        updated = name_pattern.sub(lambda match: f"{match.group(1)}{server_name};", content)
        updated = upstream_pattern.sub(lambda match: f"{match.group(1)}{upstream};", updated)
        if updated == content:
            return {"status": "updated", "verified": True, "message": "配置没有变化", "id": proxy_id, "server_name": server_name, "upstream": upstream}
        temporary = _sftp_write_temp(sftp, remote_file, updated.encode("utf-8"), 0o644)
        command = " && ".join([
            f"mkdir -p -- {shlex.quote(backup_dir)}",
            f"cp -a -- {shlex.quote(remote_file)} {shlex.quote(backup_file)}",
            f"mv -f -- {shlex.quote(temporary)} {shlex.quote(remote_file)}",
            "nginx -t",
            "systemctl reload nginx",
        ])
        _stdin, stdout, stderr = client.exec_command(command, timeout=40)
        output = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")[-4000:]
        if stdout.channel.recv_exit_status() != 0:
            client.exec_command(f"cp -a -- {shlex.quote(backup_file)} {shlex.quote(remote_file)} && nginx -t && systemctl reload nginx", timeout=30)[1].channel.recv_exit_status()
            raise RuntimeError(f"修改失败，已尝试恢复原配置：{output}")
        verified = [item for item in read_remote_reverse_proxies(settings) if item.get("id") == proxy_id]
        if not any(server_name in item.get("server_name", []) and upstream in item.get("proxy_pass", []) for item in verified):
            raise RuntimeError("重载后未回读到修改后的规则，请检查远端配置")
        return {"status": "updated", "verified": True, "message": "反向代理已修改并验证", "id": proxy_id, "server_name": server_name, "upstream": upstream, "backup": backup_file, "remote_output": output}
    finally:
        if temporary:
            try:
                sftp.remove(temporary)
            except IOError:
                pass
        sftp.close()
        client.close()

def delete_remote_reverse_proxy(proxy_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}\.conf", proxy_id):
        raise HTTPException(status_code=400, detail="反代标识无效")
    # IDs originate from nginx -T's enabled entry, not the symlink target name.
    entries = [item for item in read_remote_reverse_proxies(settings) if item["id"] == proxy_id]
    if not entries:
        raise HTTPException(status_code=404, detail=f"反代入口不存在，请刷新列表：{proxy_id}")
    if len(entries) != 1:
        raise HTTPException(status_code=409, detail="此配置包含多个反代入口，无法单独删除")
    enabled_dir = posixpath.normpath(str(settings["sites_enabled_dir"]))
    remote_entry = posixpath.join(enabled_dir, proxy_id)
    backup_dir = str(settings["nginx_backup_dir"])
    timestamp = utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_file = posixpath.join(backup_dir, f"{proxy_id}.{timestamp}.bak")

    client = remote_ssh_client(settings)
    sftp = client.open_sftp()
    try:
        try:
            entry_stat = sftp.lstat(remote_entry)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"启用目录中未找到反代入口：{proxy_id}") from exc
        if not (stat.S_ISLNK(entry_stat.st_mode) or stat.S_ISREG(entry_stat.st_mode)):
            raise HTTPException(status_code=400, detail="反代入口必须是文件或符号链接")
        entry = shlex.quote(remote_entry)
        backup = shlex.quote(backup_file)
        snapshot = shlex.quote(backup_file + ".content")
        # Keep the exact link for rollback and a dereferenced content snapshot.
        # Only remove the enabled entry: its source may be shared by other links.
        rollback = f"cp -a -- {backup} {entry} && nginx -t && systemctl reload nginx"
        command = "\n".join([
            "set -e",
            f"mkdir -p -- {shlex.quote(backup_dir)}",
            f"cp -a -- {entry} {backup}",
            f"cp -L -- {entry} {snapshot}",
            "rollback() {",
            "  result=$?",
            "  trap - EXIT",
            f"  if [ \"$result\" -ne 0 ]; then {rollback}; fi",
            "  exit \"$result\"",
            "}",
            "trap rollback EXIT",
            f"rm -- {entry}",
            "nginx -t",
            "systemctl reload nginx",
            "trap - EXIT",
        ])
        _stdin, stdout, stderr = client.exec_command(command, timeout=45)
        output = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")[-4000:]
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError(f"删除失败，已尝试恢复原入口（退出码 {exit_code}）：{output}")
        return {"status": "deleted", "message": "反向代理入口已删除并完成 Nginx reload，原始配置已保留", "id": proxy_id, "backup": backup_file, "remote_output": output}
    finally:
        sftp.close()
        client.close()


def validate_mihomo_config_text(content: str) -> dict[str, Any]:
    if not content.strip():
        raise HTTPException(status_code=400, detail="Mihomo 配置不能为空")
    if len(content.encode("utf-8")) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Mihomo 配置不能超过 4 MB")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"YAML 格式错误：{exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Mihomo 配置顶层必须是 YAML 对象")
    if "proxies" in parsed and not isinstance(parsed["proxies"], list):
        raise HTTPException(status_code=400, detail="proxies 必须是列表")
    if "proxy-groups" in parsed and not isinstance(parsed["proxy-groups"], list):
        raise HTTPException(status_code=400, detail="proxy-groups 必须是列表")
    if not any(key in parsed for key in ("external-controller", "external-controller-unix", "external-controller-tls")):
        raise HTTPException(status_code=400, detail="必须保留 external-controller，否则 NASBridge 无法继续监控 Mihomo")
    return parsed


def read_mihomo_config_file(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
        "modified_at": datetime.fromtimestamp(stat_result.st_mtime, timezone.utc).isoformat(),
        "size": stat_result.st_size,
    }


def write_mihomo_config_file(path: Path, content: str) -> str:
    old_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    backup_dir = DATA_DIR / "mihomo-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"config.yaml.{timestamp}.bak"
    if path.exists():
        backup_path.write_bytes(path.read_bytes())
    temporary = path.with_name(f".{path.name}.nasbridge-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.chmod(temporary, old_mode)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(backup_path) if backup_path.exists() else ""


def restore_mihomo_config_file(path: Path, backup_path: str) -> None:
    if backup_path:
        temporary = path.with_name(f".{path.name}.nasbridge-restore-{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(Path(backup_path).read_bytes())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
        temporary.replace(path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def status_for_expiry(expires_at: datetime | None) -> str:
    if expires_at is None:
        return "unknown"
    days = (expires_at - utc_now()).total_seconds() / 86400
    if days < 0:
        return "expired"
    if days <= 7:
        return "critical"
    if days <= 30:
        return "warning"
    return "ok"


def cert_result_base(item: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "id": item.get("id", item.get("name", kind)),
        "name": item.get("name", item.get("id", kind)),
        "kind": kind,
        "domain": item.get("domain") or item.get("server_name"),
        "status": "unknown",
        "error": None,
        "checked_at": utc_now().isoformat(),
        "local_ref": item.get("local_ref"),
        "sync_enabled": bool(item.get("sync_enabled", False)),
    }


def cert_details(cert: x509.Certificate) -> tuple[datetime, datetime, str | None, str]:
    not_before = getattr(cert, "not_valid_before_utc", None)
    not_after = getattr(cert, "not_valid_after_utc", None)
    if not_before is None:
        not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
    if not_after is None:
        not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
    issuer = cert.issuer.rfc4514_string()
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return not_before, not_after, issuer, fingerprint


def add_cert_fields(result: dict[str, Any], not_before: datetime, not_after: datetime, issuer: str | None, fingerprint: str, verified: bool | None = None) -> dict[str, Any]:
    result.update(
        {
            "not_before": not_before.isoformat(),
            "expires_at": not_after.isoformat(),
            "days_left": round((not_after - utc_now()).total_seconds() / 86400, 1),
            "issuer": issuer,
            "fingerprint_sha256": fingerprint,
            "status": status_for_expiry(not_after),
        }
    )
    if verified is not None:
        result["verified"] = verified
    return result


def inspect_local_cert(item: dict[str, Any]) -> dict[str, Any]:
    result = cert_result_base(item, "local")
    path = Path(str(item.get("path", "")))
    result["path"] = str(path)
    try:
        raw = path.read_bytes()
        if b"BEGIN CERTIFICATE" in raw:
            cert = x509.load_pem_x509_certificate(raw, default_backend())
        else:
            cert = x509.load_der_x509_certificate(raw, default_backend())
        common_names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if common_names:
            result["domain"] = common_names[0].value
        return add_cert_fields(result, *cert_details(cert))
    except Exception as exc:  # status endpoint must remain useful if one item is broken
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def inspect_remote_cert(item: dict[str, Any]) -> dict[str, Any]:
    result = cert_result_base(item, "remote")
    host = str(item.get("host", ""))
    port = int(item.get("port", 443))
    server_name = str(item.get("server_name") or host)
    result.update({"host": host, "port": port, "server_name": server_name})
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=5) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=server_name) as tls_socket:
                cert_bytes = tls_socket.getpeercert(binary_form=True)
                cert = x509.load_der_x509_certificate(cert_bytes, default_backend())
        common_names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        if common_names:
            result["domain"] = common_names[0].value
        return add_cert_fields(result, *cert_details(cert), verified=True)
    except ssl.SSLCertVerificationError as exc:
        # Still inspect expiry when the remote certificate is expired/self-signed/wrong-chain.
        try:
            context = ssl._create_unverified_context()
            with socket.create_connection((host, port), timeout=5) as raw_socket:
                with context.wrap_socket(raw_socket, server_hostname=server_name) as tls_socket:
                    cert_bytes = tls_socket.getpeercert(binary_form=True)
                    cert = x509.load_der_x509_certificate(cert_bytes, default_backend())
            common_names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
            if common_names:
                result["domain"] = common_names[0].value
            result = add_cert_fields(result, *cert_details(cert), verified=False)
            result["error"] = f"TLS verification failed: {exc}"
            return result
        except Exception as inner_exc:
            result["error"] = f"TLS verification failed: {exc}; inspection failed: {inner_exc}"
            return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def mihomo_headers(config: dict[str, Any]) -> dict[str, str]:
    secret_env = config.get("secret_env", "MIHOMO_SECRET")
    secret = os.getenv(str(secret_env), "")
    if not secret:
        try:
            secret = Path("/host/mihomo/api-secret.txt").read_text(encoding="utf-8").strip()
        except OSError:
            secret = ""
    return {"Authorization": f"Bearer {secret}"} if secret else {}


def mihomo_base_url(config: dict[str, Any]) -> str:
    return str(config.get("url", "http://host.docker.internal:9090")).rstrip("/")


async def mihomo_container_status() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.get(f"{MIHOMO_CONTROL_URL}/status")
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        return {"state": "unavailable", "running": None, "error": f"{type(exc).__name__}: {exc}"}


async def control_mihomo_container(action: str) -> dict[str, Any]:
    if action not in {"start", "stop", "restart"}:
        raise HTTPException(status_code=400, detail="仅支持启动、停止或重启 Mihomo")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{MIHOMO_CONTROL_URL}/{action}")
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        detail = "Docker 控制服务拒绝了操作"
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Docker 控制服务不可用：{type(exc).__name__}") from exc


async def mihomo_get(path: str, mihomo_config: dict[str, Any]) -> Any:
    base_url = mihomo_base_url(mihomo_config)
    timeout = float(mihomo_config.get("timeout_seconds", 3))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{base_url}{path}", headers=mihomo_headers(mihomo_config))
        response.raise_for_status()
        return response.json()


async def mihomo_reload(mihomo_config: dict[str, Any]) -> None:
    base_url = mihomo_base_url(mihomo_config)
    timeout = float(mihomo_config.get("timeout_seconds", 3))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.put(
            f"{base_url}/configs?force=true",
            headers=mihomo_headers(mihomo_config),
            json={"path": "", "payload": ""},
        )
        response.raise_for_status()


def metadata_of(connection: dict[str, Any]) -> dict[str, Any]:
    metadata = connection.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def source_ip_of(connection: dict[str, Any]) -> str:
    metadata = metadata_of(connection)
    return str(metadata.get("sourceIP") or metadata.get("sourceIp") or connection.get("sourceIP") or "")


def process_path_of(connection: dict[str, Any]) -> str:
    metadata = metadata_of(connection)
    return str(metadata.get("processPath") or metadata.get("process") or "")


def chains_of(connection: dict[str, Any]) -> list[str]:
    chains = connection.get("chains") or []
    return [str(chain) for chain in chains]


def is_direct(connection: dict[str, Any]) -> bool:
    chains = [chain.upper() for chain in chains_of(connection)]
    return not chains or all(chain in {"DIRECT", "PASS", "BYPASS"} for chain in chains)


def connection_item(connection: dict[str, Any]) -> dict[str, Any]:
    metadata = metadata_of(connection)
    return {
        "id": connection.get("id"),
        "source_ip": source_ip_of(connection),
        "source_port": metadata.get("sourcePort", ""),
        "host": metadata.get("host") or metadata.get("sniffHost") or metadata.get("destinationIP", ""),
        "destination_port": metadata.get("destinationPort", ""),
        "network": metadata.get("network", ""),
        "process": process_path_of(connection),
        "chains": chains_of(connection),
        "route": "direct" if is_direct(connection) else "proxy",
        "rule": connection.get("rule", ""),
        "upload": int(connection.get("upload", 0) or 0),
        "download": int(connection.get("download", 0) or 0),
        "start": connection.get("start"),
    }


def connection_matches(connection: dict[str, Any], item: dict[str, Any]) -> bool:
    # Overview uses normalized connection records (snake_case), while a few
    # callers may still pass raw Mihomo objects (camelCase metadata).
    source_ip = str(connection.get("source_ip") or source_ip_of(connection))
    process = str(connection.get("process") or process_path_of(connection)).lower()
    source_ips = {str(value) for value in item.get("source_ips", [])}
    process_terms = [str(value).lower() for value in item.get("process_contains", [])]
    return source_ip in source_ips or any(term and term in process for term in process_terms)


def qbt_proxy_status(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    result: dict[str, Any] = {"config_path": path, "configured": False, "type": None, "host": None, "port": None, "force": None, "peer_connections": None, "error": None}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        values: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        proxy_type = values.get("Connection\\ProxyType", values.get("Proxy\\Type", ""))
        host = values.get("Connection\\Proxy\\IP", values.get("Proxy\\IP", ""))
        port = values.get("Connection\\Proxy\\Port", values.get("Proxy\\Port", ""))
        force = values.get("Connection\\ProxyForce", "")
        peer = values.get("Connection\\ProxyPeerConnections", "")
        result.update({"configured": proxy_type not in {"", "0"}, "type": "HTTP/SOCKS (qBittorrent enum)" if proxy_type not in {"", "0"} else "none", "host": host, "port": port, "force": force.lower() == "true", "peer_connections": peer.lower() == "true"})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _sftp_mkdirs(sftp: paramiko.SFTPClient, directory: str) -> None:
    current = ""
    for part in directory.strip("/").split("/"):
        if not part:
            continue
        current += "/" + part
        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def _sftp_read(sftp: paramiko.SFTPClient, path: str) -> bytes | None:
    try:
        with sftp.open(path, "rb") as handle:
            return handle.read()
    except IOError:
        return None


def _sftp_write_atomic(sftp: paramiko.SFTPClient, path: str, data: bytes, mode: int) -> None:
    directory = posixpath.dirname(path)
    _sftp_mkdirs(sftp, directory)
    temporary = f"{path}.nasbridge-{uuid.uuid4().hex}.tmp"
    try:
        with sftp.open(temporary, "wb") as handle:
            handle.write(data)
        sftp.chmod(temporary, mode)
        # OpenSSH SFTP's regular rename may fail when the destination
        # already exists.  posix_rename() replaces the destination atomically.
        if hasattr(sftp, "posix_rename"):
            sftp.posix_rename(temporary, path)
        else:
            try:
                sftp.remove(path)
            except IOError:
                pass
            sftp.rename(temporary, path)
    finally:
        try:
            sftp.remove(temporary)
        except IOError:
            pass


def _sftp_write_temp(sftp: paramiko.SFTPClient, path: str, data: bytes, mode: int) -> str:
    """Upload a new file without trying to replace an existing remote file."""
    directory = posixpath.dirname(path)
    _sftp_mkdirs(sftp, directory)
    temporary = f"{path}.nasbridge-{uuid.uuid4().hex}.tmp"
    with sftp.open(temporary, "wb") as handle:
        handle.write(data)
    sftp.chmod(temporary, mode)
    return temporary


def _cert_from_bytes(data: bytes) -> x509.Certificate:
    if b"BEGIN CERTIFICATE" in data:
        return x509.load_pem_x509_certificate(data, default_backend())
    return x509.load_der_x509_certificate(data, default_backend())


def _sync_remote_certificate(cert_item: dict[str, Any], local_item: dict[str, Any]) -> dict[str, Any]:
    local_cert_path = Path(str(local_item.get("path", "")))
    local_key_path = Path(str(local_item.get("key_path", "")))
    local_cert = local_cert_path.read_bytes()
    local_key = local_key_path.read_bytes()
    local_fingerprint = _cert_from_bytes(local_cert).fingerprint(hashes.SHA256()).hex()

    host = str(cert_item.get("ssh_host") or cert_item.get("host"))
    user = str(cert_item.get("ssh_user", "root"))
    port = int(cert_item.get("ssh_port", 22))
    key_path = str(cert_item.get("ssh_key_path", "/root/.ssh/id_rsa"))
    known_hosts = str(cert_item.get("ssh_known_hosts", "/root/.ssh/known_hosts"))
    remote_cert_path = str(cert_item["remote_cert_path"])
    remote_key_path = str(cert_item["remote_key_path"])
    backup_dir = str(cert_item.get("remote_backup_dir", "/root/certs/backups/nasbridge"))

    client = paramiko.SSHClient()
    if Path(known_hosts).exists():
        client.load_host_keys(known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect(hostname=host, port=port, username=user, key_filename=key_path, look_for_keys=False, allow_agent=False, timeout=10, banner_timeout=10, auth_timeout=10)
    sftp = client.open_sftp()
    old_cert = _sftp_read(sftp, remote_cert_path)
    old_key = _sftp_read(sftp, remote_key_path)
    if old_cert:
        try:
            remote_fingerprint = _cert_from_bytes(old_cert).fingerprint(hashes.SHA256()).hex()
            if remote_fingerprint == local_fingerprint:
                sftp.close()
                client.close()
                return {"status": "already_equal", "message": "远端证书已与本地证书一致", "fingerprint_sha256": local_fingerprint}
        except Exception:
            pass

    timestamp = utc_now().strftime("%Y%m%d-%H%M%S")
    backups: list[str] = []
    remote_cert_temp: str | None = None
    remote_key_temp: str | None = None
    try:
        _sftp_mkdirs(sftp, backup_dir)
        if old_cert is not None:
            cert_backup = posixpath.join(backup_dir, f"fullchain.cer.{timestamp}.bak")
            _sftp_write_atomic(sftp, cert_backup, old_cert, 0o644)
            backups.append(cert_backup)
        if old_key is not None:
            key_backup = posixpath.join(backup_dir, f"privkey.{timestamp}.bak")
            _sftp_write_atomic(sftp, key_backup, old_key, 0o600)
            backups.append(key_backup)

        # Upload to fresh names via SFTP, then let the root shell replace the
        # existing files with mv -f. Some SFTP servers reject both rename()
        # and posix_rename() when the destination already exists.
        remote_cert_temp = _sftp_write_temp(sftp, remote_cert_path, local_cert, 0o644)
        remote_key_temp = _sftp_write_temp(sftp, remote_key_path, local_key, 0o600)
        replace_command = " && ".join([
            f"mv -f -- {shlex.quote(remote_cert_temp)} {shlex.quote(remote_cert_path)}",
            f"mv -f -- {shlex.quote(remote_key_temp)} {shlex.quote(remote_key_path)}",
            "nginx -t",
            "systemctl reload nginx",
        ])
        stdin, stdout, stderr = client.exec_command(replace_command, timeout=20)
        exit_code = stdout.channel.recv_exit_status()
        output = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")[-4000:]
        if exit_code != 0:
            rollback = []
            if old_cert is not None and backups:
                rollback.append(f"cp -f -- {shlex.quote(backups[0])} {shlex.quote(remote_cert_path)} && chmod 0644 {shlex.quote(remote_cert_path)}")
            if old_key is not None and len(backups) > 1:
                rollback.append(f"cp -f -- {shlex.quote(backups[1])} {shlex.quote(remote_key_path)} && chmod 0600 {shlex.quote(remote_key_path)}")
            if rollback:
                client.exec_command(" && ".join(rollback), timeout=20)[1].channel.recv_exit_status()
            raise RuntimeError(f"远端文件替换或 nginx reload 失败（退出码 {exit_code}）：{output}")
        return {"status": "synced", "message": "证书已同步并完成 Nginx reload", "fingerprint_sha256": local_fingerprint, "backups": backups, "remote_output": output}
    finally:
        for temporary in (remote_cert_temp, remote_key_temp):
            if temporary:
                try:
                    sftp.remove(temporary)
                except IOError:
                    pass
        sftp.close()
        client.close()


async def build_mihomo_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    mihomo_config = config.get("mihomo", {})
    snapshot: dict[str, Any] = {"status": "offline", "version": None, "connections": [], "error": None}
    snapshot["container"] = await mihomo_container_status()
    try:
        version = await mihomo_get("/version", mihomo_config)
        raw_connections = await mihomo_get("/connections", mihomo_config)
        connections = raw_connections.get("connections", []) if isinstance(raw_connections, dict) else []
        items = [connection_item(item) for item in connections]
        proxied = [item for item in items if item["route"] == "proxy"]
        direct = [item for item in items if item["route"] == "direct"]
        snapshot.update({
            "status": "online",
            "version": version.get("version") if isinstance(version, dict) else None,
            "memory": raw_connections.get("memory") if isinstance(raw_connections, dict) else None,
            "connections": items,
            "connection_count": len(items),
            "proxied_count": len(proxied),
            "direct_count": len(direct),
            "proxied_bytes": {"upload": sum(item["upload"] for item in proxied), "download": sum(item["download"] for item in proxied)},
            "direct_bytes": {"upload": sum(item["upload"] for item in direct), "download": sum(item["download"] for item in direct)},
        })
    except Exception as exc:
        snapshot["error"] = f"{type(exc).__name__}: {exc}"
    return snapshot


def scheduled_slot(schedule: dict[str, Any]) -> str | None:
    if not schedule.get("enabled"):
        return None
    try:
        timezone_info = ZoneInfo(LOCAL_TIMEZONE)
    except Exception:
        timezone_info = datetime.now().astimezone().tzinfo
    now = datetime.now(timezone_info)
    hour, minute = (int(part) for part in str(schedule.get("time", "03:30")).split(":"))
    if schedule.get("mode") == "monthly":
        day = int(schedule.get("month_day", 1))
        target = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        if now < target:
            return None
        return f"{target.date().isoformat()} {schedule.get('time', '03:30')}"
    if now.weekday() not in set(schedule.get("days", list(range(7)))):
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < target:
        return None
    return f"{now.date().isoformat()} {schedule.get('time', '03:30')}"

async def sync_all_configured_certificates() -> list[dict[str, Any]]:
    config = load_config()
    local_items = {item.get("id"): item for item in config.get("certificates", {}).get("local", [])}
    results: list[dict[str, Any]] = []
    async with certificate_sync_lock:
        for remote_item in config.get("certificates", {}).get("remote", []):
            if not remote_item.get("sync_enabled"):
                continue
            local_item = local_items.get(remote_item.get("local_ref"))
            if not local_item:
                results.append({"id": remote_item.get("id"), "status": "error", "error": "未找到对应的本地证书"})
                continue
            try:
                result = await asyncio.to_thread(_sync_remote_certificate, remote_item, local_item)
                results.append({"id": remote_item.get("id"), **result})
            except Exception as exc:
                results.append({"id": remote_item.get("id"), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
    return results


async def certificate_scheduler() -> None:
    while True:
        try:
            schedule = load_schedule()
            slot = scheduled_slot(schedule)
            if slot and schedule.get("last_run_slot") != slot:
                results = await sync_all_configured_certificates()
                failures = [item for item in results if item.get("status") == "error"]
                schedule = load_schedule()
                schedule.update({
                    "last_run_slot": slot,
                    "last_run_at": utc_now().isoformat(),
                    "last_result": "failed" if failures else "success",
                    "last_error": "; ".join(f"{item.get('id')}: {item.get('error')}" for item in failures) or None,
                })
                save_schedule(schedule)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            schedule = load_schedule()
            schedule["last_result"] = "failed"
            schedule["last_error"] = f"计划任务失败：{type(exc).__name__}: {exc}"
            save_schedule(schedule)
        await asyncio.sleep(20)


async def build_overview() -> dict[str, Any]:
    config = load_config()
    mihomo = await build_mihomo_snapshot(config)
    # Keep the response field for older clients, without inferring applications
    # from shared source IPs or manually configured application names.
    applications: list[dict[str, Any]] = []
    certs = [inspect_local_cert(item) for item in config.get("certificates", {}).get("local", [])]
    certs.extend(inspect_remote_cert(item) for item in config.get("certificates", {}).get("remote", []))
    local_by_id = {item.get("id"): item for item in certs if item.get("kind") == "local"}
    for remote in (item for item in certs if item.get("kind") == "remote"):
        local = local_by_id.get(remote.get("local_ref"))
        if local and local.get("fingerprint_sha256") and remote.get("fingerprint_sha256"):
            remote["matches_local"] = local["fingerprint_sha256"] == remote["fingerprint_sha256"]
        else:
            remote["matches_local"] = None
    return {"app": config.get("app", {}), "mihomo": mihomo, "applications": applications, "application_registry": {}, "certificates": certs, "schedule": schedule_state(), "generated_at": utc_now().isoformat()}


@app.on_event("startup")
async def start_certificate_scheduler() -> None:
    global scheduler_task
    await asyncio.to_thread(initialize_persisted_state)
    scheduler_task = asyncio.create_task(certificate_scheduler())


@app.on_event("shutdown")
async def stop_certificate_scheduler() -> None:
    global scheduler_task
    if scheduler_task:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
        scheduler_task = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


def load_daily_task_state() -> dict[str, set[str]]:
    stored = load_persisted_state("daily_task_state", DAILY_TASK_STATE_PATH, {"hidden_runs": [], "read_summaries": []})
    if not isinstance(stored, dict):
        stored = {}
    return {
        "hidden_runs": {str(value) for value in stored.get("hidden_runs", [])},
        "read_summaries": {str(value) for value in stored.get("read_summaries", [])},
    }


def save_daily_task_state(state: dict[str, set[str]]) -> None:
    save_persisted_state("daily_task_state", DAILY_TASK_STATE_PATH, {key: sorted(values) for key, values in state.items()})


def read_daily_task_data() -> dict[str, Any]:
    connection = None
    try:
        connection = postgres_connection("nasbridge-daily-task-panel")
        from .daily_task_control import control_command
        from .daily_task_progress import reconcile, display_run
        try:
            service=control_command('status')
        except HTTPException:
            service={'available': False}
        reconcile(connection,service)
        local_today = datetime.now(ZoneInfo(LOCAL_TIMEZONE)).date()
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*)::int AS total_runs,
                    COUNT(*) FILTER (WHERE run_date = %s)::int AS today_runs,
                    COUNT(*) FILTER (WHERE run_date = %s AND status = 'success')::int AS today_success_runs,
                    COUNT(*) FILTER (WHERE status = 'success')::int AS success_runs,
                    COUNT(*) FILTER (WHERE status IN ('failed', 'logging_failed','interrupted','timed_out'))::int AS failed_runs
                FROM public.rhino_daily_runs
            """, (local_today, local_today))
            metrics = dict(cursor.fetchone())
            cursor.execute("""
                SELECT r.run_id::text AS run_id, run_date::text AS run_date,
                       started_at, finished_at, status, media_status, summary_status,
                       LEFT(error_message, 800) AS error_message,
                       LEFT(run_summary, 600) AS run_summary,
                       jsonb_array_length(video_files) AS video_count,
                       jsonb_array_length(transcript_files) AS transcript_count,
                       jsonb_array_length(summary_files) AS summary_count,
                       skill_updated, p.progress
                FROM public.rhino_daily_runs r LEFT JOIN public.rhino_daily_progress p USING(run_id)
                ORDER BY started_at DESC
                LIMIT 20
            """)
            runs = [display_run(row,service) for row in cursor.fetchall()]
            cursor.execute("""
                SELECT summary_filename, video_id, source_url, transcript_filename,
                       updated_at, LEFT(summary_markdown, 8000) AS summary_markdown
                FROM public.rhino_video_summaries
                ORDER BY updated_at DESC, summary_filename DESC
                LIMIT 5
            """)
            summaries = [dict(row) for row in cursor.fetchall()]
        for row in runs:
            for key in ("started_at", "finished_at"):
                if row.get(key) is not None:
                    row[key] = row[key].isoformat()
        for row in summaries:
            if row.get("updated_at") is not None:
                row["updated_at"] = row["updated_at"].isoformat()
        with daily_task_state_lock:
            state = load_daily_task_state()
        local_today = datetime.now(ZoneInfo(LOCAL_TIMEZONE)).date()
        for row in runs:
            row["hidden"] = row["run_id"] in state["hidden_runs"]
        for row in summaries:
            row["is_read"] = row["summary_filename"] in state["read_summaries"]
        latest_today = None
        for row in summaries:
            stamp = row.get("updated_at")
            if not stamp:
                continue
            try:
                parsed = datetime.fromisoformat(stamp)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE))
                if parsed.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date() == local_today:
                    latest_today = row
                    break
            except ValueError:
                continue
        metrics["summary_count"] = len(summaries)
        return {
            "status": "ok",
            "read_at": utc_now().isoformat(),
            "metrics": metrics,
            "manual_control": manual_control_status(metrics,service),
            "runs": runs,
            "summaries": summaries,
            "latest_unread_summary": latest_today if latest_today and not latest_today["is_read"] else None,
            "hidden_run_count": sum(1 for row in runs if row["hidden"]),
        }
    except Exception as exc:
        return {
            "status": "error",
            "read_at": utc_now().isoformat(),
            "message": f"{type(exc).__name__}: {str(exc)[:400]}",
            "metrics": {},
            "runs": [],
            "summaries": [],
        }
    finally:
        if connection is not None:
            connection.close()


@app.get("/api/daily-tasks")
async def daily_tasks() -> dict[str, Any]:
    return await asyncio.to_thread(read_daily_task_data)

@app.post("/api/daily-tasks/state")
async def update_daily_task_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    action = payload.get("action")
    item_id = str(payload.get("id", ""))
    if not item_id or len(item_id) > 500:
        raise HTTPException(status_code=400, detail="缺少有效的记录标识")
    if action not in {"hide_run", "restore_run", "read_summary"}:
        raise HTTPException(status_code=400, detail="不支持的状态操作")
    state_key = "hidden_runs" if action in {"hide_run", "restore_run"} else "read_summaries"
    with daily_task_state_lock:
        state = load_daily_task_state()
        if action == "hide_run":
            state[state_key].add(item_id)
        elif action == "restore_run":
            state[state_key].discard(item_id)
        else:
            state[state_key].add(item_id)
        save_daily_task_state(state)
    return {"status": "ok"}

@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    return await build_overview()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    config = load_config()
    mihomo = await build_mihomo_snapshot(config)
    return {"status": "ok", "mihomo": mihomo["status"], "generated_at": utc_now().isoformat()}


@app.post("/api/mihomo/container/{action}")
async def control_mihomo(action: str) -> dict[str, Any]:
    return await control_mihomo_container(action)


@app.get("/api/mihomo/config")
async def get_mihomo_config() -> dict[str, Any]:
    config = load_config()
    path = mihomo_config_path(config)
    try:
        return await asyncio.to_thread(read_mihomo_config_file, path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Mihomo 配置失败：{type(exc).__name__}: {exc}") from exc


@app.put("/api/mihomo/config")
async def update_mihomo_config(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="请求必须包含 content 字符串")
    validate_mihomo_config_text(content)
    config = load_config()
    path = mihomo_config_path(config)
    backup_path = ""
    try:
        backup_path = await asyncio.to_thread(write_mihomo_config_file, path, content)
        await mihomo_reload(config.get("mihomo", {}))
        return {"status": "applied", "message": "Mihomo 配置已保存并重载", "backup": backup_path}
    except Exception as exc:
        if backup_path:
            try:
                await asyncio.to_thread(restore_mihomo_config_file, path, backup_path)
                await mihomo_reload(config.get("mihomo", {}))
            except Exception:
                pass
        raise HTTPException(status_code=502, detail=f"应用 Mihomo 配置失败，已尝试恢复旧配置：{type(exc).__name__}: {exc}") from exc


@app.get("/api/certificate-schedule")
async def get_certificate_schedule() -> dict[str, Any]:
    return schedule_state()


@app.get("/api/reverse-proxies")
async def get_reverse_proxies() -> dict[str, Any]:
    settings = reverse_proxy_settings(load_config())
    try:
        proxies = await asyncio.to_thread(read_remote_reverse_proxies, settings)
        return {
            "status": "ok",
            "proxies": proxies,
            "remote_host": settings.get("ssh_host"),
            "certificates": reverse_proxy_certificate_options(settings),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取远端 Nginx 反代失败：{type(exc).__name__}: {exc}") from exc


@app.post("/api/reverse-proxies")
async def create_reverse_proxy(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    settings = reverse_proxy_settings(load_config())
    try:
        return await asyncio.to_thread(add_remote_reverse_proxy, payload, settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"新增远端反代失败：{type(exc).__name__}: {exc}") from exc


@app.put("/api/reverse-proxies/{proxy_id}")
async def edit_reverse_proxy(proxy_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    settings = reverse_proxy_settings(load_config())
    try:
        return await asyncio.to_thread(update_remote_reverse_proxy, proxy_id, payload, settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"修改远端反代失败：{type(exc).__name__}: {exc}") from exc

@app.delete("/api/reverse-proxies/{proxy_id}")
async def remove_reverse_proxy(proxy_id: str) -> dict[str, Any]:
    settings = reverse_proxy_settings(load_config())
    try:
        return await asyncio.to_thread(delete_remote_reverse_proxy, proxy_id, settings)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"删除远端反代失败：{type(exc).__name__}: {exc}") from exc


@app.post("/api/applications/scan")
async def scan_applications() -> dict[str, Any]:
    config = load_config()
    snapshot = await build_mihomo_snapshot(config)
    scanned_at = utc_now().isoformat()
    applications = discovered_application_records(snapshot, load_applications(config))
    await asyncio.to_thread(save_applications, applications, scanned_at)
    return {
        "status": "saved",
        "message": "已扫描 Mihomo 当前活动连接并保存应用记录",
        "scanned_at": scanned_at,
        "connections_seen": len(snapshot.get("connections", [])),
        "applications": applications,
    }


@app.put("/api/certificate-schedule")
async def update_certificate_schedule(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    current = load_schedule()
    updated = normalize_schedule(payload, current)
    if any(payload.get(key) != current.get(key) for key in ("enabled", "mode", "time", "days", "month_day") if key in payload):
        updated["last_run_slot"] = None
        updated["last_run_at"] = None
        updated["last_result"] = None
        updated["last_error"] = None
    save_schedule(updated)
    return schedule_state()


@app.delete("/api/reverse-proxy-certificates")
async def remove_expired_reverse_proxy_certificate(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    cert_id = str(payload.get("certificate_id") or "")
    if not cert_id:
        raise HTTPException(status_code=400, detail="缺少证书标识")
    config = load_config()
    settings = reverse_proxy_settings(config)
    try:
        options = await asyncio.to_thread(reverse_proxy_certificate_options, settings)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取证书目录失败：{type(exc).__name__}: {exc}") from exc
    option = next((item for item in options if str(item.get("id")) == cert_id), None)
    if not option:
        raise HTTPException(status_code=404, detail="证书目录中不存在此证书")
    if option.get("status") != "expired":
        raise HTTPException(status_code=409, detail="只能删除已确认过期的证书记录")
    certificates = config.setdefault("certificates", {})
    proxy_config = config.setdefault("reverse_proxy", {})
    removed = 0
    if cert_id.startswith("local:"):
        local_id = cert_id[len("local:"):]
        old = certificates.get("local", [])
        filtered = [item for item in old if str(item.get("id") or item.get("name") or "") != local_id]
        removed += len(old) - len(filtered)
        certificates["local"] = filtered
        if any(item.get("local_ref") == local_id for item in certificates.get("remote", [])):
            raise HTTPException(status_code=409, detail="本地证书仍被同步配置引用，不能移除")
    else:
        for container, key in ((certificates, "remote"), (proxy_config, "remote_certificates")):
            old = container.get(key, [])
            filtered = [item for item in old if str(item.get("id") or item.get("local_ref") or item.get("remote_cert_path") or "") != cert_id]
            removed += len(old) - len(filtered)
            container[key] = filtered
    if not removed:
        raise HTTPException(status_code=409, detail="证书目录项没有对应的可移除配置记录")
    try:
        mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
        stamp = utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        backup = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{stamp}.bak")
        backup.write_bytes(CONFIG_PATH.read_bytes())
        temporary = CONFIG_PATH.with_name(f".{CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(temporary, mode)
        temporary.replace(CONFIG_PATH)
        return {"status": "deleted", "id": cert_id, "name": option.get("name"), "backup": str(backup), "message": "已从证书目录配置中移除并备份；证书文件本身保留"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除证书记录失败：{type(exc).__name__}: {exc}") from exc

@app.delete("/api/certificates/{cert_id}")
async def remove_expired_certificate(cert_id: str) -> dict[str, Any]:
    config = load_config()
    certificates = config.get("certificates", {})
    selected_kind = None
    selected = None
    for kind in ("local", "remote"):
        selected = next((item for item in certificates.get(kind, []) if str(item.get("id")) == cert_id), None)
        if selected is not None:
            selected_kind = kind
            break
    if selected is None:
        raise HTTPException(status_code=404, detail="证书记录不存在")
    current = inspect_local_cert(selected) if selected_kind == "local" else inspect_remote_cert(selected)
    if current.get("status") != "expired":
        raise HTTPException(status_code=409, detail="只能移除已确认过期的证书记录")
    if selected_kind == "local" and any(item.get("local_ref") == cert_id for item in certificates.get("remote", [])):
        raise HTTPException(status_code=409, detail="此本地证书仍被远端同步配置引用，请先移除相关远端记录")
    try:
        mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
        stamp = utc_now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        backup = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{stamp}.bak")
        backup.write_bytes(CONFIG_PATH.read_bytes())
        certificates[selected_kind] = [item for item in certificates.get(selected_kind, []) if str(item.get("id")) != cert_id]
        config["certificates"] = certificates
        temporary = CONFIG_PATH.with_name(f".{CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.chmod(temporary, mode)
        temporary.replace(CONFIG_PATH)
        return {"status": "deleted", "id": cert_id, "kind": selected_kind, "backup": str(backup), "message": "已从 NASBridge 证书列表移除并备份配置；证书文件本身保留"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除证书记录失败：{type(exc).__name__}: {exc}") from exc

@app.post("/api/certificates/{cert_id}/sync")
async def sync_certificate(cert_id: str, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="必须明确确认同步操作")

    config = load_config()
    remote_item = next((item for item in config.get("certificates", {}).get("remote", []) if item.get("id") == cert_id), None)
    if not remote_item or not remote_item.get("sync_enabled"):
        raise HTTPException(status_code=404, detail="证书同步目标不存在或未启用")
    local_ref = remote_item.get("local_ref")
    local_item = next((item for item in config.get("certificates", {}).get("local", []) if item.get("id") == local_ref), None)
    if not local_item:
        raise HTTPException(status_code=400, detail="未找到对应的本地证书")
    try:
        async with certificate_sync_lock:
            return await asyncio.to_thread(_sync_remote_certificate, remote_item, local_item)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"证书同步失败：{type(exc).__name__}: {exc}") from exc

from .sms_api import router as sms_router
app.include_router(sms_router)

from .geofence import router as geofence_router
app.include_router(geofence_router)
