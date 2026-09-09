/* GrapesJS is a layout editor only. Django compiles validated fields and markup. */
document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('visual-designer');
  if (!root) return;
  const config = JSON.parse(document.getElementById('designer-config').textContent);
  const status = document.getElementById('designer-status');
  const reviewed = document.getElementById('designer-reviewed');
  let logoUrl = config.logo || '';
  const usage = document.getElementById('designer-usage');
  function showUsage(state) {
    usage.textContent = state === 'published'
      ? `Version ${version} is in use for NEW ${config.kind} documents. Existing PDFs stay unchanged.`
      : `This design is ${state === 'default' ? 'not saved' : 'a draft — not in use'}. Operations currently use the packaged default form.`;
  }
  let version = config.version, dirty = false, busy = false, textView, revision = 0;
  showUsage(config.status);
  const fields = {...config.fields, ...Object.fromEntries(Object.entries(config.lineFields).map(([k,v]) => ['line.' + k, 'Asset: ' + v]))};
  const fieldOptions = Object.entries(fields).map(([value, name]) => ({value, name}));
  const fieldBlock = key => ({type:'stock-field', attributes:{'data-stock-field':key}});
  const plugin = editor => {
    editor.DomComponents.addType('stock-field', {
      isComponent: el => el.hasAttribute?.('data-stock-field'),
      model: {defaults:{tagName:'span', droppable:false, editable:false,
        traits:[{type:'select', name:'data-stock-field', label:'Inventory field', options:fieldOptions}]},
      init() { this.on('change:attributes', this.refreshLabel); this.refreshLabel(); },
      refreshLabel() { this.components('[' + (fields[this.getAttributes()['data-stock-field']] || 'Choose field') + ']'); }},
    });
    editor.DomComponents.addType('stock-logo', {
      isComponent: el => el.tagName === 'IMG' && el.getAttribute('src') === 'stock-logo',
      model:{defaults:{tagName:'img', void:true, droppable:false, editable:false, traits:[],
        attributes:{src:'stock-logo',alt:'Company logo'}, style:{width:'180px',height:'60px'}}},
      view:{onRender() {
        this.el.src = logoUrl || 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="180" height="60"><rect width="180" height="60" fill="#eef4f4"/><text x="20" y="35" font-size="14" fill="#526565">Choose a logo</text></svg>');
        this.el.alt = 'Company logo (shown in PDF preview)';
      }},
    });
  };
  const properties = [
    {name:'Size & spacing',open:true,buildProps:['width','height','margin','padding']},
    {name:'Text',open:true,buildProps:['font-family','font-size','font-weight','color','text-align','line-height','text-decoration']},
    {name:'Borders & background',open:true,buildProps:['background-color','border-width','border-style','border-color']},
  ];
  const editor = grapesjs.init({container:'#grapes-canvas',height:'100%',width:'auto',
    storageManager:false,telemetry:false,cssIcons:'',panels:{defaults:[]},plugins:[plugin],
    blockManager:{appendTo:'#designer-blocks'},traitManager:{appendTo:'#designer-traits'},
    layerManager:{appendTo:'#designer-layers'},styleManager:{appendTo:'#designer-styles',sectors:properties},
    selectorManager:{componentFirst:true},devicePreviewMode:true,
    deviceManager:{devices:[{name:'A4 printable area',width:'643px',widthMedia:''}]},
    canvasCss:'body{font-family:Arial,sans-serif;font-size:10pt}table{width:100%;border-collapse:collapse}td,th{border:1px solid #555;padding:5px}',
    canvas:{styles:[],scripts:[]},
  });
  const pageSize = document.getElementById('designer-page-size');
  const orientation = document.getElementById('designer-orientation');
  const margin = document.getElementById('designer-margin');
  pageSize.value = config.design?.page?.size || 'A4';
  orientation.value = config.design?.page?.orientation || 'portrait';
  margin.value = config.design?.page?.margin ?? 20;
  const pageSettings = () => ({size:pageSize.value,orientation:orientation.value,margin:Number(margin.value)});
  const fitCanvas = () => {
    const dimensions = pageSize.value === 'Letter' ? [215.9,279.4] : [210,297];
    const width = (dimensions[orientation.value === 'landscape' ? 1 : 0] - 2 * Number(margin.value)) * 96 / 25.4;
    editor.Devices.getSelected().set('width',`${width}px`);
    editor.Canvas.setZoom(Math.max(20,Math.min(100, Math.floor((document.getElementById('grapes-canvas').clientWidth - 32) / width * 100))));
  };
  editor.on('load', fitCanvas);
  editor.on('rte:enable', view => {textView = view;});
  window.addEventListener('resize', fitCanvas);
  [pageSize,orientation,margin].forEach(input => input.addEventListener('change', () => {
    if (!margin.checkValidity()) {status.textContent = 'Margins must be between 5 and 40 mm.';return;}
    revision += 1;dirty = true;reviewed.checked = false;fitCanvas();status.textContent = 'Page setup changed — preview before saving';
  }));
  document.getElementById('designer-fullscreen').onclick = event => {
    root.classList.toggle('designer-expanded');
    event.target.textContent = root.classList.contains('designer-expanded') ? 'Exit expanded workspace' : 'Expand workspace';
    fitCanvas();
  };
  function assetTable() {
    return {tagName:'table',components:[
      {tagName:'thead',components:[{tagName:'tr',components:['brand','serial','description','quantity'].map(k => ({tagName:'th',type:'text',content:config.lineFields[k]}))}]},
      {tagName:'tbody',components:[{tagName:'tr',attributes:{'data-stock-row':'true'},components:['brand','serial','description','quantity'].map(k => ({tagName:'td',components:[fieldBlock('line.' + k)]}))}]},
    ]};
  }
  const blocks = {
    text:{label:'Text',content:{type:'text',tagName:'p',content:'Double-click to edit text'}},
    heading:{label:'Heading',content:{type:'text',tagName:'h2',content:'Your heading'}},
    section:{label:'Section / container',content:{tagName:'div',style:{padding:'15px','min-height':'50px'},components:[{type:'text',tagName:'p',content:'Add your content here'}]}},
    logo:{label:'Company logo',content:{type:'stock-logo'}},
    items:{label:'Repeating asset table',content:assetTable()},
    signature:{label:'Signature line',content:{type:'text',tagName:'p',content:'Signature: ........................................',style:{'margin-top':'30px'}}},
    divider:{label:'Divider',content:'<hr>'},
    pagebreak:{label:'Page break',content:{tagName:'div',style:{'break-before':'page'},components:[{type:'text',tagName:'p',content:'Next page'}]}},
  };
  Object.entries(blocks).forEach(([key,block]) => editor.BlockManager.add(key,block));
  function starter() {
    editor.setStyle('th{background-color:#d99694}td,th{border:1px solid #555;padding:5px}table{width:100%;border-collapse:collapse}');
    const title = {delivery:'PRODUCT DELIVERY ACCEPTANCE AND SIGN OFF',assignment:'EQUIPMENT ASSIGNMENT AND ACCEPTANCE',disposal:'EQUIPMENT DISPOSAL CERTIFICATE'}[config.kind];
    const recipient = {delivery:'final_customer',assignment:'employee_name',disposal:'witness_name'}[config.kind];
    editor.setComponents([{type:'stock-logo'},
      {tagName:'p',style:{'text-align':'right',color:'#ff0000'},components:[fieldBlock('document_number')]},
      {tagName:'p',style:{'text-align':'right'},components:[fieldBlock('document_date_display')]},
      {type:'text',tagName:'h2',content:title,style:{'text-align':'center','margin-top':'40px','margin-bottom':'35px'}},
      {tagName:'p',components:[{type:'text',tagName:'span',content:'Reference: '},fieldBlock('project_reference')]},
      assetTable(),{tagName:'p',style:{'margin-top':'30px'},components:[fieldBlock(recipient)]},
      ...['Signature','Name','Position','Date'].map(label => ({tagName:'p',type:'text',content:label + ': ........................................',style:{'margin-top':'30px'}})),
    ]);
  }
  if (config.design) { editor.setComponents(config.design.html); editor.setStyle(config.design.css); }
  else starter();
  editor.on('update', () => {revision += 1;dirty = true; reviewed.checked = false; status.textContent = 'Unsaved changes';});
  editor.on('load', () => {dirty = false; status.textContent = config.design ? 'Saved design loaded' : 'Starter loaded — your existing template is unchanged until you save';});
  const select = document.getElementById('designer-field');
  Object.entries(config.fields).forEach(([value,name]) => select.add(new Option(name,value)));
  document.getElementById('designer-insert').onclick = () => {
    const selected = editor.getSelected();
    (selected && selected.get('droppable') !== false ? selected : editor.getWrapper()).append(fieldBlock(select.value));
  };
  document.getElementById('designer-blank').onclick = () => {if(confirm('Clear the canvas? Nothing is saved until you click Save.')) {editor.setComponents([]);editor.setStyle('');}};
  document.getElementById('designer-starter').onclick = () => {if(confirm('Replace the canvas with the sign-off starter?')) starter();};
  document.getElementById('designer-undo').onclick = () => editor.UndoManager.undo();
  document.getElementById('designer-redo').onclick = () => editor.UndoManager.redo();
  document.getElementById('designer-remove').onclick = () => editor.getSelected()?.remove();
  const textPanel = document.getElementById('designer-text-panel');
  const textInput = document.getElementById('designer-text');
  editor.on('component:selected', component => {
    textPanel.hidden = component.get('type') !== 'text' || component.find('[data-stock-field]').length > 0;
    if (!textPanel.hidden) textInput.value = component.getEl()?.textContent || '';
  });
  editor.on('component:deselected', () => {textPanel.hidden = true;});
  document.getElementById('designer-apply-text').onclick = () => {
    const component = editor.getSelected();
    if (!component || textPanel.hidden) return;
    const encoded = document.createElement('div');encoded.textContent = textInput.value;
    component.components(encoded.innerHTML);
    status.textContent = 'Text updated — unsaved changes';
  };
  const columnSelect = document.getElementById('designer-column');
  Object.entries(config.lineFields).forEach(([key,label]) => columnSelect.add(new Option(label,key)));
  document.getElementById('designer-add-column').onclick = () => {
    const selected = editor.getSelected();
    const table = selected?.get('tagName') === 'table' ? selected : selected?.closest('table');
    if(!table) {status.textContent = 'Select an asset table or one of its cells first.';return;}
    const key = columnSelect.value;
    table.find('thead tr').forEach(row => row.append({type:'text',tagName:'th',content:config.lineFields[key]}));
    table.find('tr[data-stock-row]').forEach(row => row.append({tagName:'td',components:[fieldBlock('line.' + key)]}));
  };
  document.getElementById('designer-remove-column').onclick = () => {
    const selected = editor.getSelected();
    const cell = ['td','th'].includes(selected?.get('tagName')) ? selected : selected?.closest('td,th');
    const table = cell?.closest('table');
    if(!table) {status.textContent = 'Select a table cell or header first.';return;}
    const index = cell.index();
    table.find('tr').forEach(row => row.components().at(index)?.remove());
  };
  document.getElementById('designer-logo').onchange = event => {
    const file = event.target.files[0];
    if (logoUrl.startsWith('blob:')) URL.revokeObjectURL(logoUrl);
    logoUrl = file ? URL.createObjectURL(file) : (config.logo || '');
    editor.getWrapper().findType('stock-logo').forEach(component => component.getView().render());
    revision += 1;dirty = true;reviewed.checked = false;status.textContent = 'Logo selected — preview before saving';
  };
  window.addEventListener('beforeunload', event => {if(dirty) {event.preventDefault();event.returnValue = '';}});
  async function send(action) {
    if (busy) return;
    if (!margin.reportValidity()) return;
    busy = true; status.textContent = action === 'save' ? 'Saving and validating PDF…' : 'Rendering preview…';
    // Commit the rich-text DOM before exporting; blur synchronization is async.
    try {if (textView?.el?.isConnected) await textView.syncContent();}
    catch {busy = false;status.textContent = 'Finish editing the text and try again.';return;}
    const sentRevision = revision;
    const data = new FormData();
    data.set('csrfmiddlewaretoken',document.querySelector('#designer-toolbar [name=csrfmiddlewaretoken]').value);
    data.set('design',JSON.stringify({html:editor.getHtml(),css:editor.getCss(),page:pageSettings()}));
    data.set('version',version);data.set('action',action);data.set('preview_confirmed',reviewed.checked);data.set('activate',action === 'save' ? 'true' : 'false');
    const logo = document.getElementById('designer-logo').files[0]; if(logo) data.set('logo',logo);
    const buttons = root.querySelectorAll('button'); buttons.forEach(b => b.disabled = true);
    try {
      const response = await fetch(root.dataset.url,{method:'POST',body:data});
      if(!response.ok) {const error = await response.json();throw new Error(error.error || 'Request failed');}
      if(action === 'save') {const result = await response.json();version = result.version;showUsage(result.status);dirty = revision !== sentRevision;status.textContent = 'Saved as version ' + version + (dirty ? ' — newer edits are unsaved' : '');}
      else {
        const frame = document.getElementById('designer-preview-frame');
        if(action === 'pdf') {const url = URL.createObjectURL(await response.blob());frame.removeAttribute('srcdoc');frame.src = url;setTimeout(() => URL.revokeObjectURL(url),60000);}
        else {frame.removeAttribute('src');frame.srcdoc = await response.text();}
        document.getElementById('designer-preview-dialog').showModal();status.textContent = 'Preview generated from sample transaction data';
      }
    } catch(error) {status.textContent = error.message;}
    finally {busy = false;buttons.forEach(b => b.disabled = false);}
  }
  ['save','preview','pdf'].forEach(action => document.getElementById('designer-' + action).onclick = () => send(action));
  document.getElementById('designer-close').onclick = () => document.getElementById('designer-preview-dialog').close();
});
