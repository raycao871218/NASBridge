"""Remote controller adapter. Token stays on the controller host."""
import asyncio
import ipaddress
import json
import re
import shlex
from urllib.parse import urlsplit
from fastapi.responses import RedirectResponse
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictBool

update_lock = asyncio.Lock()

router = APIRouter(prefix='/api/zerotier', tags=['zerotier'])
# Fixed local endpoint; requests cannot select hosts, files or arbitrary controller APIs.
REMOTE = r'''
import json,sys,subprocess,urllib.request,urllib.error,time
args=json.loads(sys.argv[1])
try:
 token=subprocess.run(['docker','exec','ztncui','cat','/var/lib/zerotier-one/authtoken.secret'],capture_output=True,check=True,timeout=10).stdout.decode().strip()
 def api(path,body=None):
  req=urllib.request.Request('http://127.0.0.1:9993'+path,data=None if body is None else json.dumps(body).encode(),headers={'X-ZT1-AUTH':token,'Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=6) as r:return json.load(r)
 def notes():
  code="import json,pathlib; root=pathlib.Path('/opt/key-networks/ztncui/etc/storage'); items=[json.loads(p.read_text()) for p in root.iterdir() if p.is_file() and len(p.name)==32]; print(json.dumps({d['key']:d.get('value','') for d in items if isinstance(d,dict) and 'key' in d}))"
  return json.loads(subprocess.check_output(['docker','exec','ztncui','python3','-c',code],timeout=10))
 def write_note(member_id,name):
  code = "import json,pathlib,hashlib,os,sys,shutil,time,signal\nmid,name=sys.argv[1:]\nroot=pathlib.Path('/opt/key-networks/ztncui/etc/storage')\npath=root/hashlib.md5(mid.encode()).hexdigest()\nold=json.loads(path.read_text()) if path.exists() else {}\nchanged=old.get('value')!=name\nif changed:\n    pids=[]\n    for proc in pathlib.Path('/proc').iterdir():\n        if not proc.name.isdigit():continue\n        try:cmd=(proc/'cmdline').read_bytes().split(bytes([0]))[0]\n        except (OSError,ProcessLookupError):continue\n        if cmd==b'/opt/key-networks/ztncui/ztncui':pids.append(int(proc.name))\n    if len(pids)!=1:raise RuntimeError('Cannot identify ztncui UI process')\n    backup=root.parent/'nasbridge-note-backups';backup.mkdir(exist_ok=True)\n    st=path.stat() if path.exists() else root.stat()\n    if path.exists():shutil.copy2(path,backup/(path.name+'.'+str(time.time_ns())))\n    temp=root/(path.name+'.tmp')\n    temp.write_text(json.dumps({'key':mid,'value':name}))\n    os.chown(temp,st.st_uid,st.st_gid);os.chmod(temp,0o600);temp.replace(path)\n    os.kill(pids[0],signal.SIGTERM)\nprint(json.dumps({'saved':True,'changed':changed}))\n"
  result=json.loads(subprocess.check_output(['docker','exec','ztncui','python3','-c',code,member_id,name],timeout=10))
  if result['changed']:
   ready=False
   for _ in range(15):
    time.sleep(1)
    try:
     with urllib.request.urlopen('http://127.0.0.1:14000/',timeout=1) as r:
      ready=r.status==200
     if ready:break
    except Exception:pass
   if not ready:raise RuntimeError('ztncui UI restart pending')
  if notes().get(member_id)!=name:raise RuntimeError('note readback mismatch')
 def network(d):return {k:d.get(k) for k in ['id','name','private','ipAssignmentPools','routes','mtu']}
 def member(d):return {k:d.get(k) for k in ['id','address','name','authorized','ipAssignments','lastAuthorizedTime','lastDeauthorizedTime']}
 mode=args['mode']
 if mode=='networks':
  ids=api('/controller/network')
  if len(ids)>100:raise ValueError('too many networks')
  result={'items':[network(api('/controller/network/'+n)) for n in ids]}
 else:
  base='/controller/network/'+args['network']
  config=api(base)
  if mode=='members':
   ids=api(base+'/member')
   if len(ids)>500:raise ValueError('too many members')
   result={'network':network(config),'items':[member(api(base+'/member/'+m)) for m in ids]}; names=notes()
   for item in result['items']:item['name']=names.get(item.get('id') or item.get('address'),'')
  else:
   path=base+'/member/'+args['member']
   before=api(path)
   patch=dict(args['patch']); name=patch.pop('name',None)
   if patch:api(path,patch)
   after=api(path)
   if any(after.get(k)!=v for k,v in patch.items()):
    print(json.dumps({'error':'写入后回读不一致，请刷新成员列表核对'}));sys.exit(0)
   if name is not None:write_note(args['member'],name)
   result={'verified':True,'member':member(after)}
   result['member']['name']=notes().get(args['member'],'')
 print(json.dumps(result))
except urllib.error.HTTPError as e:
 print(json.dumps({'error':'ZeroTier API HTTP '+str(e.code)}))
except Exception as e:
 print(json.dumps({'error':'ZeroTier 查询或更新失败：'+type(e).__name__+'；如已提交修改，请刷新成员列表核对'}))
'''


