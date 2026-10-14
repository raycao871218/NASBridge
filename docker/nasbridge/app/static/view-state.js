// Tab, scroll position and non-secret form selections survive a page refresh.
(()=>{
 const key='nasbridge-view-state-v1';let saved={};try{saved=JSON.parse(sessionStorage.getItem(key)||'{}');}catch{}
 let restoring=true;
 const ids=['proxyName','proxyPort','proxyServerName','proxyUpstream','aliRegion'];
 const persist=()=>{if(restoring)return;const view=document.querySelector('.view.active');if(!view)return;
   const fields=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)?.value||'']));
   try{sessionStorage.setItem(key,JSON.stringify({view:view.id,y:window.scrollY,fields,ssl:document.getElementById('proxySsl').checked,group:document.getElementById('aliGroup').value}));}catch{}
 };
 const views=[...document.querySelectorAll('.view')];const tabs=[...document.querySelectorAll('.view-tab')];
  const selectedTab=tabs.find(x=>x.dataset.viewTarget===saved.view);
 if(selectedTab)switchView(saved.view,selectedTab,false);
 for(const id of ids){if(typeof saved.fields?.[id]==='string')document.getElementById(id).value=saved.fields[id];}
 if(typeof saved.ssl==='boolean')document.getElementById('proxySsl').checked=saved.ssl;
 let changed=false;const mark=()=>{changed=true;restoring=false;persist();};
 document.addEventListener('input',mark);document.addEventListener('change',mark);
 document.addEventListener('click',mark);window.addEventListener('wheel',mark,{passive:true});window.addEventListener('touchstart',mark,{passive:true});
 window.addEventListener('scroll',persist,{passive:true});window.addEventListener('pagehide',persist);
 (async()=>{
  await initialPanelLoad;
  if(!changed&&saved.view==='aliyunView'&&saved.fields?.aliRegion&&saved.group){
    await aliGroups();
    if(!changed&&[...document.getElementById('aliGroup').options].some(x=>x.value===saved.group)){
      document.getElementById('aliGroup').value=saved.group;await aliRulesForRestore();
    }
  }
  if(!changed&&saved.view==='aliyunView'&&!(saved.fields?.aliRegion&&saved.group))await aliAutoLoadDefaults();
  if(!changed&&saved.view==='ztView')await loadZTNetworks();
  if(!changed)window.scrollTo(0,Number.isFinite(saved.y)?saved.y:0);
  restoring=false;persist();
 })();
 async function aliRulesForRestore(){await loadAliyunRules();}
})();
