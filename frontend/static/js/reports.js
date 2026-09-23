(function () {
  "use strict";

  var windowSelect = document.getElementById("reportWindow");

  function rows(tbodyId, items, cols, mapper, emptyLabel) {
    var body = document.getElementById(tbodyId);
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="' + cols + '">' +
        UI.emptyState(emptyLabel, "Nothing recorded in this window.") + "</td></tr>";
      return;
    }
    body.innerHTML = items.map(mapper).join("");
  }

  async function load() {
    ["vendorBody", "marketBody"].forEach(function (id) {
      document.getElementById(id).innerHTML = UI.skeletonRows(4, 5);
    });
    try {
      var data = await Api.reports({ days: windowSelect.value });
      document.getElementById("reportScope").textContent =
        "Window: last " + data.window_days + " days · generated " +
        UI.fmtDateTime(data.generated_at);

      rows("vendorBody", data.by_vendor, 5, function (r) {
        return "<tr><td>" + UI.esc(r.vendor) + "</td>" +
          '<td class="num">' + UI.fmtNumber(r.cases) + "</td>" +
          '<td class="num">' + (r.avg_confidence != null ? r.avg_confidence + "%" : "—") + "</td>" +
          '<td class="num">' + UI.fmtNumber(r.resolved) + "</td>" +
          '<td class="num" style="color:' + (r.sla_breached ? "var(--error)" : "inherit") + '">' +
            UI.fmtNumber(r.sla_breached) + "</td></tr>";
      }, "No vendor activity");

      rows("marketBody", data.by_marketplace, 4, function (r) {
        return "<tr><td>" + UI.esc(r.marketplace) + "</td>" +
          '<td class="num">' + UI.fmtNumber(r.cases) + "</td>" +
          '<td class="num">' + (r.avg_confidence != null ? r.avg_confidence + "%" : "—") + "</td>" +
          '<td class="num">' + UI.fmtNumber(r.resolved) + "</td></tr>";
      }, "No marketplace activity");
    } catch (err) {
      UI.handleError(err, "Could not load reports.");
    }
  }

  windowSelect.addEventListener("change", load);
  document.getElementById("printReport").addEventListener("click", function () {
    window.print();
  });
  load();
})();
