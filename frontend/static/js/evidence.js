(function () {
  "use strict";

  var form = document.getElementById("evidenceFilters");
  var body = document.getElementById("evidenceBody");
  var pager = document.getElementById("evidencePager");
  var state = { page: 1, page_size: 50 };
  var COLS = 10;

  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No evidence found",
          "Evidence appears here as the agents collect it and as your team uploads files.") +
        "</td></tr>";
      pager.innerHTML = "";
      return;
    }
    body.innerHTML = page.items.map(function (e) {
      return '<tr' + (e.is_archived ? ' style="opacity:.6"' : "") + ">" +
        '<td class="nowrap"><code>' + UI.esc(e.evidence_ref) + "</code></td>" +
        "<td>" + UI.esc(e.category_label || UI.titleize(e.category)) + "</td>" +
        '<td class="truncate" title="' + UI.esc(e.title) + '">' + UI.esc(e.title) + "</td>" +
        "<td>" + UI.esc(UI.titleize(e.evidence_type)) + "</td>" +
        "<td>" + UI.esc(e.collected_by) + "</td>" +
        '<td class="num">' + e.strength + "</td>" +
        "<td>" + UI.badge(e.relevance) + "</td>" +
        '<td class="mono card__hint" title="' + UI.esc(e.checksum || "") + '">' +
          UI.esc(e.checksum ? e.checksum.slice(0, 10) + "…" : "—") + "</td>" +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtDate(e.collected_at)) + "</td>" +
        '<td><a href="/cases/' + e.case_id + '">#' + e.case_id + "</a></td>" +
      "</tr>";
    }).join("");
    document.getElementById("evidenceCount").textContent =
      UI.fmtNumber(page.total) + " evidence item" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () {
        state.page = Number(b.dataset.page); load();
      });
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(6, COLS);
    var data = new FormData(form);
    var params = { page: state.page, page_size: state.page_size };
    data.forEach(function (v, k) { if (v) params[k] = v; });
    try {
      render(await Api.evidence(params));
    } catch (err) {
      UI.handleError(err, "Could not load evidence.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Evidence unavailable", err.message || "") + "</td></tr>";
    }
  }

  form.addEventListener("change", function () { state.page = 1; load(); });
  form.addEventListener("input", UI.debounce(function (e) {
    if (e.target.type === "search") { state.page = 1; load(); }
  }, 350));

  Api.meta().then(function (meta) {
    document.getElementById("category").innerHTML = '<option value="">All</option>' +
      meta.evidence_categories.map(function (c) {
        return '<option value="' + UI.esc(c.value) + '">' + UI.esc(c.label) + "</option>";
      }).join("");
    document.getElementById("relevance").innerHTML = '<option value="">Any</option>' +
      meta.evidence_relevance.map(function (r) {
        return '<option value="' + UI.esc(r.value) + '">' + UI.esc(r.label) + "</option>";
      }).join("");
  }).catch(function () {});

  load();
})();
