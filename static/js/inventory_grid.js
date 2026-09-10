(() => {
  if (!window.Tabulator) return;

  // Mirrors apps.core.templatetags.ui_extras.badge_class exactly (same
  // keyword lists, same substring heuristic) so a status/condition value
  // gets the identical badge color whether rendered server-side or by this
  // grid — see that function's docstring for why it's a heuristic, not a
  // per-choices-enum lookup.
  const BADGE_SUCCESS = ["active", "available", "in_stock", "in stock", "new", "good", "healthy", "completed"];
  const BADGE_INFO = ["reserved", "assigned", "in_use", "in use", "in_transit", "in transit", "pending", "processing"];
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

    // A dashboard/report "shortcut" link (e.g. Assets filtered to
    // ?status=in_stock) lands on this page, but this grid otherwise only
    // ever reads filter state from its own controls — never from the
    // page's own query string — so those links silently opened an
    // unfiltered grid. Seed both filter mechanisms from the URL once, up
    // front: a plain "more filters" input (extraFilters() reads these
    // live, so setting .value before the grid's first request is enough)
    // and a Tabulator header-filter column (needs Tabulator's own
    // initialHeaderFilter option, applied before that first request).
    const urlParams = new URLSearchParams(window.location.search);
    const locationNodes = JSON.parse(document.getElementById("location-filter-nodes")?.textContent || "[]");
    const switcher = document.querySelector("[data-location-switcher]");
    const countrySelect = switcher?.querySelector('[name="country"]');
    const locationSelect = switcher?.querySelector('[name="location"]');
    const nodeFor = (value) => locationNodes.find((node) => `id:${node.id}` === value || node.id === value);
    const selectedNode = nodeFor(urlParams.get("location") || urlParams.get("shelf") || urlParams.get("storage_room"));
    if (selectedNode) urlParams.set("country", selectedNode.country);
    if (countrySelect && locationSelect) {
      countrySelect.value = urlParams.get("country") || "";
      locationSelect.addEventListener("change", () => {
        const node = nodeFor(locationSelect.value);
        if (node) countrySelect.value = node.country;
      });
      countrySelect.addEventListener("change", () => {
        // Country selection broadens to all its authorized rooms and shelves.
        locationSelect.value = "";
      });
    }
    if (urlParams.has("product_type") && !urlParams.has("type")) {
      urlParams.set("type", urlParams.get("product_type"));
    }
    document.querySelectorAll("[data-filter]").forEach((input) => {
      const key = input.dataset.filter;
      if (!urlParams.has(key)) return;
      const value = urlParams.get(key);
      if (input.type === "checkbox") input.checked = value === "1";
      else input.value = value;
    });
    const initialHeaderFilter = (options.columns || [])
      .filter((col) => col.field && col.headerFilter && urlParams.has(col.field === "product_type" ? "type" : col.field))
      .map((col) => ({ field: col.field, value: urlParams.get(col.field === "product_type" ? "type" : col.field) }));

    let globalSearch = urlParams.get("q") || "";
    const initialSearch = document.querySelector(options.searchInputSelector || "[data-unused-search]");
    if (initialSearch) initialSearch.value = globalSearch;

    function buildParams(page, size) {
      const params = new URLSearchParams();
      // Context links from a product or room have no header-filter column.
      ["product", "location", "in_storage"].forEach((key) => {
        if (urlParams.has(key)) params.set(key, urlParams.get(key));
      });
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
      height: options.height || "70vh",
      placeholder: options.placeholder || "No results — try widening your filters.",
      movableColumns: true,
      columns: options.columns,
      initialSort: options.initialSort || [],
      initialHeaderFilter,
      selectableRows: options.rowSelectable !== false,
      index: "id",
      // Live per-keystroke filtering, debounced (Tabulator's own built-in
      // 300ms headerFilterLiveFilterDelay) rather than needing Enter/blur
      // to apply a column filter — matches the global search box's own
      // debounced live behavior instead of feeling inconsistent next to it.
      columnDefaults: { headerFilterLiveFilter: true, tooltip: true },
    });

    if (options.persistColumns) {
      initColumnPersistence(table, options.storageKey || "grid");
    }

    table.on("tableBuildError", (error) => {
      showError(container, error);
    });
    table.on("dataLoadError", (error) => {
      showError(container, error);
    });
    table.on("dataLoading", () => container.classList.add("is-loading"));
    table.on("dataLoaded", () => container.classList.remove("is-loading"));

    let syncingLocation = false;
    let previousLocationFilters = Object.fromEntries(initialHeaderFilter.map((f) => [f.field, f.value]));
    table.on("dataFiltering", () => {
      if (syncingLocation || !locationNodes.length) return;
      const current = Object.fromEntries(table.getHeaderFilters().map((f) => [f.field, f.value]));
      const changed = ["shelf", "storage_room", "country"].find((key) =>
        (current[key] || "") !== (previousLocationFilters[key] || ""));
      if (!changed) return;
      syncingLocation = true;
      // A fresh header selection replaces an older location shortcut constraint.
      urlParams.delete("location");
      const address = new URL(window.location.href);
      address.searchParams.delete("location");
      window.history.replaceState(null, "", address);
      if (locationSelect) locationSelect.value = "";
      const node = nodeFor(current[changed]);
      if (changed === "country") {
        table.setHeaderFilterValue("storage_room", "");
        table.setHeaderFilterValue("shelf", "");
      } else if (node) {
        table.setHeaderFilterValue("country", node.country);
        table.setHeaderFilterValue("storage_room", changed === "shelf" ? node.room : current.storage_room);
        if (changed === "storage_room") table.setHeaderFilterValue("shelf", "");
      }
      previousLocationFilters = Object.fromEntries(table.getHeaderFilters().map((f) => [f.field, f.value]));
      if (countrySelect) countrySelect.value = previousLocationFilters.country || "";
      syncingLocation = false;
    });

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

    // Cross-page/cross-search selection: Tabulator's own selection is tied
    // to whatever rows are currently loaded, so with remote filtering a
    // fresh search silently drops whatever was selected under a *previous*
    // search term — confirmed live with an event trace: Tabulator clears
    // the outgoing rows' selection as part of its own data-replace
    // teardown, firing rowSelectionChanged with everything now deselected
    // within ~2ms of dataLoaded, indistinguishable at face value from the
    // operator actually unchecking them. Reconciling on every such event
    // durably lost the earlier pick the moment a second search ran —
    // exactly the "can't add multiple different assets" complaint this was
    // built to prevent. (Reconciling only from real click events instead,
    // tried first, doesn't work either: the row-selection checkbox column
    // toggles the row directly and fires neither cellClick nor rowClick at
    // all — confirmed live.) Suppressing *removals* (never additions) for
    // a short window after each reload is the fix: comfortably longer than
    // the ~2ms the spurious clear needs, far shorter than any real human
    // click could land after a reload, so a genuine uncheck moments later
    // still works normally. selectedById is this table's full accumulated
    // selection across every page/search visited so far; onSelectionChange
    // is always called with that complete set, and dataLoaded re-applies
    // selectRow() for any newly-rendered row whose id is already known, so
    // the checkbox state stays correct when paging/searching back too.
    if (options.rowSelectable !== false) {
      const selectedById = new Map();
      const selectionKey = `${options.storageKey || "grid"}:selection`;
      if (options.persistSelection) {
        try {
          const remembered = JSON.parse(window.localStorage.getItem(selectionKey) || "[]");
          if (Array.isArray(remembered)) {
            remembered.forEach((row) => row?.id && selectedById.set(row.id, row));
          }
        } catch (error) {
          /* invalid/unavailable browser storage — start with no selection */
        }
      }
      let serverSaveTimer = null;
      const publishSelection = (saveServer = true) => {
        const rows = Array.from(selectedById.values());
        if (options.persistSelection) {
          try {
            window.localStorage.setItem(selectionKey, JSON.stringify(rows));
          } catch (error) {
            /* keep this page's selection when storage is unavailable */
          }
        }
        if (options.onSelectionChange) options.onSelectionChange(rows);
        if (saveServer && options.selectionUrl) {
          window.clearTimeout(serverSaveTimer);
          serverSaveTimer = window.setTimeout(() => {
            fetch(options.selectionUrl, {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
              body: JSON.stringify({ selected_ids: rows.map((row) => row.id) }),
            }).catch(() => {});
          }, 250);
        }
      };
      let suppressRemovals = false;
      table.on("rowSelectionChanged", () => {
        table.getRows(true).forEach((row) => {
          const data = row.getData();
          if (row.isSelected()) selectedById.set(data.id, data);
          else if (!suppressRemovals) selectedById.delete(data.id);
        });
        publishSelection();
      });
      table.on("dataLoaded", (data) => {
        suppressRemovals = true;
        window.setTimeout(() => {
          suppressRemovals = false;
        }, 150);
        const idsOnPage = data.map((row) => row.id).filter((id) => selectedById.has(id));
        if (idsOnPage.length) window.setTimeout(() => table.selectRow(idsOnPage), 0);
      });
      table.clearPersistentSelection = () => {
        selectedById.clear();
        table.deselectRow();
        publishSelection();
      };
      table.on("tableBuilt", () => publishSelection(false));
      if (options.selectionUrl) {
        fetch(options.selectionUrl, { headers: { Accept: "application/json" } })
          .then((response) => response.ok ? response.json() : Promise.reject())
          .then((payload) => {
            if (!Array.isArray(payload.selection)) return;
            selectedById.clear();
            payload.selection.forEach((row) => row?.id && selectedById.set(row.id, row));
            const ids = payload.selection.map((row) => row.id);
            if (ids.length) table.selectRow(ids);
            publishSelection(false);
          })
          .catch(() => {});
      }
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
      pageSize: table.getPageSize(),
      search: searchInput ? searchInput.value : "",
      headerFilters: table.getHeaderFilters(),
      extra: extra ? extra() : {},
    };
  }

  function applyGridState(table, container, searchInput, state, applyExtra) {
    if (!state) return;
    // Every per-item application below is wrapped so ONE stale reference —
    // a saved column for a field that's since been removed, a header
    // filter naming a value (e.g. a deactivated location) that no longer
    // matches anything — degrades to "that one thing is skipped," never a
    // thrown error that leaves the rest of the saved view (or the page)
    // unapplied. Saved views are long-lived UI state; the grid/schema they
    // describe isn't guaranteed to still match bit-for-bit.
    if (state.columns) {
      try {
        table.setColumnLayout(state.columns);
      } catch (error) {
        /* a saved column no longer exists on this grid — ignore it */
      }
    }
    if (state.density) {
      container.classList.remove("tabulator-density-compact", "tabulator-density-comfortable");
      container.classList.add(`tabulator-density-${state.density}`);
      container
        .closest(".inventory-grid")
        ?.querySelectorAll(".grid-density-toggle [data-density]")
        .forEach((b) => b.classList.toggle("is-active", b.dataset.density === state.density));
    }
    if ([25, 50, 100, 200].includes(Number(state.pageSize))) {
      table.setPageSize(Number(state.pageSize));
    }
    if (searchInput && typeof state.search === "string") {
      searchInput.value = state.search;
      searchInput.dispatchEvent(new Event("input"));
    }
    (state.headerFilters || []).forEach((f) => {
      try {
        table.setHeaderFilterValue(f.field, f.value);
      } catch (error) {
        /* saved filter references a field/value no longer offered — skip it */
      }
    });
    if (state.sorters) {
      try {
        table.setSort(state.sorters);
      } catch (error) {
        /* saved sort references a field no longer sortable — skip it */
      }
    }
    if (applyExtra) applyExtra(state.extra || {});
  }

  /**
   * Keeps an operator's ordinary column choices between visits without
   * requiring them to create a named saved view. Named/default views still
   * use applyGridState() and therefore deliberately replace this layout;
   * the resulting choice then becomes the operator's latest local layout.
   */
  function initColumnPersistence(table, storageKey) {
    const key = `${storageKey}:columns`;
    let restoring = false;

    table.on("tableBuilt", () => {
      try {
        const layout = JSON.parse(window.localStorage.getItem(key) || "null");
        if (!Array.isArray(layout)) return;
        restoring = true;
        table.setColumnLayout(layout);
      } catch (error) {
        // Corrupt or obsolete browser state must never prevent the grid
        // loading. A later valid column change replaces it automatically.
      } finally {
        restoring = false;
      }
    });

    const persist = () => {
      if (restoring) return;
      try {
        window.localStorage.setItem(key, JSON.stringify(table.getColumnLayout()));
      } catch (error) {
        /* private browsing / storage disabled — use this visit's layout */
      }
    };
    table.on("columnVisibilityChanged", persist);
    table.on("columnMoved", persist);
    table.on("columnResized", persist);
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
    // Both optional — a page that doesn't pass these selectors just doesn't
    // get rename/set-default controls, load/save/delete keep working as
    // before (apps.inventory.services.grid_views.update_saved_grid_view()
    // backs both; SavedGridView.is_default is what "default" means).
    const renameButton = options.renameButtonSelector
      ? document.querySelector(options.renameButtonSelector)
      : null;
    const defaultButton = options.defaultButtonSelector
      ? document.querySelector(options.defaultButtonSelector)
      : null;
    const pinButton = options.pinButtonSelector
      ? document.querySelector(options.pinButtonSelector)
      : null;
    if (!select) return;

    function updateActionButtons() {
      const option = select.selectedOptions[0];
      const hasSelection = Boolean(option && option.value);
      const isMine = hasSelection && option.dataset.mine === "true";
      if (deleteButton) deleteButton.hidden = !hasSelection;
      if (renameButton) renameButton.hidden = !isMine;
      if (defaultButton) {
        defaultButton.hidden = !isMine;
        defaultButton.textContent =
          option && option.dataset.default === "true" ? "Unset default" : "Set as default";
      }
      if (pinButton) {
        pinButton.hidden = !isMine;
        pinButton.textContent =
          option && option.dataset.pinned === "true" ? "Unpin from workspace" : "Pin to workspace";
      }
    }

    let hasAppliedInitialDefault = false;
    function refresh(selectAfterId) {
      fetch(options.listUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then((r) => r.json())
        .then((data) => {
          select.innerHTML = '<option value="">— Saved views —</option>';
          data.views.forEach((view) => {
            const option = document.createElement("option");
            option.value = view.id;
            const label = view.is_shared && !view.is_mine ? `${view.name} (shared)` : view.name;
            option.textContent = view.is_default ? `${label} (default)` : label;
            option.dataset.state = JSON.stringify(view.state);
            option.dataset.mine = view.is_mine;
            option.dataset.default = view.is_default;
            option.dataset.pinned = view.is_pinned;
            select.appendChild(option);
          });
          if (selectAfterId) select.value = selectAfterId;
          // Applied once, only on this control's first load (page open) —
          // never on a later refresh() (after save/rename/delete), which
          // would otherwise silently discard whatever the operator is
          // currently looking at.
          if (!hasAppliedInitialDefault && !selectAfterId) {
            const requestedId = new URLSearchParams(window.location.search).get("saved_view");
            const requestedView = data.views.find((view) => view.id === requestedId);
            if (requestedView) {
              select.value = requestedView.id;
              options.onApply(requestedView.state);
            } else if (!window.location.search) {
              const defaultView = options.applyDefaultOnLoad
                ? data.views.find((view) => view.is_default)
                : null;
              if (defaultView) {
                select.value = defaultView.id;
                options.onApply(defaultView.state);
              } else if (data.preference) {
                options.onApply(data.preference);
              }
            }
          }
          hasAppliedInitialDefault = true;
          updateActionButtons();
        });
    }
    refresh();

    // Account-backed automatic preferences complement named views: the
    // latest layout/filter/sort/density follows the operator to another
    // browser, while explicit URL filters and named defaults retain
    // priority on initial load.
    let preferenceTimer = null;
    const persistPreference = () => {
      if (!hasAppliedInitialDefault) return;
      window.clearTimeout(preferenceTimer);
      preferenceTimer = window.setTimeout(() => {
        fetch(options.listUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ automatic: true, state: options.onCapture() }),
        }).catch(() => {});
      }, 500);
    };
    if (options.table) {
      options.table.on("columnVisibilityChanged", persistPreference);
      options.table.on("columnMoved", persistPreference);
      options.table.on("columnResized", persistPreference);
      options.table.on("dataLoaded", persistPreference);
      options.table.on("pageSizeChanged", persistPreference);
    }

    select.addEventListener("change", () => {
      const option = select.selectedOptions[0];
      updateActionButtons();
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

    if (renameButton && options.updateUrlTemplate) {
      renameButton.addEventListener("click", () => {
        const option = select.selectedOptions[0];
        if (!option || !option.value) return;
        const currentName = option.textContent.replace(/ \(default\)$/, "").replace(/ \(shared\)$/, "");
        const name = window.prompt("Rename view to:", currentName);
        if (!name) return;
        fetch(options.updateUrlTemplate.replace("__ID__", option.value), {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ name }),
        })
          .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
          .then(({ ok, body }) => {
            if (!ok) throw new Error(body.error || "Couldn't rename view");
            refresh(option.value);
          })
          .catch((error) => window.alert(error.message));
      });
    }

    if (defaultButton && options.updateUrlTemplate) {
      defaultButton.addEventListener("click", () => {
        const option = select.selectedOptions[0];
        if (!option || !option.value) return;
        const makeDefault = option.dataset.default !== "true";
        fetch(options.updateUrlTemplate.replace("__ID__", option.value), {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ is_default: makeDefault }),
        })
          .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
          .then(({ ok, body }) => {
            if (!ok) throw new Error(body.error || "Couldn't update default view");
            refresh(option.value);
          })
          .catch((error) => window.alert(error.message));
      });
    }

    if (pinButton && options.updateUrlTemplate) {
      pinButton.addEventListener("click", () => {
        const option = select.selectedOptions[0];
        if (!option || !option.value || option.dataset.mine !== "true") return;
        fetch(options.updateUrlTemplate.replace("__ID__", option.value), {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ is_pinned: option.dataset.pinned !== "true" }),
        })
          .then((r) => r.json().then((body) => ({ ok: r.ok, body })))
          .then(({ ok, body }) => {
            if (!ok) throw new Error(body.error || "Couldn't update workspace shortcut");
            refresh(option.value);
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

  /**
   * Wires a grid's "Columns" toggle button + visibility panel from the
   * table's live column list, so the panel always matches what's actually
   * configured — no separate list to keep in sync. Previously duplicated
   * almost verbatim across asset_list.html/balance_list.html; product_list.
   * html needing the exact same behavior was the third copy that made
   * extracting it worthwhile.
   *
   * options: toggleSelector, panelSelector, table (the Tabulator instance).
   */
  function initColumnsPanel(options) {
    const panel = document.querySelector(options.panelSelector);
    const toggle = document.querySelector(options.toggleSelector);
    if (!panel || !toggle) return;

    function renderColumns() {
      panel.replaceChildren();
      const hint = document.createElement("p");
      hint.textContent = "Column choices are saved automatically on this device. Save a named view to keep filters too or share it.";
      panel.appendChild(hint);
      const columns = options.table.getColumns().filter((column) => column.getField() && !column.getDefinition().frozen);
      columns.forEach((column, index) => {
        const field = column.getField();
        if (!field) return;
        const row = document.createElement("div");
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = column.isVisible();
        checkbox.addEventListener("change", () => (checkbox.checked ? column.show() : column.hide()));
        label.appendChild(checkbox);
        label.append(column.getDefinition().title);
        row.appendChild(label);
        [-1, 1].forEach((direction) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "btn btn--sm";
          button.textContent = direction < 0 ? "↑" : "↓";
          button.setAttribute("aria-label", `Move ${column.getDefinition().title} ${direction < 0 ? "left" : "right"}`);
          button.disabled = !columns[index + direction];
          button.addEventListener("click", () => {
            options.table.moveColumn(field, columns[index + direction].getField(), direction > 0);
            renderColumns();
            const buttons = panel.querySelectorAll("button");
            buttons[(index + direction) * 2 + (direction > 0 ? 1 : 0)]?.focus();
          });
          row.appendChild(button);
        });
        panel.appendChild(row);
      });
    }
    options.table.on("tableBuilt", renderColumns);
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      if (!expanded) renderColumns();
      toggle.setAttribute("aria-expanded", String(!expanded));
      panel.hidden = expanded;
    });
    document.addEventListener("click", (event) => {
      if (!panel.hidden && !panel.contains(event.target) && event.target !== toggle) {
        panel.hidden = true;
        toggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  /**
   * Wires an "Export filtered CSV" link so its href mirrors whatever's
   * currently applied in the grid (global search + header filters, plus
   * whatever `extraParams()` returns for filters that live outside
   * Tabulator's own state, e.g. asset_list.html's date-range panel) onto a
   * plain server-rendered `?format=csv` download — apps.core.csv_export.
   * CSVExportMixin reads the exact same param names, so no client-side CSV
   * assembly is needed. Refreshes on every `dataLoaded` so the link is
   * always in sync with what's currently on screen.
   *
   * options: linkSelector, table, searchInputSelector (optional),
   *          extraParams() -> {key: value} (optional).
   */
  function initExportLink(options) {
    const link = document.querySelector(options.linkSelector);
    if (!link) return;
    const searchInput = options.searchInputSelector
      ? document.querySelector(options.searchInputSelector)
      : null;

    function refresh() {
      const params = new URLSearchParams({ format: "csv" });
      const context = new URLSearchParams(window.location.search);
      ["product", "location", "in_storage"].forEach((key) => {
        if (context.has(key)) params.set(key, context.get(key));
      });
      if (searchInput) {
        const search = searchInput.value.trim();
        if (search) params.set("q", search);
      }
      options.table.getHeaderFilters().forEach((f) => f.value !== "" && params.set(f.field, f.value));
      if (options.extraParams) {
        Object.entries(options.extraParams()).forEach(([key, value]) => {
          if (value) params.set(key, value);
        });
      }
      link.href = `?${params.toString()}`;
    }
    options.table.on("dataLoaded", refresh);
  }

  window.InventoryGrid = {
    init: initInventoryGrid,
    initDetailPanel,
    initSavedViews,
    initColumnsPanel,
    initExportLink,
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
