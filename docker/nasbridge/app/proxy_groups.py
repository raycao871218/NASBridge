"""Proxy group selection through the configured Mihomo controller only."""
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/mihomo/proxies')
SELECTABLE = {'Selector', 'URLTest', 'Fallback'}
AUTOMATIC = {'URLTest', 'Fallback'}
TEST_URL = 'https://www.gstatic.com/generate_204'


class GroupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    group: str = Field(min_length=1, max_length=512)


class Selection(GroupRequest):
    name: str = Field(min_length=1, max_length=512)


class DelayRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=512)


def check_origin(request):
    origin = request.headers.get('origin')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
        raise HTTPException(403, '不接受跨站操作')


async def controller(method, path, **kwargs):
    from .main import load_config, mihomo_base_url, mihomo_headers
    settings = load_config().get('mihomo', {})
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.request(method, mihomo_base_url(settings) + path,
                                            headers=mihomo_headers(settings), **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Mihomo 控制器暂不可用或拒绝了操作，请刷新确认状态') from None


async def registry():
    data = await controller('GET', '/proxies')
    proxies = data.get('proxies')
    if not isinstance(proxies, dict):
        raise HTTPException(502, 'Mihomo 返回了无效的代理列表')
    return proxies


def group_record(proxies, name):
    group = proxies.get(name)
    if not isinstance(group, dict) or not isinstance(group.get('all'), list):
        raise HTTPException(404, '代理组不存在，请刷新列表')
    return group


def public_proxy(name, proxy):
    history = proxy.get('history') or []
    latest = history[-1] if history and isinstance(history[-1], dict) else {}
    return {'name': name, 'type': proxy.get('type', ''), 'now': proxy.get('now', ''),
            'delay': latest.get('delay'), 'tested_at': latest.get('time'), 'alive': proxy.get('alive'),
            'udp': bool(proxy.get('udp'))}


@router.get('')
async def groups():
    proxies = await registry()
    items = []
    for name, proxy in proxies.items():
        if not isinstance(proxy, dict) or not isinstance(proxy.get('all'), list):
            continue
        item = public_proxy(name, proxy)
        item.update(fixed=proxy.get('fixed', ''), selectable=proxy.get('type') in SELECTABLE,
                    automatic=proxy.get('type') in AUTOMATIC,
                    members=[public_proxy(member, proxies.get(member, {})) for member in proxy['all'] if isinstance(member, str)])
        items.append(item)
    return {'items': items}


@router.put('/selection')
async def select(payload: Selection, request: Request):
    check_origin(request)
    group = group_record(await registry(), payload.group)
    if group.get('type') not in SELECTABLE:
        raise HTTPException(400, '此代理组不支持手动切换')
    if payload.name not in group['all']:
        raise HTTPException(400, '目标节点不在该代理组中，请刷新列表')
    path = '/proxies/' + quote(payload.group, safe='')
    await controller('PUT', path, json={'name': payload.name})
    current = await controller('GET', path)
    if current.get('now') != payload.name and current.get('fixed') != payload.name:
        raise HTTPException(409, '已提交切换，但尚未确认当前节点，请刷新查看')
    return {'status': 'ok', 'message': '已切换到 ' + payload.name}


@router.post('/automatic')
async def automatic(payload: GroupRequest, request: Request):
    check_origin(request)
    group = group_record(await registry(), payload.group)
    if group.get('type') not in AUTOMATIC:
        raise HTTPException(400, '此代理组不支持恢复自动选择')
    path = '/proxies/' + quote(payload.group, safe='')
    await controller('DELETE', path)
    current = await controller('GET', path)
    if current.get('fixed'):
        raise HTTPException(409, '已提交恢复自动选择，请刷新确认状态')
    return {'status': 'ok', 'message': '已恢复自动选择'}


@router.post('/delay')
async def delay(payload: DelayRequest, request: Request):
    check_origin(request)
    if payload.name not in await registry():
        raise HTTPException(404, '节点不存在，请刷新列表')
    data = await controller('GET', '/proxies/' + quote(payload.name, safe='') + '/delay',
                            params={'url': TEST_URL, 'timeout': 5000})
    return {'name': payload.name, 'delay': data.get('delay')}
