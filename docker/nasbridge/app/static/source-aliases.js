let sourceConnections=[];
function sourceAddress(ip,port){return `${ip.includes(':')?'['+ip+']':ip}${port!==null&&port!==''?':'+port:''}`;}
function renderSourceConnections(){
  const select=document.getElementById('sourceFilter');
  const selected=select.value;
  const groups=new Map();
  sourceConnections.forEach(c=>groups.set(c.source_key||'ip:'+c.source_ip,c.source_alias||c.source_ip||'未知来源'));
  select.innerHTML='<option value="">全部来源</option>'+[...groups].map(([key,name])=>`<option value="${esc(key)}">${esc(name)}</option>`).join('');
  if(selected&&!groups.has(selected)) select.insertAdjacentHTML('beforeend',`<option value="${esc(selected)}">所选来源（当前无连接）</option>`);
  select.value=selected;
  const conns=sourceConnections.filter(c=>!selected||(c.source_key||'ip:'+c.source_ip)===selected);
  document.getElementById('sourceTraffic').textContent=`${conns.length} 个连接 · 下载 ${bytes(conns.reduce((n,c)=>n+Number(c.download||0),0))} · 上传 ${bytes(conns.reduce((n,c)=>n+Number(c.upload||0),0))}`;
  document.getElementById('connections').innerHTML=conns.length?`<table><tr><th>路由</th><th>来源</th><th>目标</th><th>链路</th><th>规则</th><th>流量</th></tr>${conns.map(c=>`<tr><td>${mode(c.route)}</td><td>${c.source_alias?'<strong>'+esc(c.source_alias)+'</strong><br>':''}<code>${esc(sourceAddress(c.source_ip,c.source_port))}</code><br><span class="muted small">${esc(c.process)}</span><br><button type="button" data-source-ip="${esc(c.source_ip)}" data-source-port="${esc(c.source_port)}" onclick="prefillSourceAlias(this)">设置别名</button></td><td>${esc(c.host)}:${esc(c.destination_port)}</td><td>${esc((c.chains||[]).join(' → '))}</td><td>${esc(c.rule)}</td><td>${bytes(c.download)} ↓<br>${bytes(c.upload)} ↑</td></tr>`).join('')}</table>`:'当前来源没有活动连接。';
}
function prefillSourceAlias(button){
  const input=document.getElementById('sourceAliasIp');
  input.closest('details').open=true;
  input.value=button.dataset.sourceIp;
  document.getElementById('sourceAliasPort').value='';
  document.getElementById('sourceAliasPort').placeholder=`留空：全部端口；当前 ${button.dataset.sourcePort}`;
  document.getElementById('sourceAliasName').value='';
  document.getElementById('sourceAliasName').focus();
}
async function sourceAliasRequest(method,payload,id){
  const r=await fetch('/api/mihomo/source-aliases'+(id?'/'+encodeURIComponent(id):''),{method,cache:'no-store',headers:{'Content-Type':'application/json'},...(payload?{body:JSON.stringify(payload)}:{})});
  const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'请求失败');
  document.getElementById('sourceAliasRules').innerHTML=data.items.length?`<table><tr><th>来源</th><th>别名</th><th>操作</th></tr>${data.items.map(r=>`<tr><td>${esc(sourceAddress(r.ip,r.port))}${r.port===null?'（全部端口）':''}</td><td>${esc(r.name)}</td><td><button type="button" data-rule-id="${esc(r.id)}" onclick="deleteSourceAlias(this.dataset.ruleId)">删除</button></td></tr>`).join('')}</table>`:'尚未设置别名。';
}
async function loadSourceAliases(){try{await sourceAliasRequest('GET');}catch(e){document.getElementById('sourceAliasStatus').textContent=e.message;}}
async function saveSourceAlias(event){
  event.preventDefault();
  const status=document.getElementById('sourceAliasStatus');
  const port=document.getElementById('sourceAliasPort').value;
  try{await sourceAliasRequest('PUT',{ip:document.getElementById('sourceAliasIp').value,port:port===''?null:Number(port),name:document.getElementById('sourceAliasName').value});status.textContent='已保存（相同 IP 和端口的别名会被更新）';await load();}catch(e){status.textContent='保存失败：'+e.message;}
}
async function deleteSourceAlias(id){try{await sourceAliasRequest('DELETE',null,id);document.getElementById('sourceAliasStatus').textContent='已删除';await load();}catch(e){document.getElementById('sourceAliasStatus').textContent='删除失败：'+e.message;}}
