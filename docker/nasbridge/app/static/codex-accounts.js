(() => {
  const $ = id => document.getElementById(id);
  const refresh = $('codexRefresh'), cards = $('codexCards'), feedback = $('codexFeedback');
  const dialog = $('codexConfirm');
  let current = null, busy = false;
  let pending = null;
  let quota = {};
  const resetFormat = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false
  });

  function quotaView(windows = []) {
    const quota = document.createElement('span');
    quota.className = 'codex-quota';
    if (!windows.length) {
      quota.textContent = '余量暂不可用';
      return quota;
    }
    for (const window of windows) {
      const item = document.createElement('span');
      item.className = 'codex-quota-item';
      const name = window.window_seconds === 604800 ? '周额度'
        : window.window_seconds % 3600 === 0 ? (window.window_seconds / 3600) + ' 小时额度'
        : Math.round(window.window_seconds / 60) + ' 分钟额度';
      const value = document.createElement('strong');
      value.textContent = name + ' · 剩余 ' + window.remaining_percent + '%';
      const reset = document.createElement('span');
      reset.textContent = resetFormat.format(new Date(window.reset_at * 1000)) + ' 重置（北京时间）';
      item.append(value, reset);
      quota.append(item);
    }
    return quota;
  }

  function show(data) {
    current = data?.current || null;
    if (!data || data.quota) quota = data?.quota || {};
    cards.replaceChildren();
    if (!current) return;
    for (const email of new Set([...data.accounts, current])) {
      const active = email === current;
      const card = document.createElement(active ? 'article' : 'button');
      card.className = active ? 'codex-card is-current' : 'codex-card';
      const title = document.createElement('span');
      title.className = 'codex-card-email';
      title.textContent = email;
      const label = document.createElement('span');
      label.className = active ? 'codex-card-label is-current' : 'codex-card-label';
      label.textContent = active ? '当前登录' : '切换到此账号 →';
      if (!active) {
        card.type = 'button';
        card.addEventListener('click', () => {
          if (busy || dialog.open) return;
          pending = email;
          $('codexConfirmEmail').textContent = email;
          dialog.returnValue = '';
          dialog.showModal();
        });
      }
      card.append(title, quotaView(quota[email]), label);
      cards.append(card);
    }
  }

  function message(value) {
    feedback.hidden = !value;
    feedback.textContent = value;
  }

  async function send(selected) {
    if (busy || (selected && !current)) return;
    const body = selected ? JSON.stringify({target: selected, expected_current: current}) : null;
    const headers = body ? {'Content-Type': 'application/json'} : {};
    busy = refresh.disabled = true;
    cards.setAttribute('aria-busy', 'true');
    for (const button of cards.querySelectorAll('button')) button.disabled = true;
    message(selected ? '正在切换…' : '正在读取…');
    try {
      const response = await fetch('/api/codex-accounts' + (body ? '/switch' : ''), {
        method: body ? 'POST' : 'GET', body, headers, cache: 'no-store', redirect: 'error'
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '读取失败，请检查鸟巢连接');
      show(data);
      message(selected ? '已切换。重新启动 Codex 后使用该账号。' : '');
    } catch (error) {
      show(null);
      message((error.message || '请求失败') + (selected ? '。请刷新确认后再操作。' : ''));
    } finally {
      busy = refresh.disabled = false;
      cards.removeAttribute('aria-busy');
    }
  }

  const refreshView = () => { void send(null); };
  refresh.addEventListener('click', refreshView);
  document.querySelector('[data-view-target="codexAccountsView"]').addEventListener('click', refreshView);
  window.addEventListener('DOMContentLoaded', () => {
    if ($('codexAccountsView').classList.contains('active')) refreshView();
  });
  dialog.addEventListener('close', () => {
    const selected = pending;
    pending = null;
    if (selected && dialog.returnValue === 'switch') void send(selected);
  });
})();
