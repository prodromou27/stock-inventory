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

  // Type-to-filter for long <select> lists (Product/Location pickers) —
  // no AJAX, no new value format: the element stays a real <select> and
  // submits exactly as before, this only hides non-matching <option>s as
  // the paired text input is typed into. Opt in per-select via
  // data-filterable (added by templates/inventory/*_form.html next to any
  // Product/Location field), skipped entirely below a size threshold where
  // filtering wouldn't help.
  document.querySelectorAll("select[data-filterable]").forEach((select) => {
    if (select.options.length < 8) return;
    const filterInput = document.createElement("input");
    filterInput.type = "search";
    filterInput.className = "select-filter";
    filterInput.placeholder = `Filter ${select.options.length} options…`;
    filterInput.setAttribute("aria-label", `Filter ${select.name}`);
    select.insertAdjacentElement("beforebegin", filterInput);

    const options = Array.from(select.options);
    filterInput.addEventListener("input", () => {
      const query = filterInput.value.trim().toLowerCase();
      options.forEach((option) => {
        option.hidden = query.length > 0 && !option.text.toLowerCase().includes(query);
      });
    });
  });

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
