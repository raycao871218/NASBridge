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
vm.runInContext(fs.readFileSync(__dirname+'/app/static/date-format.js','utf8'),ctx);
vm.runInContext(fs.readFileSync(__dirname+'/app/static/videos.js','utf8'),ctx);
const cards=()=>nodes.videoItems.children.flatMap(group=>group.children[1].children);
(async()=>{
  await vm.runInContext('loadVideos()',ctx);
  assert.equal(cards().length,1);
  const card=cards()[0];assert.equal(card.children[2].textContent,reply.items[0].title);assert.equal(card.children[1].textContent,'发布日期：日期未知');
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
  for(const [day,label,recent] of [['2026-10-15','今天',true],['2026-10-14','昨天',true],['2026-10-13','前天',true],['2026-10-12','2026/10/12',false],['2026-10-16','2026/10/16',false]]){
    ctx.item={published_date:day,title:day.replaceAll('-','')+'-标题'};
    const info=vm.runInContext('videoDateInfo(item,new Date("2026-10-15T00:01:00"))',ctx);
    assert.equal(info.label,label);assert.equal(info.recent,recent);assert.equal(info.title,'标题');
  }
  ctx.item={published_date:'2026-02-30',title:'20260230-标题'};assert.equal(vm.runInContext('videoDateInfo(item).day',ctx),null);
  // Deliberately shuffled dates must render recent in one row and the archive below it.
  ctx.rows=[{video_id:'older',title:'20200101-old',published_date:'2020-01-01'},{video_id:'newest',title:'new',published_date:new Date().toLocaleDateString('en-CA')},{video_id:'unknown',title:'unknown'},{video_id:'lessold',title:'20200102-old',published_date:'2020-01-02'}];
  // Build today's ISO date from local calendar fields, independent of locale formatting.
  const now=new Date();ctx.rows[1].published_date=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');
  vm.runInContext('videoItems=rows;renderVideos()',ctx);
  assert.equal(nodes.videoItems.children[0].children[0].textContent,'最近三天');
  assert.equal(nodes.videoItems.children[0].children[1].className,'video-recent-row');
  assert.equal(nodes.videoItems.children[1].children[0].textContent,'往期视频');
  assert.deepEqual(cards().map(c=>c.children[2].textContent),['new','old','old','unknown']);
  assert.equal(cards()[1].children[1].datetime,'2020-01-02');
  console.log('PASS: list, text safety, same-origin playback, search, seek bounds, speed, close, failure/retry, empty and stale responses');
})().catch(error=>{console.error(error);process.exitCode=1;});
