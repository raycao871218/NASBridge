/* Saved profiles load from NAS. Plaintext tokens stay out of browser storage. */
(() => {
  const el=id=>document.getElementById(id), form=el('androidSetupForm');
  let imageUrl=null, timer=null, serial=0, controller=null, revision=0, savedToken=false, busy=false;
  const fields=['setupSsid','setupPublic','setupHaUrl','setupEntity'];
  const localList=el('setupLocalList');
  const localInputs=()=>Array.from(localList.querySelectorAll('input'));
  function localControls() {
    const rows=Array.from(localList.children);
    rows.forEach((row,index)=>{
      row.querySelector('label').firstChild.textContent=`内网地址 ${index+1} `;
      const buttons=row.querySelectorAll('button');
      buttons[0].disabled=busy||index===0;
      buttons[1].disabled=busy||index===rows.length-1;
      buttons[2].disabled=busy||rows.length===1;
    });
    el('setupAddLocal').disabled=busy||rows.length>=8;
  }
  function addLocal(value='') {
    if(localInputs().length>=8)return;
    const row=document.createElement('div');
    row.style.cssText='display:flex;flex-wrap:wrap;align-items:end;gap:8px;margin-bottom:10px';
    const label=document.createElement('label');label.className='field';label.style.cssText='flex:1;min-width:180px';
    label.appendChild(document.createTextNode('内网地址 '));
    const input=document.createElement('input');input.type='text';input.inputMode='url';input.required=true;input.maxLength=512;input.placeholder='192.168.1.10:18080';input.value=value;
    label.appendChild(input);row.appendChild(label);
    for(const [text,action] of [['上移',()=>{if(row.previousElementSibling)localList.insertBefore(row,row.previousElementSibling);}],['下移',()=>{if(row.nextElementSibling)localList.insertBefore(row.nextElementSibling,row);}],['删除',()=>row.remove()]]){
      const button=document.createElement('button');button.type='button';button.textContent=text;
      button.onclick=()=>{if(busy)return;action();localControls();form.dispatchEvent(new Event('input',{bubbles:true}));};row.appendChild(button);
    }
    localList.appendChild(row);localControls();
  }
  function setLocals(values){localList.replaceChildren();for(const value of values)addLocal(value);localControls();}
  setLocals(['']);
  try {
    const saved=JSON.parse(localStorage.getItem('nasbridge-android-setup')||'{}');
    for(const id of fields)if(typeof saved[id]==='string')el(id).value=saved[id];
    if(typeof saved.setupLocal==='string')setLocals([saved.setupLocal]);
  } catch {}
  function clearQr() {
    serial++; controller?.abort(); controller=null; clearTimeout(timer);
    el('setupQrPanel').hidden=true; el('setupQr').removeAttribute('src');
    el('setupDownload').removeAttribute('href');
    if(imageUrl)URL.revokeObjectURL(imageUrl); imageUrl=null;
  }
  function presenceFields() {
    for(const input of el('setupPresenceFields').querySelectorAll('input')) {
      input.disabled=busy||!el('setupPresence').checked;
      input.required=el('setupPresence').checked && input.type!=='checkbox' && (input.id!=='setupToken'||!savedToken);
    }
    el('setupToken').placeholder=savedToken?'已保存，留空保留原令牌':'首次保存请填写 HA 长期访问令牌';
  }
  function setBusy(value) {
    busy=value;
    for(const input of form.querySelectorAll('input,button'))input.disabled=value;
    localControls();
    presenceFields();
  }
  function data() {
    const addresses=localInputs().map(input=>input.value.trim());
    const value={network:{ssid:el('setupSsid').value,local_url:addresses[0],local_urls:addresses,public_url:el('setupPublic').value.trim()},include_geofence:el('setupFence').checked};
    if(el('setupPresence').checked)value.presence={url:el('setupHaUrl').value.trim(),entity:el('setupEntity').value.trim(),token:el('setupToken').value.trim(),test_mode:el('setupTest').checked};
    return value;
  }
  function applyProfile(profile) {
    revision=profile.revision; savedToken=profile.has_saved_token;
    const s=profile.settings;
    if(s){
      el('setupSsid').value=s.network.ssid;setLocals(s.network.local_urls?.length?s.network.local_urls:[s.network.local_url]);el('setupPublic').value=s.network.public_url;
      el('setupPresence').checked=!!s.presence;el('setupFence').checked=s.include_geofence;
      el('setupHaUrl').value=s.presence?.url||'';el('setupEntity').value=s.presence?.entity||'input_boolean.home';
      el('setupTest').checked=!!s.presence?.test_mode;
    }
    el('setupToken').value='';presenceFields();
  }
  async function request(path,options={}) {
    const timeoutController=new AbortController(), timeout=setTimeout(()=>timeoutController.abort(),20000);
    try {
      const response=await fetch('/api/android-setup/'+path,{...options,cache:'no-store',signal:options.signal||timeoutController.signal});
      if(!response.ok){const error=await response.json();throw Error(typeof error.detail==='string'?error.detail:'请求失败，请重试');}
      return response;
    }finally{clearTimeout(timeout);}
  }
  async function loadProfile() {
    if(busy)return;clearQr();setBusy(true);el('setupStatus').textContent='正在读取后台配置…';
    try {
      const profile=await (await request('config')).json();applyProfile(profile);
      el('setupStatus').textContent=profile.settings?'已读取后台配置；可直接生成二维码，或修改后保存。':'后台尚无保存配置，请填写后点击“保存配置”。';
    }catch(error){el('setupStatus').textContent=error.name==='AbortError'?'读取超时，请重试':error.message;}
    finally{setBusy(false);}
  }
  el('setupPresence').onchange=presenceFields;presenceFields();
  el('setupAddLocal').onclick=()=>{if(busy)return;addLocal();localInputs().at(-1).focus();form.dispatchEvent(new Event('input',{bubbles:true}));};
  form.addEventListener('input',()=>{clearQr();el('setupStatus').textContent='有未保存的修改，点击“保存配置”后下次会自动加载。';});
  el('setupLoad').onclick=loadProfile;
  el('setupSave').onclick=async()=>{
    if(busy||!form.reportValidity())return;
    const value={...data(),expected_revision:revision};clearQr();setBusy(true);el('setupStatus').textContent='正在保存到后台…';
    try {
      const profile=await (await request('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)})).json();
      applyProfile(profile);el('setupStatus').textContent='配置已保存到 NAS；下次自动加载，HA 令牌已加密保存。';
    }catch(error){el('setupStatus').textContent=error.name==='AbortError'?'保存超时，请点击读取后台配置核对':error.message;}
    finally{setBusy(false);}
  };
  el('setupHide').onclick=()=>{clearQr();el('setupToken').value='';el('setupStatus').textContent='二维码已隐藏，输入框中的令牌已清空，后台已保存的配置保留。';};
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    const value=data();clearQr();const current=serial;controller=new AbortController();
    const pending=controller,timeout=setTimeout(()=>pending.abort(),20000);
    setBusy(true);el('setupStatus').textContent='正在生成…';
    try {
      const response=await request('qr.svg',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),signal:pending.signal});
      const blob=await response.blob();if(current!==serial)return;
      imageUrl=URL.createObjectURL(blob);el('setupQr').src=imageUrl;el('setupDownload').href=imageUrl;el('setupQrPanel').hidden=false;
      el('setupStatus').textContent='在 App“配置 → 扫码导入配置”扫描。生成二维码不会自动保存修改。';
      timer=setTimeout(()=>{clearQr();el('setupToken').value='';el('setupStatus').textContent='二维码已自动隐藏，后台已保存配置保留。';},300000);
    }catch(error){if(current===serial)el('setupStatus').textContent=error.name==='AbortError'?'请求超时，请重试':error.message;}
    finally{clearTimeout(timeout);setBusy(false);if(controller===pending)controller=null;}
  });
  window.addEventListener('pagehide',()=>{clearQr();el('setupToken').value='';});
  window.addEventListener('pageshow',()=>{clearQr();el('setupToken').value='';});
  void loadProfile();
})();
