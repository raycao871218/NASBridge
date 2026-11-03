(() => {
  const el = id => document.getElementById(id);
  let article = null;
  let working = false, historyLoading = false, historyPage = 1, historyTotal = 0, historyQuery = '', historySerial = 0;
  let stopRequested = false, batchRunning = false, wakeDelay = null, exporting = false;
  const selected = new Set();
  function selectTab(history) {
    el('wechatFetchPanel').hidden = history; el('wechatHistoryPanel').hidden = !history;
    for (const [id, active] of [['wechatFetchTab', !history], ['wechatHistoryTab', history]]) {
      el(id).setAttribute('aria-selected', String(active)); el(id).classList.toggle('primary', active); el(id).tabIndex = active ? 0 : -1;
    }
    if (history) void window.loadWechatHistory();
  }
  el('wechatFetchTab').onclick = () => selectTab(false);
  el('wechatHistoryTab').onclick = () => selectTab(true);
  for (const id of ['wechatFetchTab', 'wechatHistoryTab']) el(id).onkeydown = event => {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); const history = event.key === 'End' || (event.key !== 'Home' && id === 'wechatFetchTab');
      selectTab(history); el(history ? 'wechatHistoryTab' : 'wechatFetchTab').focus();
    }
  };
  const errors = {
    verify_required: '微信要求验证，请停止抓取，人工处理后再重试。',
    deleted: '文章已被发布者删除。', blocked: '文章被屏蔽或限制查看。',
    expired: '临时链接已过期，请换用永久文章链接。', invalid: '微信文章链接参数错误。',
    invalid_url: '请填写有效的微信公众号文章链接。', migrated: '公众号已迁移，原链接可能不可用。',
    unavailable: '未能解析到可用正文。', fetch_failed: '抓取失败，请稍后手动重试。'
  };
  function status(message, error = false) {
    el('wechatStatus').textContent = message;
    el('wechatStatus').className = 'status-line' + (error ? ' error' : '');
  }
  function busy(value) {
    working = value;
    for (const id of ['wechatHealth', 'wechatRead', 'wechatUrl', 'wechatImages']) el(id).disabled = value;
    historyControls();
  }
  function historyControls() {
    el('wechatHistoryRefresh').disabled = working || historyLoading;
    el('wechatHistoryPrev').disabled = working || historyLoading || historyPage <= 1;
    el('wechatHistoryNext').disabled = working || historyLoading || historyPage * 20 >= historyTotal;
    el('wechatHistoryItems').querySelectorAll('button').forEach(button => { button.disabled = working; });
    el('wechatExportSelected').disabled = exporting || selected.size === 0;
    el('wechatExportSelected').textContent = '下载选中（' + selected.size + '）';
    el('wechatExportAll').disabled = exporting || historyLoading || historyTotal === 0;
    el('wechatExportAll').textContent = historyQuery ? '打包下载搜索结果' : '打包下载全部';
    const boxes = [...el('wechatHistoryItems').querySelectorAll('input[type=checkbox]')];
    el('wechatSelectPage').disabled = !boxes.length;
    el('wechatSelectPage').checked = boxes.length > 0 && boxes.every(box => box.checked);
    el('wechatSelectPage').indeterminate = boxes.some(box => box.checked) && !el('wechatSelectPage').checked;
  }
  async function api(path, options = {}) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch('/api/wechat/' + path, {...options, signal: controller.signal, cache: 'no-store'});
      const payload = await response.json();
      if (!response.ok) {
        const error = Error(errors[payload.error] || (typeof payload.detail === 'string' ? payload.detail : payload.message) || '请求失败，请检查链接和服务配置。');
        error.code = payload.error; error.status = response.status; throw error;
      }
      return payload;
    } catch (error) {
      if (error.name === 'AbortError') throw Error('请求超时，请稍后手动重试。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  function markdown() {
    const metadata = [['公众号', article.account], ['作者', article.author], ['发布时间', article.publish_time], ['原文', article.url]];
    return '# ' + (article.title || '未命名文章') + '\n\n' + metadata.filter(([, value]) => value).map(([name, value]) => name + '：' + value).join('\n\n') + '\n\n' + article.markdown;
  }
  function download(extension, content, type) {
    if (!article) return;
    const url = URL.createObjectURL(new Blob([content], {type})), link = document.createElement('a');
    link.href = url;
    link.download = (article.title || '微信文章').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 100) + '.' + extension;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function showArticle(data, sourceUrl) {
    article = data;
    el('wechatTitle').textContent = article.title || '未命名文章';
    el('wechatMeta').textContent = [article.account, article.author, article.publish_time].filter(Boolean).join(' · ');
    el('wechatBody').innerHTML = renderMarkdown(article.markdown);
    el('wechatRaw').textContent = article.markdown;
    let safeUrl = null;
    try { const url = new URL(sourceUrl); if (['https:', 'http:'].includes(url.protocol) && url.hostname === 'mp.weixin.qq.com') safeUrl = url.href; } catch {}
    el('wechatOriginal').hidden = !safeUrl;
    if (safeUrl) el('wechatOriginal').href = safeUrl;
    else el('wechatOriginal').removeAttribute('href');
    el('wechatWarning').textContent = article.history_error || (!article.title.trim() || !article.markdown.trim() ? '标题或正文为空，未保存到历史，请打开原文核对。' : '请核对正文完整性；图片仅保留链接，不会下载。');
    el('wechatResult').hidden = false;
  }
  async function openArticle(id) {
    if (working) return;
    busy(true); status('正在打开历史文章…');
    try {
      const data = await api('articles/' + encodeURIComponent(id));
      showArticle(data, data.url);
      selectTab(false);
      status('已打开保存的文章，无需重新抓取');
      el('wechatResult').scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) { el('wechatHistoryStatus').textContent = error.message; el('wechatHistoryStatus').className = 'status-line error'; }
    finally { busy(false); }
  }
  window.loadWechatHistory = async function(page = historyPage) {
    const serial = ++historySerial;
    historyLoading = true; historyControls();
    el('wechatHistoryStatus').textContent = '正在加载历史…';
    try {
      const data = await api('articles?' + new URLSearchParams({q: historyQuery, page, limit: 20}));
      if (serial !== historySerial) return;
      historyPage = data.page; historyTotal = data.total;
      el('wechatHistoryItems').replaceChildren();
      for (const item of data.items) {
        const row = document.createElement('div'); row.className = 'proxy-card';
        const title = document.createElement('strong'); title.textContent = item.title || '未命名文章';
        const meta = document.createElement('p'); meta.className = 'small muted';
        meta.textContent = [item.account, item.author, item.publish_time ? '发布：' + item.publish_time : '', '抓取：' + new Date(item.fetched_at).toLocaleString('zh-CN', {hour12: false})].filter(Boolean).join(' · ');
        const button = document.createElement('button'); button.type = 'button'; button.textContent = '打开文章';
        button.onclick = () => void openArticle(item.id);
        const label = document.createElement('label'); label.className = 'check';
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = item.id; checkbox.checked = selected.has(item.id);
        checkbox.onchange = () => { if (checkbox.checked) selected.add(item.id); else selected.delete(item.id); historyControls(); };
        label.append(checkbox, title);
        row.append(label, meta, button); el('wechatHistoryItems').appendChild(row);
      }
      el('wechatHistoryStatus').textContent = data.total ? '共 ' + data.total + ' 篇，按最近抓取排序' : historyQuery ? '没有匹配的历史文章' : '暂无抓取历史，成功抓取后将自动保存在这里。';
      el('wechatHistoryStatus').className = 'status-line';
      el('wechatHistoryPage').textContent = data.total ? historyPage + ' / ' + Math.ceil(data.total / 20) : '';
    } catch (error) {
      if (serial !== historySerial) return;
      el('wechatHistoryStatus').textContent = error.message;
      el('wechatHistoryStatus').className = 'status-line error';
    } finally { if (serial === historySerial) { historyLoading = false; historyControls(); } }
  };
  el('wechatHistoryRefresh').onclick = () => void window.loadWechatHistory();
  el('wechatHistoryPrev').onclick = () => void window.loadWechatHistory(historyPage - 1);
  el('wechatHistoryNext').onclick = () => void window.loadWechatHistory(historyPage + 1);
  el('wechatSearchForm').onsubmit = event => { event.preventDefault(); historyQuery = el('wechatSearch').value.trim(); void window.loadWechatHistory(1); };
  el('wechatSelectPage').onchange = () => {
    el('wechatHistoryItems').querySelectorAll('input[type=checkbox]').forEach(box => {
      box.checked = el('wechatSelectPage').checked; if (box.checked) selected.add(box.value); else selected.delete(box.value);
    }); historyControls();
  };
  async function exportHistory(ids) {
    if (exporting) return;
    exporting = true; historyControls(); el('wechatExportStatus').textContent = '正在打包历史文章…';
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 300000);
    try {
      const response = await fetch('/api/wechat/export', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(ids ? {ids} : {q: historyQuery}), signal: controller.signal, cache: 'no-store'});
      if (!response.ok) {
        const data = await response.json(); throw Error(typeof data.detail === 'string' ? data.detail : '历史文章打包失败');
      }
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = 'wechat-articles.zip'; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      el('wechatExportStatus').textContent = 'ZIP 已下载，每篇包含 Markdown 和 JSON。';
    } catch (error) { el('wechatExportStatus').textContent = error.name === 'AbortError' ? '打包超时，请减少选择后重试。' : error.message; }
    finally { clearTimeout(timer); exporting = false; historyControls(); }
  }
  el('wechatExportSelected').onclick = () => void exportHistory([...selected]);
  el('wechatExportAll').onclick = () => void exportHistory();
  el('wechatHealth').onclick = async () => {
    busy(true); status('正在检查服务…');
    try { const data = await api('health'); status('读取服务在线' + (data.version ? ' · ' + data.version : '') + '；实际抓取能力请通过文章链接验证。'); }
    catch (error) { status(error.message, true); }
    finally { busy(false); }
  };
  el('wechatForm').onsubmit = async event => {
    event.preventDefault(); if (working) return;
    const urls = [...new Set(el('wechatUrl').value.split(/\r?\n/).map(value => value.trim()).filter(Boolean))];
    if (!urls.length || urls.length > 100) { status('请填写 1～100 个链接，每行一个。', true); return; }
    for (const value of urls) {
      try {
        const url = new URL(value);
        if (value.length > 8192 || /[\s\\\x00-\x1f]/.test(value) || !['http:', 'https:'].includes(url.protocol) || url.hostname !== 'mp.weixin.qq.com' || url.username || url.password || !['', '80', '443'].includes(url.port) || !(url.pathname === '/s' || url.pathname.startsWith('/s/'))) throw Error();
      } catch { status('链接无效：' + value + '，请填写微信公众号文章链接，每行一个。', true); return; }
    }
    busy(true); stopRequested = false; batchRunning = true;
    el('wechatStop').hidden = false; el('wechatStop').disabled = false;
    article = null; el('wechatResult').hidden = true; el('wechatBatchItems').replaceChildren();
    const rows = urls.map((url, index) => {
      const row = document.createElement('div'); row.className = 'small muted'; row.style.overflowWrap = 'anywhere';
      row.textContent = (index + 1) + '. 等待抓取 · ' + url; el('wechatBatchItems').appendChild(row); return row;
    });
    let completed = 0, saved = 0, failed = 0, stopReason = '';
    try {
      for (let index = 0; index < urls.length; index++) {
        if (stopRequested) break;
        status('正在抓取 ' + (index + 1) + ' / ' + urls.length + '，已保存 ' + saved + ' 篇…');
        rows[index].textContent = (index + 1) + '. 正在抓取 · ' + urls[index];
        try {
          const data = await api('read', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: urls[index], images: el('wechatImages').checked})});
          showArticle(data, urls[index]);
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = (index + 1) + '. ' + (data.title || '未命名文章') + (data.history_id ? ' · 已保存' : ' · 未保存');
          button.onclick = () => showArticle(data, urls[index]); rows[index].replaceChildren(button);
          if (data.history_id) saved++; else { failed++; if (data.history_error) { stopReason = data.history_error; stopRequested = true; } }
        } catch (error) {
          failed++; rows[index].className = 'small error'; rows[index].textContent = (index + 1) + '. ' + error.message + ' · ' + urls[index];
          if (error.code === 'verify_required' || [409, 503].includes(error.status) || !error.status) { stopReason = error.message; stopRequested = true; }
        }
        completed++;
        if (!stopRequested && index < urls.length - 1) {
          status('已完成 ' + completed + ' / ' + urls.length + '，等待 10 秒后抓取下一篇…');
          await new Promise(resolve => { const timer = setTimeout(() => { wakeDelay = null; resolve(); }, 10000); wakeDelay = () => { clearTimeout(timer); wakeDelay = null; resolve(); }; });
        }
      }
      for (let index = completed; index < urls.length; index++) rows[index].textContent = (index + 1) + '. 未抓取 · ' + urls[index];
      status((stopRequested ? '已停止' : '抓取完成') + '：已保存 ' + saved + ' 篇，失败或未保存 ' + failed + ' 篇，未抓取 ' + (urls.length - completed) + ' 篇。' + (stopReason ? ' ' + stopReason : ''), !!failed || !!stopReason);
    } finally { batchRunning = false; el('wechatStop').hidden = true; busy(false); }
  };
  el('wechatStop').onclick = () => { stopRequested = true; el('wechatStop').disabled = true; status('已请求停止，当前文章完成后不再抓取后续链接。'); if (wakeDelay) wakeDelay(); };
  window.addEventListener('beforeunload', event => { if (batchRunning) { event.preventDefault(); event.returnValue = ''; } });
  el('wechatCopy').onclick = async () => {
    if (!article) return;
    try { await navigator.clipboard.writeText(markdown()); status('Markdown 已复制'); }
    catch { status('复制失败，可下载 Markdown 或从原文区域手动复制。', true); }
  };
  el('wechatMarkdown').onclick = () => article && download('md', markdown(), 'text/markdown;charset=utf-8');
  el('wechatJson').onclick = () => article && download('json', JSON.stringify(article, null, 2), 'application/json;charset=utf-8');
})();
