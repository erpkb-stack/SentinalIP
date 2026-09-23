(function () {
  "use strict";

  var windowSelect = document.getElementById("windowSelect");

  function statCard(card) {
    var tone = { warning: "warning", danger: "danger", success: "success" }[card.tone] || "accent";
    var value = card.unit === "%" ? Number(card.value).toFixed(1) + "%"
                                  : UI.fmtNumber(card.value);
    return '<div class="card stat stat--' + tone + '">' +
      '<div class="stat__label">' + UI.esc(card.label) + "</div>" +
      '<div class="stat__value">' + UI.esc(value) + "</div>" +
      (card.hint ? '<div class="stat__hint">' + UI.esc(card.hint) + "</div>" : "") +
      "</div>";
  }

  function caseLink(row) {
    return '<a href="/cases/' + row.id + '"><strong>' + UI.esc(row.case_number) + "</strong></a>" +
      '<div class="card__hint truncate" style="max-width:280px">' + UI.esc(row.title) + "</div>" +
      (row.vendor ? '<div class="card__hint">' + UI.esc(row.vendor) + "</div>" : "");
  }

  function renderAttention(rows) {
    var body = document.getElementById("attentionBody");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="4">' +
        UI.emptyState("Nothing waiting on you", "No cases are awaiting a human decision.") +
        "</td></tr>";
      return;
    }
    body.innerHTML = rows.map(function (r) {
      return "<tr>" +
        "<td>" + caseLink(r) + "</td>" +
        "<td>" + UI.confidenceCell(r.confidence, r.risk_level) + "</td>" +
        "<td>" + (r.recommended_action ? UI.badge("AWAITING_APPROVAL", r.recommended_action, "accent") : "-") + "</td>" +
        '<td class="nowrap">' + UI.badge(r.sla_state) +
          '<div class="card__hint">' + UI.esc(r.sla_label || "") + "</div></td>" +
      "</tr>";
    }).join("");
  }

  function renderRecent(rows) {
    var body = document.getElementById("recentBody");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="4">' +
        UI.emptyState("No cases yet",
          "Create your first case to start the AI pipeline.",
          '<a class="btn btn--primary" href="/cases/new">Create Case</a>') +
        "</td></tr>";
      return;
    }
    body.innerHTML = rows.map(function (r) {
      return "<tr>" +
        "<td>" + caseLink(r) + "</td>" +
        "<td>" + UI.badge(r.status) + "</td>" +
        "<td>" + UI.confidenceCell(r.confidence, r.risk_level) + "</td>" +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtRelative(r.created_at)) + "</td>" +
      "</tr>";
    }).join("");
  }

  function hasData(series) {
    return series && series.values && series.values.some(function (v) { return v > 0; });
  }

  function renderCharts(data) {
    // ---- cases over time ----
    if (hasData(data.cases_over_time)) {
      UI.renderChart("chartOverTime", {
        type: "line",
        data: {
          labels: data.cases_over_time.labels,
          datasets: [{
            label: "New cases",
            data: data.cases_over_time.values,
            borderColor: "#BF5700",
            backgroundColor: "rgba(191,87,0,.12)",
            fill: true, tension: .32, pointRadius: 2, borderWidth: 2,
          }],
        },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            y: { beginAtZero: true, ticks: { precision: 0 },
                 grid: { color: "#EFEFEF" } },
            x: { grid: { display: false } },
          },
        },
      });
    } else { UI.noData("chartOverTime", "No cases in this window."); }

    // ---- status ----
    if (hasData(data.cases_by_status)) {
      UI.renderChart("chartStatus", {
        type: "bar",
        data: {
          labels: data.cases_by_status.labels,
          datasets: [{
            label: "Cases", data: data.cases_by_status.values,
            backgroundColor: data.cases_by_status.colors || UI.PALETTE,
            borderRadius: 4, maxBarThickness: 34,
          }],
        },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#EFEFEF" } },
            x: { grid: { display: false } },
          },
        },
      });
    } else { UI.noData("chartStatus", "No cases yet."); }

    // ---- confidence ----
    if (hasData(data.confidence_distribution)) {
      UI.renderChart("chartConfidence", {
        type: "bar",
        data: {
          labels: data.confidence_distribution.labels,
          datasets: [{
            label: "Cases", data: data.confidence_distribution.values,
            backgroundColor: data.confidence_distribution.colors,
            borderRadius: 4, maxBarThickness: 40,
          }],
        },
        options: {
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: {
            x: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#EFEFEF" } },
            y: { grid: { display: false } },
          },
        },
      });
    } else { UI.noData("chartConfidence", "No analysed cases yet."); }

    // ---- marketplace ----
    if (hasData(data.cases_by_marketplace)) {
      UI.renderChart("chartMarketplace", {
        type: "doughnut",
        data: {
          labels: data.cases_by_marketplace.labels,
          datasets: [{
            data: data.cases_by_marketplace.values,
            backgroundColor: data.cases_by_marketplace.colors || UI.PALETTE,
            borderWidth: 2, borderColor: "#fff",
          }],
        },
        options: { cutout: "58%", plugins: { legend: { position: "bottom" } } },
      });
    } else { UI.noData("chartMarketplace", "No marketplace data yet."); }

    // ---- infringement ----
    if (hasData(data.cases_by_infringement)) {
      UI.renderChart("chartInfringement", {
        type: "doughnut",
        data: {
          labels: data.cases_by_infringement.labels,
          datasets: [{
            data: data.cases_by_infringement.values,
            backgroundColor: data.cases_by_infringement.colors || UI.PALETTE,
            borderWidth: 2, borderColor: "#fff",
          }],
        },
        options: { cutout: "58%", plugins: { legend: { position: "bottom" } } },
      });
    } else { UI.noData("chartInfringement", "No cases yet."); }

    // ---- SLA ----
    if (hasData(data.sla_performance)) {
      UI.renderChart("chartSla", {
        type: "bar",
        data: {
          labels: data.sla_performance.labels,
          datasets: [{
            label: "Cases", data: data.sla_performance.values,
            backgroundColor: data.sla_performance.colors,
            borderRadius: 4, maxBarThickness: 46,
          }],
        },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#EFEFEF" } },
            x: { grid: { display: false } },
          },
        },
      });
    } else { UI.noData("chartSla", "No SLA data yet."); }

    // ---- enforcement success ----
    if (hasData(data.enforcement_success)) {
      UI.renderChart("chartSuccess", {
        type: "bar",
        data: {
          labels: data.enforcement_success.labels,
          datasets: [{
            label: "Success rate (%)", data: data.enforcement_success.values,
            backgroundColor: "#BF5700", borderRadius: 4, maxBarThickness: 40,
          }],
        },
        options: {
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: {
            x: { beginAtZero: true, max: 100, grid: { color: "#EFEFEF" },
                 ticks: { callback: function (v) { return v + "%"; } } },
            y: { grid: { display: false } },
          },
        },
      });
    } else { UI.noData("chartSuccess", "No concluded filings yet."); }
  }

  async function load() {
    try {
      var data = await Api.dashboard({ days: windowSelect.value });
      document.getElementById("scopeLine").textContent =
        (data.scope === "platform"
          ? "Platform-wide view across every vendor"
          : "Scoped to " + (data.vendor_name || "your organization")) +
        " · updated " + UI.fmtDateTime(data.generated_at);
      document.getElementById("statCards").innerHTML =
        data.cards.map(statCard).join("");
      renderAttention(data.attention || []);
      renderRecent(data.recent_cases || []);
      renderCharts(data);
    } catch (err) {
      UI.handleError(err, "Could not load the dashboard.");
      document.getElementById("statCards").innerHTML =
        '<div class="card stat">' + UI.emptyState("Dashboard unavailable",
          "Reload the page or try again shortly.") + "</div>";
    }
  }

  windowSelect.addEventListener("change", load);
  load();
})();
