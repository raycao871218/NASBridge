const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
class Node {
  constructor(){this.children=[];this.value='';this.events={};this.classList={contains:()=>false};this.currentTime=0;this.duration=60;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this[key]=value;}
  removeAttribute(key){delete this[key];}
  addEventListener(key,fn){this.events[key]=fn;}
  pause(){this.paused=true;}
  load(){} scrollIntoView(){} focus(){}
}
const nodes={};const document={getElementById:id=>nodes[id]??=new Node(),createElement:()=>new Node(),addEventListener(){}};
let reply={items:[{video_id:'safe-id',title:'<img onerror=alert(1)> 测试视频',size:100,stream_path:'https://untrusted.invalid'},{video_id:'../bad',title:'invalid'}]};
let fail=false;
const ctx=vm.createContext({document,AbortController,URLSearchParams,bytes:String,fetch:async()=>({ok:!fail,status:503,json:async()=>reply})});
vm.runInContext(fs.readFileSync(__dirname+'/app/static/videos.js','utf8'),ctx);
(async()=>{
  await vm.runInContext('loadVideos()',ctx);
  assert.equal(nodes.videoItems.children.length,1);
  const card=nodes.videoItems.children[0];assert.equal(card.children[1].textContent,reply.items[0].title);
  card.onclick();assert.equal(nodes.videoPlayer.src,'/api/videos/safe-id');assert.equal(nodes.videoPlayerPanel.hidden,false);
  nodes.videoPlayer.currentTime=55;nodes.videoForward.onclick();assert.equal(nodes.videoPlayer.currentTime,60);
  nodes.videoPlayer.currentTime=3;nodes.videoBack.onclick();assert.equal(nodes.videoPlayer.currentTime,0);
  nodes.videoSpeed.onchange({target:{value:'1.5'}});assert.equal(nodes.videoPlayer.playbackRate,1.5);
  nodes.videosQuery.value='不存在';nodes.videosQuery.events.input();assert.equal(nodes.videoItems.children.length,0);assert.match(nodes.videosStatus.textContent,/没有匹配/);
  nodes.videoClose.onclick();assert.equal(nodes.videoPlayerPanel.hidden,true);assert.equal(nodes.videoPlayer.src,undefined);assert.equal(nodes.videoPlayer.paused,true);
  fail=true;await vm.runInContext('loadVideos()',ctx);assert.match(nodes.videosStatus.textContent,/503/);assert.equal(nodes.videosQuery.disabled,false);
  fail=false;reply={items:[]};nodes.videosQuery.value='';await vm.runInContext('loadVideos()',ctx);assert.match(nodes.videosStatus.textContent,/暂无/);
  // A slower old response must not overwrite the latest refresh.
  let resolveOld,calls=0;ctx.fetch=()=>++calls===1?new Promise(resolve=>{resolveOld=resolve;}):Promise.resolve({ok:true,json:async()=>({items:[]})});
  const old=vm.runInContext('loadVideos()',ctx);await vm.runInContext('loadVideos()',ctx);
  resolveOld({ok:true,json:async()=>({items:[{video_id:'stale',title:'stale'}]})});await old;assert.equal(nodes.videoItems.children.length,0);
  console.log('PASS: list, text safety, same-origin playback, search, seek bounds, speed, close, failure/retry, empty and stale responses');
})().catch(error=>{console.error(error);process.exitCode=1;});
