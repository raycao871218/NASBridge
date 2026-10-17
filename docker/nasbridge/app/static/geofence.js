/* Configuration only: never store HA credentials or ask the browser for location. */
(() => {
  const el=id=>document.getElementById(id);
  let config=null, dirty=false, busy=false, requestId=0, mapData=null;
  const fieldIds=['geoEnabled','geoInterval','geoDuration','geoBuffer','geoAccuracy','geoAge','geoTest'];
  function status(message,error=false){el('geoStatus').textContent=message;el('geoStatus').className='status-line'+(error?' error':'');}
  async function api(path,options={}){
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
    try{
      const r=await fetch('/api/geofence/'+path,{...options,cache:'no-store',signal:controller.signal});
      const data=await r.json();
      if(!r.ok){const e=new Error(r.status===409?'其他页面已修改配置。请点击“重新读取”后再保存。':typeof data.detail==='string'?data.detail:r.status===422?'参数无效：请检查间隔、时长和缓冲距离。':'请求失败（HTTP '+r.status+'）');e.status=r.status;throw e;}
      return data;
    }catch(e){if(e.name==='AbortError')throw Error('请求超时，请重试');throw e;}
    finally{clearTimeout(timer);}
  }
  function setBusy(value){busy=value;el('geoReload').disabled=value;el('geoSave').disabled=value;el('geoFields').disabled=value||!config;}
  function settingsFromForm(){
    return {enabled:el('geoEnabled').checked,region_id:'510726',
      check_interval_seconds:Number(el('geoInterval').value)*60,max_detection_seconds:Math.round(Number(el('geoDuration').value)*3600),
      boundary_buffer_meters:Number(el('geoBuffer').value),max_location_age_seconds:Number(el('geoAge').value)*60,
      max_location_accuracy_meters:Number(el('geoAccuracy').value),test_mode:el('geoTest').checked};
  }
  function summary(settings){
    const period=settings.test_mode?'15 秒':settings.check_interval_seconds/60+' 分钟';
    const duration=settings.test_mode?'1 分钟':settings.max_detection_seconds/3600+' 小时';
    const count=settings.test_mode?4:settings.max_detection_seconds/settings.check_interval_seconds;
    el('geoSummary').textContent=`外网后每 ${period} 检测一次，最长 ${duration}，最多 ${Number.isFinite(count)?count:'—'} 次；定位点需在县界外超过 ${settings.boundary_buffer_meters} 米及自身精度范围，才确认出县。`;
    el('geoMode').textContent=(dirty?'未保存 · ':'')+(settings.enabled?(settings.test_mode?'测试模式':'正常模式'):'未启用');
  }
  function apply(data){
    if(data.schema_version!==1||data.region?.id!=='510726'||!data.settings)throw Error('配置格式不兼容，请检查服务版本');
    config=data;dirty=false;
    const s=data.settings;
    el('geoEnabled').checked=s.enabled;el('geoTest').checked=s.test_mode;
    el('geoInterval').value=s.check_interval_seconds/60;el('geoDuration').value=Number((s.max_detection_seconds/3600).toFixed(8));
    el('geoBuffer').value=s.boundary_buffer_meters;el('geoAccuracy').value=s.max_location_accuracy_meters;el('geoAge').value=s.max_location_age_seconds/60;
    el('geoRevision').textContent='版本 '+data.revision;
    el('geoDirty').textContent=data.updated_at?'已保存 · '+new Date(data.updated_at).toLocaleString('zh-CN',{hour12:false}):'默认配置 · 尚未保存';
    summary(s);
  }
  function markDirty(){if(!config||busy)return;dirty=true;el('geoDirty').textContent='有未保存的修改';summary(settingsFromForm());}
  fieldIds.forEach(id=>el(id).addEventListener('input',markDirty));
  window.loadGeofence=async function(force=false){
    if(busy)return;
    if(config&&!force)return;
    if(dirty&&!window.confirm('重新读取将丢弃本页未保存的修改，继续吗？'))return;
    const id=++requestId;setBusy(true);status('正在读取配置…');
    try{const data=await api('config');if(id!==requestId)return;apply(data);status('配置已读取；修改后请保存。');}
    catch(e){status(e.message||'读取配置失败',true);}
    finally{if(id===requestId)setBusy(false);}
    if(!mapData)void loadMap();else drawMap();
  };
  el('geoReload').onclick=()=>void window.loadGeofence(true);
  el('geoForm').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!config)return;
    const s=settingsFromForm();
    if(s.max_detection_seconds<s.check_interval_seconds||s.max_detection_seconds%s.check_interval_seconds!==0||s.max_detection_seconds/s.check_interval_seconds>24){status('最长时长需为间隔的整数倍，且每轮检测 1～24 次。',true);return;}
    setBusy(true);status('正在保存…');
    try{const data=await api('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...s,expected_revision:config.revision})});apply(data);status('配置已保存，支持此接口的手机将在下次同步时读取。');}
    catch(e){status(e.message||'保存失败，请重新读取核对',true);}
    finally{setBusy(false);}
  });
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  async function loadMap(){
    try{mapData=await api('preview');drawMap();}
    catch(e){const p=document.createElement('p');p.textContent='边界预览加载失败。点击重新读取可重试。';p.className='error';el('geoMap').replaceChildren(p);}
  }
  // Small regional Mercator projection. Coordinates are the bundled, published GCJ-02 geometry.
  function drawMap(){
    if(!mapData||el('geofenceView').offsetParent===null)return;
    const host=el('geoMap'),w=host.clientWidth,h=Math.max(310,Math.min(460,w*0.9)),svgNS='http://www.w3.org/2000/svg';
    const features=mapData.features;
    const rings=feature=>feature.geometry.type==='Polygon'?feature.geometry.coordinates:feature.geometry.coordinates.flat();
    const merc=coord=>[coord[0]*Math.PI/180,-Math.log(Math.tan(Math.PI/4+coord[1]*Math.PI/360))];
    const points=features.flatMap(f=>rings(f).flat().map(merc)),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
    const scale=Math.min((w-36)/(xmax-xmin),(h-36)/(ymax-ymin));
    const project=coord=>{const p=merc(coord);return[(w-(xmax-xmin)*scale)/2+(p[0]-xmin)*scale,(h-(ymax-ymin)*scale)/2+(p[1]-ymin)*scale];};
    const node=(tag,attrs,text)=>{const n=document.createElementNS(svgNS,tag);for(const [key,value] of Object.entries(attrs))n.setAttribute(key,value);if(text)n.textContent=text;return n;};
    const svg=node('svg',{viewBox:`0 0 ${w} ${h}`,width:'100%',height:h,role:'img','aria-label':'北川县位于绵阳市西北部，南邻安州，东邻江油。'});
    svg.append(node('title',{},'北川县及绵阳市县域边界'));
    for(const f of features){
      const highlight=f.properties.adcode===510726;
      const d=rings(f).map(r=>'M'+r.map(c=>project(c).map(v=>v.toFixed(2)).join(',')).join('L')+'Z').join('');
      svg.append(node('path',{d,fill:highlight?'rgba(142,181,255,.26)':'var(--surface-3)',stroke:highlight?'var(--blue)':'var(--line)','stroke-width':highlight?2:1,'fill-rule':'evenodd'}));
    }
    const selected=w<370?['北川羌族自治县','江油市','安州区','平武县']:['北川羌族自治县','江油市','安州区','平武县','梓潼县','三台县','盐亭县'];
    for(const f of features){
      if(!selected.includes(f.properties.name))continue;
      const [x,y]=project(f.properties.centroid||f.properties.center);
      svg.append(node('text',{x,y,fill:'var(--text)','text-anchor':'middle','font-size':f.properties.adcode===510726?15:12},f.properties.adcode===510726?'北川县':f.properties.name));
    }
    const county=features.find(f=>f.properties.adcode===510726),city=features.find(f=>f.properties.adcode===510703);
    for(const [f,name,dy] of [[county,'新县城',-8],[city,'绵阳市区',18]]){
      const [x,y]=project(f.properties.center);svg.append(node('circle',{cx:x,cy:y,r:3,fill:'var(--text)'}));
      svg.append(node('text',{x:x+6,y:y+dy,fill:'var(--text)','font-size':12},name));
    }
    svg.append(node('text',{x:12,y:20,fill:'var(--muted)','font-size':12},'↑ 北'));
    host.replaceChildren(svg);
  }
  new ResizeObserver(drawMap).observe(el('geoMap'));
  document.addEventListener('DOMContentLoaded',()=>{if(el('geofenceView').classList.contains('active'))void window.loadGeofence();});
})();
