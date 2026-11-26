"""Restricted SSH controller. Run as the Ubuntu user who owns Codex."""
import base64
import fcntl
import importlib.util
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


EMAIL = re.compile(r"[a-zA-Z0-9.!_+%=-]+@[a-zA-Z0-9.-]+", re.ASCII)
AUTH_CLAIM = "https://api.openai.com/auth"


class Rejected(Exception):
    pass


def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and EMAIL.fullmatch(value)


def claims(token):
    encoded = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))


def identity(token):
    data = claims(token)
    address = data.get("email") or data.get("https://api.openai.com/profile", {}).get("email")
    return address, data.get(AUTH_CLAIM, {}).get("chatgpt_account_id")


def save(path, value):
    fd, name = tempfile.mkstemp(prefix=".codex-account-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def openai_client():
    proxy = os.environ.get("NASBRIDGE_CODEX_HTTPS_PROXY", "http://127.0.0.1:7890")
    return build_opener(ProxyHandler({"https": proxy} if proxy else {}), NoRedirect())


def refresh(record):
    request = Request("https://auth.openai.com/oauth/token", urlencode({
        "grant_type": "refresh_token",
        "refresh_token": record["refresh_token"],
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "scope": "openid profile email",
    }).encode(), {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with openai_client().open(request, timeout=14) as response:
            return json.loads(response.read(65536))
    except HTTPError as error:
        with error:
            if error.code in (400, 401):
                raise Rejected("reauth") from None
        raise Rejected("refresh_failed") from None
    except OSError:
        raise Rejected("refresh_failed") from None


def read_windows(access_token, account_id):
    request = Request("https://chatgpt.com/backend-api/wham/usage", headers={
        "Authorization": "Bearer " + access_token,
        "ChatGPT-Account-Id": account_id,
        "Accept": "application/json",
    })
    with openai_client().open(request, timeout=5) as response:
        limit = json.loads(response.read(65536)).get("rate_limit") or {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        window = limit.get(key) or {}
        used = window.get("used_percent")
        if type(used) not in (int, float) or not 0 <= used <= 100:
            continue
        windows.append({"remaining_percent": round(100 - used, 1),
                        "window_seconds": window.get("limit_window_seconds"),
                        "reset_at": window.get("reset_at")})
    return windows


def update_record(record, updated, email):
    id_token = updated.get("id_token") or record["id_token"]
    access_token = updated.get("access_token")
    if identity(id_token) != (email, record["chatgpt_account_id"]) or not access_token:
        raise Rejected("identity_mismatch")
    record.update(id_token=id_token, access_token=access_token,
                  refresh_token=updated.get("refresh_token") or record["refresh_token"],
                  token_updated_at_ms=int(time.time() * 1000))


def quota_for(email, tokens, record, store_path, store, previous):
    try:
        if tokens is not None:
            return read_windows(tokens["access_token"], tokens["account_id"])
        if record is None:
            return []
        access_token = record.get("access_token")
        try:
            usable = bool(access_token and claims(access_token)["exp"] > time.time() + 60)
        except (ValueError, TypeError, KeyError, IndexError):
            usable = False
        if usable:
            try:
                return read_windows(access_token, record["chatgpt_account_id"])
            except HTTPError as error:
                with error:
                    if error.code != 401:
                        return []
        if store_path.read_bytes() != previous:
            return []
        update_record(record, refresh(record), email)
        if store_path.read_bytes() != previous:
            return []
        save(store_path, store)
        return read_windows(record["access_token"], record["chatgpt_account_id"])
    except (Rejected, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return []


def quotas(auth_path, store_path, store, old_store, records, current, tokens, accounts):
    cache_path = auth_path.parent / ".nasbridge-codex-usage.json"
    try:
        cache = json.loads(cache_path.read_bytes())
    except (OSError, ValueError):
        cache = {}
    result = {}
    for email in dict.fromkeys([current, *accounts]):
        record = records.get(email)
        account_id = tokens["account_id"] if email == current else record["chatgpt_account_id"]
        entry = cache.get(email, {})
        now = time.time()
        if entry.get("account_id") == account_id and 0 <= now - entry.get("checked_at", 0) < 60:
            result[email] = entry["windows"]
            continue
        # Re-read after earlier accounts may have persisted rotated credentials.
        windows = quota_for(email, tokens if email == current else None, record, store_path, store, old_store)
        result[email] = windows
        cache[email] = {"account_id": account_id, "checked_at": now, "windows": windows}
        # Our successful store write is identifiable by its exact content.
        snapshot = store_path.read_bytes()
        if json.loads(snapshot) == store:
            old_store = snapshot
    save(cache_path, cache)
    return result


def manage(auth_path, store_path, target=None, expected=None, include_quota=False):
    with (auth_path.parent / ".account-switch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("busy") from None
        return manage_locked(auth_path, store_path, target, expected, include_quota)


def manage_locked(auth_path, store_path, target, expected, include_quota):
    old_auth, old_store = auth_path.read_bytes(), store_path.read_bytes()
    active, store = json.loads(old_auth), json.loads(old_store)
    if active.get("auth_mode") not in (None, "chatgpt") or active.get("OPENAI_API_KEY"):
        raise Rejected("unsupported_login")
    tokens = active["tokens"]
    current, current_id = identity(tokens["id_token"])
    if not valid_email(current) or not current_id or tokens.get("account_id") != current_id:
        raise Rejected("unsupported_login")

    records = {}
    duplicates = set()
    for record in store["accounts"].values():
        address = record.get("email")
        try:
            verified = (valid_email(address) and record.get("refresh_token")
                        and record.get("chatgpt_account_id")
                        and identity(record["id_token"]) == (address, record["chatgpt_account_id"]))
        except (ValueError, KeyError, IndexError, AttributeError, TypeError):
            continue
        if verified:
            if address in records:
                duplicates.add(address)
            records[address] = record
    for address in duplicates:
        records.pop(address)

    accounts = sorted(records, key=str.casefold)
    if target is None:
        result = {"current": current, "accounts": accounts}
        if include_quota:
            result["quota"] = quotas(auth_path, store_path, store, old_store, records, current, tokens, accounts)
        return result
    if current != expected:
        raise Rejected("stale")
    if target not in records:
        raise Rejected("unknown_account")
    if target == current:
        return {"current": current, "accounts": accounts, "changed": False}

    # Keep a newer refresh token issued by Codex itself when leaving this account.
    outgoing = records.get(current)
    active_time = datetime.fromisoformat(active["last_refresh"].replace("Z", "+00:00"))
    if (outgoing and outgoing["chatgpt_account_id"] == current_id and tokens.get("refresh_token")
            and active_time.timestamp() * 1000 >= (outgoing.get("token_updated_at_ms") or 0)):
        outgoing.update(id_token=tokens["id_token"], access_token=tokens.get("access_token"), refresh_token=tokens["refresh_token"],
                        token_updated_at_ms=int(active_time.timestamp() * 1000))

    record = records[target]
    updated = refresh(record)
    account_id = record["chatgpt_account_id"]
    update_record(record, updated, target)
    now = datetime.now(timezone.utc)
    if store_path.read_bytes() != old_store:
        raise Rejected("stale")
    # Keep the rotated credential even if an outside Codex process changed its login.
    save(store_path, store)
    if auth_path.read_bytes() != old_auth:
        raise Rejected("stale")
    save(auth_path, {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                     "tokens": {"id_token": record["id_token"], "access_token": record["access_token"],
                                "refresh_token": record["refresh_token"], "account_id": account_id},
                     "last_refresh": now.isoformat().replace("+00:00", "Z")})
    if identity(json.loads(auth_path.read_bytes())["tokens"]["id_token"]) != (target, account_id):
        raise Rejected("stale")
    return {"current": target, "accounts": accounts, "changed": True}


def main():
    parts = os.environ.get("SSH_ORIGINAL_COMMAND", "").split()
    try:
        if parts in (["power-status"], ["poweroff"]):
            spec = importlib.util.spec_from_file_location(
                "manual_poweroff", Path(__file__).with_name("manual-poweroff.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            print(json.dumps(module.power(parts[0])))
            return
        if parts in (["status"], ["status-quota"]):
            args = ()
        elif len(parts) == 3 and parts[0] == "switch" and all(map(valid_email, parts[1:])):
            args = tuple(parts[1:])
        else:
            raise Rejected("unsupported")
        codex_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        result = manage(codex_dir / "auth.json", Path.home() / ".cc-switch/codex_oauth_auth.json", *args,
                        include_quota=parts == ["status-quota"])
    except Rejected as error:
        result = {"error": str(error)}
    except Exception:
        result = {"error": "unavailable"}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    os.umask(0o077)
    main()
