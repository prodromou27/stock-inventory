/* Structured controls only: all user labels are assigned through text/value APIs. */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('template-style-form');
  if (!form) return;
  const frame = document.getElementById('template-preview-frame');
  const state = document.getElementById('template-preview-empty');
  let timer, controller;

  function arrange(containerId, definitions, orderField, columns) {
    const container = document.getElementById(containerId);
    const field = form.elements[orderField];
    const order = field.value.split(',').map(x => x.trim()).filter(Boolean);
    const known = new Map(definitions);
    const keys = [...new Set([...order.filter(k => known.has(k)), ...known.keys()])];
    const labels = new Map(form.elements.column_labels.value.split('\n').map(line => {
      const split = line.indexOf(':');
      return [line.slice(0, split).trim(), line.slice(split + 1).trim()];
    }));
    const rows = document.createElement('ol');
    rows.className = 'layout-arranger';
    let dragged;
    const sync = () => {
      field.value = [...rows.children].map(row => row.dataset.key).join(',');
      if (columns) {
        form.elements.column_labels.value = [...rows.children].map(row =>
          `${row.dataset.key}:${row.querySelector('input[type="text"]').value}`).join('\n');
      }
      form.dispatchEvent(new Event('input', {bubbles: true}));
    };
    keys.forEach(key => {
      const row = document.createElement('li');
      row.dataset.key = key;
      row.draggable = true;
      const title = document.createElement('span');
      title.textContent = known.get(key);
      row.append(title);
      if (columns) {
        const input = document.createElement('input');
        input.type = 'text';
        input.maxLength = 120;
        input.value = labels.get(key) || known.get(key);
        input.setAttribute('aria-label', `${known.get(key)} column label`);
        input.addEventListener('input', sync);
        row.append(input);
      }
      [['Up', -1], ['Down', 1]].forEach(([text, direction]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn--sm';
        button.textContent = text;
        button.setAttribute('aria-label', `Move ${known.get(key)} ${text.toLowerCase()}`);
        button.addEventListener('click', () => {
          if (direction < 0 && row.previousElementSibling) rows.insertBefore(row, row.previousElementSibling);
          if (direction > 0 && row.nextElementSibling) rows.insertBefore(row.nextElementSibling, row);
          sync();
          button.focus();
        });
        row.append(button);
      });
      row.addEventListener('dragstart', event => {
        dragged = row;
        event.dataTransfer.setData('text/plain', key);
      });
      row.addEventListener('dragover', event => event.preventDefault());
      row.addEventListener('drop', event => {
        event.preventDefault();
        if (dragged && dragged !== row && dragged.parentElement === rows) {
          rows.insertBefore(dragged, row);
          sync();
        }
      });
      rows.append(row);
    });
    container.append(rows);
    field.closest('p, .form-field')?.setAttribute('hidden', '');
    if (columns) form.elements.column_labels.closest('p, .form-field')?.setAttribute('hidden', '');
    sync();
  }

  async function refreshPreview() {
    if (!form.reportValidity()) return;
    controller?.abort();
    controller = new AbortController();
    state.textContent = 'Updating preview…';
    state.hidden = false;
    try {
      const response = await fetch(`${form.dataset.previewUrl}?format=html`, {
        method: 'POST', body: new FormData(form), signal: controller.signal,
      });
      const html = await response.text();
      if (!response.ok) throw new Error(html || 'Preview failed. Please check your settings.');
      frame.srcdoc = html;
      frame.hidden = false;
      state.hidden = true;
    } catch (error) {
      if (error.name !== 'AbortError') state.textContent = error.message;
    }
  }
  form.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(refreshPreview, 600); });
  form.addEventListener('change', () => { clearTimeout(timer); timer = setTimeout(refreshPreview, 200); });
  document.getElementById('template-preview-refresh').addEventListener('click', refreshPreview);
  document.getElementById('template-preview-pdf').addEventListener('click', () => {
    if (!form.reportValidity()) return;
    const preview = document.createElement('form');
    preview.method = 'post'; preview.action = form.dataset.previewUrl; preview.target = '_blank';
    // Preserve file uploads and CSRF without changing the editor's save action.
    const oldAction = form.action, oldTarget = form.target;
    form.action = preview.action; form.target = '_blank';
    HTMLFormElement.prototype.submit.call(form);
    form.action = oldAction; form.target = oldTarget;
  });
  arrange('section-layout', JSON.parse(document.getElementById('editor-sections').textContent), 'section_order', false);
  arrange('column-layout', JSON.parse(document.getElementById('editor-columns').textContent), 'column_order', true);
  refreshPreview();
});
