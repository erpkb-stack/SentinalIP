(function () {
  "use strict";

  var tiers = [];
  var me = null;

  function tierCard(t) {
    return '<div class="card">' +
      '<div class="card__head"><h3>' + UI.esc(t.label) + "</h3>" +
        '<div class="card__actions">' +
          (t.is_available
            ? UI.badge("ACTIVE", "Enabled", "success")
            : UI.badge("INACTIVE", "Reserved", "neutral")) +
        "</div></div>" +
      '<div class="card__body">' +
        '<p class="card__hint">' + UI.esc(t.description) + "</p>" +
        '<p style="margin:.75rem 0 0"><strong>' + UI.fmtNumber(t.vendor_count) +
          "</strong> vendor(s) at this tier.</p>" +
        '<p style="margin:.25rem 0 0">Human approval: <strong>' +
          (t.requires_human_approval ? "required" : "not required") + "</strong></p>" +
      "</div></div>";
  }

  async function loadVendors() {
    var body = document.getElementById("vendorTierBody");
    body.innerHTML = UI.skeletonRows(4, 4);
    try {
      var page = await Api.vendors({ page_size: 200 });
      if (!page.items.length) {
        body.innerHTML = '<tr><td colspan="4">' +
          UI.emptyState("No vendors", "Create a vendor first.") + "</td></tr>";
        return;
      }
      body.innerHTML = page.items.map(function (v) {
        return "<tr>" +
          "<td><strong>" + UI.esc(v.name) + "</strong>" +
            '<div class="card__hint">' + UI.esc(v.status) + "</div></td>" +
          '<td><span class="pill-tier">' +
            UI.esc(v.autonomy_label || ("Tier " + v.autonomy_tier)) + "</span></td>" +
          '<td><select class="select" data-vendor="' + v.id + '" style="max-width:280px">' +
            tiers.map(function (t) {
              return '<option value="' + t.tier + '"' +
                (t.tier === v.autonomy_tier ? " selected" : "") +
                (t.is_available ? "" : " disabled") + ">" + UI.esc(t.label) +
                (t.is_available ? "" : " — reserved") + "</option>";
            }).join("") + "</select></td>" +
          '<td><button class="btn btn--sm" data-apply="' + v.id + '">Apply</button></td>' +
        "</tr>";
      }).join("");

      body.querySelectorAll("[data-apply]").forEach(function (btn) {
        btn.addEventListener("click", async function () {
          var id = btn.dataset.apply;
          var select = body.querySelector('[data-vendor="' + id + '"]');
          var tier = Number(select.value);
          var tierMeta = tiers.find(function (t) { return t.tier === tier; });
          var ok = await UI.confirm({
            title: "Change autonomy tier?",
            message: "This vendor moves to " + (tierMeta ? tierMeta.label : "Tier " + tier) + ".",
            detail: "Legal and enforcement filings still require a recorded human approval " +
                    "at every tier. The change is written to the audit trail.",
            confirmLabel: "Change tier", tone: "primary",
          });
          if (!ok) return;
          UI.setLoading(btn, true, "Applying");
          try {
            var res = await Api.put("/api/admin/autonomy/vendors/" + id + "?tier=" + tier);
            UI.toast.success(res.message || "Autonomy tier updated.");
            await loadVendors();
            await loadTiers();
          } catch (err) {
            UI.setLoading(btn, false);
            UI.handleError(err, "Could not change the tier.");
          }
        });
      });
    } catch (err) {
      body.innerHTML = '<tr><td colspan="4">' +
        UI.emptyState("Vendors unavailable", err.message || "") + "</td></tr>";
    }
  }

  async function loadTiers() {
    try {
      tiers = await Api.autonomy();
      document.getElementById("tierCards").innerHTML = tiers.map(tierCard).join("");
    } catch (err) { UI.handleError(err, "Could not load autonomy tiers."); }
  }

  (async function bootstrap() {
    try {
      me = await Api.me();
    } catch (e) {
      if (e.code !== "NAVIGATION_ABORTED" && e.status !== 401) {
        UI.toast("Could not confirm your permissions; some actions may be "
                 + "limited until you reload.", "warning", 8000);
      }
      me = null;
    }
    await loadTiers();
    try {
      var policy = await Api.autonomyPolicy();
      document.getElementById("autonomyIntro").textContent =
        "Default tier for new vendors: " + policy.default_tier +
        " · highest tier enabled on this deployment: " + policy.max_enabled_tier;
      document.getElementById("autonomyStatement").innerHTML =
        "<strong>" + UI.esc(policy.statement) + "</strong>";
      document.getElementById("legalActions").innerHTML =
        policy.always_requires_human_approval.map(function (a) {
          return '<li><span class="marker">!</span><span>' +
            UI.esc(UI.titleize(a)) + "</span></li>";
        }).join("");
      document.getElementById("autoActions").innerHTML =
        policy.tier3_automatable.map(function (a) {
          return '<li><span class="marker">•</span><span>' +
            UI.esc(UI.titleize(a)) + "</span></li>";
        }).join("");
    } catch (err) { /* non-critical */ }
    await loadVendors();
  })();
})();
