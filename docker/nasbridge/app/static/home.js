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
 function preview(site,index){
  const frame=document.createElement('div');frame.className='site-preview';
  const hint=document.createElement('span');hint.className='site-preview-hint';
  hint.textContent=Number.isInteger(index)?'正在生成网页首屏预览…':'保存后自动生成网页首屏截图';frame.append(hint);
  if(Number.isInteger(index)){
   const img=document.createElement('img');img.src=`/api/home/sites/${index}/preview?v=${state.revision}`;img.alt=`${site.name}的网页首屏`;img.className='site-cover';img.loading='lazy';
   img.addEventListener('load',()=>hint.remove(),{once:true});
   img.addEventListener('error',()=>{img.remove();hint.textContent='网页暂时无法预览，仍可点击打开';},{once:true});frame.append(img);
  }
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
    link.append(preview(site,index),number,name,address);card.append(link);
   }
   const button=document.createElement('button');button.type='button';button.className=site?'site-edit':'site-add';
   if(site)button.textContent='编辑网站';else{card.classList.add('home-site-empty');const icon=document.createElement('span');icon.textContent='＋';const label=document.createElement('strong');label.textContent='添加常用网站';const hint=document.createElement('small');hint.textContent='粘贴网址，自动生成网页首屏预览';button.append(icon,label,hint);}
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
  $('homeSiteRemove').hidden=!site;$('homeSiteError').textContent='';$('homeSitePreview').replaceChildren();if(site)$('homeSitePreview').append(preview(site,index));dialog.showModal();$('homeSiteUrl').focus();
 }
 async function inspect(force=false){
  let url;try{url=normalizeUrl($('homeSiteUrl').value);}catch(error){$('homeSiteError').textContent=error.message;return;}
  if(inspection?.url===url)return inspection.promise;
  if(!force&&inspectedUrl===url)return;
  const session=editSession;
  const promise=(async()=>{
   $('homeSiteError').textContent='正在读取网站标题…';
   try{
    const response=await fetch('/api/home/metadata',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
    const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'暂时无法读取网站信息，可直接保存');
    if(session!==editSession||!dialog.open||normalizeUrl($('homeSiteUrl').value)!==url)return;
    if(!$('homeSiteName').value.trim()||$('homeSiteName').value===autoName){autoName=data.name;$('homeSiteName').value=data.name;}
    imageUrl=data.image_url||null;inspectedUrl=url;$('homeSitePreview').replaceChildren(preview({name:data.name,url},state.sites[editing]?.url===url?editing:undefined));$('homeSiteError').textContent='标题已填写，可自行调整；保存后自动生成网页截图';
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
