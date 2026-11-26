// SMS contents are inserted only as text; never render message bodies as HTML.
let smsOffset=0,smsQuery='',smsRequest=0,smsAbort=null;
const smsReadStorageKey='nasbridge-sms-read-v1';
let smsReadKeys=new Set(),smsVisible=[];
try{const saved=JSON.parse(localStorage.getItem(smsReadStorageKey)||'[]');if(Array.isArray(saved))smsReadKeys=new Set(saved.filter(key=>typeof key==='string'));}catch{}
function smsKey(row){return JSON.stringify([row.device_id,row.message_id]);}
function updateSmsAppearance(){
  const now=Date.now();
  for(const item of smsVisible){
    const read=smsReadKeys.has(smsKey(item.row));
    // Use the original SMS timestamp so importing old messages cannot make them new.
    const received=Date.parse(item.row.sent_at||item.row.received_at);
    const recent=Number.isFinite(received)&&now>=received&&now-received<180000;
    item.card.classList.toggle('sms-unread',!read);
    item.card.classList.toggle('sms-recent',recent);
    item.readLabel.textContent=read?'已读':'未读';
    item.readLabel.className='sms-read-label '+(read?'is-read':'is-unread');
    item.recentLabel.hidden=!recent;
  }
}
function markSmsRead(row){
  smsReadKeys.add(smsKey(row));
  try{localStorage.setItem(smsReadStorageKey,JSON.stringify([...smsReadKeys]));}
  catch{document.getElementById('smsStatus').textContent='浏览器未能保存已读状态，刷新后可能恢复为未读';}
  updateSmsAppearance();
}
window.addEventListener('storage',event=>{
  if(event.key!==smsReadStorageKey)return;
  try{const saved=JSON.parse(event.newValue||'[]');if(Array.isArray(saved))smsReadKeys=new Set(saved.filter(key=>typeof key==='string'));}catch{}
  updateSmsAppearance();
});
setInterval(updateSmsAppearance,1000);
function smsNode(tag,value,cls){const e=document.createElement(tag);if(value!=null)e.textContent=value;if(cls)e.className=cls;return e;}
function smsDate(value){return formatPanelDate(value);}
async function copySmsCode(code,button){
  try{
    if(navigator.clipboard && window.isSecureContext)await navigator.clipboard.writeText(code);
    else{
      const field=document.createElement('textarea');field.value=code;field.style.position='fixed';field.style.opacity='0';field.setAttribute('readonly','');document.body.append(field);
      let success=false;try{field.select();success=document.execCommand('copy');}finally{field.remove();}
      if(!success)throw Error('复制失败');
    }
    button.textContent='已复制';button.setAttribute('aria-label','验证码已复制');
    setTimeout(()=>{button.textContent='复制验证码';button.setAttribute('aria-label','复制验证码');},1600);
  }catch(e){button.textContent='复制失败，请手动复制';}
}
async function loadSms(reset=false){
  if(reset)smsOffset=0;
  const offset=smsOffset,request=++smsRequest;
  if(smsAbort)smsAbort.abort();smsAbort=new AbortController();
  const status=document.getElementById('smsStatus'),items=document.getElementById('smsItems');
  document.getElementById('smsPrev').disabled=true;document.getElementById('smsNext').disabled=true;
  smsVisible=[];items.replaceChildren();status.textContent='正在加载短信…';status.className='status-line';
  try{
    const r=await fetch('/api/sms?'+new URLSearchParams({offset,limit:40,q:smsQuery}),{cache:'no-store',signal:smsAbort.signal});
    const data=await r.json();if(!r.ok)throw Error(data.detail||'读取失败');if(request!==smsRequest)return;
    for(const row of data.items){
      const card=smsNode('article',null,'panel sms-card');
      const heading=smsNode('div',null,'sms-card-heading');
      const badges=smsNode('div',null,'sms-badges');
      const readLabel=smsNode('span',null,'sms-read-label');
      const recentLabel=smsNode('span','3 分钟内','sms-recent-label');
      badges.append(readLabel,recentLabel);
      heading.append(smsNode('h3',row.sender||'未知发送人'),badges);
      card.append(heading);
      smsVisible.push({row,card,readLabel,recentLabel});
      card.append(smsNode('p','发送：'+smsDate(row.sent_at)+' · 转存：'+smsDate(row.received_at),'muted small'));
      if(row.verification_code){const copy=smsNode('button','复制验证码');copy.type='button';copy.onclick=()=>{markSmsRead(row);void copySmsCode(row.verification_code,copy);};card.append(copy);}
      const details=smsNode('details'),summary=smsNode('summary',(row.body||'（空短信）').slice(0,100)+((row.body||'').length>100?'…':''));
      summary.style.whiteSpace='pre-wrap';summary.style.overflowWrap='anywhere';
      const body=smsNode('p',row.body||'（空短信）');body.style.whiteSpace='pre-wrap';body.style.overflowWrap='anywhere';
      details.append(summary,body);if(row.truncated)details.append(smsNode('p','内容过长，仅显示前 16000 字。','muted small'));
      details.addEventListener('toggle',()=>{if(details.open)markSmsRead(row);});
      card.append(details,smsNode('p','来源：'+(row.source||'—')+' · 设备：'+row.device_id,'muted small'));items.append(card);
    }
    updateSmsAppearance();
    status.textContent=data.items.length?'本页 '+data.items.length+' 条 · 点击摘要展开全文':'暂无匹配短信';
    document.getElementById('smsPage').textContent='第 '+(offset/40+1)+' 页';
    document.getElementById('smsPrev').disabled=offset===0;document.getElementById('smsNext').disabled=!data.has_more;
  }catch(e){if(e.name==='AbortError'||request!==smsRequest)return;status.textContent=e.message+'，请点击刷新重试';status.className='status-line error';document.getElementById('smsPrev').disabled=smsOffset===0;}
}
document.getElementById('smsSearchForm').addEventListener('submit',e=>{e.preventDefault();smsQuery=document.getElementById('smsQuery').value.trim();void loadSms(true);});
document.getElementById('smsReset').onclick=()=>{smsQuery='';document.getElementById('smsQuery').value='';void loadSms(true);};
document.getElementById('smsRefresh').onclick=()=>void loadSms(true);
document.getElementById('smsPrev').onclick=()=>{smsOffset=Math.max(0,smsOffset-40);void loadSms();};
document.getElementById('smsNext').onclick=()=>{smsOffset+=40;void loadSms();};
// Restore the SMS tab after a page reload, using the dashboard's existing state keeper.
if(document.getElementById('smsView').classList.contains('active'))void loadSms(true);
document.addEventListener('DOMContentLoaded',()=>{if(document.getElementById('smsView').classList.contains('active'))void loadSms(true);});
