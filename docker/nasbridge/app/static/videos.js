// Build media URLs from validated IDs; titles are always rendered as text.
let videoItems=[],videosRequest=0,videosAbort=null;
const videoPlayer=document.getElementById('videoPlayer');
function videoNode(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;}
function videoDateInfo(item,now=new Date()){
  const day=item.published_date;
  const date=typeof day==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(day)?new Date(day+'T00:00:00'):null;
  const valid=date&&!Number.isNaN(date.getTime())&&date.getFullYear()===Number(day.slice(0,4))&&date.getMonth()+1===Number(day.slice(5,7))&&date.getDate()===Number(day.slice(8,10));
  const age=valid?(Date.UTC(now.getFullYear(),now.getMonth(),now.getDate())-Date.UTC(date.getFullYear(),date.getMonth(),date.getDate()))/86400000:null;
  return {day:valid?day:null,recent:age!==null&&age>=0&&age<=2,
    label:valid?formatPanelDate(day,{dateOnly:true,now}):'日期未知',
    title:valid?String(item.title||'').replace(/^\d{8}[-_ ]+/,''):String(item.title||'')};
}
function renderVideos(){
  const query=document.getElementById('videosQuery').value.trim().toLocaleLowerCase();
  const matches=videoItems.filter(item=>String(item.title||'').toLocaleLowerCase().includes(query))
    .map(item=>({item,info:videoDateInfo(item)})).sort((a,b)=>(b.info.day||'').localeCompare(a.info.day||'')||String(a.item.title).localeCompare(String(b.item.title),'zh-CN'));
  const container=document.getElementById('videoItems');container.replaceChildren();
  for(const recent of [true,false]){
    const groupItems=matches.filter(row=>row.info.recent===recent);if(!groupItems.length)continue;
    const group=videoNode('section',null,'video-group'),grid=videoNode('div',null,recent?'video-recent-row':'video-grid');
    group.append(videoNode('h3',recent?'最近三天':'往期视频'),grid);container.append(group);
    for(const {item,info} of groupItems){
    const card=videoNode('button',null,'video-card');card.type='button';card.setAttribute('aria-label','播放：'+info.label+' · '+info.title);
    const cover=videoNode('span',null,'video-cover'),image=document.createElement('img');
    image.alt='';image.loading='lazy';image.src='/api/videos/'+encodeURIComponent(item.video_id)+'/thumbnail?v='+encodeURIComponent(item.thumbnail_version||'');
    image.onerror=()=>cover.replaceChildren(videoNode('span','暂无封面 · 点击播放'));
    const date=videoNode('time','发布日期：'+info.label,'video-card-date');if(info.day)date.setAttribute('datetime',info.day);
    cover.append(image);card.append(cover,date,videoNode('span',info.title,'video-card-title'),videoNode('span',bytes(item.size),'video-card-size'));
    card.onclick=()=>openVideo(item);grid.append(card);
    }
  }
  const status=document.getElementById('videosStatus');status.className='status-line';
  status.textContent=matches.length?'显示 '+matches.length+' 部视频（目录最多返回最近 200 部）':query?'没有匹配的视频':'暂无可播放视频';
}
async function loadVideos(){
  const request=++videosRequest;if(videosAbort)videosAbort.abort();videosAbort=new AbortController();
  const status=document.getElementById('videosStatus');status.className='status-line';status.textContent='正在加载视频…';
  videoItems=[];document.getElementById('videoItems').replaceChildren();document.getElementById('videosQuery').disabled=true;
  try{
    const response=await fetch('/api/videos',{cache:'no-store',signal:videosAbort.signal});
    if(!response.ok)throw Error('视频目录读取失败（'+response.status+'），请点击刷新重试');
    const data=await response.json();if(request!==videosRequest)return;
    if(!Array.isArray(data.items))throw Error('视频目录格式异常，请刷新重试');
    videoItems=data.items.filter(item=>item&&/^[A-Za-z0-9_-]{1,64}$/.test(item.video_id));renderVideos();
  }catch(error){if(error.name==='AbortError'||request!==videosRequest)return;status.className='status-line error';status.textContent=error.message;}
  finally{if(request===videosRequest)document.getElementById('videosQuery').disabled=false;}
}
function openVideo(item){
  videoPlayer.pause();document.getElementById('videoTitle').textContent=item.title;
  document.getElementById('videoPlaybackStatus').textContent='点击播放按钮开始播放，可通过进度条跳转、调整音量或全屏。';
  const panel=document.getElementById('videoPlayerPanel');panel.hidden=false;
  videoPlayer.src='/api/videos/'+encodeURIComponent(item.video_id);
  videoPlayer.poster='/api/videos/'+encodeURIComponent(item.video_id)+'/thumbnail?v='+encodeURIComponent(item.thumbnail_version||'');
  videoPlayer.load();panel.scrollIntoView({block:'start'});
}
function seekVideo(delta){if(Number.isFinite(videoPlayer.duration))videoPlayer.currentTime=Math.max(0,Math.min(videoPlayer.duration,videoPlayer.currentTime+delta));}
videoPlayer.addEventListener('loadedmetadata',()=>{videoPlayer.playbackRate=Number(document.getElementById('videoSpeed').value);});
videoPlayer.addEventListener('error',()=>{document.getElementById('videoPlaybackStatus').textContent='视频加载失败或浏览器不支持此编码，请重新选择视频重试。';});
document.getElementById('videoSpeed').onchange=event=>{videoPlayer.playbackRate=Number(event.target.value);};
document.getElementById('videoBack').onclick=()=>seekVideo(-10);
document.getElementById('videoForward').onclick=()=>seekVideo(10);
document.getElementById('videoClose').onclick=()=>{videoPlayer.pause();videoPlayer.removeAttribute('src');videoPlayer.removeAttribute('poster');videoPlayer.load();document.getElementById('videoPlayerPanel').hidden=true;document.getElementById('videosRefresh').focus();};
document.getElementById('videosRefresh').onclick=()=>void loadVideos();
document.getElementById('videosQuery').addEventListener('input',renderVideos);
document.addEventListener('DOMContentLoaded',()=>{if(document.getElementById('videosView').classList.contains('active'))void loadVideos();});
