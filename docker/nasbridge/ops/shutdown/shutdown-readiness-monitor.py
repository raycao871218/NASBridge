#!/usr/bin/env python3
"""Observe whether this Ubuntu host appears safe to shut down.

Request a safe shutdown through a locally configured HA webhook only after guards pass.
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


SAMPLE_SECONDS = 5
REQUIRED_IDLE_SECONDS = 30 * 60
MAX_CPU_PERCENT = 20.0
MAX_NETWORK_BYTES_PER_SECOND = 1024 * 1024
MAX_DISK_BYTES_PER_SECOND = 5 * 1024 * 1024
CODEX_ACTIVITY_GRACE_SECONDS = 5 * 60
SCHEDULE_TIMEZONE = ZoneInfo("Asia/Shanghai")
HOME_ASSISTANT_SHUTDOWN_WEBHOOK = os.environ.get("NASBRIDGE_SHUTDOWN_WEBHOOK", "")

STATE_FILE = Path("/var/lib/shutdown-readiness-monitor/state.json")
LOG_FILE = Path("/var/log/shutdown-readiness.log")

PROTECTED_COMMANDS = {
    "apt",
    "apt-get",
    "dpkg",
    "rsync",
    "rclone",
    "restic",
    "borg",
    "duplicity",
    "mkfs",
    "fsck",
}


def cpu_counters() -> tuple[int, int]:
    values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def network_bytes() -> int:
    total = 0
    for interface in Path("/sys/class/net").iterdir():
        if interface.name == "lo":
            continue
        try:
            total += int((interface / "statistics/rx_bytes").read_text())
            total += int((interface / "statistics/tx_bytes").read_text())
        except (FileNotFoundError, ValueError):
            continue
    return total


def disk_sectors() -> int:
    total = 0
    device_pattern = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+)$")
    for line in Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        if len(fields) < 10 or not device_pattern.match(fields[2]):
            continue
        total += int(fields[5]) + int(fields[9])
    return total


def logged_in_users() -> list[str]:
    result = subprocess.run(["who"], check=False, text=True, capture_output=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def protected_processes() -> list[str]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,comm=,args="], check=False, text=True, capture_output=True
    )
    matches: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) < 3 or fields[0] == str(os.getpid()):
            continue
        command = fields[1].lower()
        arguments = fields[2].lower()
        protected = command in PROTECTED_COMMANDS or command.startswith(("mkfs.", "fsck."))
        protected = protected or "docker pull" in arguments or "docker build" in arguments
        protected = protected or "git clone" in arguments
        protected = protected or (
            "unattended-upgrade" in arguments
            and "unattended-upgrade-shutdown --wait-for-signal" not in arguments
        )
        if protected:
            matches.append(line.strip())
    return matches[:10]


def codex_activity() -> dict:
    result = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,comm=,args="], check=False, text=True, capture_output=True
    )
    processes: list[tuple[int, int, str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) < 4:
            continue
        try:
            processes.append((int(fields[0]), int(fields[1]), fields[2].lower(), fields[3]))
        except ValueError:
            continue

    codex_processes = {
        pid
        for pid, _ppid, command, arguments in processes
        if command in {"codex", "codex-code-mode"}
        or (command == "node" and "/usr/local/bin/codex" in arguments)
    }
    descendants = set(codex_processes)
    changed = True
    while changed:
        changed = False
        for pid, ppid, _command, _arguments in processes:
            if ppid in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True

    active_commands: list[str] = []
    for pid, _ppid, command, arguments in processes:
        if pid not in descendants or pid in codex_processes:
            continue
        persistent_helper = command == "node" and "mcp/server.bundle.mjs" in arguments
        if not persistent_helper:
            active_commands.append(f"{pid} {command} {arguments}")

    session_files = list(Path("/home").glob("*/.codex/sessions/**/*.jsonl"))
    latest_session_age_seconds = None
    if session_files:
        latest_mtime = max(path.stat().st_mtime for path in session_files)
        latest_session_age_seconds = max(0, int(time.time() - latest_mtime))
    recently_active = (
        latest_session_age_seconds is not None
        and latest_session_age_seconds < CODEX_ACTIVITY_GRACE_SECONDS
    )

    return {
        "process_count": len(codex_processes),
        "recently_active": recently_active,
        "seconds_since_session_write": latest_session_age_seconds,
        "active_commands": active_commands[:10],
    }


def docker_containers() -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], check=False, text=True, capture_output=True
    )
    if result.returncode != 0:
        return []
    return [name for name in result.stdout.splitlines() if name]


def active_work_services() -> list[str]:
    """Protect long-running scheduled work from idle shutdown."""
    units = ("rhino-daily.service", "rhino-retry.service")
    active = []
    for unit in units:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"],
            check=False, text=True, capture_output=True,
        )
        if result.returncode or result.stdout.strip() not in {"active", "activating", "reloading", "deactivating", "inactive", "failed"}:
            raise RuntimeError("Cannot verify work service state")
        if result.stdout.strip() in {"active", "activating", "reloading", "deactivating"}:
            active.append(unit)
    return active


def work_protection() -> list[str]:
    try:
        reasons = ["active_work_service:" + unit for unit in active_work_services()]
        path = Path("/home/caobowen/rhino-finance/state/retry.json")
        try:
            retry = json.loads(path.read_text())
            if not isinstance(retry, dict) or retry.get("status") not in {"waiting", "retrying", "completed", "cancelled"}:
                raise ValueError("Invalid retry state")
            if retry.get("status") in {"waiting", "retrying"}:
                reasons.append("rhino_retry_" + retry["status"])
        except FileNotFoundError:
            pass
        return reasons
    except Exception:
        # Do not infer that work ended when a service/state query fails.
        return ["work_state_unavailable"]


def protected_exit(reasons: list[str], moment: datetime) -> None:
    record = {"time": moment.isoformat(timespec="seconds"), "status": "PROTECTED",
              "reasons": reasons, "shutdown_requested": False,
              "shutdown_request_result": "not_requested"}
    save_state({"idle_since": None, "status": "PROTECTED", "updated_at": time.time(), "shutdown_requested": False})
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False))


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False) + "\n")
    temporary.replace(STATE_FILE)


def inside_detection_window(moment: datetime) -> bool:
    """Skip only Mon-Fri from 11:00 up to 18:00 Asia/Shanghai."""
    weekday = moment.weekday()  # Monday is 0; Sunday is 6.
    return not (weekday <= 4 and 11 <= moment.hour < 18)


def request_safe_shutdown() -> str:
    if "--dry-run" in sys.argv: return "dry_run_not_requested"
    if not HOME_ASSISTANT_SHUTDOWN_WEBHOOK: raise RuntimeError("Shutdown webhook is not configured")
    request = urllib.request.Request(
        HOME_ASSISTANT_SHUTDOWN_WEBHOOK,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return f"accepted_http_{response.status}"


def main() -> None:
    check_time = datetime.now(SCHEDULE_TIMEZONE)
    protection = work_protection()
    if protection:
        protected_exit(protection, check_time)
        return
    if not inside_detection_window(check_time):
        print(
            json.dumps(
                {
                    "time": check_time.isoformat(timespec="seconds"),
                    "status": "OUTSIDE_WINDOW",
                    "message": "Detection is skipped only Mon-Fri from 11:00 to 18:00 Asia/Shanghai",
                },
                ensure_ascii=False,
            )
        )
        return

    users = logged_in_users()
    protected = protected_processes()
    codex = codex_activity()
    containers = docker_containers()
    active_work = active_work_services()

    cpu_start, idle_start = cpu_counters()
    network_start = network_bytes()
    disk_start = disk_sectors()
    time.sleep(SAMPLE_SECONDS)
    cpu_end, idle_end = cpu_counters()
    network_end = network_bytes()
    disk_end = disk_sectors()

    cpu_delta = max(cpu_end - cpu_start, 1)
    idle_delta = max(idle_end - idle_start, 0)
    cpu_percent = max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / cpu_delta)))
    network_bps = max(0, network_end - network_start) / SAMPLE_SECONDS
    disk_bps = max(0, disk_end - disk_start) * 512 / SAMPLE_SECONDS

    # Shutdown readiness is based on Codex plus host CPU, network, and disk
    # activity. Login sessions and other processes are recorded only.
    reasons: list[str] = []
    if cpu_percent >= MAX_CPU_PERCENT:
        reasons.append("cpu_busy")
    if network_bps >= MAX_NETWORK_BYTES_PER_SECOND:
        reasons.append("network_busy")
    if disk_bps >= MAX_DISK_BYTES_PER_SECOND:
        reasons.append("disk_busy")
    if codex["recently_active"]:
        reasons.append("codex_recent_activity")
    if codex["active_commands"]:
        reasons.append("codex_active_command")
    if active_work:
        reasons.append("active_work_service:" + ",".join(active_work))

    now = time.time()
    state = load_state()
    if now - state.get("updated_at", 0) > 3 * 60:
        state = {}
    previous_status = state.get("status")
    shutdown_requested = bool(state.get("shutdown_requested", False))
    if reasons:
        idle_since = None
        status = "NOT_READY"
        idle_seconds = 0
        shutdown_requested = False
    else:
        idle_since = state.get("idle_since") or now
        idle_seconds = int(now - idle_since)
        status = "READY" if idle_seconds >= REQUIRED_IDLE_SECONDS else "CANDIDATE"
        if status != "READY":
            shutdown_requested = False

    shutdown_request_result = "not_requested"
    if status == "READY" and not shutdown_requested:
        protection = work_protection()
        if protection:
            protected_exit(protection, datetime.now(SCHEDULE_TIMEZONE))
            return
        try:
            shutdown_request_result = request_safe_shutdown()
            shutdown_requested = True
        except Exception as error:
            shutdown_request_result = f"failed: {type(error).__name__}: {error}"

    state = {
        "idle_since": idle_since,
        "status": status,
        "updated_at": now,
        "shutdown_requested": shutdown_requested,
    }
    save_state(state)

    record = {
        "time": check_time.isoformat(timespec="seconds"),
        "status": status,
        "idle_seconds": idle_seconds,
        "cpu_percent": round(cpu_percent, 2),
        "network_bytes_per_second": round(network_bps, 1),
        "disk_bytes_per_second": round(disk_bps, 1),
        "reasons": reasons,
        "shutdown_requested": shutdown_requested,
        "shutdown_request_result": shutdown_request_result,
        "logged_in_users": users,
        "protected_processes": protected,
        "codex": codex,
        "running_docker_containers": containers,
        "active_work_services": active_work,
    }
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(LOG_FILE, 0o644)

    if shutdown_request_result.startswith("accepted_http_"):
        subprocess.run(
            [
                "logger",
                "-t",
                "shutdown-readiness-monitor",
                "READY: Home Assistant accepted the safe shutdown request",
            ],
            check=False,
        )

    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
