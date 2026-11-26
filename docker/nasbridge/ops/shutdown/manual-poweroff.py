"""Fixed actions called by the NASBridge forced SSH command."""
import json
import fcntl
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


SYSTEMCTL = "/usr/bin/systemctl"
SAFE_STATES = {"inactive", "failed"}
WEBHOOK = Path(__file__).with_name("manual-poweroff-webhook")
REQUEST = Path.home() / ".local/state/nasbridge/manual-poweroff.request"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=3).stdout


def protection():
    for service in ("rhino-daily.service", "rhino-retry.service"):
        output = run(SYSTEMCTL, "show", service, "-p", "LoadState", "-p", "ActiveState")
        properties = dict(line.split("=", 1) for line in output.splitlines())
        if properties["LoadState"] != "loaded":
            raise ValueError("Missing work service")
        if properties["ActiveState"] not in SAFE_STATES:
            return "work_protected"
    retry = Path.home() / "rhino-finance/state/retry.json"
    try:
        with retry.open() as stream:
            state = json.load(stream)["status"]
    except FileNotFoundError:
        state = "completed"
    if state in {"waiting", "retrying"}:
        return "work_protected"
    if state not in {"completed", "cancelled"}:
        raise ValueError("Unknown retry state")
    inhibitors = json.loads(run("/usr/bin/busctl", "--json=short", "call", "org.freedesktop.login1",
                                "/org/freedesktop/login1", "org.freedesktop.login1.Manager", "ListInhibitors"))
    if any("shutdown" in row[0].split(":") and row[3] != "delay" for row in inhibitors["data"][0]):
        return "power_inhibited"
    return "ready"


def request_home_assistant():
    url = WEBHOOK.read_text().strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid Home Assistant webhook")
    if not parsed.path.startswith("/api/webhook/") or parsed.query or parsed.fragment:
        raise ValueError("Invalid Home Assistant webhook")
    request = Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with build_opener(NoRedirect()).open(request, timeout=6) as response:
        if not 200 <= response.status < 300:
            raise ValueError("Home Assistant rejected the request")


def power(action):
    try:
        if not WEBHOOK.is_file():
            return {"error": "power_unavailable"}
        REQUEST.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with REQUEST.open("a+") as marker:
            try:
                fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"power": "power_pending"}
            marker.seek(0)
            previous = marker.read().strip().split()
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            elapsed = time.time() - float(previous[1]) if len(previous) == 2 and previous[0] == boot_id else None
            if elapsed is not None and 0 <= elapsed < 45:
                return {"power": "power_pending"}
            state = protection()
            if state != "ready":
                return {"power": state}
            if action != "poweroff":
                return {"power": "power_still_online" if elapsed is not None else "ready"}
            marker.seek(0)
            marker.truncate()
            marker.write(f"{boot_id} {time.time()}\n")
            marker.flush()
            request_home_assistant()
            return {"power": "power_pending"}
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        return {"error": "power_unavailable"}
