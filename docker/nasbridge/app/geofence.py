"""Versioned geofence settings only. Location checks and HA writes run on the phone."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

router = APIRouter(prefix="/api/geofence", tags=["geofence"])
GEODATA = Path(__file__).parent / "geodata"
_lock = threading.Lock()
Integer = Annotated[int, Field(strict=True)]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool = False
    region_id: Literal["510726"] = "510726"
    check_interval_seconds: Annotated[Integer, Field(ge=60, le=86400)] = 3600
    max_detection_seconds: Annotated[Integer, Field(ge=60, le=604800)] = 14400
    boundary_buffer_meters: Annotated[Integer, Field(ge=0, le=10000)] = 1000
    max_location_age_seconds: Annotated[Integer, Field(ge=60, le=3600)] = 900
    max_location_accuracy_meters: Annotated[Integer, Field(ge=100, le=10000)] = 3000
    test_mode: StrictBool = False

    @model_validator(mode="after")
    def validate_window(self):
        if self.max_detection_seconds < self.check_interval_seconds:
            raise ValueError("最长检测时长不能小于检测间隔")
        if self.max_detection_seconds % self.check_interval_seconds:
            raise ValueError("最长检测时长必须是检测间隔的整数倍")
        if self.max_detection_seconds // self.check_interval_seconds > 24:
            raise ValueError("每轮最多检测 24 次")
        return self


class Update(Settings):
    expected_revision: Annotated[Integer, Field(ge=0)]


def default_state():
    return {"revision": 0, "updated_at": None, "settings": Settings().model_dump()}


def load_state():
    from .main import DATA_DIR, load_persisted_state
    state = load_persisted_state("geofence_config", DATA_DIR / "geofence.yaml", default_state())
    # Never silently replace corrupt persisted rules with enabled defaults.
    if not isinstance(state, dict) or type(state.get("revision")) is not int or state["revision"] < 0:
        raise ValueError("invalid persisted state")
    Settings.model_validate(state["settings"])
    return state


def save_state(state):
    from .main import DATA_DIR, save_persisted_state
    save_persisted_state("geofence_config", DATA_DIR / "geofence.yaml", state)


def boundary():
    raw = (GEODATA / "beichuan.geojson").read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def document(state):
    settings = Settings.model_validate(state["settings"])
    region, digest = boundary()
    interval = 15 if settings.test_mode else settings.check_interval_seconds
    duration = 60 if settings.test_mode else settings.max_detection_seconds
    return {
        "schema_version": 1,
        "revision": state["revision"],
        "updated_at": state["updated_at"],
        "settings": settings.model_dump(),
        "effective": {"check_interval_seconds": interval, "max_detection_seconds": duration,
                      "max_checks": duration // interval, "first_check_after_seconds": interval},
        "region": {"id": "510726", "name": region["properties"]["name"],
                   "coordinate_system": "GCJ-02", "boundary_sha256": digest,
                   "boundary_url": "/api/geofence/boundary", "source": region["properties"]["source"]},
        "rules": {
            "executor": "android", "ha_reporter": "android", "local_route": "home_immediately",
            "public_route": "start_periodic_location_checks", "outside_region": "away_immediately",
            "inside_region": "keep_previous_state", "unknown_location": "keep_previous_state",
            "window_expired": "away_if_still_public", "return_local": "cancel_checks_and_report_home",
            "offline": "unknown_and_cancel_window", "route_probe": "preserve_window_no_ha_write",
            "outside_margin": "boundary_buffer_plus_location_accuracy",
            "coordinate_requirement": "convert_WGS84_location_to_GCJ02_before_polygon_test",
            "cached_config": "keep_last_valid_configuration_until_next_successful_refresh",
        },
    }


def response(data):
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/config")
def get_config():
    try:
        with _lock:
            return response(document(load_state()))
    except Exception:
        raise HTTPException(503, "围栏配置读取失败，请稍后重试") from None


@router.put("/config")
def update_config(payload: Update, request: Request):
    # Match existing dashboard access control; additionally reject cross-origin browser writes.
    origin = request.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
            raise HTTPException(403, "不接受跨站配置修改")
    try:
        with _lock:
            current = load_state()
            if current["revision"] != payload.expected_revision:
                raise HTTPException(409, "配置已被其他页面修改，请重新读取后再保存")
            state = {"revision": current["revision"] + 1,
                     "updated_at": datetime.now(timezone.utc).isoformat(),
                     "settings": payload.model_dump(exclude={"expected_revision"})}
            save_state(state)
            return response(document(state))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "围栏配置保存失败，未确认保存成功，请刷新核对") from None


@router.get("/boundary")
def get_boundary():
    try:
        region, digest = boundary()
        return response({"schema_version": 1, "coordinate_system": "GCJ-02", "sha256": digest, "feature": region})
    except Exception:
        raise HTTPException(503, "围栏边界读取失败") from None


@router.get("/preview")
def get_preview():
    try:
        return response(json.loads((GEODATA / "mianyang.geojson").read_bytes()))
    except Exception:
        raise HTTPException(503, "预览地图读取失败") from None
