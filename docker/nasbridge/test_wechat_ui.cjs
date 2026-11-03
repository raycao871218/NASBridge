// Isolated browser test: every API is mocked, no real articles are fetched.
const {chromium} = require('playwright');
const http = require('node:http'), fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.join(__dirname, 'app', 'static');
(async () => {
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(root, pathname === '/' ? 'index.html' : pathname.replace(/^\/static\//, ''));
    if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
    try { res.setHeader('Content-Type', ({'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'})[path.extname(file)] || 'application/octet-stream'); res.end(fs.readFileSync(file)); }
    catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: process.env.NASBRIDGE_BROWSER_CHANNEL || 'msedge'});
    const page = await browser.newPage({viewport: {width: 1365, height: 1000}, acceptDownloads: true});
    let failure = false, reads = 0;
    const exports = [];
    const articleUrl = 'https://mp.weixin.qq.com/s?__biz=abc%3D%3D&mid=123&idx=1&sn=xyz';
    const secondUrl = 'https://mp.weixin.qq.com/s/second';
    const archived = {title: '<img src=x onerror=alert(1)>文章', account: '示例公众号', author: '作者', publish_time: '',
      markdown: '## 小标题\n\n正文 <script>alert(1)</script>\n\n**重点**', url: articleUrl, images: [],
      history_id: 'a'.repeat(64), fetched_at: '2026-11-03T08:00:00+00:00'};
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/wechat/export') {
        exports.push(route.request().postDataJSON());
        return route.fulfill({contentType: 'application/zip', body: Buffer.from('504b0506000000000000000000000000000000000000', 'hex')});
      }
      if (url.pathname === '/api/wechat/articles') {
        const found = reads > 0 && (!url.searchParams.get('q') || url.searchParams.get('q') === '示例公众号');
        return route.fulfill({json: {items: found ? [{...archived, id: archived.history_id}] : [], total: found ? 1 : 0, page: Number(url.searchParams.get('page')), limit: 20}});
      }
      if (url.pathname.startsWith('/api/wechat/articles/')) return route.fulfill({json: archived});
      if (url.pathname === '/api/wechat/health') return route.fulfill({json: {ok: true, version: '0.1.0'}});
      if (url.pathname === '/api/wechat/read') {
        reads++;
        const body = route.request().postDataJSON();
        assert.ok([articleUrl, secondUrl].includes(body.url)); assert.equal(body.images, false);
        return route.fulfill(failure ? {status: 502, json: {error: 'verify_required', message: 'verify'}} : {json: {...archived, url: body.url}});
      }
      return route.fulfill({json: url.pathname === '/api/home/sites' ? {revision: 0, sites: Array(6).fill(null)} : {}});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator('[data-view-target=wechatView]').click();
    assert.equal(await page.locator('#wechatFetchPanel').isVisible(), true);
    assert.equal(await page.locator('#wechatHistoryPanel').isVisible(), false);
    await page.locator('#wechatHealth').click();
    await page.getByText(/读取服务在线/).waitFor();
    await page.locator('#wechatUrl').fill(articleUrl);
    await page.locator('#wechatImages').uncheck();
    await page.locator('#wechatRead').click();
    await page.locator('#wechatResult').waitFor();
    assert.equal(await page.locator('#wechatTitle img').count(), 0);
    assert.equal(await page.locator('#wechatBody script').count(), 0);
    assert.equal(await page.locator('#wechatBody strong').textContent(), '重点');
    assert.equal(await page.locator('#wechatOriginal').getAttribute('href'), articleUrl);
    for (const [id, extension] of [['wechatMarkdown', '.md'], ['wechatJson', '.json']]) {
      const event = page.waitForEvent('download'); await page.locator('#' + id).click();
      const download = await event;
      assert.ok(download.suggestedFilename().endsWith(extension));
      const content = fs.readFileSync(await download.path(), 'utf8');
      if (extension === '.json') assert.equal(JSON.parse(content).account, '示例公众号');
      else { assert.ok(content.includes('公众号：示例公众号')); assert.ok(content.includes('**重点**')); }
    }
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    failure = true; await page.locator('#wechatRead').click();
    await page.locator('#wechatStatus').filter({hasText: '微信要求验证'}).waitFor();
    assert.equal(await page.locator('#wechatResult').isVisible(), false);
    assert.equal(reads, 2);
    await page.reload();
    assert.equal(await page.locator('#wechatFetchPanel').isVisible(), true);
    await page.locator('#wechatHistoryTab').click();
    await page.locator('#wechatHistoryItems button').waitFor();
    await page.locator('#wechatHistoryItems button').click();
    await page.locator('#wechatResult').waitFor();
    assert.equal(reads, 2); // Reopen from storage, never fetch WeChat again.
    assert.equal(await page.locator('#wechatBody strong').textContent(), '重点');
    assert.equal(await page.locator('#wechatHistoryItems img').count(), 0);
    assert.equal(await page.locator('#wechatHistoryPanel').isVisible(), false);
    await page.locator('#wechatHistoryTab').click();
    await page.locator('#wechatSearch').fill('missing');
    await page.locator('#wechatSearchForm button').click();
    await page.getByText('没有匹配的历史文章', {exact: true}).waitFor();
    await page.locator('#wechatSearch').fill('示例公众号');
    await page.locator('#wechatSearchForm button').click();
    await page.locator('#wechatHistoryItems button').waitFor();
    let downloadEvent = page.waitForEvent('download');
    await page.locator('#wechatExportAll').click();
    assert.equal((await downloadEvent).suggestedFilename(), 'wechat-articles.zip');
    assert.deepEqual(exports[0], {q: '示例公众号'});
    await page.locator('#wechatSelectPage').check();
    downloadEvent = page.waitForEvent('download'); await page.locator('#wechatExportSelected').click();
    assert.equal((await downloadEvent).suggestedFilename(), 'wechat-articles.zip');
    assert.deepEqual(exports[1], {ids: [archived.history_id]});
    await page.locator('#wechatFetchTab').click();
    await page.locator('#wechatImages').uncheck();
    failure = false;
    await page.clock.install();
    await page.locator('#wechatUrl').fill(articleUrl + '\n' + secondUrl + '\n' + articleUrl);
    await page.locator('#wechatRead').click();
    await page.getByText(/等待 10 秒后/).waitFor();
    await page.clock.fastForward(10000);
    await page.getByText(/抓取完成：已保存 2 篇/).waitFor();
    assert.equal(reads, 4); // Duplicated input is fetched once.
    assert.equal(await page.locator('#wechatBatchItems button').count(), 2);
    await page.locator('#wechatRead').click();
    await page.getByText(/等待 10 秒后/).waitFor();
    await page.locator('#wechatStop').click();
    await page.getByText(/已停止：已保存 1 篇/).waitFor();
    assert.equal(reads, 5);
    failure = true;
    await page.locator('#wechatRead').click();
    await page.locator('#wechatStatus').filter({hasText: '微信要求验证'}).waitFor();
    await page.clock.fastForward(20000);
    assert.equal(reads, 6); // Verification halts the remaining batch.
    assert.match(await page.locator('#wechatStatus').textContent(), /未抓取 1 篇/);
    console.log('WeChat UI: tabs, preview, history, ZIP selection/search, serial batch/dedup, stop and verification passed');
  } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
