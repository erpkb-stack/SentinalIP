(function () {
  "use strict";

  var form = document.getElementById("filterForm");
  var body = document.getElementById("casesBody");
  var pager = document.getElementById("pager");
  var scope = form.dataset.scope;
  var COLS = 12;

  var state = {
    page: 1, page_size: 25, sort: "created_at", direction: "desc",
    mine: scope === "mine" ? true : undefined,
  };

  function fillSelect(id, items, valueKey, labelKey, placeholder) {
    var el = document.getElementById(id);
    if (!el) return;
    var current = el.value;
    el.innerHTML = '<option value="">' + UI.esc(placeholder) + "</option>" +
      items.map(function (i) {
        return '<option value="' + UI.esc(i[valueKey]) + '">' +
          UI.esc(i[labelKey]) + "</option>";
      }).join("");
    if (current) el.value = current;
  }

  function readFilters() {
    var data = new FormData(form);
    var out = {};
    data.forEach(function (v, k) { if (v) out[k] = v; });
    return out;
  }

  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState(
          "No cases match these filters",
          "Adjust the filters, or create a case to start an investigation.",
          '<a class="btn btn--primary" href="/cases/new">Create Case</a>'
        ) + "</td></tr>";
      pager.innerHTML = "";
      document.getElementById("resultCount").textContent = "Cases";
      return;
    }

    body.innerHTML = page.items.map(function (c) {
      return '<tr class="is-clickable" data-href="/cases/' + c.id + '">' +
        '<td class="nowrap"><a href="/cases/' + c.id + '"><strong>' +
          UI.esc(c.case_number) + "</strong></a></td>" +
        "<td>" + UI.esc(c.vendor_name || "-") + "</td>" +
        '<td class="truncate" title="' + UI.esc(c.product_name || c.title) + '">' +
          UI.esc(c.product_name || c.title) + "</td>" +
        "<td>" + UI.esc(c.marketplace || "-") + "</td>" +
        '<td class="truncate">' + UI.esc(c.seller_name || "-") + "</td>" +
        "<td>" + UI.esc(c.infringement_label || UI.titleize(c.infringement_type)) + "</td>" +
        "<td>" + UI.confidenceCell(c.ai_confidence, c.risk_level) + "</td>" +
        "<td>" + UI.badge(c.status) + "</td>" +
        "<td>" + UI.slaCell(c.sla) + "</td>" +
        "<td>" + UI.esc(c.assigned_to_name || "Unassigned") + "</td>" +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtDate(c.created_at)) + "</td>" +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtRelative(c.updated_at)) + "</td>" +
      "</tr>";
    }).join("");

    body.querySelectorAll("tr[data-href]").forEach(function (tr) {
      tr.addEventListener("click", function (e) {
        if (e.target.closest("a")) return;
        location.href = tr.dataset.href;
      });
    });

    document.getElementById("resultCount").textContent =
      UI.fmtNumber(page.total) + " case" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.page = Number(btn.dataset.page);
        load();
      });
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(6, COLS);
    var params = Object.assign({}, readFilters(), {
      page: state.page, page_size: state.page_size,
      sort: state.sort, direction: state.direction,
    });
    if (state.mine) params.mine = true;
    try {
      render(await Api.cases(params));
    } catch (err) {
      UI.handleError(err, "Could not load cases.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Cases unavailable", err.message || "") + "</td></tr>";
    }
  }

  var debouncedLoad = UI.debounce(function () { state.page = 1; load(); }, 350);

  form.addEventListener("input", function (e) {
    if (e.target.type === "search" || e.target.type === "text") debouncedLoad();
  });
  form.addEventListener("change", function (e) {
    if (e.target.type !== "search") { state.page = 1; load(); }
  });
  document.getElementById("resetFilters").addEventListener("click", function () {
    form.reset();
    state.page = 1;
    load();
  });

  document.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.dataset.sort;
      if (state.sort === key) {
        state.direction = state.direction === "desc" ? "asc" : "desc";
      } else {
        state.sort = key; state.direction = "desc";
      }
      state.page = 1;
      load();
    });
  });

  async function bootstrap() {
    // Pre-fill filters from the URL (e.g. the dashboard's "View all" links).
    var params = new URLSearchParams(location.search);
    ["status", "vendor_id", "marketplace_id", "infringement_type",
     "risk_level", "sla_state"].forEach(function (key) {
      var v = params.get(key);
      if (v) {
        var el = document.getElementById(key);
        if (el) setTimeout(function () { el.value = v; }, 0);
      }
    });

    try {
      var meta = await Api.meta();
      fillSelect("status", meta.case_statuses, "value", "label", "Any");
      fillSelect("infringement_type", meta.infringement_types, "value", "label", "Any");
      fillSelect("risk_level", meta.risk_levels, "value", "label", "Any");
      fillSelect("sla_state", meta.sla_states, "value", "label", "Any");
    } catch (e) { /* filters degrade to free-text search */ }

    try {
      var mps = await Api.marketplaces();
      fillSelect("marketplace_id", mps, "id", "name", "Any");
    } catch (e) { /* optional */ }

    try {
      var me = await Api.me();
      if (me.is_global) {
        document.getElementById("vendorField").hidden = false;
        var vendors = await Api.vendorOptions();
        fillSelect("vendor_id", vendors, "id", "name", "All vendors");
        document.getElementById("activeScope").textContent =
          "Platform scope · every vendor";
      } else {
        document.getElementById("activeScope").textContent =
          "Tenant scope · " + (me.vendor_name || "your organization");
      }
      if (me.is_global || me.vendor_id) {
        try {
          var users = await Api.users({ page_size: 200 });
          fillSelect(
            "assigned_to_id",
            users.items.map(function (u) { return { id: u.id, name: u.full_name }; }),
            "id", "name", "Anyone"
          );
        } catch (e) {
          // Vendor users cannot list accounts; hide the filter rather than fail.
          document.getElementById("assigned_to_id").closest(".field").hidden = true;
        }
      }
    } catch (e) { /* handled by the API client */ }

    // Re-apply URL params after the selects are populated.
    ["status", "vendor_id", "marketplace_id", "infringement_type",
     "risk_level", "sla_state"].forEach(function (key) {
      var v = params.get(key);
      var el = document.getElementById(key);
      if (v && el) el.value = v;
    });

    load();
  }

  bootstrap();
})();
