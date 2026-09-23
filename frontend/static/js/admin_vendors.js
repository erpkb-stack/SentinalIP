(function () {
  "use strict";

  var form = document.getElementById("vendorFilters");
  var body = document.getElementById("vendorsBody");
  var pager = document.getElementById("vendorsPager");
  var state = { page: 1, page_size: 25 };
  var COLS = 9;
  var policies = [];
  var tiers = [];
  var me = null;

  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No vendors yet",
          "Create a vendor to start onboarding a brand.",
          '<button class="btn btn--primary" id="emptyCreateVendor">Create Vendor</button>') +
        "</td></tr>";
      var b = document.getElementById("emptyCreateVendor");
      if (b) b.addEventListener("click", function () { openVendorForm(); });
      pager.innerHTML = "";
      return;
    }
    body.innerHTML = page.items.map(function (v) {
      return "<tr>" +
        "<td><strong>" + UI.esc(v.name) + "</strong>" +
          (v.legal_name ? '<div class="card__hint">' + UI.esc(v.legal_name) + "</div>" : "") +
          (v.industry ? '<div class="card__hint">' + UI.esc(v.industry) + "</div>" : "") + "</td>" +
        "<td>" + UI.esc(v.contact_name || "—") +
          (v.contact_email ? '<div class="card__hint truncate">' +
            UI.esc(v.contact_email) + "</div>" : "") + "</td>" +
        '<td class="num">' + UI.fmtNumber(v.user_count) + "</td>" +
        '<td class="num">' + UI.fmtNumber(v.open_case_count) + "</td>" +
        '<td><span class="pill-tier">Tier ' + v.autonomy_tier + "</span></td>" +
        "<td>" + UI.esc(v.sla_policy_name || "Global default") + "</td>" +
        "<td>" + UI.badge(v.status) + "</td>" +
        '<td class="nowrap card__hint">' + UI.esc(UI.fmtDate(v.created_at)) + "</td>" +
        '<td class="nowrap">' +
          '<button class="btn btn--sm" data-view="' + v.id + '">View</button> ' +
          '<button class="btn btn--sm" data-edit="' + v.id + '">Edit</button> ' +
          (v.status !== "INACTIVE"
            ? '<button class="btn btn--sm" data-disable="' + v.id + '">Disable</button>' : "") +
        "</td>" +
      "</tr>";
    }).join("");

    function find(id) { return page.items.find(function (v) { return v.id === Number(id); }); }
    body.querySelectorAll("[data-view]").forEach(function (b) {
      b.addEventListener("click", function () { showVendor(find(b.dataset.view)); });
    });
    body.querySelectorAll("[data-edit]").forEach(function (b) {
      b.addEventListener("click", function () { openVendorForm(find(b.dataset.edit)); });
    });
    body.querySelectorAll("[data-disable]").forEach(function (b) {
      b.addEventListener("click", function () { disableVendor(find(b.dataset.disable)); });
    });

    document.getElementById("vendorCount").textContent =
      UI.fmtNumber(page.total) + " vendor" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () { state.page = Number(b.dataset.page); load(); });
    });
  }

  function showVendor(v) {
    UI.openModal({
      title: v.name,
      body: '<dl class="dl">' +
        [["Legal name", v.legal_name], ["Contact", v.contact_name],
         ["Contact email", v.contact_email], ["Phone", v.phone],
         ["Country", v.country], ["Industry", v.industry],
         ["Website", v.website], ["SLA policy", v.sla_policy_name || "Global default"],
         ["Autonomy", v.autonomy_label || ("Tier " + v.autonomy_tier)],
         ["Users", UI.fmtNumber(v.user_count)],
         ["Open cases", UI.fmtNumber(v.open_case_count)],
         ["Created", UI.fmtDateTime(v.created_at)]]
        .filter(function (r) { return r[1]; })
        .map(function (r) {
          return "<div><dt>" + UI.esc(r[0]) + "</dt><dd>" + UI.esc(r[1]) + "</dd></div>";
        }).join("") + "</dl>",
      footer: '<a class="btn" href="/cases?vendor_id=' + v.id + '">View cases</a>' +
              '<button class="btn btn--primary" data-modal-close>Close</button>',
    });
  }

  async function disableVendor(v) {
    var ok = await UI.confirm({
      title: "Disable " + v.name + "?",
      message: "This will prevent all users associated with this vendor from accessing " +
               "Sentinel IP AI.",
      detail: v.user_count + " user account(s) and " + v.open_case_count +
              " open case(s) are affected. Cases and audit records are preserved.",
      confirmLabel: "Disable Vendor",
    });
    if (!ok) return;
    try {
      var res = await Api.del("/api/vendors/" + v.id);
      UI.toast.success(res.message || "Vendor disabled.");
      load();
    } catch (err) { UI.handleError(err, "Could not disable the vendor."); }
  }

  function openVendorForm(existing) {
    var isEdit = !!existing;
    var maxTier = 3;
    var panel = UI.openModal({
      title: isEdit ? "Edit vendor" : "Create vendor",
      wide: true,
      body: '<form id="vendorForm" novalidate><div class="form-grid">' +
        '<div class="field" data-field="name"><label for="vName">Company Name<span class="req">*</span></label>' +
          '<input class="input" id="vName" required><div class="field__error" hidden></div></div>' +
        '<div class="field" data-field="legal_name"><label for="vLegal">Legal Name</label>' +
          '<input class="input" id="vLegal"></div>' +
        '<div class="field" data-field="contact_name"><label for="vContact">Contact Name</label>' +
          '<input class="input" id="vContact"></div>' +
        '<div class="field" data-field="contact_email"><label for="vEmail">Contact Email</label>' +
          '<input class="input" type="email" id="vEmail"><div class="field__error" hidden></div></div>' +
        '<div class="field" data-field="phone"><label for="vPhone">Phone</label>' +
          '<input class="input" id="vPhone"></div>' +
        '<div class="field" data-field="country"><label for="vCountry">Country</label>' +
          '<input class="input" id="vCountry"></div>' +
        '<div class="field" data-field="website"><label for="vWebsite">Website</label>' +
          '<input class="input" id="vWebsite" placeholder="https://…"></div>' +
        '<div class="field" data-field="industry"><label for="vIndustry">Industry</label>' +
          '<input class="input" id="vIndustry"></div>' +
        '<div class="field" data-field="sla_policy_id"><label for="vSla">SLA Policy</label>' +
          '<select class="select" id="vSla"><option value="">Global default</option>' +
            policies.filter(function (p) { return !p.vendor_id; }).map(function (p) {
              return '<option value="' + p.id + '">' + UI.esc(p.name) + " · " +
                p.response_hours + "h</option>";
            }).join("") + "</select></div>" +
        '<div class="field" data-field="autonomy_tier"><label for="vTier">Autonomy Tier</label>' +
          '<select class="select" id="vTier">' +
            tiers.map(function (t) {
              return '<option value="' + t.tier + '"' +
                (t.is_available ? "" : " disabled") + '>' + UI.esc(t.label) +
                (t.is_available ? "" : " — not enabled") + "</option>";
            }).join("") + "</select>" +
          "<small>New vendors default to Tier 1. Human approval is required at every tier.</small></div>" +
        '<div class="field" data-field="status"><label for="vStatus">Status</label>' +
          '<select class="select" id="vStatus">' +
            '<option value="ACTIVE">Active</option>' +
            '<option value="SUSPENDED">Suspended</option>' +
            '<option value="INACTIVE">Inactive</option></select></div>' +
        '<div class="field field--full"><label for="vNotes">Notes</label>' +
          '<textarea class="textarea" id="vNotes"></textarea></div>' +
      "</div></form>",
      footer: '<button class="btn" data-modal-close>Cancel</button>' +
              '<button class="btn btn--primary" id="vendorSubmit">' +
              (isEdit ? "Save changes" : "Create Vendor") + "</button>",
    });

    if (isEdit) {
      panel.querySelector("#vName").value = existing.name || "";
      panel.querySelector("#vLegal").value = existing.legal_name || "";
      panel.querySelector("#vContact").value = existing.contact_name || "";
      panel.querySelector("#vEmail").value = existing.contact_email || "";
      panel.querySelector("#vPhone").value = existing.phone || "";
      panel.querySelector("#vCountry").value = existing.country || "";
      panel.querySelector("#vWebsite").value = existing.website || "";
      panel.querySelector("#vIndustry").value = existing.industry || "";
      panel.querySelector("#vSla").value = existing.sla_policy_id || "";
      panel.querySelector("#vTier").value = existing.autonomy_tier;
      panel.querySelector("#vStatus").value = existing.status;
    } else {
      panel.querySelector("#vTier").value = "1";
    }

    panel.querySelector("#vendorSubmit").addEventListener("click", async function () {
      var btn = panel.querySelector("#vendorSubmit");
      panel.querySelectorAll(".field").forEach(function (f) {
        f.classList.remove("has-error");
        var e = f.querySelector(".field__error");
        if (e) { e.hidden = true; e.textContent = ""; }
      });

      var payload = {
        name: panel.querySelector("#vName").value.trim(),
        legal_name: panel.querySelector("#vLegal").value.trim() || null,
        contact_name: panel.querySelector("#vContact").value.trim() || null,
        contact_email: panel.querySelector("#vEmail").value.trim() || null,
        phone: panel.querySelector("#vPhone").value.trim() || null,
        country: panel.querySelector("#vCountry").value.trim() || null,
        website: panel.querySelector("#vWebsite").value.trim() || null,
        industry: panel.querySelector("#vIndustry").value.trim() || null,
        sla_policy_id: panel.querySelector("#vSla").value
          ? Number(panel.querySelector("#vSla").value) : null,
        autonomy_tier: Number(panel.querySelector("#vTier").value),
        status: panel.querySelector("#vStatus").value,
        notes: panel.querySelector("#vNotes").value.trim() || null,
      };
      if (!payload.name) {
        var nf = panel.querySelector('[data-field="name"]');
        nf.classList.add("has-error");
        var ne = nf.querySelector(".field__error");
        ne.textContent = "Enter the company name."; ne.hidden = false;
        return;
      }

      UI.setLoading(btn, true, "Saving");
      try {
        if (isEdit) {
          await Api.put("/api/vendors/" + existing.id, payload);
          UI.toast.success("Vendor updated.");
        } else {
          await Api.post("/api/vendors", payload);
          UI.toast.success("Vendor created successfully.");
        }
        UI.closeModal();
        load();
      } catch (err) {
        UI.setLoading(btn, false);
        var fields = Api.fieldErrors(err);
        var shown = false;
        Object.keys(fields).forEach(function (k) {
          var fl = panel.querySelector('[data-field="' + k + '"]');
          if (fl) {
            fl.classList.add("has-error");
            var e = fl.querySelector(".field__error");
            if (e) { e.textContent = fields[k]; e.hidden = false; shown = true; }
          }
        });
        if (!shown) UI.handleError(err, "The vendor could not be saved.");
      }
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(5, COLS);
    var data = new FormData(form);
    var params = { page: state.page, page_size: state.page_size };
    data.forEach(function (v, k) { if (v) params[k] = v; });
    try {
      render(await Api.vendors(params));
    } catch (err) {
      UI.handleError(err, "Could not load vendors.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Vendors unavailable", err.message || "") + "</td></tr>";
    }
  }

  form.addEventListener("change", function () { state.page = 1; load(); });
  form.addEventListener("input", UI.debounce(function (e) {
    if (e.target.type === "search") { state.page = 1; load(); }
  }, 350));
  document.getElementById("createVendorBtn").addEventListener("click", function () {
    openVendorForm();
  });

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
    // If we could not confirm the role, hide the action; the API enforces it anyway.
    if (!me || !me.is_global) {
      document.getElementById("createVendorBtn").hidden = true;
    }
    try { policies = await Api.slaPolicies(); } catch (e) { policies = []; }
    try { tiers = await Api.autonomy(); } catch (e) {
      tiers = [{ tier: 1, label: "Tier 1 - AI Recommendation", is_available: true }];
    }
    load();
  })();
})();
