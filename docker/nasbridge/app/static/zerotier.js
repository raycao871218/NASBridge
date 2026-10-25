let ztMembers=[],ztBusy=false,ztNetworksLoaded=false;
const ztEl=id=>document.getElementById(id);
function ztMessage(t){ztEl('ztStatus').textContent=t;ztEl('ztEditStatus').textContent=t;}
async function ztApi(path,body){
 const c=new AbortController(),timer=setTimeout(()=>c.abort(),50000);
 try{const r=await fetch('/api/zerotier/'+path,{signal:c.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'})});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));return d;}
 catch(e){if(e.name==='AbortError')throw new Error('请求超时；如已提交成员修改，请刷新列表确认结果。');throw e;}finally{clearTimeout(timer);}
}
async function ztRun(fn){if(ztBusy)return;ztBusy=true;ztEl('ztView').querySelectorAll('button,input,select').forEach(x=>x.disabled=true);try{await fn();}catch(e){ztMessage(e.message||String(e));}finally{ztBusy=false;ztEl('ztView').querySelectorAll('button,input,select').forEach(x=>x.disabled=false);}}
async function downloadZTPlanet(){await ztRun(async()=>{
 ztMessage('正在读取 planet 文件…');
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),50000);
 try{
  const response=await fetch('/api/zerotier/planet',{cache:'no-store',signal:controller.signal});
  if(!response.ok){const d=await response.json();throw new Error(typeof d.detail==='string'?d.detail:'下载 planet 失败');}
  const blob=await response.blob();if(!blob.size)throw new Error('planet 文件为空');
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='planet';document.body.appendChild(link);
  try{link.click();}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  ztMessage('已开始下载 planet 文件。');
 }catch(e){if(e.name==='AbortError')throw new Error('下载 planet 超时，请稍后重试。');throw e;}finally{clearTimeout(timer);}
});}
async function loadZTNetworks(force=false){if(ztNetworksLoaded&&!force)return;await ztRun(async()=>{
 ztMessage('正在连接控制器并读取默认网络…');const d=await ztApi('networks'),select=ztEl('ztNetwork'),previous=select.value;let saved='';try{saved=localStorage.getItem('nasbridge-zt-network')||'';}catch{}
 select.innerHTML='<option value="">请选择网络</option>'+d.items.map(n=>`<option value="${esc(n.id)}">${esc(n.name||'未命名')} · ${esc(n.id)}</option>`).join('');
 const preferred=[previous,saved,d.default_network].find(id=>d.items.some(n=>n.id===id))||d.items[0]?.id;
 if(preferred){select.value=preferred;try{localStorage.setItem('nasbridge-zt-network',preferred);}catch{};await readZTMembers();ztMessage(`已自动读取默认网络 ${d.items.find(n=>n.id===preferred)?.name||preferred} 的 ${ztMembers.length} 个成员。`);}else ztMessage('控制器没有可用网络。');
 ztNetworksLoaded=true;
});}
function clearZTMembers(){try{if(ztEl('ztNetwork').value)localStorage.setItem('nasbridge-zt-network',ztEl('ztNetwork').value);}catch{}ztMembers=[];ztEl('ztMembers').textContent='请选择网络并读取成员';ztEl('ztConfig').textContent='';ztEl('ztEditor').hidden=true;}
async function readZTMembers(){
 const network=ztEl('ztNetwork').value;if(!network)throw new Error('请先选择网络');
 const d=await ztApi('networks/'+encodeURIComponent(network)+'/members');ztMembers=d.items;
 ztEl('ztConfig').textContent=`${d.network.private?'私有网络':'公开网络'} · MTU ${d.network.mtu||'—'}\n地址池：`+(d.network.ipAssignmentPools||[]).map(x=>`${x.ipRangeStart} — ${x.ipRangeEnd}`).join('，')+'\n路由：'+(d.network.routes||[]).map(x=>`${x.target} → ${x.via||'直连'}`).join('，');
 ztEl('ztMembers').innerHTML=ztMembers.length?'<table><thead><tr><th>操作</th><th>成员 ID</th><th>成员备注</th><th>授权</th><th>分配 IP</th></tr></thead><tbody>'+ztMembers.map((m,i)=>`<tr><td><button onclick="editZTMember(${i})">编辑 / 同步</button></td><td><code>${esc(m.id||m.address)}</code></td><td>${esc(m.name||'—')}</td><td>${m.authorized?'已授权':'未授权'}</td><td>${esc((m.ipAssignments||[]).join(', ')||'—')}</td></tr>`).join('')+'</tbody></table>':'暂无成员';
}
async function loadZTMembers(){await ztRun(async()=>{ztMessage('正在读取成员…');await readZTMembers();ztMessage(`已读取 ${ztMembers.length} 个成员。`);});}
function editZTMember(index){const m=ztMembers[index];if(!m)return;ztEl('ztMemberId').value=m.id||m.address;ztEl('ztMemberName').value=m.name||'';ztEl('ztMemberIPs').value=(m.ipAssignments||[]).join(', ');ztEl('ztMemberAuth').checked=!!m.authorized;ztEl('ztEditor').hidden=false;ztEl('ztEditor').scrollIntoView({block:'center',behavior:'smooth'});}
async function saveZTMember(){
 if(ztBusy)return;const network=ztEl('ztNetwork').value,id=ztEl('ztMemberId').value;
 const before=ztMembers.find(m=>(m.id||m.address)===id);if(!before){ztMessage('成员已变化，请重新读取列表');return;}
 const patch={confirm:true},name=ztEl('ztMemberName').value,authorized=ztEl('ztMemberAuth').checked,ips=ztEl('ztMemberIPs').value.split(',').map(x=>x.trim()).filter(Boolean);
 if(name!==(before.name||''))patch.name=name;if(authorized!==!!before.authorized)patch.authorized=authorized;if(JSON.stringify(ips)!==JSON.stringify(before.ipAssignments||[]))patch.ipAssignments=ips;
 if(Object.keys(patch).length===1){ztMessage('没有修改');return;}
 if(!confirm(`确认修改网络 ${network} 的成员 ${id}？\n${[('name' in patch?'备注：'+patch.name:''),('authorized' in patch?'授权：'+(patch.authorized?'允许':'取消'):''),('ipAssignments' in patch?'IP：'+patch.ipAssignments.join(', '):'')].filter(Boolean).join('\n')}\n取消授权或更换 IP 可能使设备断开连接。`))return;
 await ztRun(async()=>{ztMessage('正在更新并回读验证…');const d=await ztApi(`networks/${network}/members/${id}`,patch);if(!d.verified)throw new Error('未确认更新成功，请刷新核对');ztEl('ztEditor').hidden=true;try{await readZTMembers();ztMessage('成员已更新，回读验证通过。');}catch(e){ztMessage('成员已更新，但刷新失败：'+e.message);}});
}
