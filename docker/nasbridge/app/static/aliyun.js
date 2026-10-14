let aliyunBusy=false, aliyunPending=null, aliyunEpoch=0, aliDefaultsLoaded=false;
const aliEl=id=>document.getElementById(id);
async function aliApi(path, payload){
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),25000);
  try {
    const options=payload?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}:{cache:'no-store'};
    const r=await fetch('/api/aliyun/'+path,{...options,signal:controller.signal});
    const text=await r.text(); let d;
    try{d=JSON.parse(text);}catch{throw new Error(`服务器返回异常（HTTP ${r.status}），请刷新规则确认结果`);}
    if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));
    return d;
  } catch(e){
    if(e.name==='AbortError')throw new Error(payload?.confirm?'提交等待超过 25 秒，云端结果未知。请先读取规则确认，勿重复提交。':'读取或预览超时，请稍后重试。');
    throw e;
  } finally {clearTimeout(timer);}
}
function aliRememberSelection(){try{const region=aliEl('aliRegion').value.trim(),group=aliEl('aliGroup').value;if(region)localStorage.setItem('nasbridge-aliyun-region',region);if(group)localStorage.setItem('nasbridge-aliyun-group',group);}catch{}}function aliMessage(text){aliEl('aliStatus').textContent=text;aliEl('aliActionStatus').textContent=text;}
function aliInvalidate(){aliyunPending=null; aliEl('aliApply').disabled=true; aliEl('aliPreview').textContent='';}
function aliClear(){aliyunEpoch++; aliInvalidate(); aliEl('aliGroup').innerHTML='<option value="">请选择安全组</option>'; aliEl('aliRules').textContent='请选择安全组后读取规则';}
function aliPayload(){
  const p={region:aliEl('aliRegion').value.trim(),group:aliEl('aliGroup').value,ports:aliEl('aliPorts').value.trim(),source:aliEl('aliSource').value.trim(),protocol:aliEl('aliProtocol').value,priority:Number(aliEl('aliPriority').value),description:aliEl('aliDescription').value.trim()||'NASBridge'};
  if(!p.region||!p.group)throw new Error('请填写地域并选择安全组'); return p;
}
async function aliRun(fn){
  if(aliyunBusy)return;
  aliyunBusy=true;
  const controls=[...aliEl('aliyunView').querySelectorAll('input,select,button')];
  const states=controls.map(x=>x.disabled);
  controls.forEach(x=>x.disabled=true);
  const apply=aliEl('aliApply');
  apply.textContent='处理中…';
  try{await fn();}catch(e){aliMessage(e.message||String(e));}
  finally{
    controls.forEach((x,i)=>x.disabled=states[i]);
    aliyunBusy=false;
    apply.disabled=!aliyunPending;
    apply.textContent='确认开放端口';
  }
}
async function aliConnect(){await aliAutoLoadDefaults(true);}
async function aliAutoLoadDefaults(force=false){if(aliDefaultsLoaded&&!force)return;await aliRun(async()=>{
  aliMessage('正在自动读取默认地域、安全组和规则…');
  const status=await aliApi('status');if(!status.configured)throw new Error('容器尚未配置 AccessKey');
  const [defaults,regions]=await Promise.all([aliApi('defaults'),aliApi('regions')]);
  aliEl('aliRegions').innerHTML=regions.items.map(x=>`<option value="${esc(x.RegionId)}">${esc(x.LocalName||x.RegionId)}</option>`).join('');
  let savedRegion='',savedGroup='';try{savedRegion=localStorage.getItem('nasbridge-aliyun-region')||'';savedGroup=localStorage.getItem('nasbridge-aliyun-group')||'';}catch{}
  const validRegion=value=>regions.items.some(x=>x.RegionId===value);
  const region=[savedRegion,defaults.region].find(validRegion)||regions.items[0]?.RegionId;
  if(!region)throw new Error('阿里云账号没有可用地域');
  aliEl('aliRegion').value=region;aliClear();
  const groups=await aliApi('security-groups?region='+encodeURIComponent(region));
  aliEl('aliGroup').innerHTML='<option value="">请选择安全组</option>'+groups.items.map(x=>`<option value="${esc(x.SecurityGroupId)}">${esc(x.SecurityGroupName||'未命名')} · ${esc(x.SecurityGroupId)}</option>`).join('');
  const chosen=[savedGroup,defaults.group_id].find(value=>groups.items.some(x=>x.SecurityGroupId===value))||groups.items.find(x=>x.SecurityGroupName===defaults.group_name)?.SecurityGroupId||(groups.items.length===1?groups.items[0].SecurityGroupId:groups.items[0]?.SecurityGroupId);
  if(!chosen)throw new Error(`地域 ${region} 没有 VPC 安全组`);
  aliEl('aliGroup').value=chosen;
  try{localStorage.setItem('nasbridge-aliyun-region',region);localStorage.setItem('nasbridge-aliyun-group',chosen);}catch{}
  await aliReadRules();aliDefaultsLoaded=true;
  aliMessage(`已自动读取 ${region} / ${groups.items.find(x=>x.SecurityGroupId===chosen)?.SecurityGroupName||chosen} 的入方向规则，可在上方切换地域和安全组。`);
});}
async function aliGroups(){await aliRun(async()=>{
  aliClear();const region=aliEl('aliRegion').value.trim();if(!region)throw new Error('请填写地域');
  aliMessage('正在读取安全组…');const d=await aliApi('security-groups?region='+encodeURIComponent(region));
  aliEl('aliGroup').innerHTML='<option value="">请选择安全组</option>'+d.items.map(x=>`<option value="${esc(x.SecurityGroupId)}">${esc(x.SecurityGroupName||'未命名')} · ${esc(x.SecurityGroupId)}</option>`).join('');
  aliMessage(d.items.length?`已读取 ${d.items.length} 个 VPC 安全组`:'该地域没有 VPC 安全组，请确认地域');
});}
async function aliReadRules(){
  const region=aliEl('aliRegion').value.trim(),group=aliEl('aliGroup').value, epoch=aliyunEpoch;
  if(!group)throw new Error('请先选择安全组');
  const d=await aliApi('rules?'+new URLSearchParams({region,group}));if(epoch!==aliyunEpoch)return;
  aliEl('aliRules').innerHTML=d.items.length?'<table><thead><tr><th>策略</th><th>协议</th><th>端口</th><th>来源</th><th>优先级</th><th>说明</th></tr></thead><tbody>'+d.items.map(x=>`<tr><td>${esc(x.Policy)}</td><td>${esc(x.IpProtocol)}</td><td>${esc(x.PortRange||x.PortRangeListId)}</td><td>${esc(x.SourceCidrIp||x.Ipv6SourceCidrIp||x.SourceGroupId||x.SourcePrefixListId||'—')}</td><td>${esc(x.Priority)}</td><td>${esc(x.Description)}</td></tr>`).join('')+'</tbody></table>':'暂无入方向规则';
}
async function loadAliyunRules(){await aliRun(async()=>{aliMessage('正在读取入方向规则…');await aliReadRules();aliMessage('规则已更新');});}
async function previewAliyunPorts(){await aliRun(async()=>{
  aliInvalidate();const p=aliPayload(),d=await aliApi('open-ports',p);aliyunPending=p;
  aliEl('aliPreview').textContent=`地域：${p.region}\n安全组：${p.group}\n`+d.items.map(x=>`${x.IpProtocol.toUpperCase()} ${x.PortRange} ← ${x.SourceCidrIp||x.Ipv6SourceCidrIp}，优先级 ${x.Priority}`).join('\n')+'\n这些入方向放行规则将影响安全组关联的实例。';aliMessage('预览完成，尚未修改云端规则');
});}
async function submitAliyunPorts(){await aliRun(async()=>{
  if(!aliyunPending)throw new Error('请先预览规则');const p=aliyunPending;aliInvalidate();
  aliMessage('正在提交规则…');const d=await aliApi('open-ports',{...p,confirm:true});
  aliMessage(`提交成功：${d.submitted_rules} 条规则；请求 ID：${d.request_id||'—'}。重复规则不会重复添加。`);
  try{await aliReadRules();}catch(e){aliMessage('规则已提交成功，但刷新失败：'+e.message);}
});}
