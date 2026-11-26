(() => {
  const view = document.getElementById('devicesView');
  const button = document.getElementById('devPowerButton');
  const refresh = document.getElementById('devPowerRefresh');
  const status = document.getElementById('devPowerStatus');
  const dialog = document.getElementById('devPowerConfirm');
  let busy = false;

  async function check(submit = false) {
    if (busy || !view.classList.contains('active') || dialog.open) return;
    busy = button.disabled = refresh.disabled = true;
    if (submit) status.textContent = '正在提交关机请求…';
    try {
      const response = await fetch('/api/codex-accounts/power', {
        method: submit ? 'POST' : 'GET',
        headers: submit ? {'Content-Type': 'application/json'} : {},
        body: submit ? '{}' : null, cache: 'no-store', redirect: 'error'
      });
      const data = await response.json();
      status.textContent = data.message || data.detail || '无法确认开发机状态';
      button.disabled = !['ready', 'power_still_online'].includes(data.state);
    } catch {
      status.textContent = submit ? '关机结果未知，请查看设备状态。' : '无法连接 NASBridge，开发机状态未知';
    } finally {
      busy = refresh.disabled = false;
    }
  }

  button.addEventListener('click', () => {
    dialog.returnValue = '';
    dialog.showModal();
  });
  dialog.addEventListener('close', () => {
    if (dialog.returnValue === 'poweroff') void check(true);
  });
  const update = () => { void check(); };
  refresh.addEventListener('click', update);
  document.querySelector('[data-view-target="devicesView"]').addEventListener('click', update);
  window.addEventListener('DOMContentLoaded', update);
  window.setInterval(update, 15000);
})();
