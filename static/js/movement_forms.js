(() => {
  // Progressive enhancement only: every field starts visible (the server-
  // rendered default, and what stays true if this script never runs), and
  // this only ever hides the one that doesn't apply to the selected
  // product's tracking method — apps.inventory.forms.TrackingMethodSelect
  // puts data-tracking-method="unit"/"quantity" on each <option>;
  // templates/_form_field.html puts data-tracking-field="unit"/"quantity"
  // on the <p> for a field that only makes sense for one tracking method
  // (e.g. ReceiveStockForm's vendor_serial vs quantity). Server-side
  // validation in apps.inventory.forms is completely unaffected either way.
  function applyTrackingVisibility(select) {
    const form = select.closest("form");
    if (!form) return;
    const selected = select.options[select.selectedIndex];
    const trackingMethod = selected ? selected.dataset.trackingMethod : undefined;
    form.querySelectorAll("[data-tracking-field]").forEach((field) => {
      const show = !trackingMethod || field.dataset.trackingField === trackingMethod;
      field.style.display = show ? "" : "none";
    });
  }

  document.querySelectorAll("select").forEach((select) => {
    const hasTrackingOptions = Array.from(select.options).some(
      (option) => option.dataset.trackingMethod
    );
    if (!hasTrackingOptions) return;
    applyTrackingVisibility(select);
    select.addEventListener("change", () => applyTrackingVisibility(select));
  });

  // Search-as-you-type for long <select> lists (Product/Location pickers) —
  // Tom Select (static/js/vendor/, self-hosted, no jQuery/build step) wraps
  // the existing native <select> in place: no new value format, no AJAX, the
  // element still submits exactly as before, and it still fires a real
  // `change` event (applyTrackingVisibility above keeps working unmodified).
  // Opt in per-select via data-filterable (apps/inventory/forms.py's
  // _apply_scoped_location / TrackingMethodSelect), skipped below a size
  // threshold where a combobox wouldn't help, and skipped entirely if the
  // script failed to load — the plain <select> stays fully usable either way.
  if (window.TomSelect) {
    document.querySelectorAll("select[data-filterable]").forEach((select) => {
      if (select.options.length < 8) return;
      new TomSelect(select, {
        create: false,
        maxOptions: 300,
        maxItems: select.multiple ? null : 1,
      });
    });
  }

  // Same idea for templates/inventory/_asset_picker.html's checkbox table —
  // an <input data-table-filter-for="table-id"> hides non-matching <tr>s.
  document.querySelectorAll("[data-table-filter-for]").forEach((input) => {
    const table = document.getElementById(input.dataset.tableFilterFor);
    if (!table) return;
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      rows.forEach((row) => {
        row.hidden = query.length > 0 && !row.textContent.toLowerCase().includes(query);
      });
    });
  });
})();