def check_id(value, length):
    if not re.fullmatch('[0-9a-f]{%d}' % length, value):
        raise HTTPException(422, 'ZeroTier ID 格式不正确')


def remote(args):
    from .main import remote_ssh_client, reverse_proxy_settings, load_config
    client = remote_ssh_client(reverse_proxy_settings(load_config()))
    try:
        command='python3 -c '+shlex.quote(REMOTE)+' '+shlex.quote(json.dumps(args))
        _, out, err=client.exec_command(command, timeout=45)
        raw=out.read()  # Drain before waiting for exit; avoid SSH window deadlock.
        if out.channel.recv_exit_status()!=0:
            raise HTTPException(502,'无法执行控制器查询，请检查 SSH 与 ztncui 容器')
        result=json.loads(raw)
        if 'error' in result:raise HTTPException(502,result['error'])
        return result
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502,'控制器连接超时或响应异常；修改结果未知时请先刷新核对') from None
    finally:
        client.close()


@router.get('/admin')
def admin_page():
    from .main import load_config
    url = str(load_config().get('zerotier', {}).get('admin_url', '')).strip()
    parsed = urlsplit(url)
    if not url:
        raise HTTPException(404, '请在本地配置 zerotier.admin_url')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or any(ord(c) < 32 for c in url):
        raise HTTPException(400, 'ZeroTier 管理页面地址配置无效')
    return RedirectResponse(url, status_code=307, headers={'Cache-Control': 'no-store'})


@router.get('/networks')
async def networks():
    from .main import load_config
    result = await asyncio.to_thread(remote, {'mode':'networks'})
    result['default_network'] = load_config().get('zerotier', {}).get('default_network_id')
    return result


@router.get('/networks/{network}/members')
async def members(network: str):
    check_id(network,16)
    return await asyncio.to_thread(remote,{'mode':'members','network':network})


class MemberPatch(BaseModel):
    name: str | None=Field(default=None,max_length=128)
    authorized: StrictBool | None=None
    ipAssignments: list[str] | None=Field(default=None,max_length=32)
    confirm: StrictBool=False


@router.post('/networks/{network}/members/{member}')
async def update_member(network: str, member: str, payload: MemberPatch, request: Request):
    check_id(network,16);check_id(member,10)
    origin=request.headers.get('origin')
    if origin and urlsplit(origin).netloc!=request.headers.get('host'):
        raise HTTPException(403,'不接受跨站请求')
    if request.headers.get('content-type','').split(';')[0]!='application/json':
        raise HTTPException(415,'请使用 JSON')
    if not payload.confirm:raise HTTPException(400,'请确认成员变更')
    patch=payload.model_dump(exclude_none=True,exclude={'confirm'})
    if not patch:raise HTTPException(422,'没有可更新的字段')
    if 'ipAssignments' in patch:
        try:patch['ipAssignments']=list(dict.fromkeys(str(ipaddress.ip_address(x.strip())) for x in patch['ipAssignments']))
        except ValueError:raise HTTPException(422,'成员 IP 必须为有效 IPv4/IPv6 地址，不带 / 掩码') from None
    async with update_lock:
        return await asyncio.to_thread(remote,{'mode':'update','network':network,'member':member,'patch':patch})
