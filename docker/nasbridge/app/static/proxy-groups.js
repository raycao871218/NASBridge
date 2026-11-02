/* Proxy names are always assigned as text or JSON, never HTML or URL paths. */
let selectedProxyGroup="";
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
  if(!proxyGroupItems.some(group=>group.name===selectedProxyGroup))selectedProxyGroup=(proxyGroupItems.find(group=>group.name==='PROXY')||proxyGroupItems[0]).name;
  const tabs=proxyNode('div',null,'mihomo-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','代理组');
  proxyGroupItems.forEach((group,index)=>{
    const tab=proxyNode('button',group.name);tab.type='button';tab.id='proxy-group-tab-'+index;tab.setAttribute('role','tab');
    tab.setAttribute('aria-controls','proxy-group-panel-'+index);tab.setAttribute('aria-selected',String(group.name===selectedProxyGroup));tab.tabIndex=group.name===selectedProxyGroup?0:-1;
    tab.onclick=()=>{selectedProxyGroup=group.name;renderProxyGroups();document.getElementById('proxy-group-tab-'+index).focus();};tabs.append(tab);
  });
  installTabKeys(tabs);host.append(tabs);
  for(const [index,group] of proxyGroupItems.entries()){
    const section=proxyNode('section',null,'proxy-group'),header=proxyNode('div',null,'proxy-group-header'),title=proxyNode('div');
    section.id='proxy-group-panel-'+index;section.setAttribute('role','tabpanel');section.setAttribute('aria-labelledby','proxy-group-tab-'+index);section.hidden=group.name!==selectedProxyGroup;
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


setInterval(()=>{if(!document.hidden&&document.getElementById('mihomoView').classList.contains('active')&&!document.getElementById('mihomoNodesPanel').hidden)void loadProxyGroups(true);},15000);

function installTabKeys(host){
  host.onkeydown=event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    const tabs=[...host.querySelectorAll('[role="tab"]')],current=tabs.indexOf(document.activeElement);if(current<0)return;
    event.preventDefault();let next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(current+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    tabs[next].click();
  };
}
function activateMihomoTab(id){
  const tab=document.querySelector('[data-mihomo-panel="'+id+'"]');if(!tab)return;
  document.querySelectorAll('[data-mihomo-panel]').forEach(button=>{
    const active=button===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
    document.getElementById(button.dataset.mihomoPanel).hidden=!active;
  });
  if(id==='mihomoNodesPanel')void loadProxyGroups();
}
const mihomoTabs=document.querySelector('#mihomoView > .mihomo-tabs');
if(mihomoTabs){
  mihomoTabs.querySelectorAll('[data-mihomo-panel]').forEach(tab=>tab.onclick=()=>activateMihomoTab(tab.dataset.mihomoPanel));
  installTabKeys(mihomoTabs);
}
