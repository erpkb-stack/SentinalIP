(function () {
  "use strict";

  var body = document.getElementById("policyBody");
  var vendors = [];
  var COLS = 8;

  function render(items) {
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No SLA policies", "Create one to set decision and resolution windows.") +
        "</td></tr>";
      return;
    }
    body.innerHTML = items.map(function (p) {
      return "<tr>" +
        "<td><strong>" + UI.esc(p.name) + "</strong>" +
          (p.description ? '<div class="card__hint">' + UI.esc(p.description) + "</div>" : "") + "</td>" +
        "<td>" + UI.esc(p.vendor_name || "Global") + "</td>" +
        '<td class="num">' + p.response_hours + " h</td>" +
        '<td class="num">' + p.resolution_hours + " h</td>" +
        '<td class="num">' + p.at_risk_percent + "%</td>" +
        "<td>" + UI.badge(p.priority) + "</td>" +
        "<td>" + (p.is_default ? UI.badge("APPROVED", "Default", "accent") : "—") + "</td>" +
        '<td><button class="btn btn--sm" data-edit="' + p.id + '">Edit</button></td>' +
      "</tr>";
    }).join("");
    body.querySelectorAll("[data-edit]").forEach(function (b) {
      b.addEventListener("click", function () {
        openForm(items.find(function (p) { return p.id === Number(b.dataset.edit); }));
      });
    });
  }

  function openForm(existing) {
    var isEdit = !!existing;
    var panel = UI.openModal({
      title: isEdit ? "Edit SLA policy" : "New SLA policy",
      wide: true,
      body: '<div class="form-grid">' +
        '<div class="field field--full"><label for="pName">Name<span class="req">*</span></label>' +
          '<input class="input" id="pName"></div>' +
        '<div class="field field--full"><label for="pDesc">Description</label>' +
          '<input class="input" id="pDesc"></div>' +
        '<div class="field"><label for="pResp">Decision window (hours)</label>' +
          '<input class="input" type="number" id="pResp" min="1" max="8760" value="48">' +
          "<small>Time to a recorded human decision.</small></div>" +
        '<div class="field"><label for="pRes">Resolution window (hours)</label>' +
          '<input class="input" type="number" id="pRes" min="1" max="8760" value="168">' +
          "<small>Time allowed for the marketplace to respond.</small></div>" +
        '<div class="field"><label for="pRisk">At-risk threshold (%)</label>' +
          '<input class="input" type="number" id="pRisk" min="10" max="99" value="75"></div>' +
        '<div class="field"><label for="pPriority">Priority</label>' +
          '<select class="select" id="pPriority">' +
            '<option value="LOW">Low</option><option value="MEDIUM" selected>Medium</option>' +
            '<option value="HIGH">High</option><option value="CRITICAL">Critical</option>' +
          "</select></div>" +
        '<div class="field"><label for="pVendor">Scope</label>' +
          '<select class="select" id="pVendor"><option value="">Global (all vendors)</option>' +
            vendors.map(function (v) {
              return '<option value="' + v.id + '">' + UI.esc(v.name) + "</option>";
            }).join("") + "</select></div>" +
        '<div class="field"><label>Options</label>' +
          '<label class="checkbox"><input type="checkbox" id="pDefault"> Default for this scope</label>' +
          '<label class="checkbox"><input type="checkbox" id="pActive" checked> Active</label></div>' +
      "</div>",
      footer: '<button class="btn" data-modal-close>Cancel</button>' +
              '<button class="btn btn--primary" id="policySubmit">' +
              (isEdit ? "Save changes" : "Create policy") + "</button>",
    });

    if (isEdit) {
      panel.querySelector("#pName").value = existing.name;
      panel.querySelector("#pDesc").value = existing.description || "";
      panel.querySelector("#pResp").value = existing.response_hours;
      panel.querySelector("#pRes").value = existing.resolution_hours;
      panel.querySelector("#pRisk").value = existing.at_risk_percent;
      panel.querySelector("#pPriority").value = existing.priority;
      panel.querySelector("#pVendor").value = existing.vendor_id || "";
      panel.querySelector("#pDefault").checked = existing.is_default;
      panel.querySelector("#pActive").checked = existing.is_active;
    }

    panel.querySelector("#policySubmit").addEventListener("click", async function () {
      var btn = panel.querySelector("#policySubmit");
      var payload = {
        name: panel.querySelector("#pName").value.trim(),
        description: panel.querySelector("#pDesc").value.trim() || null,
        response_hours: Number(panel.querySelector("#pResp").value),
        resolution_hours: Number(panel.querySelector("#pRes").value),
        at_risk_percent: Number(panel.querySelector("#pRisk").value),
        priority: panel.querySelector("#pPriority").value,
        vendor_id: panel.querySelector("#pVendor").value
          ? Number(panel.querySelector("#pVendor").value) : null,
        is_default: panel.querySelector("#pDefault").checked,
        is_active: panel.querySelector("#pActive").checked,
      };
      if (!payload.name) { UI.toast("Enter a policy name.", "warning"); return; }

      UI.setLoading(btn, true, "Saving");
      try {
        if (isEdit) {
          await Api.put("/api/admin/sla-policies/" + existing.id, payload);
          UI.toast.success("SLA policy updated.");
        } else {
          await Api.post("/api/admin/sla-policies", payload);
          UI.toast.success("SLA policy created.");
        }
        UI.closeModal();
        load();
      } catch (err) {
        UI.setLoading(btn, false);
        UI.handleError(err, "The policy could not be saved.");
      }
    });
  }

  async function load() {
    body.innerHTML = UI.skeletonRows(4, COLS);
    try {
      render(await Api.slaPolicies());
    } catch (err) {
      UI.handleError(err, "Could not load SLA policies.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Unavailable", err.message || "") + "</td></tr>";
    }
  }

  document.getElementById("createPolicyBtn").addEventListener("click", function () {
    openForm();
  });

  (async function bootstrap() {
    try { vendors = await Api.vendorOptions(); } catch (e) { vendors = []; }
    load();
  })();
})();
