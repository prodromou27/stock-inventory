(() => {
  const escapeHtml = (value) => {
    const node = document.createElement("span");
    node.textContent = String(value);
    return node.innerHTML;
  };
  const body = document.body;
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const closeSidebar = () => {
    body.classList.remove("sidebar-open");
    toggle?.setAttribute("aria-expanded", "false");
  };

  toggle?.addEventListener("click", () => {
    const open = body.classList.toggle("sidebar-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  document.querySelector("[data-sidebar-close]")?.addEventListener("click", closeSidebar);
  window.addEventListener("keydown", (event) => event.key === "Escape" && closeSidebar());

  const searchInput = document.querySelector(".topbar__search input");
  window.addEventListener("keydown", (event) => {
    if (event.key !== "/" || !searchInput) return;
    const active = document.activeElement;
    const isTyping =
      active && (["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) || active.isContentEditable);
    if (isTyping) return;
    event.preventDefault();
    searchInput.focus();
    searchInput.select();
  });

  document.querySelectorAll("[data-alert-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".messages__item")?.remove());
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("form[data-review-submit]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.reviewConfirmed === "true") return;
      event.preventDefault();
      if (!form.reportValidity()) return;
      const data = new FormData(form);
      const selectedAssets = data.getAll("unit_asset_ids").filter(Boolean);
      const selectedQuantities = Array.from(form.querySelectorAll('[name^="quantity_"]'))
        .map((input) => Number(input.value || 0)).filter((value) => value > 0)
        .reduce((total, value) => total + value, 0);
      const details = Array.from(form.querySelectorAll("input, select, textarea"))
        .filter((field) => field.name && field.type !== "hidden" && field.type !== "checkbox" && field.value)
        .slice(0, 8)
        .map((field) => {
          const label = form.querySelector(`label[for="${field.id}"]`)?.textContent.trim() || field.name;
          const value = field.tagName === "SELECT" ? field.selectedOptions[0]?.textContent.trim() : field.value;
          return `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value || "")}</dd>`;
        }).join("");
      const dialog = document.createElement("dialog");
      dialog.className = "confirmation-dialog";
      const titleId = `confirmation-title-${Date.now()}`;
      dialog.setAttribute("aria-labelledby", titleId);
      dialog.innerHTML = `<form method="dialog"><h2 id="${titleId}">Review before completing</h2>
        <p><strong>${selectedAssets.length}</strong> individual asset(s) and <strong>${selectedQuantities}</strong> counted item(s) selected.</p>
        <dl class="review-summary">${details}</dl>
        <p class="messages__item messages__item--warning">${escapeHtml(form.dataset.reviewSubmit)}</p>
        <div class="form-actions"><button class="btn" value="cancel">Go back</button><button class="btn btn--primary" value="confirm">Confirm and complete</button></div></form>`;
      document.body.appendChild(dialog);
      dialog.addEventListener("close", () => {
        if (dialog.returnValue === "confirm") {
          form.dataset.reviewConfirmed = "true";
          form.requestSubmit(event.submitter);
        }
        dialog.remove();
      });
      dialog.showModal();
      dialog.querySelector('[value="cancel"]')?.focus();
    });
  });

  // Complete the server-rendered form semantics without changing Django's
  // widgets: help/errors are announced and invalid fields are identifiable.
  document.querySelectorAll("[data-form-field]").forEach((wrapper) => {
    const field = wrapper.querySelector("input:not([type=hidden]), select, textarea");
    if (!field?.id) return;
    const describedBy = [];
    if (wrapper.querySelector(`#${CSS.escape(field.id)}-help`)) describedBy.push(`${field.id}-help`);
    if (wrapper.querySelector(`#${CSS.escape(field.id)}-errors`)) {
      describedBy.push(`${field.id}-errors`);
      field.setAttribute("aria-invalid", "true");
    }
    if (describedBy.length) field.setAttribute("aria-describedby", describedBy.join(" "));
  });

  document.querySelectorAll(".sidebar__link.active").forEach((link) =>
    link.setAttribute("aria-current", "page")
  );
  document.querySelectorAll("table").forEach((table) => {
    table.querySelectorAll("thead th").forEach((heading) => heading.setAttribute("scope", "col"));
    if (!table.querySelector("caption") && !table.hasAttribute("aria-label")) {
      const caption = document.createElement("caption");
      caption.className = "sr-only";
      caption.textContent = document.querySelector("h1")?.textContent?.trim() || "Data table";
      table.prepend(caption);
    }
  });

  // Disclosure menus otherwise remain open after keyboard focus moves away.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll("details[open]").forEach((details) => details.removeAttribute("open"));
  });

  // Drag/drop file upload with a live image preview — the file input itself
  // is a full-size transparent overlay (see .dropzone in app.css) so there's
  // exactly one interactive element (click OR drag both land on it), no
  // separate "click zone -> input.click()" forwarding needed.
  document.querySelectorAll("[data-dropzone]").forEach((zone) => {
    const input = zone.querySelector('input[type="file"]');
    const preview = zone.querySelector("[data-dropzone-preview]");
    const prompt = zone.querySelector("[data-dropzone-prompt]");
    if (!input) return;

    function showPreview(file) {
      if (!file || !file.type.startsWith("image/")) return;
      const reader = new FileReader();
      reader.onload = () => {
        if (preview) {
          preview.src = reader.result;
          preview.hidden = false;
        }
        if (prompt) prompt.hidden = true;
      };
      reader.readAsDataURL(file);
    }

    input.addEventListener("change", () => showPreview(input.files[0]));

    ["dragenter", "dragover"].forEach((eventName) => {
      zone.addEventListener(eventName, (event) => {
        event.preventDefault();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      zone.addEventListener(eventName, () => zone.classList.remove("is-dragover"));
    });
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      const file = event.dataTransfer.files[0];
      if (!file) return;
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      showPreview(file);
    });
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || !form.checkValidity()) return;
      const submitter = event.submitter;
      if (!submitter || submitter.dataset.noLoading !== undefined) return;
      submitter.disabled = true;
      submitter.classList.add("is-loading");
      submitter.setAttribute("aria-busy", "true");
      submitter.dataset.originalLabel = submitter.textContent;
      submitter.textContent = submitter.dataset.loadingLabel || "Working…";
    });
  });

  // A plain download link (?format=csv, results.csv, ...) gives no visible
  // feedback at all while the export is being generated server-side — the
  // browser shows nothing until the file is actually ready, which on a
  // slow report can look indistinguishable from a hang. This doesn't
  // preventDefault() — the real navigation/download proceeds exactly as
  // before — it just swaps the link's text and marks it busy for a few
  // seconds so a click is visibly acknowledged. There's no reliable "the
  // file started downloading" event for an <a> (unlike a page navigation),
  // so it reverts on a fixed timeout rather than waiting for one.
  document.querySelectorAll("[data-loading-link]").forEach((link) => {
    link.addEventListener("click", () => {
      link.classList.add("is-loading");
      link.setAttribute("aria-busy", "true");
      const original = link.textContent;
      link.textContent = link.dataset.loadingLink || "Preparing download…";
      setTimeout(() => {
        link.textContent = original;
        link.classList.remove("is-loading");
        link.removeAttribute("aria-busy");
      }, 4000);
    });
  });

  if (document.querySelector("[data-job-refresh]")) {
    setTimeout(() => window.location.reload(), 3000);
  }
})();
