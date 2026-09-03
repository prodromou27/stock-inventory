(() => {
  if (!window.Tabulator) return;

  // Mirrors apps.core.templatetags.ui_extras.badge_class exactly (same
  // keyword lists, same substring heuristic) so a status/condition value
  // gets the identical badge color whether rendered server-side or by this
  // grid — see that function's docstring for why it's a heuristic, not a
  // per-choices-enum lookup.
  const BADGE_SUCCESS = ["active", "available", "in_stock", "in stock", "new", "good", "healthy", "completed"];
  const BADGE_INFO = ["reserved", "assigned", "in_transit", "in transit", "pending", "processing"];
  const BADGE_WARNING = ["damaged", "fair", "returned"];
  const BADGE_DANGER = ["lost", "disposed", "inactive", "error", "failed", "rejected", "cancelled"];

  function badgeClass(value) {
    const text = String(value || "").trim().toLowerCase();
    if (BADGE_SUCCESS.some((k) => text.includes(k))) return "badge--success";
    if (BADGE_INFO.some((k) => text.includes(k))) return "badge--info";
    if (BADGE_WARNING.some((k) => text.includes(k))) return "badge--warning";
    if (BADGE_DANGER.some((k) => text.includes(k))) return "badge--danger";
    return "badge--neutral";
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function badgeFormatter(cell) {
    const value = cell.getValue();
    const displayField = cell.getColumn().getDefinition().displayField;
    const displayValue = displayField ? cell.getData()[displayField] : value;
    if (!value) return "";
    return `<span class="badge ${badgeClass(value)}">${escapeHtml(displayValue)}</span>`;
  }

  function dateFormatter(cell) {
    const value = cell.getValue();
    return value ? escapeHtml(value) : "—";
  }

  function datetimeFormatter(cell) {
    const value = cell.getValue();
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return escapeHtml(value);
    return escapeHtml(parsed.toLocaleString());
  }

  function linkFormatter(cell) {
    const url = cell.getData().detail_url;
    const value = cell.getValue();
    if (!url) return escapeHtml(value);
    return `<a href="${url}">${escapeHtml(value)}</a>`;
  }

  // <details>/<summary> instead of a hand-rolled dropdown: natively
  // keyboard-operable (Tab reaches it, Enter/Space toggles it) and
  // announced correctly by screen readers with zero extra ARIA wiring.
  // Server sends an empty quick_actions list for a read-only user (or a
  // status with no eligible action), so the column is quietly empty for
  // them rather than showing actions that would 403 on click.
  function actionsFormatter(cell) {
    const actions = cell.getData().quick_actions || [];
    if (!actions.length) return "";
    const items = actions
      .map((action) => `<a href="${action.url}">${escapeHtml(action.label)}</a>`)
      .join("");
    return `<details class="grid-row-menu"><summary aria-label="Actions">⋯</summary><div>${items}</div></details>`;
  }

  function closeOpenRowMenus(exceptEl) {
    document.querySelectorAll(".grid-row-menu[open]").forEach((el) => {
      if (el !== exceptEl) el.removeAttribute("open");
    });
  }
  document.addEventListener("click", (event) => {
    const menu = event.target.closest(".grid-row-menu");
    closeOpenRowMenus(menu);
  });

  /**
   * Builds and wires an Excel-like Tabulator grid.
   *
   * options:
   *   containerSelector  - CSS selector for the table container element.
   *   dataUrl            - JSON grid-data endpoint (server-scoped/filtered).
   *   columns            - Tabulator column definitions.
   *   initialSort         - [{field, dir}] applied on first load.
   *   searchInputSelector - global-search <input> selector (optional).
   *   densityToggleSelector - density button-group container selector (optional).
   *   pageSizeSelector    - <select> for page size (optional; Tabulator's own
   *                         pagination controls also expose this).
   *   onSelectionChange(selectedRowData) - called whenever row selection changes.
   *   rowSelectable       - forwarded to Tabulator's selectableRows.
   *
   * The server contract (apps.inventory.views.UnitAssetGridDataView /
   * StockBalanceGridDataView) expects `page`, `size`, repeated
   * `sort=field:dir`, and per-column filter values under the SAME names as
   * each column's `field` — no client-side name translation needed, by
   * design (see the plan's "reused, not reinvented" note).
   */
  function initInventoryGrid(options) {
    const container = document.querySelector(options.containerSelector);
    if (!container) return null;

    let globalSearch = "";

    function buildParams(page, size) {
      const params = new URLSearchParams();
      params.set("page", page);
      params.set("size", size);
      if (globalSearch) params.set("q", globalSearch);

      table.getSorters().forEach((sorter) => {
        params.append("sort", `${sorter.field}:${sorter.dir}`);
      });
      table.getHeaderFilters().forEach((filter) => {
        if (filter.value === "" || filter.value == null) return;
        params.set(filter.field, filter.value);
      });
      Object.entries(options.extraFilters ? options.extraFilters() : {}).forEach(([key, value]) => {
        if (value === "" || value == null || value === false) return;
        params.set(key, value);
      });
      return params;
    }

    const table = new Tabulator(container, {
      ajaxURL: options.dataUrl,
      ajaxRequestFunc: (url, config, params) =>
        fetch(`${url}?${buildParams(params.page, params.size).toString()}`, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
        }).then((response) => {
          if (!response.ok) throw new Error(`Grid data request failed (${response.status})`);
          return response.json();
        }),
      paginationMode: "remote",
      sortMode: "remote",
      filterMode: "remote",
      pagination: true,
      paginationSize: options.pageSize || 50,
      paginationSizeSelector: [25, 50, 100, 200],
      paginationCounter: "rows",
      dataSendParams: { page: "page", size: "size" },
      layout: "fitDataStretch",
      height: "70vh",
      placeholder: "No results — try widening your filters.",
      columns: options.columns,
      initialSort: options.initialSort || [],
      selectableRows: options.rowSelectable !== false,
      selectableRowsRangeMode: "click",
      index: "id",
      columnDefaults: { headerFilterLiveFilter: false, tooltip: true },
    });

    table.on("tableBuildError", (error) => {
      showError(container, error);
    });
    table.on("dataLoadError", (error) => {
      showError(container, error);
    });
    table.on("dataLoading", () => container.classList.add("is-loading"));
    table.on("dataLoaded", () => container.classList.remove("is-loading"));

    table.on("tableBuilt", () => {
      const fallback = document.querySelector(options.fallbackSelector);
      if (fallback) fallback.hidden = true;
      container.closest(".inventory-grid")?.classList.remove("is-hidden");
      // Tabulator's virtual-DOM row renderer sizes itself against the
      // container as of *this* moment, synchronously, during tableBuilt —
      // but the container was still display:none (via .is-hidden) a line
      // above, and un-hiding it doesn't retroactively trigger a resize. Left
      // alone, the table reports the correct row *data* (getData() is
      // right) while rendering zero row elements, permanently, until
      // something else forces a redraw. One explicit redraw(true) right
      // after un-hiding fixes it — confirmed via getData().length being
      // correct but .tabulator-row count being 0 until this call.
      table.redraw(true);
      makeSortableHeadersKeyboardOperable(container);

      // Column show/hide (the "Columns" panel) re-renders the header row —
      // re-scan whenever it changes so a newly-shown column's header is
      // keyboard-sortable too, without needing every call site to remember
      // to call this itself.
      const header = container.querySelector(".tabulator-header");
      if (header) {
        new MutationObserver(() => makeSortableHeadersKeyboardOperable(container)).observe(header, {
          childList: true,
          subtree: true,
        });
      }
    });

    if (options.rowSelectable !== false && options.onSelectionChange) {
      table.on("rowSelectionChanged", (data) => options.onSelectionChange(data));
    }

    if (options.searchInputSelector) {
      const searchInput = document.querySelector(options.searchInputSelector);
      if (searchInput) {
        let debounceTimer = null;
        searchInput.addEventListener("input", () => {
          window.clearTimeout(debounceTimer);
          debounceTimer = window.setTimeout(() => {
            globalSearch = searchInput.value.trim();
            table.setPage(1).then(() => table.replaceData());
          }, 250);
        });
      }
    }

    if (options.densityToggleSelector) {
      const densityGroup = document.querySelector(options.densityToggleSelector);
      densityGroup?.querySelectorAll("[data-density]").forEach((button) => {
        button.addEventListener("click", () => {
          const density = button.dataset.density;
          container.classList.remove("tabulator-density-compact", "tabulator-density-comfortable");
          container.classList.add(`tabulator-density-${density}`);
          densityGroup.querySelectorAll("[data-density]").forEach((b) => b.classList.toggle("is-active", b === button));
          try {
            window.localStorage.setItem(`${options.storageKey || "grid"}:density`, density);
          } catch (error) {
            /* private browsing / storage disabled — density just won't persist */
          }
        });
      });
      try {
        const savedDensity = window.localStorage.getItem(`${options.storageKey || "grid"}:density`);
        if (savedDensity) densityGroup.querySelector(`[data-density="${savedDensity}"]`)?.click();
      } catch (error) {
        /* ignore */
      }
    }

    if (options.editableFields && options.editUrlTemplate) {
      wireInlineEditing(table, options.editUrlTemplate, options.editableFields);
    }

    return table;
  }

  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  // Saves one field via apps.inventory.views.AssetGridFieldUpdateView — a
  // hard server-side allow-list (ASSET_INLINE_EDITABLE_FIELDS), so even a
  // tampered request for a field never configured as `editor:` here (e.g.
  // "status") is rejected there, not just hidden from this column list.
  function wireInlineEditing(table, urlTemplate, editableFields) {
    table.on("cellEdited", (cell) => {
      const field = cell.getField();
      if (!editableFields.includes(field)) return;
      const row = cell.getData();
      const previousValue = cell.getOldValue();
      const url = urlTemplate.replace("__ID__", row.id);

      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify({ field, value: cell.getValue() }),
      })
        .then((response) => response.json().then((body) => ({ ok: response.ok, body })))
        .then(({ ok, body }) => {
          if (!ok) throw new Error(body.error || "Save failed");
        })
        .catch((error) => {
          cell.setValue(previousValue, true);
          window.alert(`Couldn't save that change: ${error.message}`);
        });
    });
  }

  /**
   * Captures everything a "saved view" is: column order/width/visibility
   * (Tabulator's own getColumnLayout(), which already round-trips through
   * setColumnLayout()), sort, density, global search, and header filters.
   * `extra()` lets a page add its own bits (the "More filters" date-range
   * panel isn't part of Tabulator's state at all).
   */
  function captureGridState(table, container, searchInput, extra) {
    return {
      columns: table.getColumnLayout(),
      sorters: table.getSorters().map((s) => ({ field: s.field, dir: s.dir })),
      density: container.classList.contains("tabulator-density-compact") ? "compact" : "comfortable",
      search: searchInput ? searchInput.value : "",
      headerFilters: table.getHeaderFilters(),
      extra: extra ? extra() : {},
    };
  }

  function applyGridState(table, container, searchInput, state, applyExtra) {
    if (!state) return;
    if (state.columns) table.setColumnLayout(state.columns);
    if (state.density) {
      container.classList.remove("tabulator-density-compact", "tabulator-density-comfortable");
      container.classList.add(`tabulator-density-${state.density}`);
      container
        .closest(".inventory-grid")
        ?.querySelectorAll(".grid-density-toggle [data-density]")
        .forEach((b) => b.classList.toggle("is-active", b.dataset.density === state.density));
    }
    if (searchInput && typeof state.search === "string") {
      searchInput.value = state.search;
      searchInput.dispatchEvent(new Event("input"));
    }
    (state.headerFilters || []).forEach((f) => table.setHeaderFilterValue(f.field, f.value));
    if (state.sorters) table.setSort(state.sorters);
    if (applyExtra) applyExtra(state.extra || {});
  }

  /**
   * Wires the "Views" dropdown: load/apply a saved view, save the current
   * state as a new one, delete one you own (or any, as Administrator — the
   * server enforces that, this UI just doesn't hide the button either way
   * since apps.inventory.services.grid_views.delete_saved_grid_view is the
   * real check).
   */
  function initSavedViews(options) {
    const select = document.querySelector(options.selectSelector);
    const saveButton = document.querySelector(options.saveButtonSelector);
    const deleteButton = document.querySelector(options.deleteButtonSelector);
    if (!select) return;

    function refresh(selectAfterId) {
      fetch(options.listUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then((r) => r.json())
        .then((data) => {
          select.innerHTML = '<option value="">— Saved views —</option>';
          data.views.forEach((view) => {
            const option = document.createElement("option");
            option.value = view.id;
            option.textContent = view.is_shared && !view.is_mine ? `${view.name} (shared)` : view.name;
            option.dataset.state = JSON.stringify(view.state);
            option.dataset.mine = view.is_mine;
            select.appendChild(option);
          });
          if (selectAfterId) select.value = selectAfterId;
        });
    }
    refresh();

    select.addEventListener("change", () => {
      const option = select.selectedOptions[0];
      if (deleteButton) deleteButton.hidden = !select.value;
      if (!option || !option.value) return;
      options.onApply(JSON.parse(option.dataset.state || "{}"));
    });

    if (saveButton) {
      saveButton.addEventListener("click", () => {
        const name = window.prompt("Save current view as:");
        if (!name) return;
        const isShared = options.canShare && window.confirm("Share this view with every user?");
        fetch(options.listUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ name, state: options.onCapture(), is_shared: isShared }),
        })
          .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
          .then(({ ok, body }) => {
            if (!ok) throw new Error(body.error || "Couldn't save view");
            refresh(body.id);
          })
          .catch((error) => window.alert(error.message));
      });
    }

    if (deleteButton) {
      deleteButton.addEventListener("click", () => {
        if (!select.value || !window.confirm("Delete this saved view?")) return;
        fetch(options.deleteUrlTemplate.replace("__ID__", select.value), {
          method: "POST",
          headers: { "X-CSRFToken": getCsrfToken() },
        })
          .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
          .then(({ ok, body }) => {
            if (!ok) throw new Error(body.error || "Couldn't delete view");
            deleteButton.hidden = true;
            refresh();
          })
          .catch((error) => window.alert(error.message));
      });
    }
  }

  // Tabulator's sortable column headers are clickable <div>s with correct
  // ARIA (role="columnheader", aria-sort) but no tabindex — a mouse-only
  // interaction otherwise. Make them real keyboard targets: Tab reaches
  // them, Enter/Space triggers the same sort a click would.
  function makeSortableHeadersKeyboardOperable(container) {
    container.querySelectorAll(".tabulator-col.tabulator-sortable").forEach((col) => {
      const titleHolder = col.querySelector(".tabulator-col-title-holder");
      if (!titleHolder || titleHolder.dataset.keyboardSortBound) return;
      titleHolder.dataset.keyboardSortBound = "true";
      titleHolder.setAttribute("tabindex", "0");
      titleHolder.setAttribute("role", "button");
      titleHolder.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        titleHolder.querySelector(".tabulator-col-title")?.dispatchEvent(
          new MouseEvent("click", { bubbles: true })
        );
      });
    });
  }

  function showError(container, error) {
    container.classList.remove("is-loading");
    container.classList.add("has-error");
    let banner = container.querySelector(".grid-error-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "grid-error-banner";
      container.prepend(banner);
    }
    banner.textContent = "Couldn't load results. Check your connection and try again.";
  }

  /**
   * Wires a slide-over detail panel: clicking a grid row (anywhere except an
   * interactive element already handling the click — a link, checkbox, or
   * the actions menu) fetches that row's detail_url as a fragment (the
   * server tells apps.inventory.views.UnitAssetDetailView to render the
   * partial via the X-Requested-With header, same content-negotiation
   * pattern as apps.core.csv_export.CSVExportMixin's ?format=csv) and shows
   * it in the panel.
   *
   * options: panelSelector, contentSelector, table (the Tabulator instance).
   */
  function initDetailPanel(options) {
    const panel = document.querySelector(options.panelSelector);
    const content = document.querySelector(options.contentSelector);
    if (!panel || !content) return;

    function open(url) {
      content.innerHTML = '<p class="empty-state">Loading…</p>';
      panel.hidden = false;
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then((response) => (response.ok ? response.text() : Promise.reject(response.status)))
        .then((html) => {
          content.innerHTML = html;
        })
        .catch(() => {
          content.innerHTML = '<p class="empty-state">Couldn\'t load asset details.</p>';
        });
    }

    function close() {
      panel.hidden = true;
      content.innerHTML = "";
    }

    panel.addEventListener("click", (event) => {
      if (event.target.closest("[data-panel-close]")) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) close();
    });

    options.table.on("rowClick", (event, row) => {
      if (event.target.closest("a, input, .grid-row-menu")) return;
      open(row.getData().detail_url);
    });

    return { open, close };
  }

  window.InventoryGrid = {
    init: initInventoryGrid,
    initDetailPanel,
    initSavedViews,
    captureGridState,
    applyGridState,
    badgeFormatter,
    dateFormatter,
    datetimeFormatter,
    linkFormatter,
    actionsFormatter,
    badgeClass,
  };
})();
