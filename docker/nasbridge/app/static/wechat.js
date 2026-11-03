(() => {
  const el = id => document.getElementById(id);
  let article = null;
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
    for (const id of ['wechatHealth', 'wechatRead', 'wechatUrl', 'wechatImages']) el(id).disabled = value;
  }
  async function api(path, options = {}) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch('/api/wechat/' + path, {...options, signal: controller.signal, cache: 'no-store'});
      const payload = await response.json();
      if (!response.ok) throw Error(errors[payload.error] || (typeof payload.detail === 'string' ? payload.detail : payload.message) || '请求失败，请检查链接和服务配置。');
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
  el('wechatHealth').onclick = async () => {
    busy(true); status('正在检查服务…');
    try { const data = await api('health'); status('读取服务在线' + (data.version ? ' · ' + data.version : '') + '；实际抓取能力请通过文章链接验证。'); }
    catch (error) { status(error.message, true); }
    finally { busy(false); }
  };
  el('wechatForm').onsubmit = async event => {
    event.preventDefault(); busy(true); article = null; el('wechatResult').hidden = true;
    status('正在抓取文章，请稍候…');
    try {
      article = await api('read', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({url: el('wechatUrl').value.trim(), images: el('wechatImages').checked})});
      el('wechatTitle').textContent = article.title || '未命名文章';
      el('wechatMeta').textContent = [article.account, article.author, article.publish_time].filter(Boolean).join(' · ');
      el('wechatBody').innerHTML = renderMarkdown(article.markdown);
      el('wechatRaw').textContent = article.markdown;
      // Use the validated input, never an untrusted upstream URL, for navigation.
      el('wechatOriginal').href = el('wechatUrl').value.trim();
      el('wechatWarning').textContent = !article.title.trim() || !article.markdown.trim() ? '标题或正文为空，请打开原文核对抓取结果。' : '请核对正文完整性；图片仅保留链接，不会下载。';
      el('wechatResult').hidden = false; status('抓取完成');
    } catch (error) { article = null; status(error.message, true); }
    finally { busy(false); }
  };
  el('wechatCopy').onclick = async () => {
    if (!article) return;
    try { await navigator.clipboard.writeText(markdown()); status('Markdown 已复制'); }
    catch { status('复制失败，可下载 Markdown 或从原文区域手动复制。', true); }
  };
  el('wechatMarkdown').onclick = () => article && download('md', markdown(), 'text/markdown;charset=utf-8');
  el('wechatJson').onclick = () => article && download('json', JSON.stringify(article, null, 2), 'application/json;charset=utf-8');
})();
