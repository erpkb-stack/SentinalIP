(function () {
  "use strict";

  var form = document.getElementById("enfFilters");
  var body = document.getElementById("enfBody");
  var pager = document.getElementById("enfPager");
  var state = { page: 1, page_size: 25 };
  var COLS = 9;

  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No enforcement actions",
          "Actions appear once the Enforcement Strategist makes a recommendation.") +
        "</td></tr>";
      pager.innerHTML = "";
      return;
    }
    body.innerHTML = page.items.map(function (a) {
      return "<tr>" +
        '<td class="nowrap"><code>' + UI.esc(a.reference) + "</code>" +
          (a.is_simulated ? ' <span class="demo-tag">DEMO</span>' : "") + "</td>" +
        '<td><a href="/cases/' + a.case_id + '">#' + a.case_id + "</a></td>" +
        "<td>" + UI.esc(a.action_label) + "</td>" +
        "<td>" + UI.badge(a.status) + "</td>" +
        "<td>" + (a.has_valid_approval
          ? UI.badge("APPROVED", "Approved", "success")
          : UI.badge("AWAITING_APPROVAL", "Not approved", "warning")) + "</td>" +
        '<td class="num">' + (a.ai_confidence != null
          ? Math.round(a.ai_confidence) + "%" : "—") + "</td>" +
        '<td class="nowrap card__hint">' +
          UI.esc(a.submitted_at ? UI.fmtDateTime(a.submitted_at) : "Not filed") + "</td>" +
        "<td>" + (a.sla ? UI.slaCell(a.sla) : '<span class="card__hint">—</span>') + "</td>" +
        "<td>" + (a.result ? UI.badge(
            ["LISTING_REMOVED", "SELLER_SUSPENDED", "SELLER_COMPLIED"].indexOf(a.result) !== -1
              ? "RESOLVED" : "ESCALATED",
            UI.titleize(a.result)) : '<span class="card__hint">Pending</span>') + "</td>" +
      "</tr>";
    }).join("");
    document.getElementById("enfCount").textContent =
      UI.fmtNumber(page.total) + " enforcement action" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () { state.page = Number(b.dataset.page); load(); });
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(6, COLS);
    var data = new FormData(form);
    var params = { page: state.page, page_size: state.page_size };
    data.forEach(function (v, k) { if (v) params[k] = v; });
    try {
      render(await Api.enforcement(params));
    } catch (err) {
      UI.handleError(err, "Could not load enforcement actions.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Unavailable", err.message || "") + "</td></tr>";
    }
  }

  form.addEventListener("change", function () { state.page = 1; load(); });
  form.addEventListener("input", UI.debounce(function (e) {
    if (e.target.type === "search") { state.page = 1; load(); }
  }, 350));

  Api.get("/api/enforcement/pipeline").then(function (def) {
    document.getElementById("status").innerHTML = '<option value="">Any</option>' +
      def.statuses.map(function (s) {
        return '<option value="' + UI.esc(s) + '">' + UI.esc(UI.titleize(s)) + "</option>";
      }).join("");
    document.getElementById("action_type").innerHTML = '<option value="">Any</option>' +
      def.actions.map(function (a) {
        return '<option value="' + UI.esc(a.value) + '">' + UI.esc(a.label) + "</option>";
      }).join("");
  }).catch(function () {});

  load();
})();
