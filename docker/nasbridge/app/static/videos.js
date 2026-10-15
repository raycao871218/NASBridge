// Build media URLs from validated IDs; titles are always rendered as text.
let videoItems=[],videosRequest=0,videosAbort=null;
const videoPlayer=document.getElementById('videoPlayer');
function videoNode(tag,text,cls){const node=document.createElement(tag);if(text!=null)node.textContent=text;if(cls)node.className=cls;return node;}
function renderVideos(){
  const query=document.getElementById('videosQuery').value.trim().toLocaleLowerCase();
  const matches=videoItems.filter(item=>String(item.title||'').toLocaleLowerCase().includes(query));
  const container=document.getElementById('videoItems');container.replaceChildren();
  for(const item of matches){
    const card=videoNode('button',null,'video-card');card.type='button';card.setAttribute('aria-label','播放：'+item.title);
    const cover=videoNode('span',null,'video-cover'),image=document.createElement('img');
    image.alt='';image.loading='lazy';image.src='/api/videos/'+encodeURIComponent(item.video_id)+'/thumbnail?v='+encodeURIComponent(item.thumbnail_version||'');
    image.onerror=()=>cover.replaceChildren(videoNode('span','暂无封面 · 点击播放'));
    cover.append(image);card.append(cover,videoNode('span',item.title,'video-card-title'),videoNode('span',bytes(item.size),'video-card-size'));
    card.onclick=()=>openVideo(item);container.append(card);
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
