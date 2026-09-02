(() => {
  const input = document.querySelector(".topbar__search input");
  if (!input) return;
  const form = input.closest("form");
  const suggestUrl = form.dataset.suggestUrl;
  const resultsUrl = form.getAttribute("action");
  if (!suggestUrl || !resultsUrl) return;

  const panel = document.createElement("div");
  panel.className = "search-suggest";
  panel.hidden = true;
  form.appendChild(panel);

  const GROUPS = [
    ["products", "Products"],
    ["assets", "Assets"],
    ["transactions", "Transactions"],
  ];

  let debounceTimer = null;
  let latestQuery = "";
  let activeIndex = -1;

  // Labels come from user-entered data (product model/brand, asset serial,
  // transaction reference) rendered via innerHTML below — escape it, same
  // as Django's autoescape would for the equivalent server-rendered markup.
  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function close() {
    panel.hidden = true;
    panel.innerHTML = "";
    activeIndex = -1;
  }

  function links() {
    return Array.from(panel.querySelectorAll("a"));
  }

  function setActive(index) {
    const items = links();
    if (!items.length) return;
    activeIndex = (index + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle("is-active", i === activeIndex));
    items[activeIndex].scrollIntoView({ block: "nearest" });
  }

  function render(data) {
    // A slower earlier request can resolve after a faster later one —
    // only render the response that still matches what's currently typed.
    if (data.query !== input.value.trim()) return;

    const hasResults = GROUPS.some(([key]) => data[key].length > 0);
    if (!data.query) {
      close();
      return;
    }
    if (!hasResults) {
      panel.innerHTML = '<p class="search-suggest__empty">No matches.</p>';
      panel.hidden = false;
      activeIndex = -1;
      return;
    }

    const sections = GROUPS.filter(([key]) => data[key].length > 0)
      .map(([key, label]) => {
        const items = data[key]
          .map((row) => `<a href="${row.url}">${escapeHtml(row.label)}</a>`)
          .join("");
        return `<div class="search-suggest__group"><h3>${label}</h3>${items}</div>`;
      })
      .join("");
    const seeAll = `<a class="search-suggest__all" href="${resultsUrl}?q=${encodeURIComponent(data.query)}">See all results for "${escapeHtml(data.query)}"</a>`;
    panel.innerHTML = sections + seeAll;
    panel.hidden = false;
    activeIndex = -1;
  }

  input.addEventListener("input", () => {
    const query = input.value.trim();
    latestQuery = query;
    window.clearTimeout(debounceTimer);
    if (!query) {
      close();
      return;
    }
    debounceTimer = window.setTimeout(() => {
      fetch(`${suggestUrl}?q=${encodeURIComponent(query)}`, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => data && data.query === latestQuery && render(data))
        .catch(() => {});
    }, 200);
  });

  input.addEventListener("keydown", (event) => {
    if (panel.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(activeIndex - 1);
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      links()[activeIndex].click();
    } else if (event.key === "Escape") {
      close();
    }
  });

  document.addEventListener("click", (event) => {
    if (!form.contains(event.target)) close();
  });
})();
