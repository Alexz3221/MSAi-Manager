(function () {
  const statusEl = document.getElementById('slack-status');
  const inputEl = document.getElementById('slack-webhook');
  const companyPicker = document.getElementById('settings-company-picker');
  const companySelect = document.getElementById('settings-company');
  let isInternal = false;
  let changeListenerAttached = false;

  function currentCompanyId() {
    return isInternal ? companySelect.value : null;
  }

  async function loadStatus() {
    const params = isInternal && companySelect.value
      ? `?company_id=${encodeURIComponent(companySelect.value)}`
      : '';
    const res = await fetch(`/api/notifications/slack${params}`);
    if (!res.ok) {
      statusEl.textContent = (await res.json()).error || 'Unable to load status.';
      return;
    }
    const data = await res.json();
    statusEl.textContent = data.configured
      ? `Connected at (${data.webhook_preview})`
      : 'No webhook configured yet.';
    inputEl.value = '';
    inputEl.placeholder = data.configured
      ? 'Update your Slack webhook URL'
      : 'https://hooks.slack.com/services/...';
  }

  async function init() {
    const me = await (await fetch('/api/me')).json();
    isInternal = me.role === 'internal';
    if (isInternal) {
      companyPicker.hidden = false;
      const companies = (await (await fetch('/api/companies')).json()).companies;
      companySelect.innerHTML = companies
        .map(c => `<option value="${c.id}">${c.name}</option>`)
        .join('');
      if (!changeListenerAttached) {
        companySelect.addEventListener('change', loadStatus);
        changeListenerAttached = true;
      }
    }
    await loadStatus();
  }

  document.getElementById('slack-save').addEventListener('click', async () => {
    const body = { webhook_url: inputEl.value.trim() };
    if (isInternal) body.company_id = currentCompanyId();
    const res = await fetch('/api/notifications/slack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    statusEl.textContent = res.ok
      ? (data.configured ? 'Saved.' : 'Removed.')
      : (data.error || 'Something went wrong.');
    if (res.ok) await loadStatus();
  });

  document.getElementById('slack-remove').addEventListener('click', async () => {
    const body = { webhook_url: '' };
    if (isInternal) body.company_id = currentCompanyId();
    await fetch('/api/notifications/slack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await loadStatus();
  });

  window.initSlackSettings = init;
})();