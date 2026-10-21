// Browser integration test with an isolated in-memory API; never contacts a NAS.
const {chromium}=require('playwright');
const http=require('node:http'), fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
const root=path.join(__dirname,'app','static');
(async()=>{
 const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  const file=pathname==='/'?'index.html':pathname.replace(/^\/static\//,'');
  const resolved=path.resolve(root,file);
  if(!resolved.startsWith(root+path.sep)){res.writeHead(404).end();return;}
  try{res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(resolved));}catch{res.writeHead(404).end();}
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.HOME_TEST_BROWSER?{channel:process.env.HOME_TEST_BROWSER}:{})});
  const thumbnailPage=await browser.newPage({viewport:{width:1280,height:800}});
  await thumbnailPage.setContent('<body style="margin:0;font:24px system-ui;background:#f5f6f8;color:#27334c"><header style="padding:24px 48px;background:#26364c;color:white">我的相册　　照片　　时间线　　收藏</header><main style="padding:36px 48px"><h1>生活里的好风景</h1><p>最近上传 · 2026 年 10 月</p><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:20px">'+['#809b84','#9eabc6','#d7b08c','#b4a1bd','#8baba8','#c8bc92'].map((c,i)=>`<section style="height:220px;border-radius:14px;background:linear-gradient(150deg,${c},#f3e9cd);padding:20px;box-sizing:border-box">相册 ${i+1}<div style="margin:25px auto;width:90px;height:90px;border-radius:50%;background:#fff5"></div></section>`).join('')+'</div></main></body>');
  const previewBytes=await thumbnailPage.screenshot({type:'jpeg'});await thumbnailPage.close();
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  let state={revision:0,sites:Array(6).fill(null)}, failure=false, loadFailure=false, metadataFailure=false;
  await page.route('**/api/**',async route=>{
   if(/\/api\/home\/sites\/\d\/preview/.test(route.request().url()))return route.fulfill({contentType:'image/jpeg',body:previewBytes});
   if(route.request().url().endsWith('/api/home/metadata'))return route.fulfill(metadataFailure?{status:502,json:{detail:'暂时无法读取网站信息，可直接保存'}}:{json:{name:'自动读取的相册',image_url:new URL('/static/nest.svg',route.request().url()).href}});
   if(!route.request().url().endsWith('/api/home/sites'))return route.fulfill({json:{}});
   if(route.request().method()==='PUT'){
    if(failure)return route.fulfill({status:409,json:{detail:'网站配置已被其他页面修改，请重新加载首页后再试'}});
    const body=route.request().postDataJSON();assert.equal(body.expected_revision,state.revision);
    state={revision:state.revision+1,sites:body.sites};
   }else if(loadFailure)return route.fulfill({status:503,json:{detail:'暂时不可用'}});
   return route.fulfill({json:state});
  });
  const url=`http://127.0.0.1:${server.address().port}`;
  await page.goto(url);
  await page.locator('.site-add').first().waitFor();
  assert.equal(await page.locator('.view.active').getAttribute('id'),'homeView');
  assert.equal(await page.locator('.site-add').count(),6);
  await page.locator('.site-add').first().click();
  assert.equal(await page.locator('#homeSiteRemove').isVisible(),false);
  await page.locator('#homeSiteName').fill('我的相册');
  await page.locator('#homeSiteUrl').fill('javascript:alert(1)');
  await page.locator('#homeSiteForm button[type=submit]').click();
  assert.equal(state.revision,0);assert.match(await page.locator('#homeSiteError').textContent(),/有效/);
  await page.locator('#homeSiteUrl').fill('photos.example.com');
  await page.locator('#homeSiteForm button[type=submit]').click();
  await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  const link=page.locator('.home-site a').first();
  assert.equal(await link.getAttribute('href'),'https://photos.example.com');assert.equal(await link.getAttribute('target'),'_blank');
  await page.reload();await page.locator('.site-edit').waitFor();
  assert.equal(await page.locator('.home-site a strong').textContent(),'我的相册');
  await page.locator('.site-edit').click();await page.locator('#homeSiteName').fill('<img src=x onerror=alert(1)>');
  await page.locator('#homeSiteForm button[type=submit]').click();await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('.home-site strong img').count(),0);
  failure=true;await page.locator('.site-edit').click();await page.locator('#homeSiteForm button[type=submit]').click();
  await page.getByText('网站配置已被其他页面修改，请重新加载首页后再试',{exact:true}).waitFor();
  assert.equal(await page.locator('#homeSiteDialog').isVisible(),true);failure=false;
  await page.locator('#homeSiteCancel').click();
  await page.locator('[data-view-target=mihomoView]').click();await page.reload();
  assert.equal(await page.locator('.view.active').getAttribute('id'),'mihomoView');
  await page.locator('[data-view-target=homeView]').click();
  await page.locator('.site-edit').click();await page.locator('#homeSiteName').fill('我的相册');
  await page.locator('#homeSiteForm button[type=submit]').click();await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  const output=path.join(__dirname,'..','..','artifacts');fs.mkdirSync(output,{recursive:true});
  await page.screenshot({path:path.join(output,'home-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'home-mobile.png'),fullPage:true});
  await page.locator('.site-edit').click();await page.locator('#homeSiteRemove').click();await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('.site-add').count(),6);
  await page.locator('.site-add').first().click();await page.locator('#homeSiteUrl').fill('photos.example.com');
  await page.locator('#homeSiteForm button[type=submit]').click();await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('.home-site a strong').textContent(),'自动读取的相册');
  assert.ok(state.sites[0].image_url.endsWith('/static/nest.svg'));
  await page.locator('.home-sites .site-identity').waitFor();
  assert.equal(state.sites[0].cover_mode,'identity');
  await page.locator('.site-edit').first().click();
  await page.selectOption('#homeSiteCover','screenshot');
  await page.locator('#homeSiteForm button[type=submit]').click();
  await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  await page.locator('.home-sites .site-cover').waitFor();
  assert.equal(state.sites[0].cover_mode,'screenshot');
  metadataFailure=true;await page.locator('.site-add').first().click();await page.locator('#homeSiteUrl').fill('unavailable.example.com');
  await page.locator('#homeSiteForm button[type=submit]').click();await page.locator('#homeSiteDialog').waitFor({state:'hidden'});
  assert.equal(state.sites[1].name,'unavailable.example.com');assert.equal(state.sites[1].image_url,undefined);
  assert.equal(await page.locator('.home-sites .site-preview').count(),2);
  loadFailure=true;await page.locator('#homeReload').click();await page.getByText(/加载失败：暂时不可用/).waitFor();
  loadFailure=false;await page.locator('#homeReload').click();await page.locator('#homeStatus:empty').waitFor({state:'attached'});
  console.log('PASS: six slots, default home, validation, save/reload, safe text, conflict, tab restoration, mobile layout, removal, load failure/retry');
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
