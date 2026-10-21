(()=>{
 const $=id=>document.getElementById(id);
 let state=null, editing=-1, busy=false;
 const dialog=$('homeSiteDialog'), form=$('homeSiteForm');
 function render(){
  const grid=$('homeSites');grid.replaceChildren();
  state.sites.forEach((site,index)=>{
   const card=document.createElement('article');card.className='panel home-site';
   if(site){
    const link=document.createElement('a');link.href=site.url;link.target='_blank';link.rel='noopener noreferrer';
    const number=document.createElement('span');number.className='site-number';number.textContent=`常用网站 / 0${index+1}`;
    const name=document.createElement('strong');name.textContent=site.name;
    const address=document.createElement('small');address.textContent=new URL(site.url).host;
    link.append(number,name,address);card.append(link);
   }
   const button=document.createElement('button');button.type='button';button.className=site?'site-edit':'site-add';
   button.textContent=site?'编辑网站':`＋ 添加网站 ${index+1}`;button.setAttribute('aria-label',site?`编辑 ${site.name}`:`添加第 ${index+1} 个网站`);
   button.addEventListener('click',()=>edit(index));card.append(button);grid.append(card);
  });
 }
 async function request(options){
  const response=await fetch('/api/home/sites',{cache:'no-store',...options});
  const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'网站配置读取或保存失败，请检查名称和网址');
  return data;
 }
 async function load(){
  if(busy)return;
  busy=true;$('homeReload').disabled=true;
  $('homeStatus').textContent='正在加载常用网站…';
  try{state=await request();render();$('homeStatus').textContent='';}
  catch(error){$('homeStatus').textContent=`加载失败：${error.message}，请点击重新加载。`;}
  finally{busy=false;$('homeReload').disabled=false;}
 }
 function edit(index){
  if(busy||!state)return;
  editing=index;const site=state.sites[index];
  $('homeSiteTitle').textContent=site?'编辑常用网站':'添加常用网站';
  $('homeSiteName').value=site?.name||'';$('homeSiteUrl').value=site?.url||'';
  $('homeSiteRemove').hidden=!site;$('homeSiteError').textContent='';dialog.showModal();
 }
 async function save(remove=false){
  if(busy||!state)return;
  let site=null;
  if(!remove){
   const name=$('homeSiteName').value.trim();let url=$('homeSiteUrl').value.trim();
   if(!name){$('homeSiteName').focus();$('homeSiteError').textContent='请填写网站名称';return;}
   if(!url.includes('://'))url='https://'+url;
   try{const parsed=new URL(url);if(!['http:','https:'].includes(parsed.protocol)||!parsed.hostname||parsed.username||parsed.password||/[\s\\]/u.test(url))throw new Error();}
   catch{$('homeSiteUrl').focus();$('homeSiteError').textContent='请填写有效的 HTTP 或 HTTPS 网址，不含登录凭据';return;}
   site={name,url};
  }
  const sites=[...state.sites];sites[editing]=site;busy=true;
  form.querySelectorAll('button').forEach(button=>button.disabled=true);
  $('homeSiteError').textContent='正在保存…';
  try{state=await request({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision,sites})});render();dialog.close();$('homeStatus').textContent=remove?'已移除网站':'网站已保存';}
  catch(error){$('homeSiteError').textContent=error.message;}
  finally{busy=false;form.querySelectorAll('button').forEach(button=>button.disabled=false);}
 }
 form.addEventListener('submit',event=>{event.preventDefault();void save();});
 dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 $('homeSiteCancel').addEventListener('click',()=>dialog.close());
 $('homeSiteRemove').addEventListener('click',()=>void save(true));
 $('homeReload').addEventListener('click',()=>void load());
 void load();
})();
