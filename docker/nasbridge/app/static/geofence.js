/* WGS-84 circle editor. HA reporting remains on Android. */
(() => {
  const el=id=>document.getElementById(id);
  let config=null,dirty=false,busy=false,map=null,marker=null,circle=null,center=null,tiles=null,editSerial=0;
  let searchId=0,searchBusy=false,tileErrors=0;
  const ids=['geoName','geoEnabled','geoInterval','geoDuration','geoBuffer','geoAccuracy','geoAge','geoTest'];
  const validCenter=(lat,lon)=>Number.isFinite(lat)&&Number.isFinite(lon)&&lat>=-85&&lat<=85&&lon>=-180&&lon<=180;
  function status(text,error=false){el('geoStatus').textContent=text;el('geoStatus').className='status-line'+(error?' error':'');}
  function radius(){return Math.max(100,Math.min(500000,Number(el('geoRadius').value)||100));}
  function distanceLabel(m){return m>=1000?Number((m/1000).toFixed(2))+' 公里':m+' 米';}
  async function api(path,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    try{
      const r=await fetch('/api/geofence/'+path,{...options,cache:'no-store',signal:controller.signal});
      const data=await r.json();
      if(!r.ok)throw Error(r.status===409?'其他页面已修改配置，请重新读取后再保存。':typeof data.detail==='string'?data.detail:r.status===422?'请检查中心坐标、半径和时间设置。':'请求失败（HTTP '+r.status+'）');
      return data;
    }catch(e){if(e.name==='AbortError')throw Error('请求超时，请重试');throw e;}
    finally{clearTimeout(timer);}
  }
  function setBusy(value){
    busy=value;el('geoReload').disabled=value;el('geoSave').disabled=value;el('geoFields').disabled=value||!config;
    el('geoSearchButton').disabled=value||searchBusy;el('geoLocate').disabled=value||!config;el('geoFit').disabled=value||!config;
    if(marker){if(value)marker.dragging.disable();else marker.dragging.enable();}
  }
  function settings(){
    return {enabled:el('geoEnabled').checked,name:el('geoName').value.trim(),fence_type:'circle',
      center_latitude:center?.lat??null,center_longitude:center?.lng??null,radius_meters:Number(el('geoRadius').value),
      check_interval_seconds:Number(el('geoInterval').value)*60,max_detection_seconds:Math.round(Number(el('geoDuration').value)*3600),
      boundary_buffer_meters:Number(el('geoBuffer').value),max_location_age_seconds:Number(el('geoAge').value)*60,
      max_location_accuracy_meters:Number(el('geoAccuracy').value),test_mode:el('geoTest').checked};
  }
  function summary(){
    const s=settings();
    el('geoRadiusLabel').textContent=distanceLabel(radius());
    el('geoSummary').textContent=`${s.test_mode?'每 15 秒检测，最长 1 分钟':`每 ${s.check_interval_seconds/60} 分钟检测，最长 ${s.max_detection_seconds/3600} 小时`}。超出圆形范围及 ${s.boundary_buffer_meters} 米缓冲和定位误差后，才确认离开。`;
    el('geoMode').textContent=(dirty?'未保存 · ':'')+(s.enabled?(s.test_mode?'测试模式':'已启用'):'未启用');
    el('geoCoordinates').textContent=center?`${center.lat.toFixed(6)}, ${center.lng.toFixed(6)} · WGS-84`:'尚未选择中心点';
  }
  function markDirty(){if(!config||busy)return;dirty=true;editSerial++;el('geoDirty').textContent='有未保存的修改';summary();}
  function fit(){if(map&&circle)map.fitBounds(circle.getBounds(),{padding:[35,35],maxZoom:16,animate:false});}
  function renderCircle(){
    if(!map)return;
    if(!center){if(marker)map.removeLayer(marker);if(circle)map.removeLayer(circle);marker=null;circle=null;return;}
    if(!circle){
      circle=L.circle(center,{radius:radius(),color:'#548bef',weight:2,fillColor:'#548bef',fillOpacity:0.18,interactive:false}).addTo(map);
      marker=L.marker(center,{draggable:!busy,title:'围栏中心，拖动可调整',alt:'围栏中心',icon:L.divIcon({className:'geo-center-icon',iconSize:[20,20],iconAnchor:[10,10]})}).addTo(map);
      marker.on('drag',()=>{const p=marker.getLatLng();if(!busy&&validCenter(p.lat,p.lng))selectPoint(p.lat,p.lng,false,false);});
    }else{circle.setLatLng(center).setRadius(radius());marker.setLatLng(center);}
  }
  function selectPoint(lat,lon,zoom=false,setDefaultName=false){
    if(!config||busy||!validCenter(lat,lon))return;
    center={lat:Number(lat.toFixed(6)),lng:Number(lon.toFixed(6))};
    el('geoLatitude').value=center.lat;el('geoLongitude').value=center.lng;
    if(setDefaultName&&!el('geoName').value.trim())el('geoName').value='我的围栏';
    markDirty();renderCircle();if(zoom)fit();
    el('geoMapStatus').textContent='中心点已更新，保存后生效。可拖动标记继续调整。';
  }
  function ensureMap(){
    if(map){map.invalidateSize();return;}
    if(!window.L){el('geoMapStatus').textContent='地图组件未加载，请刷新页面。仍可在下方输入中心经纬度。';return;}
    map=L.map(el('geoMap'),{zoomControl:true,minZoom:2,maxZoom:19,scrollWheelZoom:true}).setView([31.62,104.46],11);
    tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    }).addTo(map);
    tiles.on('tileerror',()=>{if(++tileErrors>=3)el('geoMapStatus').textContent='底图加载较慢或网络不可用，可用搜索或手动经纬度选点，稍后拖动地图重试。';});
    tiles.on('tileload',()=>{if(tileErrors){tileErrors=0;el('geoMapStatus').textContent='点击地图选点，拖动中心标记可微调位置。';}});
    L.control.scale({imperial:false,position:'bottomleft'}).addTo(map);
    map.on('click',event=>{const p=event.latlng.wrap();selectPoint(p.lat,p.lng);});
  }
  function apply(data){
    if(data.schema_version!==2||data.fence?.type!=='circle'||data.fence?.coordinate_system!=='WGS-84')throw Error('配置格式不兼容，请检查服务版本');
    config=data;dirty=false;editSerial++;const s=data.settings;
    el('geoName').value=s.name;el('geoEnabled').checked=s.enabled;el('geoTest').checked=s.test_mode;
    el('geoRadius').value=s.radius_meters;el('geoRadiusSlider').max=Math.max(100000,s.radius_meters);el('geoRadiusSlider').value=s.radius_meters;
    el('geoInterval').value=s.check_interval_seconds/60;el('geoDuration').value=Number((s.max_detection_seconds/3600).toFixed(8));
    el('geoBuffer').value=s.boundary_buffer_meters;el('geoAccuracy').value=s.max_location_accuracy_meters;el('geoAge').value=s.max_location_age_seconds/60;
    center=s.center_latitude==null?null:{lat:s.center_latitude,lng:s.center_longitude};
    el('geoLatitude').value=center?.lat??'';el('geoLongitude').value=center?.lng??'';
    el('geoRevision').textContent='版本 '+data.revision;
    el('geoDirty').textContent=data.updated_at?'已保存 · '+new Date(data.updated_at).toLocaleString('zh-CN',{hour12:false}):'尚未保存';
    summary();ensureMap();renderCircle();fit();
  }
  window.loadGeofence=async function(force=false){
    if(busy)return;if(config&&!force){ensureMap();return;}
    if(dirty&&!confirm('重新读取将丢弃未保存的选点和半径修改，继续吗？'))return;
    setBusy(true);status('正在读取围栏…');
    try{const data=await api('config');apply(data);status(data.migration_required?'已改为圆形围栏，请在地图选定中心点并保存。旧的县域围栏已暂停。':'配置已读取；选点或调整半径后请保存。');}
    catch(e){status(e.message||'读取失败',true);}
    finally{setBusy(false);}
  };
  el('geoReload').onclick=()=>void window.loadGeofence(true);
  ids.forEach(id=>el(id).addEventListener('input',markDirty));
  function updateRadius(value){
    el('geoRadius').value=value;el('geoRadiusSlider').max=Math.max(100000,Number(value)||100000);el('geoRadiusSlider').value=value;
    markDirty();renderCircle();summary();
  }
  el('geoRadius').addEventListener('input',()=>updateRadius(el('geoRadius').value));
  el('geoRadiusSlider').addEventListener('input',()=>updateRadius(el('geoRadiusSlider').value));
  el('geoRadiusSlider').addEventListener('change',fit);
  document.querySelectorAll('[data-geo-radius]').forEach(b=>b.onclick=()=>{if(!busy&&config){updateRadius(b.dataset.geoRadius);fit();}});
  for(const id of ['geoLatitude','geoLongitude'])el(id).addEventListener('input',()=>{
    const lat=el('geoLatitude'),lon=el('geoLongitude');
    if(!lat.value&&!lon.value){center=null;markDirty();renderCircle();return;}
    if(lat.value&&lon.value&&validCenter(Number(lat.value),Number(lon.value))){
      center={lat:Number(lat.value),lng:Number(lon.value)};markDirty();renderCircle();
    }else{markDirty();}
  });
  el('geoFit').onclick=fit;
  el('geoLocate').onclick=()=>{
    if(!config||busy)return;
    if(!window.isSecureContext||!navigator.geolocation){el('geoMapStatus').textContent='浏览器定位需要 HTTPS。请通过搜索或点击地图选中心点。';return;}
    const serial=editSerial;el('geoLocate').disabled=true;el('geoMapStatus').textContent='正在获取你的位置，请允许浏览器定位…';
    navigator.geolocation.getCurrentPosition(pos=>{
      el('geoLocate').disabled=busy;
      if(serial!==editSerial||busy)return;
      selectPoint(pos.coords.latitude,pos.coords.longitude,true);
      el('geoMapStatus').textContent=`已定位，精度约 ${Math.round(pos.coords.accuracy)} 米。可拖动标记校正后保存。`;
    },()=>{el('geoLocate').disabled=busy;if(serial===editSerial)el('geoMapStatus').textContent='未取得位置。请检查定位权限，或直接搜索、点击地图选点。';},{enableHighAccuracy:false,timeout:12000,maximumAge:60000});
  };
  el('geoSearchForm').addEventListener('submit',async event=>{
    event.preventDefault();if(searchBusy||busy||!config)return;
    const q=el('geoSearch').value.trim();if(q.length<2)return;
    const id=++searchId;searchBusy=true;el('geoSearchButton').disabled=true;el('geoSearchStatus').textContent='正在搜索…';el('geoSearchResults').replaceChildren();
    try{
      const data=await api('search?'+new URLSearchParams({q}));if(id!==searchId)return;
      el('geoSearchStatus').textContent=data.items.length?'选择一个结果作为围栏中心 · © OpenStreetMap contributors':'没有找到位置，试试更完整的地名，或直接点击地图。';
      for(const item of data.items){
        const b=document.createElement('button');b.type='button';b.textContent=item.name;
        b.onclick=()=>{if(busy)return;selectPoint(item.latitude,item.longitude,true);el('geoName').value=item.name.split(/[,，]/)[0].slice(0,100);summary();el('geoSearchResults').replaceChildren();el('geoSearchStatus').textContent='已选中：'+item.name;};
        el('geoSearchResults').append(b);
      }
    }catch(e){if(id===searchId)el('geoSearchStatus').textContent=e.message;}
    finally{if(id===searchId){searchBusy=false;el('geoSearchButton').disabled=busy;}}
  });
  el('geoForm').addEventListener('submit',async event=>{
    event.preventDefault();if(busy||!config)return;
    const lat=el('geoLatitude').value,lon=el('geoLongitude').value;
    if(!center||!lat||!lon||!validCenter(Number(lat),Number(lon))){status('请先在地图上选定中心点，或填写有效的中心经纬度。',true);return;}
    const s=settings();
    if(s.max_detection_seconds<s.check_interval_seconds||s.max_detection_seconds%s.check_interval_seconds!==0||s.max_detection_seconds/s.check_interval_seconds>24){status('最长时长需为间隔的整数倍，且每轮检测 1～24 次。',true);return;}
    setBusy(true);status('正在保存围栏…');
    try{apply(await api('config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...s,expected_revision:config.revision})}));status('围栏已保存，支持圆形围栏的手机可读取中心点和半径。');}
    catch(e){status(e.message||'保存失败',true);}
    finally{setBusy(false);}
  });
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
  new ResizeObserver(()=>{if(map&&el('geofenceView').offsetParent!==null)map.invalidateSize();}).observe(el('geoMap'));
  document.addEventListener('DOMContentLoaded',()=>{if(el('geofenceView').classList.contains('active'))void window.loadGeofence();});
})();
