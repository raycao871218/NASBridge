// SMS contents are inserted only as text; never render message bodies as HTML.
let smsOffset=0,smsQuery='',smsRequest=0,smsAbort=null;
function smsNode(tag,value,cls){const e=document.createElement(tag);if(value!=null)e.textContent=value;if(cls)e.className=cls;return e;}
function smsDate(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('zh-CN',{hour12:false});}
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
  items.replaceChildren();status.textContent='正在加载短信…';status.className='status-line';
  try{
    const r=await fetch('/api/sms?'+new URLSearchParams({offset,limit:40,q:smsQuery}),{cache:'no-store',signal:smsAbort.signal});
    const data=await r.json();if(!r.ok)throw Error(data.detail||'读取失败');if(request!==smsRequest)return;
    for(const row of data.items){
      const card=smsNode('article',null,'panel');
      card.append(smsNode('h3',row.sender||'未知发送人'));
      card.append(smsNode('p','发送：'+smsDate(row.sent_at)+' · 转存：'+smsDate(row.received_at),'muted small'));
      if(row.verification_code){const copy=smsNode('button','复制验证码');copy.type='button';copy.onclick=()=>void copySmsCode(row.verification_code,copy);card.append(copy);}
      const details=smsNode('details'),summary=smsNode('summary',(row.body||'（空短信）').slice(0,100)+((row.body||'').length>100?'…':''));
      summary.style.whiteSpace='pre-wrap';summary.style.overflowWrap='anywhere';
      const body=smsNode('p',row.body||'（空短信）');body.style.whiteSpace='pre-wrap';body.style.overflowWrap='anywhere';
      details.append(summary,body);if(row.truncated)details.append(smsNode('p','内容过长，仅显示前 16000 字。','muted small'));
      card.append(details,smsNode('p','来源：'+(row.source||'—')+' · 设备：'+row.device_id,'muted small'));items.append(card);
    }
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
