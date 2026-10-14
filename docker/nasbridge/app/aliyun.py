"""NASBridge ECS security groups; credentials never leave the backend."""
import asyncio
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

router = APIRouter(prefix='/api/aliyun', tags=['aliyun'])
KEYS = ('ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_ACCESS_KEY_SECRET')


def validate_target(region, group=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,50}', region):
        raise HTTPException(422, '地域 ID 格式不正确')
    if group is not None and not re.fullmatch(r'sg-[A-Za-z0-9]+', group):
        raise HTTPException(422, '安全组 ID 格式不正确')


def call_api(action, region, params=None):
    validate_target(region)
    if not all(os.getenv(k) for k in KEYS):
        raise HTTPException(503, '未配置阿里云 AccessKey，请检查容器环境变量')
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest
    from aliyunsdkcore.acs_exception.exceptions import ServerException, ClientException
    client = AcsClient(os.environ[KEYS[0]], os.environ[KEYS[1]], region,
                       connect_timeout=5, timeout=15, auto_retry=False)
    req = CommonRequest()
    req.set_domain('ecs.aliyuncs.com')
    req.set_version('2014-05-26')
    req.set_action_name(action)
    req.set_method('POST')
    req.set_protocol_type('HTTPS')
    req.set_accept_format('json')
    req.add_query_param('RegionId', region)
    for key, value in (params or {}).items():
        req.add_query_param(key, value)
    try:
        return json.loads(client.do_action_with_exception(req))
    except (ServerException, ClientException) as exc:
        # Never serialize the SDK exception: it may contain request/credential data.
        code = re.sub(r'[^A-Za-z0-9_.-]', '', str(exc.get_error_code()))[:120]
        raise HTTPException(502, f'阿里云 {action} 调用失败：{code}。请检查 RAM 权限、地域和网络；写入失败后请先刷新规则确认结果。') from None
    except Exception:
        raise HTTPException(502, '阿里云响应异常或网络超时；如为写入操作，请刷新规则确认结果。') from None


def paged(action, region, params, container, item):
    result, seen = [], set()
    params = dict(params, MaxResults=100)
    for _ in range(100):
        data = call_api(action, region, params)
        result.extend(data.get(container, {}).get(item, []))
        token = data.get('NextToken')
        if not token:
            return result
        if token in seen:
            break
        seen.add(token)
        params['NextToken'] = token
    raise HTTPException(502, '分页未完成，请缩小查询范围后重试')


@router.get('/status')
def status():
    return {'configured': all(bool(os.getenv(k)) for k in KEYS)}


@router.get('/defaults')
def defaults():
    from .main import load_config
    settings = load_config().get('aliyun', {})
    return {'region': settings.get('default_region'), 'group_id': settings.get('default_security_group_id'), 'group_name': settings.get('default_security_group_name')}

@router.get('/regions')
async def regions():
    data = await asyncio.to_thread(call_api, 'DescribeRegions', 'cn-hangzhou')
    return {'items': data.get('Regions', {}).get('Region', [])}


@router.get('/security-groups')
async def groups(region: str):
    validate_target(region)
    items = await asyncio.to_thread(paged, 'DescribeSecurityGroups', region, {}, 'SecurityGroups', 'SecurityGroup')
    return {'items': [x for x in items if x.get('VpcId')]}


@router.get('/rules')
async def rules(region: str, group: str):
    validate_target(region, group)
    items = await asyncio.to_thread(paged, 'DescribeSecurityGroupAttribute', region,
                                   {'SecurityGroupId': group, 'Direction': 'ingress', 'NicType': 'intranet'},
                                   'Permissions', 'Permission')
    return {'items': items}


class OpenPorts(BaseModel):
    region: str = Field(max_length=52)
    group: str = Field(max_length=80)
    ports: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=80)
    protocol: Literal['tcp', 'udp'] = 'tcp'
    priority: int = Field(default=1, ge=1, le=100)
    description: str = Field(default='NASBridge', min_length=1, max_length=512)
    confirm: bool = False


def permissions(payload):
    validate_target(payload.region, payload.group)
    try:
        network = ipaddress.ip_network(payload.source.strip(), strict=True)
    except ValueError:
        raise HTTPException(422, '来源必须为有效 IP 或 CIDR（例如 192.0.2.1/32）；网段须填写网络地址') from None
    source_key = 'SourceCidrIp' if network.version == 4 else 'Ipv6SourceCidrIp'
    result = []
    for value in payload.ports.split(','):
        match = re.fullmatch(r'(\d{1,5})(?:[-/](\d{1,5}))?', value.strip())
        if not match:
            raise HTTPException(422, '端口格式示例：443,8080,9000-9010')
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= 65535:
            raise HTTPException(422, '端口范围必须在 1～65535 之间，起始不能大于结束')
        rule = {'IpProtocol': payload.protocol, 'PortRange': f'{start}/{end}', source_key: str(network),
                'Priority': str(payload.priority), 'Policy': 'accept', 'NicType': 'intranet',
                'Description': payload.description}
        if rule not in result:
            result.append(rule)
    if len(result) > 100:
        raise HTTPException(422, '一次最多添加 100 条规则')
    return result


@router.post('/open-ports')
async def open_ports(payload: OpenPorts, request: Request):
    # Require JSON and same-origin browser requests, matching the panel's local access model.
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        raise HTTPException(403, '不接受跨站请求')
    if request.headers.get('content-type', '').split(';')[0] != 'application/json':
        raise HTTPException(415, '请使用 application/json')
    items = permissions(payload)
    if not payload.confirm:
        return {'preview': True, 'region': payload.region, 'group': payload.group, 'items': items}
    params = {'SecurityGroupId': payload.group}
    for index, rule in enumerate(items, 1):
        for key, value in rule.items():
            params[f'Permissions.{index}.{key}'] = value
    data = await asyncio.to_thread(call_api, 'AuthorizeSecurityGroup', payload.region, params)
    return {'success': True, 'request_id': data.get('RequestId'), 'submitted_rules': len(items)}
