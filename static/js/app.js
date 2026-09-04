(() => {
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
})();
