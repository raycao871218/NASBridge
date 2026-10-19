"""Circle geofence configuration. The phone measures distance and reports to HA."""
from __future__ import annotations

import asyncio
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

router = APIRouter(prefix="/api/geofence", tags=["geofence"])
_lock = threading.Lock()
_search_lock = asyncio.Lock()
_search_cache: dict[str, tuple[float, list]] = {}
_last_search = 0.0
Integer = Annotated[int, Field(strict=True)]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: StrictBool = False
    name: Annotated[str, Field(strict=True, min_length=1, max_length=100)] = "我的围栏"
    fence_type: Literal["circle"] = "circle"
    center_latitude: Annotated[float | None, Field(strict=True, ge=-85, le=85)] = None
    center_longitude: Annotated[float | None, Field(strict=True, ge=-180, le=180)] = None
    radius_meters: Annotated[Integer, Field(ge=100, le=500000)] = 10000
    check_interval_seconds: Annotated[Integer, Field(ge=60, le=86400)] = 3600
    max_detection_seconds: Annotated[Integer, Field(ge=60, le=604800)] = 14400
    boundary_buffer_meters: Annotated[Integer, Field(ge=0, le=10000)] = 1000
    max_location_age_seconds: Annotated[Integer, Field(ge=60, le=3600)] = 900
    max_location_accuracy_meters: Annotated[Integer, Field(ge=100, le=10000)] = 3000
    test_mode: StrictBool = False

    @model_validator(mode="after")
    def validate_rules(self):
        if not self.name.strip():
            raise ValueError("围栏名称不能为空")
        if (self.center_latitude is None) != (self.center_longitude is None):
            raise ValueError("中心经纬度必须同时填写")
        if self.enabled and self.center_latitude is None:
            raise ValueError("启用前请先在地图上选择中心点")
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


def normalize_state(state):
    if not isinstance(state, dict) or type(state.get("revision")) is not int or state["revision"] < 0:
        raise ValueError("invalid persisted state")
    settings = dict(state["settings"])
    legacy = "region_id" in settings
    if legacy:
        # County -> circle is a semantic change: never invent a centre or silently enable a new fence.
        if settings.pop("region_id") != "510726":
            raise ValueError("unknown legacy region")
        settings["enabled"] = False
    return {**state, "settings": Settings.model_validate(settings).model_dump(), "migration_required": legacy}


def load_state():
    from .main import DATA_DIR, load_persisted_state
    return normalize_state(load_persisted_state("geofence_config", DATA_DIR / "geofence.yaml", default_state()))


def save_state(state):
    from .main import DATA_DIR, save_persisted_state
    save_persisted_state("geofence_config", DATA_DIR / "geofence.yaml", state)


def document(state):
    state = normalize_state(state) if "migration_required" not in state else state
    settings = Settings.model_validate(state["settings"])
    interval = 15 if settings.test_mode else settings.check_interval_seconds
    duration = 60 if settings.test_mode else settings.max_detection_seconds
    center = None if settings.center_latitude is None else {
        "latitude": settings.center_latitude, "longitude": settings.center_longitude}
    return {
        "schema_version": 2, "revision": state["revision"], "updated_at": state["updated_at"],
        "migration_required": state.get("migration_required", False),
        "settings": settings.model_dump(),
        "fence": {"type": "circle", "name": settings.name, "coordinate_system": "WGS-84",
                  "center": center, "radius_meters": settings.radius_meters},
        "effective": {"check_interval_seconds": interval, "max_detection_seconds": duration,
                      "max_checks": duration // interval, "first_check_after_seconds": interval},
        "rules": {
            "executor": "android", "ha_reporter": "android", "local_route": "home_immediately",
            "public_route": "start_periodic_location_checks", "outside_fence": "away_immediately",
            "inside_fence": "keep_previous_state", "unknown_location": "keep_previous_state",
            "window_expired": "away_if_still_public", "return_local": "cancel_checks_and_report_home",
            "offline": "unknown_and_cancel_window", "route_probe": "preserve_window_no_ha_write",
            "outside_margin": "radius_plus_boundary_buffer_plus_location_accuracy",
            "coordinate_requirement": "WGS84_great_circle_distance_in_meters",
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


async def geocode_query(query):
    """Fixed upstream only. Reuse NAS's configured Mihomo egress, if present."""
    from .main import load_config, mihomo_base_url, mihomo_get
    from .mobile_api import proxy_address
    config = load_config().get("mihomo", {})
    proxy = os.getenv("NASBRIDGE_GEOCODING_PROXY") or None
    if config and not proxy:
        ports = await mihomo_get("/configs", config)
        proxy = proxy_address(mihomo_base_url(config), ports)
    async with httpx.AsyncClient(proxy=proxy, timeout=httpx.Timeout(12, connect=5),
                                 follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", "https://nominatim.openstreetmap.org/search",
                                 params={"q": query, "format": "jsonv2", "limit": 5, "accept-language": "zh-CN"},
                                 headers={"User-Agent": "NASBridge-Geofence/2.0 (https://github.com/raycao871218/NASBridge)"}) as r:
            r.raise_for_status()
            raw = bytearray()
            async for chunk in r.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("geocoding response too large")
    import json
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("invalid response")
    items = []
    for item in data[:5]:
        lat, lon = float(item["lat"]), float(item["lon"])
        if not math.isfinite(lat) or not math.isfinite(lon) or not -85 <= lat <= 85 or not -180 <= lon <= 180:
            continue
        items.append({"name": str(item.get("display_name", ""))[:300], "latitude": lat, "longitude": lon})
    return items


@router.get("/search")
async def search(q: Annotated[str, Query(min_length=2, max_length=120)]):
    global _last_search
    query = q.strip()
    if len(query) < 2:
        raise HTTPException(422, "请输入至少两个字的地名")
    if _search_lock.locked():
        raise HTTPException(429, "搜索正在进行，请稍后重试")
    async with _search_lock:
        now = time.monotonic()
        cached = _search_cache.get(query)
        if cached and now - cached[0] < 86400:
            return response({"items": cached[1], "attribution": "© OpenStreetMap contributors"})
        if _last_search and now - _last_search < 1:
            raise HTTPException(429, "搜索过于频繁，请稍后重试")
        _last_search = now
        try:
            items = await geocode_query(query)
        except Exception:
            raise HTTPException(502, "地名搜索暂时不可用，请直接点击地图选点或输入经纬度") from None
        if len(_search_cache) >= 100:
            del _search_cache[next(iter(_search_cache))]
        _search_cache[query] = (now, items)
        return response({"items": items, "attribution": "© OpenStreetMap contributors"})
