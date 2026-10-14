from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import socket
from urllib.parse import urlsplit

SOCKET_PATH = "/var/run/docker.sock"
API_VERSION = "v1.41"
TARGET_CONTAINER = "mihomo"


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=8)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET_PATH)


def docker_request(method, path):
    connection = UnixSocketHTTPConnection()
    try:
        connection.request(method, f"/{API_VERSION}{path}")
        response = connection.getresponse()
        body = response.read()
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {"message": body.decode("utf-8", errors="replace")[:300]}
        return response.status, data
    finally:
        connection.close()


def get_snapshot():
    code, data = docker_request("GET", f"/containers/{TARGET_CONTAINER}/json")
    if code != 200:
        message = data.get("message", "Docker returned an unexpected response")
        raise RuntimeError(f"Docker API {code}: {message}")
    state = data.get("State", {})
    return {
        "name": TARGET_CONTAINER,
        "state": state.get("Status", "unknown"),
        "running": bool(state.get("Running")),
        "image": (data.get("Config") or {}).get("Image"),
        "started_at": state.get("StartedAt"),
        "restart_count": data.get("RestartCount", 0),
        "exit_code": state.get("ExitCode"),
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path != "/status":
            self.send_json(404, {"detail": "not found"})
            return
        try:
            self.send_json(200, get_snapshot())
        except Exception as exc:
            self.send_json(502, {"detail": str(exc)[:300]})

    def do_POST(self):
        action = urlsplit(self.path).path.strip("/")
        if action not in {"start", "stop", "restart"}:
            self.send_json(404, {"detail": "unsupported action"})
            return
        try:
            current = get_snapshot()
            if action == "start" and not current["running"]:
                code, data = docker_request("POST", f"/containers/{TARGET_CONTAINER}/start")
            elif action == "stop" and current["running"]:
                code, data = docker_request("POST", f"/containers/{TARGET_CONTAINER}/stop?t=12")
            elif action == "restart":
                code, data = docker_request("POST", f"/containers/{TARGET_CONTAINER}/restart?t=12")
            else:
                code, data = 204, {}
            if code not in {204, 304}:
                raise RuntimeError(f"Docker API {code}: {data.get('message', 'action failed')}")
            self.send_json(200, {"action": action, "container": get_snapshot()})
        except Exception as exc:
            self.send_json(502, {"detail": str(exc)[:300]})

    def log_message(self, fmt, *args):
        print("mihomo-control: " + fmt % args, flush=True)


ThreadingHTTPServer(("0.0.0.0", 8001), Handler).serve_forever()
