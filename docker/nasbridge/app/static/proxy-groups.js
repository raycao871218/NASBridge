/* Proxy names are always assigned as text or JSON, never HTML or URL paths. */
let proxyGroupItems=[],proxyGroupBusy=false,proxyGroupLoaded=false,proxyGroupsLoading=false;
function proxyNode(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;}
async function proxyApi(path,method='GET',body){
  const response=await fetch('/api/mihomo/proxies'+path,{method,cache:'no-store',...(body?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
  const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'代理操作失败');return data;
}
function renderProxyGroups(){
  const host=document.getElementById('proxyGroups');host.replaceChildren();
  document.getElementById('proxyGroupsRefresh').disabled=proxyGroupBusy||proxyGroupsLoading;
  if(!proxyGroupItems.length){host.textContent=proxyGroupLoaded?'没有可管理的代理组':'暂未读取到代理组';return;}
  for(const group of proxyGroupItems){
    const section=proxyNode('section',null,'proxy-group'),header=proxyNode('div',null,'proxy-group-header'),title=proxyNode('div');
    title.append(proxyNode('h3',group.name),proxyNode('div',group.type+' · '+(group.automatic?(group.fixed?'固定：'+group.fixed:'自动选择'):group.selectable?'手动选择':'只读'),'small muted'));
    title.append(proxyNode('div',group.now?'当前：'+group.now:'按组策略分配连接','proxy-current small'));header.append(title);
    if(group.automatic){const reset=proxyNode('button','恢复自动');reset.type='button';reset.disabled=proxyGroupBusy||proxyGroupsLoading||!group.fixed||!proxyGroupLoaded;reset.onclick=()=>proxyGroupAction('/automatic','POST',{group:group.name});header.append(reset);}
    section.append(header);const nodes=proxyNode('div',null,'proxy-nodes');
    for(const member of group.members){
      const selected=group.now===member.name,tile=proxyNode('div',null,'proxy-node'+(selected?' selected':''));
      const choice=proxyNode('button',null,'proxy-node-choice');choice.type='button';choice.setAttribute('aria-pressed',String(selected));
      choice.append(proxyNode('strong',(selected?'✓ ':'')+member.name),proxyNode('small',member.type+(member.now?' → '+member.now:'')+(member.udp?' · UDP':'')));
      choice.disabled=proxyGroupBusy||proxyGroupsLoading||!proxyGroupLoaded||!group.selectable||(selected&&(!group.automatic||group.fixed===member.name));
      choice.onclick=()=>proxyGroupAction('/selection','PUT',{group:group.name,name:member.name});
      const delay=proxyNode('button',member.delay>0?member.delay+' ms':member.delay===0?'超时':'测速','proxy-delay');delay.type='button';
      delay.setAttribute('aria-label','测试 '+member.name+' 的延迟');delay.title=member.tested_at?'上次测速：'+member.tested_at:'尚未测速';
      delay.disabled=proxyGroupBusy||proxyGroupsLoading||!proxyGroupLoaded;delay.onclick=()=>proxyGroupAction('/delay','POST',{name:member.name});
      tile.append(choice,delay);nodes.append(tile);
    }
    section.append(nodes);host.append(section);
  }
}
async function loadProxyGroups(preserveMessage=false){
  if(proxyGroupsLoading||proxyGroupBusy)return;
  proxyGroupsLoading=true;renderProxyGroups();
  const status=document.getElementById('proxyGroupsStatus');if(!preserveMessage)status.textContent='正在读取代理组…';
  try{const data=await proxyApi('');proxyGroupItems=data.items||[];proxyGroupLoaded=true;if(!preserveMessage)status.textContent='已更新';}
  catch(error){proxyGroupLoaded=false;status.textContent='读取失败：'+error.message+'；请刷新后再操作';}
  finally{proxyGroupsLoading=false;renderProxyGroups();}
}
async function proxyGroupAction(path,method,body){
  if(proxyGroupBusy||proxyGroupsLoading||!proxyGroupLoaded)return;
  proxyGroupBusy=true;renderProxyGroups();const status=document.getElementById('proxyGroupsStatus');status.textContent=path==='/delay'?'正在测速…':'正在切换…';
  try{const data=await proxyApi(path,method,body);status.textContent=path==='/delay'?body.name+'：'+(data.delay>0?data.delay+' ms':'测速失败或超时'):data.message;
    if(path==='/delay')for(const group of proxyGroupItems)for(const member of group.members)if(member.name===body.name)member.delay=data.delay;
  }catch(error){status.textContent='操作未确认：'+error.message+'；请刷新确认当前节点';}
  finally{proxyGroupBusy=false;await loadProxyGroups(true);}
}
document.getElementById('proxyGroupsRefresh').onclick=()=>void loadProxyGroups();
if(document.getElementById('mihomoView').classList.contains('active'))void loadProxyGroups();

setInterval(()=>{if(!document.hidden&&document.getElementById('mihomoView').classList.contains('active'))void loadProxyGroups(true);},15000);
