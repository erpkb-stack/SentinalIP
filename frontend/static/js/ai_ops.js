(function () {
  "use strict";

  function agentCard(a) {
    var successRate = a.processed_total
      ? Math.round(a.success_count / a.processed_total * 100) : null;
    return '<div class="card">' +
      '<div class="card__head">' +
        "<h2>" + UI.esc(a.label) + "</h2>" +
        '<div class="card__actions">' +
          UI.badge(a.error_count > 0 ? "AT_RISK" : "ACTIVE",
                   a.error_count > 0 ? "Degraded" : "Online",
                   a.error_count > 0 ? "warning" : "success") +
        "</div>" +
      "</div>" +
      '<div class="card__body">' +
        '<p class="card__hint" style="margin-bottom:.875rem">' + UI.esc(a.charter) + "</p>" +
        '<div class="grid grid--stats" style="gap:.75rem">' +
          '<div><div class="stat__label">' + UI.esc(a.metric_label) + "</div>" +
            '<div class="stat__value" style="font-size:1.5rem">' +
              UI.fmtNumber(a.processed_today) + "</div></div>" +
          '<div><div class="stat__label">Total runs</div>' +
            '<div class="stat__value" style="font-size:1.5rem">' +
              UI.fmtNumber(a.processed_total) + "</div></div>" +
          '<div><div class="stat__label">Success</div>' +
            '<div class="stat__value" style="font-size:1.5rem;color:var(--success)">' +
              (successRate === null ? "—" : successRate + "%") + "</div></div>" +
          '<div><div class="stat__label">Errors</div>' +
            '<div class="stat__value" style="font-size:1.5rem;color:' +
              (a.error_count ? "var(--error)" : "var(--medium-gray)") + '">' +
              UI.fmtNumber(a.failure_count) + "</div></div>" +
        "</div>" +
        '<dl class="dl" style="margin-top:1rem">' +
          "<div><dt>Average duration</dt><dd>" +
            UI.esc(UI.fmtDuration(a.avg_duration_ms)) + "</dd></div>" +
          "<div><dt>Last execution</dt><dd>" +
            UI.esc(a.last_execution ? UI.fmtRelative(a.last_execution) : "Never") + "</dd></div>" +
          "<div><dt>Provider</dt><dd>" + UI.esc(a.provider) + " · " +
            UI.esc(a.model) + "</dd></div>" +
        "</dl>" +
      "</div></div>";
  }

  async function loadOps() {
    try {
      var data = await Api.aiOps();
      document.getElementById("providerLine").innerHTML =
        "Provider <strong>" + UI.esc(data.provider) + "</strong> · model " +
        UI.esc(data.model) +
        (data.simulated ? ' <span class="demo-tag">DEMO / SIMULATED</span>' : "") +
        " · " + UI.fmtNumber(data.runs_today) + " runs today, " +
        UI.fmtNumber(data.total_runs) + " total";
      document.getElementById("agentCards").innerHTML =
        data.agents.map(agentCard).join("");
    } catch (err) {
      UI.handleError(err, "Could not load AI operations.");
    }
  }

  async function loadRuns() {
    var body = document.getElementById("runsBody");
    body.innerHTML = UI.skeletonRows(6, 7);
    try {
      var rows = await Api.agentRuns({ limit: 60 });
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="7">' +
          UI.emptyState("No agent runs yet",
            "Create a case and run the pipeline to populate this view.") + "</td></tr>";
        return;
      }
      body.innerHTML = rows.map(function (r) {
        return "<tr>" +
          "<td>" + UI.esc(r.label) + "</td>" +
          '<td><a href="/cases/' + r.case_id + '">#' + r.case_id + "</a></td>" +
          "<td>" + UI.badge(r.status) + "</td>" +
          '<td class="num">' + (r.confidence != null ? Math.round(r.confidence) + "%" : "—") + "</td>" +
          '<td class="num nowrap">' + UI.esc(UI.fmtDuration(r.duration_ms)) + "</td>" +
          '<td class="nowrap card__hint">' + UI.esc(UI.fmtRelative(r.started_at)) + "</td>" +
          '<td class="truncate" title="' + UI.esc(r.summary || r.error || "") + '">' +
            UI.esc(r.summary || r.error || "—") + "</td>" +
        "</tr>";
      }).join("");
      document.getElementById("runsHint").textContent = rows.length + " most recent";
    } catch (err) {
      body.innerHTML = '<tr><td colspan="7">' +
        UI.emptyState("Runs unavailable", err.message || "") + "</td></tr>";
    }
  }

  function refresh() { loadOps(); loadRuns(); }
  document.getElementById("refreshOps").addEventListener("click", refresh);
  refresh();
  setInterval(refresh, 45000);
})();
