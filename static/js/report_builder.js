(function () {
  "use strict";

  const table = document.querySelector("[data-report-filters]");
  if (!table) return;

  const rows = table.querySelector("[data-filter-rows]");
  const total = document.querySelector("[name$='-TOTAL_FORMS']");
  const emptyTemplate = document.querySelector("[data-filter-empty-form]");
  const kindsNode = document.getElementById("report-field-kinds");
  const kinds = kindsNode ? JSON.parse(kindsNode.textContent) : {};

  function updateValueType(row) {
    const field = row.querySelector("select[name$='-field_key']");
    const operator = row.querySelector("select[name$='-op']");
    const value = row.querySelector("input[name$='-value']");
    if (!field || !value) return;
    const kind = kinds[field.value] || "text";
    value.type = operator && operator.value === "in" ? "text" : kind === "date" ? "date" : kind === "number" ? "number" : "text";
    value.step = kind === "number" ? "any" : "";
    value.placeholder = operator && operator.value === "in" ? "Comma-separated values" : "";
  }

  function wire(row) {
    row.querySelectorAll("select[name$='-field_key'], select[name$='-op']").forEach((control) => {
      control.addEventListener("change", () => updateValueType(row));
    });
    const remove = row.querySelector("[data-remove-filter]");
    if (remove) remove.addEventListener("click", () => row.remove());
    updateValueType(row);
  }

  rows.querySelectorAll("tr").forEach(wire);
  document.querySelector("[data-add-filter]").addEventListener("click", () => {
    const index = Number(total.value);
    if (index >= 20) return;
    const wrapper = document.createElement("tbody");
    wrapper.innerHTML = emptyTemplate.innerHTML.replaceAll("__prefix__", String(index));
    const row = wrapper.firstElementChild;
    rows.appendChild(row);
    total.value = String(index + 1);
    wire(row);
  });
})();
