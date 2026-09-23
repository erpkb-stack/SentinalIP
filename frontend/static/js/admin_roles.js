(function () {
  "use strict";

  (async function load() {
    var table = document.getElementById("matrix");
    try {
      var data = await Api.permissions();

      table.querySelector("thead").innerHTML = "<tr><th>Permission</th>" +
        data.roles.map(function (r) {
          return '<th style="text-align:center">' + UI.esc(r.label) + "</th>";
        }).join("") + "</tr>";

      var groups = {};
      data.permissions.forEach(function (p) {
        var area = p.value.split(":")[0];
        (groups[area] = groups[area] || []).push(p);
      });

      table.querySelector("tbody").innerHTML = Object.keys(groups).map(function (area) {
        return '<tr><td colspan="' + (data.roles.length + 1) +
            '" style="background:var(--surface-sunken);font-weight:620;' +
            'text-transform:uppercase;font-size:.72rem;letter-spacing:.05em;' +
            'color:var(--medium-gray)">' + UI.esc(area) + "</td></tr>" +
          groups[area].map(function (p) {
            return "<tr><td>" + UI.esc(p.label) +
              '<div class="card__hint mono">' + UI.esc(p.value) + "</div></td>" +
              data.roles.map(function (r) {
                var granted = (r.granted || []).indexOf(p.value) !== -1;
                return '<td style="text-align:center;color:' +
                  (granted ? "var(--success)" : "var(--border-gray)") +
                  ';font-weight:700">' + (granted ? "✓" : "—") + "</td>";
              }).join("") + "</tr>";
          }).join("");
      }).join("");

      document.getElementById("roleCards").innerHTML = data.roles.map(function (r) {
        return '<div class="card"><div class="card__head"><h3>' + UI.esc(r.label) +
            "</h3></div><div class=\"card__body\">" +
          '<p class="card__hint">' + UI.esc(r.description || "") + "</p>" +
          '<p style="margin:.625rem 0 0"><strong>Vendor:</strong> ' +
            (r.requires_vendor
              ? "required — belongs to exactly one vendor"
              : r.code === "ADMIN"
                ? "optional — platform-wide when unset"
                : "not permitted — platform-wide role") + "</p>" +
          '<p style="margin:.375rem 0 0"><strong>' + (r.granted || []).length +
            "</strong> permissions granted.</p>" +
          "</div></div>";
      }).join("");
    } catch (err) {
      UI.handleError(err, "Could not load the permission matrix.");
      table.querySelector("tbody").innerHTML =
        '<tr><td>' + UI.emptyState("Unavailable", err.message || "") + "</td></tr>";
    }
  })();
})();
