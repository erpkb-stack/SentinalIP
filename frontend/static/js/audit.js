(function () {
  "use strict";

  var form = document.getElementById("auditFilters");
  var body = document.getElementById("auditBody");
  var pager = document.getElementById("auditPager");
  var state = { page: 1, page_size: 50 };
  var COLS = 9;

  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No audit records match", "Widen the filters or clear the search.") +
        "</td></tr>";
      pager.innerHTML = "";
      return;
    }
    body.innerHTML = page.items.map(function (r) {
      return '<tr class="is-clickable" data-id="' + r.id + '">' +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtDateTime(r.timestamp)) + "</td>" +
        '<td class="truncate">' + UI.esc(r.user_email || "system") + "</td>" +
        "<td>" + UI.esc(UI.titleize(r.role_code || "")) + "</td>" +
        "<td>" + UI.esc(r.vendor_name || "—") + "</td>" +
        "<td>" + UI.badge(r.success ? "APPROVED" : "REJECTED", UI.titleize(r.action),
                          r.success ? "neutral" : "danger") + "</td>" +
        '<td class="truncate">' + UI.esc(r.object_label ||
          ((r.object_type || "") + (r.object_id ? " #" + r.object_id : ""))) + "</td>" +
        '<td class="truncate">' + UI.esc(r.detail || "—") + "</td>" +
        '<td class="card__hint nowrap">' + UI.esc(r.ip_address || "—") + "</td>" +
        '<td class="mono card__hint">' + UI.esc(r.correlation_id || "—") + "</td>" +
      "</tr>";
    }).join("");

    body.querySelectorAll("tr[data-id]").forEach(function (tr) {
      tr.addEventListener("click", function () {
        var record = page.items.find(function (i) { return i.id === Number(tr.dataset.id); });
        if (record) showRecord(record);
      });
    });

    document.getElementById("auditCount").textContent =
      UI.fmtNumber(page.total) + " audit record" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () { state.page = Number(b.dataset.page); load(); });
    });
  }

  function jsonBlock(label, value) {
    if (!value) return "";
    return "<h4 style=\"margin:1rem 0 .375rem;font-size:.85rem\">" + UI.esc(label) + "</h4>" +
      '<pre class="notice-draft">' + UI.esc(JSON.stringify(value, null, 2)) + "</pre>";
  }

  function showRecord(r) {
    UI.openModal({
      title: UI.titleize(r.action),
      wide: true,
      body:
        '<dl class="dl">' +
          "<div><dt>Timestamp</dt><dd>" + UI.esc(UI.fmtDateTime(r.timestamp)) + "</dd></div>" +
          "<div><dt>User</dt><dd>" + UI.esc(r.user_email || "system") + "</dd></div>" +
          "<div><dt>Role</dt><dd>" + UI.esc(UI.titleize(r.role_code || "—")) + "</dd></div>" +
          "<div><dt>Vendor</dt><dd>" + UI.esc(r.vendor_name || "—") + "</dd></div>" +
          "<div><dt>Object</dt><dd>" + UI.esc(r.object_type || "—") +
            (r.object_id ? " #" + UI.esc(r.object_id) : "") + "</dd></div>" +
          "<div><dt>Label</dt><dd>" + UI.esc(r.object_label || "—") + "</dd></div>" +
          "<div><dt>IP address</dt><dd>" + UI.esc(r.ip_address || "—") + "</dd></div>" +
          "<div><dt>Correlation ID</dt><dd class=\"mono\">" +
            UI.esc(r.correlation_id || "—") + "</dd></div>" +
          "<div><dt>Outcome</dt><dd>" +
            UI.badge(r.success ? "APPROVED" : "REJECTED",
                     r.success ? "Succeeded" : "Denied / failed",
                     r.success ? "success" : "danger") + "</dd></div>" +
        "</dl>" +
        (r.detail ? "<p style=\"margin-top:1rem\">" + UI.esc(r.detail) + "</p>" : "") +
        jsonBlock("Previous value", r.previous_value) +
        jsonBlock("New value", r.new_value),
      footer: '<button class="btn" data-modal-close>Close</button>',
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(8, COLS);
    var data = new FormData(form);
    var params = { page: state.page, page_size: state.page_size };
    data.forEach(function (v, k) { if (v) params[k] = v; });
    try {
      render(await Api.audit(params));
    } catch (err) {
      UI.handleError(err, "Could not load the audit trail.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Audit trail unavailable", err.message || "") + "</td></tr>";
    }
  }

  form.addEventListener("change", function () { state.page = 1; load(); });
  form.addEventListener("input", UI.debounce(function (e) {
    if (e.target.type === "search") { state.page = 1; load(); }
  }, 350));

  (async function bootstrap() {
    try {
      var actions = await Api.get("/api/audit/actions");
      document.getElementById("action").innerHTML = '<option value="">Any</option>' +
        actions.actions.map(function (a) {
          return '<option value="' + UI.esc(a.value) + '">' + UI.esc(a.label) + "</option>";
        }).join("");
    } catch (e) { /* optional */ }
    try {
      var me = await Api.me();
      if (me.is_global) {
        document.getElementById("auditVendorField").hidden = false;
        var vendors = await Api.vendorOptions();
        document.getElementById("vendor_id").innerHTML = '<option value="">All</option>' +
          vendors.map(function (v) {
            return '<option value="' + v.id + '">' + UI.esc(v.name) + "</option>";
          }).join("");
      }
    } catch (e) { /* optional */ }
    load();
  })();
})();
