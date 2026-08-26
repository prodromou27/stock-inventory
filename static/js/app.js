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

  document.querySelectorAll("[data-alert-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest(".messages__item")?.remove());
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
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
