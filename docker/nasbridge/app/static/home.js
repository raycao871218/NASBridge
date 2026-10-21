(()=>{
 const $=id=>document.getElementById(id);
 let state=null, editing=-1, busy=false, imageUrl=null, autoName='', inspectedUrl='', inspection=null, editSession=0;
 const dialog=$('homeSiteDialog'), form=$('homeSiteForm');
 function normalizeUrl(value){
  let url=value.trim();if(!url.includes('://'))url='https://'+url;
  try{const parsed=new URL(url);
   if(!['http:','https:'].includes(parsed.protocol)||!parsed.hostname||parsed.username||parsed.password||/[\s\\]/u.test(url))throw new Error();
  }catch{throw new Error('请填写有效的 HTTP 或 HTTPS 网址，不含登录凭据');}
  return url;
 }
 function preview(site){
  const frame=document.createElement('div');frame.className='site-preview';
  const mark=document.createElement('span');mark.className='site-monogram';mark.textContent=(site.name||new URL(site.url).hostname).slice(0,2).toUpperCase();frame.append(mark);
  const addImage=(url,className,onError)=>{const img=document.createElement('img');img.src=url;img.alt='';img.className=className;img.loading='lazy';img.referrerPolicy='no-referrer';img.addEventListener('error',()=>{img.remove();if(onError)onError();},{once:true});frame.append(img);};
  const favicon=()=>addImage(new URL('/favicon.ico',site.url).href,'site-favicon');
  if(site.image_url)addImage(site.image_url,'site-cover',favicon);else favicon();
  return frame;
 }
 function render(){
  const grid=$('homeSites');grid.replaceChildren();
  state.sites.forEach((site,index)=>{
   const card=document.createElement('article');card.className='panel home-site';
   if(site){
    const link=document.createElement('a');link.href=site.url;link.target='_blank';link.rel='noopener noreferrer';
    const number=document.createElement('span');number.className='site-number';number.textContent=`常用网站 / 0${index+1}`;
    const name=document.createElement('strong');name.textContent=site.name;
    const address=document.createElement('small');address.textContent=new URL(site.url).host;
    link.append(preview(site),number,name,address);card.append(link);
   }
   const button=document.createElement('button');button.type='button';button.className=site?'site-edit':'site-add';
   if(site)button.textContent='编辑网站';else{const icon=document.createElement('span');icon.textContent='＋';const label=document.createElement('strong');label.textContent='添加常用网站';const hint=document.createElement('small');hint.textContent='粘贴网址，自动获取标题和预览';button.append(icon,label,hint);}
   button.setAttribute('aria-label',site?`编辑 ${site.name}`:`添加第 ${index+1} 个网站`);
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
  editing=index;const site=state.sites[index];editSession++;inspection=null;inspectedUrl=site?.image_url?site.url:'';imageUrl=site?.image_url||null;autoName='';
  $('homeSiteTitle').textContent=site?'编辑常用网站':'添加常用网站';
  $('homeSiteName').value=site?.name||'';$('homeSiteUrl').value=site?.url||'';
  $('homeSiteRemove').hidden=!site;$('homeSiteError').textContent='';$('homeSitePreview').replaceChildren();if(site)$('homeSitePreview').append(preview(site));dialog.showModal();$('homeSiteUrl').focus();
 }
 async function inspect(force=false){
  let url;try{url=normalizeUrl($('homeSiteUrl').value);}catch(error){$('homeSiteError').textContent=error.message;return;}
  if(inspection?.url===url)return inspection.promise;
  if(!force&&inspectedUrl===url)return;
  const session=editSession;
  const promise=(async()=>{
   $('homeSiteError').textContent='正在读取标题和预览图…';
   try{
    const response=await fetch('/api/home/metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'暂时无法读取网站信息，可直接保存');
    if(session!==editSession||!dialog.open||normalizeUrl($('homeSiteUrl').value)!==url)return;
    if(!$('homeSiteName').value.trim()||$('homeSiteName').value===autoName){autoName=data.name;$('homeSiteName').value=data.name;}
    imageUrl=data.image_url||null;inspectedUrl=url;$('homeSitePreview').replaceChildren(preview({name:data.name,url,image_url:imageUrl}));$('homeSiteError').textContent='已读取网站信息，标题可以自行调整';
   }catch(error){if(session===editSession&&dialog.open)$('homeSiteError').textContent=error.message;}
   finally{if(inspection?.promise===promise)inspection=null;}
  })();
  inspection={url,promise};return promise;
 }
 async function save(remove=false){
  if(busy||!state)return;
  let site=null;
  if(!remove){
   let url;try{url=normalizeUrl($('homeSiteUrl').value);}catch(error){$('homeSiteUrl').focus();$('homeSiteError').textContent=error.message;return;}
   site={url};
  }
  busy=true;
  form.querySelectorAll('button,input').forEach(control=>control.disabled=true);
  try{
   if(site){await inspect();site.name=$('homeSiteName').value.trim()||new URL(site.url).hostname.slice(0,40);if(imageUrl)site.image_url=imageUrl;}
   const sites=[...state.sites];sites[editing]=site;
   $('homeSiteError').textContent='正在保存…';state=await request({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.revision,sites})});render();dialog.close();$('homeStatus').textContent=remove?'已移除网站':'网站已保存';}
  catch(error){$('homeSiteError').textContent=error.message;}
  finally{busy=false;form.querySelectorAll('button,input').forEach(control=>control.disabled=false);}
 }
 form.addEventListener('submit',event=>{event.preventDefault();void save();});
 dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
 $('homeSiteCancel').addEventListener('click',()=>dialog.close());
 $('homeSiteRemove').addEventListener('click',()=>void save(true));
 $('homeSiteRead').addEventListener('click',()=>void inspect(true));
 $('homeSiteUrl').addEventListener('blur',()=>{if($('homeSiteUrl').value.trim())void inspect();});
 $('homeSiteUrl').addEventListener('input',()=>{imageUrl=null;inspectedUrl='';$('homeSitePreview').replaceChildren();if($('homeSiteName').value===autoName)$('homeSiteName').value='';autoName='';});
 $('homeReload').addEventListener('click',()=>void load());
 void load();
})();
