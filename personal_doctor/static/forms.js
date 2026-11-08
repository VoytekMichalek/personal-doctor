// Keep form actions usable in embedded browsers that restrict POST navigations.
// Ordinary HTML submissions remain available when JavaScript is disabled.
document.querySelectorAll('form[method="post"]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.pending) return;
    form.dataset.pending = 'true';
    const button = event.submitter;
    if (button) button.disabled = true;
    form.querySelector('[data-form-error]')?.remove();
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new URLSearchParams(new FormData(form)), credentials: 'same-origin',
      });
      if (response.redirected && response.ok) {
        location.assign(response.url);
        return;
      }
      const text = await response.text();
      const parsed = new DOMParser().parseFromString(text, 'text/html');
      const message = parsed.querySelector('[role="alert"]')?.textContent;
      const error = document.createElement('div');
      error.className = 'notice error';
      error.dataset.formError = 'true';
      error.setAttribute('role', 'alert');
      error.textContent = message || (response.headers.get('content-type')?.includes('text/html')
        ? 'The request could not be completed. Reload the page and try again.' : text);
      form.prepend(error);
    } catch {
      const error = document.createElement('div');
      error.className = 'notice error';
      error.dataset.formError = 'true';
      error.setAttribute('role', 'alert');
      error.textContent = 'Connection interrupted. Check your connection and try again.';
      form.prepend(error);
    } finally {
      delete form.dataset.pending;
      if (button) button.disabled = false;
    }
  });
});
