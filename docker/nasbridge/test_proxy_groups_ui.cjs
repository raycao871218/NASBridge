const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.NASBRIDGE_PLAYWRIGHT||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.NASBRIDGE_BROWSER||undefined,headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:850}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const node=name=>({name,type:'Vless',udp:true,delay:123});
  let groups=[{name:'PROXY',type:'Selector',now:'AUTO-MATCH',selectable:true,members:[{...node('AUTO-MATCH'),type:'URLTest',now:'AU'},node('JP'),node('<img src=x onerror=alert(1)>')]},
   {name:'AUTO-MATCH',type:'URLTest',now:'AU',fixed:'',automatic:true,selectable:true,members:[node('AU'),node('JP')]},
   {name:'BALANCE',type:'LoadBalance',selectable:false,members:[node('JP')]}];
  const calls=[];let failRead=false;
  const html=fs.readFileSync(path.join(__dirname,'app/static/index.html'),'utf8');
  const section=html.match(/    <section class="panel" aria-label="代理节点管理">[\s\S]*?<\/section>/)[0];
  await page.route('http://fixture/**',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.pathname.startsWith('/api/')){
    calls.push([request.method(),url.pathname]);
    if(request.method()==='GET')return route.fulfill({status:failRead?502:200,json:failRead?{detail:'fixture unavailable'}:{items:groups}});
    const body=request.postDataJSON();
    if(url.pathname.endsWith('/selection')){const group=groups.find(g=>g.name===body.group);group.now=body.name;if(group.automatic)group.fixed=body.name;}
    if(url.pathname.endsWith('/automatic'))groups.find(g=>g.name===body.group).fixed='';
    return route.fulfill({json:{message:'已切换',delay:80}});
   }
   if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(__dirname,'app',url.pathname))});
   return route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${html.match(/<style>([\s\S]*?)<\/style>/)[1]}</style><link rel="stylesheet" href="/static/proxy-groups.css"><link rel="stylesheet" href="/static/crayon.css"><div id="mihomoView" class="active" style="padding:20px">${section}</div><script src="/static/proxy-groups.js"></script>`});
  });
  await page.goto('http://fixture/');await page.getByText('已更新',{exact:true}).waitFor();
  const selector=page.locator('.proxy-group').nth(0),auto=page.locator('.proxy-group').nth(1);
  assert.equal(await selector.locator('img').count(),0,'Untrusted node names remain text');
  await selector.locator('.proxy-node-choice').filter({hasText:'JP'}).click();
  await selector.getByText('当前：JP',{exact:true}).waitFor();
  assert.equal(await selector.locator('[aria-pressed="true"] strong').textContent(),'✓ JP');
  // Clicking the currently automatic node can explicitly pin it.
  await auto.locator('.proxy-node-choice').filter({hasText:'AU'}).click();await auto.getByText('URLTest · 固定：AU',{exact:true}).waitFor();
  await auto.getByRole('button',{name:'恢复自动'}).click();await auto.getByText('URLTest · 自动选择',{exact:true}).waitFor();
  assert.equal(await page.locator('.proxy-group').nth(2).locator('.proxy-node-choice').isDisabled(),true);
  failRead=true;await page.getByRole('button',{name:'刷新节点'}).click();await page.getByText(/读取失败/).waitFor();
  assert.equal(await selector.locator('.proxy-node-choice').nth(1).isDisabled(),true);
  failRead=false;await page.getByRole('button',{name:'刷新节点'}).click();await page.getByText('已更新',{exact:true}).waitFor();
  for(const width of [1100,390]){await page.setViewportSize({width,height:850});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal overflow');}
  assert.deepEqual(errors,[]);
  assert(calls.some(([m,p])=>m==='PUT'&&p.endsWith('/selection')));
  console.log('PASS: selection, automatic pin/reset, readonly groups, error disable/recovery, escaped names, desktop/mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
