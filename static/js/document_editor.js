/* Structured controls only: all user labels are assigned through text/value APIs. */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('template-style-form');
  if (!form) return;
  const frame = document.getElementById('template-preview-frame');
  const state = document.getElementById('template-preview-empty');
  const paper = document.getElementById('template-paper');
  const paperViewport = document.getElementById('template-paper-viewport');
  const pageBreaks = document.getElementById('template-page-breaks');
  let timer, controller, resizeTimer, lastContentHeightPx;

  // True CSS-pixel page dimensions at 96dpi, so the iframe's own text
  // reflow matches how WeasyPrint would actually wrap it — the whole
  // "paper" is then scaled down visually to fit the sidebar, never by
  // shrinking the iframe's real width (see layoutPaper()).
  const PAGE_SIZES_MM = {A4: [210, 297], Letter: [215.9, 279.4]};
  const PX_PER_MM = 96 / 25.4;
  const MARGIN_CM = {compact: 1.5, normal: 2, spacious: 2.5};

  function paperDimensions() {
    const sizeKey = form.elements.page_size ? form.elements.page_size.value : 'A4';
    const [wMm, hMm] = PAGE_SIZES_MM[sizeKey] || PAGE_SIZES_MM.A4;
    const landscape = form.elements.orientation && form.elements.orientation.value === 'landscape';
    const widthMm = landscape ? hMm : wMm;
    const heightMm = landscape ? wMm : hMm;
    const marginKey = form.elements.page_margin ? form.elements.page_margin.value : 'normal';
    const marginCm = MARGIN_CM[marginKey] || MARGIN_CM.normal;
    return {
      widthPx: Math.round(widthMm * PX_PER_MM),
      heightPx: Math.round(heightMm * PX_PER_MM),
      marginPx: Math.round(marginCm * 10 * PX_PER_MM),
    };
  }

  function layoutPaper() {
    if (!paper || paper.hidden) return;
    const {widthPx, heightPx, marginPx} = paperDimensions();
    let contentHeightPx = heightPx;
    try {
      contentHeightPx = Math.max(heightPx, frame.contentDocument.body.scrollHeight);
    } catch (error) {
      contentHeightPx = lastContentHeightPx || heightPx;
    }
    lastContentHeightPx = contentHeightPx;

    paper.style.width = `${widthPx}px`;
    paper.style.height = `${contentHeightPx}px`;
    paper.querySelector('.template-paper__margin-guide').style.inset = `${marginPx}px`;

    pageBreaks.innerHTML = '';
    const pageCount = Math.max(1, Math.ceil(contentHeightPx / heightPx));
    for (let page = 1; page < pageCount; page += 1) {
      const y = page * heightPx;
      const line = document.createElement('div');
      line.className = 'template-paper__page-break-line';
      line.style.top = `${y}px`;
      const label = document.createElement('span');
      label.className = 'template-paper__page-break-label';
      label.style.top = `${y}px`;
      label.textContent = `Page ${page + 1}`;
      pageBreaks.append(line, label);
    }

    const scale = paperViewport.clientWidth > 0 ? Math.min(1, paperViewport.clientWidth / widthPx) : 1;
    paper.style.transform = `scale(${scale})`;
    paperViewport.style.height = `${contentHeightPx * scale}px`;
  }
  frame.addEventListener('load', layoutPaper);
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(layoutPaper, 150); });

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
      paper.hidden = false;
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
