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
  frame.addEventListener('load', () => {
    if (form.elements.custom_html_enabled?.checked) return;
    const doc = frame.contentDocument;
    if (!doc) return;
    const style = doc.createElement('style');
    style.textContent = `@media screen{body{margin:${paperDimensions().marginPx}px!important;}}` +
      '[contenteditable=true]{outline:1px dashed #0f766e;cursor:text;min-height:1em}' +
      '[contenteditable=true]:focus{outline:2px solid #0f766e;background:#f0fdfa}';
    doc.head.append(style);
    layoutPaper();
    doc.querySelectorAll('[data-editor-field], [data-editor-column]').forEach(element => {
      const column = element.dataset.editorColumn;
      const target = column
        ? document.querySelector(`#column-layout li[data-key="${column}"] input[type="text"]`)
        : form.elements[element.dataset.editorField];
      if (!target) return;
      element.contentEditable = 'plaintext-only';
      element.setAttribute('role', 'textbox');
      element.setAttribute('aria-label', target.getAttribute('aria-label') || target.name.replaceAll('_', ' '));
      element.title = 'Click to edit. Click outside to apply.';
      // Only text is copied back. Never save DOM markup or Django template syntax.
      element.addEventListener('blur', () => {
        target.value = element.innerText.trim();
        target.dispatchEvent(new Event('input', {bubbles: true}));
      });
    });
  });
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(layoutPaper, 150); });

  function arrange(containerId, definitions, orderField, columns) {
    const container = document.getElementById(containerId);
    container.replaceChildren();
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
        const hidden = [...form.querySelectorAll('[name="hidden_columns"]')].find(input => input.value === key);
        if (hidden) {
          const visibility = document.createElement('input');
          visibility.type = 'checkbox';
          visibility.checked = !hidden.checked;
          visibility.setAttribute('aria-label', `Show ${known.get(key)} column`);
          visibility.addEventListener('change', () => {
            hidden.checked = !visibility.checked;
            hidden.dispatchEvent(new Event('change', {bubbles: true}));
          });
          hidden.addEventListener('change', () => { visibility.checked = !hidden.checked; });
          row.append(visibility);
        }
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
    if (!form.checkValidity()) {
      state.hidden = false;
      state.textContent = 'Complete or correct the highlighted settings, then refresh the preview.';
      return;
    }
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
      if (frame.contentDocument?.activeElement?.isContentEditable) {
        state.textContent = 'Finish editing the paper to refresh the preview.';
        return;
      }
      frame.srcdoc = html;
      paper.hidden = false;
      state.hidden = true;
    } catch (error) {
      if (error.name !== 'AbortError') state.textContent = error.message;
    }
  }
  form.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(refreshPreview, 600); });
  form.addEventListener('change', () => { clearTimeout(timer); timer = setTimeout(refreshPreview, 200); });
  document.getElementById('template-preview-refresh').addEventListener('click', () => {
    if (form.reportValidity()) refreshPreview();
  });
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
  const navigation = document.getElementById('template-designer-navigation');
  const blockContainer = document.getElementById('template-text-blocks');
  const blockField = form.elements.custom_blocks;
  if (blockContainer && blockField) {
    let blocks;
    try { blocks = JSON.parse(blockField.value || '[]') || []; } catch { blocks = []; }
    const syncBlocks = () => {
      blockField.value = JSON.stringify(blocks);
      form.dispatchEvent(new Event('input', {bubbles: true}));
    };
    const renderBlocks = () => {
      blockContainer.replaceChildren();
      blocks.forEach((block, index) => {
        const group = document.createElement('fieldset');
        group.className = 'card';
        const legend = document.createElement('legend');
        legend.textContent = `Text block ${index + 1}`;
        group.append(legend);
        const text = document.createElement('textarea');
        text.value = block.text; text.rows = 4; text.maxLength = 5000;
        text.setAttribute('aria-label', `Text block ${index + 1} wording`);
        text.addEventListener('input', () => { block.text = text.value; syncBlocks(); });
        group.append(text);
        [['before', JSON.parse(document.getElementById('editor-sections').textContent)],
          ['alignment', [['left', 'Left'], ['center', 'Center'], ['right', 'Right']]]].forEach(([key, choices]) => {
          const label = document.createElement('label');
          label.textContent = key === 'before' ? 'Place before section' : 'Alignment';
          const select = document.createElement('select');
          choices.forEach(([value, title]) => select.add(new Option(title, value)));
          select.value = block[key];
          select.addEventListener('change', () => { block[key] = select.value; syncBlocks(); });
          label.append(select); group.append(label);
        });
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'btn btn--sm'; remove.textContent = 'Remove block';
        remove.addEventListener('click', () => { blocks.splice(index, 1); renderBlocks(); syncBlocks(); });
        group.append(remove); blockContainer.append(group);
      });
      document.getElementById('template-add-text-block').disabled = blocks.length >= 12;
    };
    document.getElementById('template-add-text-block').addEventListener('click', () => {
      blocks.push({text: '', before: 'signatures', alignment: 'left'});
      renderBlocks(); syncBlocks();
      blockContainer.lastElementChild.querySelector('textarea').focus();
    });
    renderBlocks();
  }
  const cards = [...form.querySelectorAll(':scope > .card')];
  ['Branding', 'Wording', 'Page & content', 'Arrange & columns', 'Developer source'].forEach((label, index) => {
    if (!cards[index] || !navigation) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn--sm';
    button.textContent = label;
    button.addEventListener('click', () => {
      if (cards[index].tagName === 'DETAILS') cards[index].open = true;
      cards[index].scrollIntoView({block: 'start', behavior: 'smooth'});
    });
    navigation.append(button);
  });
  const saveActions = form.querySelector('.template-save-actions');
  if (saveActions && navigation) navigation.append(saveActions);
  const mode = document.getElementById('template-designer-mode');
  const updateMode = () => {
    if (!mode) return;
    mode.replaceChildren(document.createTextNode(form.elements.custom_html_enabled.checked
      ? 'Developer source is active. Visual arrangement controls cannot change this custom source. '
      : 'Visual designer active. Click outlined text on the paper to edit it. '));
    if (form.elements.custom_html_enabled.checked) {
      const switchButton = document.createElement('button');
      switchButton.type = 'button';
      switchButton.className = 'btn btn--sm';
      switchButton.textContent = 'Use visual designer';
      switchButton.addEventListener('click', () => {
        if (!window.confirm('Use the visual layout instead? Your current source is preserved until you save, and previous saved versions remain available.')) return;
        form.elements.custom_html_enabled.checked = false;
        form.elements.custom_html_enabled.dispatchEvent(new Event('change', {bubbles: true}));
      });
      mode.append(switchButton);
    }
  };
  form.elements.custom_html_enabled.addEventListener('change', updateMode);
  updateMode();
  document.getElementById('template-acceptance-preset')?.addEventListener('click', () => {
    if (!window.confirm('Apply the sign-off layout to this editor? Save afterward to keep it. Existing versions and generated documents remain unchanged.')) return;
    const preset = JSON.parse(document.getElementById('signoff-preset').textContent);
    const values = {...preset.fields, ...preset.layout_config};
    Object.entries(values).forEach(([key, value]) => {
      const field = form.elements[key];
      if (!field || key === 'hidden_columns') return;
      if (key === 'column_labels') field.value = Object.entries(value).map(([k, v]) => `${k}:${v}`).join('\n');
      else if (field.type === 'checkbox') field.checked = value;
      else field.value = Array.isArray(value) ? value.join(',') : value;
    });
    form.elements.custom_html_enabled.checked = false;
    form.querySelectorAll('[name="hidden_columns"]').forEach(input => {
      input.checked = preset.layout_config.hidden_columns.includes(input.value);
    });
    arrange('section-layout', JSON.parse(document.getElementById('editor-sections').textContent), 'section_order', false);
    arrange('column-layout', JSON.parse(document.getElementById('editor-columns').textContent), 'column_order', true);
    updateMode(); refreshPreview();
  });
  refreshPreview();
});
