(function () {
  "use strict";

  var form = document.getElementById("userFilters");
  var body = document.getElementById("usersBody");
  var pager = document.getElementById("usersPager");
  var state = { page: 1, page_size: 25 };
  var COLS = 7;

  var roles = [];
  var vendors = [];
  var me = null;

  /* ---------------- table ---------------- */
  function render(page) {
    if (!page.items.length) {
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("No users match", "Adjust the filters or create a user.",
          '<button class="btn btn--primary" id="emptyCreate">Create User</button>') +
        "</td></tr>";
      var b = document.getElementById("emptyCreate");
      if (b) b.addEventListener("click", function () { openUserForm(); });
      pager.innerHTML = "";
      return;
    }
    body.innerHTML = page.items.map(function (u) {
      return "<tr>" +
        "<td><strong>" + UI.esc(u.full_name) + "</strong>" +
          (u.title ? '<div class="card__hint">' + UI.esc(u.title) + "</div>" : "") + "</td>" +
        '<td class="truncate">' + UI.esc(u.email) + "</td>" +
        "<td>" + UI.badge(u.role_code === "SUPER_ADMIN" ? "CRITICAL" :
                          u.role_code === "ADMIN" ? "HIGH" : "MEDIUM",
                          u.role_label,
                          u.role_code === "SUPER_ADMIN" ? "danger" :
                          u.role_code === "ADMIN" ? "accent" : "neutral") + "</td>" +
        "<td>" + UI.esc(u.vendor_name || "— platform-wide") + "</td>" +
        "<td>" + UI.badge(u.status) + "</td>" +
        '<td class="nowrap card__hint">' +
          UI.esc(u.last_login_at ? UI.fmtRelative(u.last_login_at) : "Never") + "</td>" +
        '<td class="nowrap">' +
          '<button class="btn btn--sm" data-edit="' + u.id + '">Edit</button> ' +
          (u.status === "ACTIVE"
            ? '<button class="btn btn--sm" data-disable="' + u.id + '">Disable</button>'
            : '<button class="btn btn--sm" data-enable="' + u.id + '">Enable</button>') +
        "</td>" +
      "</tr>";
    }).join("");

    body.querySelectorAll("[data-edit]").forEach(function (b) {
      b.addEventListener("click", function () {
        openUserForm(page.items.find(function (u) { return u.id === Number(b.dataset.edit); }));
      });
    });
    body.querySelectorAll("[data-disable]").forEach(function (b) {
      b.addEventListener("click", function () {
        var u = page.items.find(function (x) { return x.id === Number(b.dataset.disable); });
        toggleStatus(u, "DISABLED");
      });
    });
    body.querySelectorAll("[data-enable]").forEach(function (b) {
      b.addEventListener("click", function () {
        var u = page.items.find(function (x) { return x.id === Number(b.dataset.enable); });
        toggleStatus(u, "ACTIVE");
      });
    });

    document.getElementById("userCount").textContent =
      UI.fmtNumber(page.total) + " user" + (page.total === 1 ? "" : "s");
    pager.innerHTML = UI.paginationHtml(page);
    pager.querySelectorAll("[data-page]").forEach(function (b) {
      b.addEventListener("click", function () { state.page = Number(b.dataset.page); load(); });
    });
  }

  async function toggleStatus(user, status) {
    if (status === "DISABLED") {
      var ok = await UI.confirm({
        title: "Disable this account?",
        message: UI.esc(user.email) + " will be signed out immediately and cannot sign in again.",
        detail: "Their case history and audit records are preserved.",
        confirmLabel: "Disable user",
      });
      if (!ok) return;
    }
    try {
      await Api.put("/api/users/" + user.id, { status: status });
      UI.toast.success(status === "ACTIVE" ? "User enabled." : "User disabled.");
      load();
    } catch (err) { UI.handleError(err, "Could not update the user."); }
  }

  /* ---------------- create / edit ---------------- */
  function openUserForm(existing) {
    var isEdit = !!existing;
    var assignableRoles = roles.filter(function (r) {
      if (r.code === "SUPER_ADMIN" && !(me && me.role_code === "SUPER_ADMIN")) return false;
      if (r.code === "ADMIN" && !(me && me.role_code === "SUPER_ADMIN")) return false;
      return true;
    });

    var panel = UI.openModal({
      title: isEdit ? "Edit user" : "Create user",
      wide: true,
      body:
        '<form id="userForm" novalidate><div class="form-grid">' +
          '<div class="field" data-field="first_name"><label for="uFirst">First Name<span class="req">*</span></label>' +
            '<input class="input" id="uFirst" name="first_name" required></div>' +
          '<div class="field" data-field="last_name"><label for="uLast">Last Name<span class="req">*</span></label>' +
            '<input class="input" id="uLast" name="last_name" required></div>' +
          '<div class="field field--full" data-field="email"><label for="uEmail">Email<span class="req">*</span></label>' +
            '<input class="input" type="email" id="uEmail" name="email" required>' +
            '<div class="field__error" hidden></div></div>' +
          '<div class="field" data-field="role_code"><label for="uRole">Role<span class="req">*</span></label>' +
            '<select class="select" id="uRole" name="role_code">' +
              assignableRoles.map(function (r) {
                return '<option value="' + UI.esc(r.code) + '">' + UI.esc(r.name) + "</option>";
              }).join("") +
            "</select>" +
            '<small id="roleHint"></small></div>' +
          '<div class="field" data-field="vendor_id"><label for="uVendor">Vendor<span class="req" id="vendorReq">*</span></label>' +
            '<select class="select" id="uVendor" name="vendor_id">' +
              '<option value="">Select Vendor</option>' +
              vendors.map(function (v) {
                return '<option value="' + v.id + '">' + UI.esc(v.name) + "</option>";
              }).join("") +
            "</select>" +
            '<small id="vendorHint"></small>' +
            '<div class="field__error" hidden></div></div>' +
          '<div class="field" data-field="title"><label for="uTitle">Job Title</label>' +
            '<input class="input" id="uTitle" name="title"></div>' +
          '<div class="field" data-field="status"><label for="uStatus">Status</label>' +
            '<select class="select" id="uStatus" name="status">' +
              '<option value="ACTIVE">Active</option>' +
              '<option value="PENDING">Pending</option>' +
              '<option value="DISABLED">Disabled</option>' +
            "</select></div>" +
          '<div class="field field--full" data-field="password">' +
            '<label for="uPassword">' + (isEdit ? "New password" : "Temporary password") + "</label>" +
            '<input class="input" type="password" id="uPassword" name="password" ' +
              'autocomplete="new-password" placeholder="Leave blank to generate one">' +
            "<small>Minimum 12 characters with upper, lower, number and symbol.</small>" +
            '<div class="field__error" hidden></div></div>' +
        "</div></form>",
      footer: '<button class="btn" data-modal-close>Cancel</button>' +
              '<button class="btn btn--primary" id="userSubmit">' +
              (isEdit ? "Save changes" : "Create user") + "</button>",
    });

    var roleSelect = panel.querySelector("#uRole");
    var vendorSelect = panel.querySelector("#uVendor");
    var vendorReq = panel.querySelector("#vendorReq");
    var vendorHint = panel.querySelector("#vendorHint");
    var roleHint = panel.querySelector("#roleHint");

    function syncVendorField() {
      var role = roleSelect.value;
      if (role === "SUPER_ADMIN") {
        vendorSelect.value = "";
        vendorSelect.disabled = true;
        vendorReq.hidden = true;
        vendorHint.textContent = "Super Admins are platform-wide; no vendor is required.";
        roleHint.textContent = "Sees every vendor, every case and all configuration.";
      } else if (role === "ADMIN") {
        vendorSelect.disabled = false;
        vendorReq.hidden = true;
        vendorHint.textContent =
          "Optional. Leave blank for a platform administrator; choose a vendor to " +
          "scope this admin to that tenant only.";
        roleHint.textContent = "Manages users and vendors.";
      } else {
        vendorSelect.disabled = false;
        vendorReq.hidden = false;
        vendorHint.textContent = "Required. A Vendor User belongs to exactly one vendor.";
        roleHint.textContent = "Sees only their own vendor's cases.";
      }
    }
    roleSelect.addEventListener("change", syncVendorField);

    if (isEdit) {
      panel.querySelector("#uFirst").value = existing.first_name;
      panel.querySelector("#uLast").value = existing.last_name;
      panel.querySelector("#uEmail").value = existing.email;
      panel.querySelector("#uTitle").value = existing.title || "";
      panel.querySelector("#uStatus").value = existing.status;
      roleSelect.value = existing.role_code;
      syncVendorField();
      vendorSelect.value = existing.vendor_id || "";
    } else {
      roleSelect.value = "VENDOR_USER";
      syncVendorField();
    }

    panel.querySelector("#userSubmit").addEventListener("click", async function () {
      var btn = panel.querySelector("#userSubmit");
      var f = panel.querySelector("#userForm");
      f.querySelectorAll(".field").forEach(function (fl) {
        fl.classList.remove("has-error");
        var e = fl.querySelector(".field__error");
        if (e) { e.hidden = true; e.textContent = ""; }
      });

      var payload = {
        first_name: panel.querySelector("#uFirst").value.trim(),
        last_name: panel.querySelector("#uLast").value.trim(),
        email: panel.querySelector("#uEmail").value.trim(),
        role_code: roleSelect.value,
        status: panel.querySelector("#uStatus").value,
        title: panel.querySelector("#uTitle").value.trim() || null,
      };
      var vendorValue = vendorSelect.value;
      payload.vendor_id = vendorValue ? Number(vendorValue) : null;
      var password = panel.querySelector("#uPassword").value;
      if (password) payload.password = password;

      if (payload.role_code === "VENDOR_USER" && !payload.vendor_id) {
        var vf = panel.querySelector('[data-field="vendor_id"]');
        vf.classList.add("has-error");
        var ve = vf.querySelector(".field__error");
        ve.textContent = "A Vendor User must be assigned to a vendor."; ve.hidden = false;
        return;
      }

      UI.setLoading(btn, true, "Saving");
      try {
        var res;
        if (isEdit) {
          res = await Api.put("/api/users/" + existing.id, payload);
          UI.toast.success("User updated.");
        } else {
          res = await Api.post("/api/users", payload);
          UI.toast.success("User created.");
          if (res.temporary_password) showTemporaryPassword(res);
        }
        if (!(res && res.temporary_password)) UI.closeModal();
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
        if (!shown) UI.handleError(err, "The user could not be saved.");
      }
    });
  }

  function showTemporaryPassword(res) {
    UI.openModal({
      title: "User created",
      body:
        "<p>" + UI.esc(res.user.email) + " was created as " +
          UI.esc(res.user.role_label) +
          (res.user.vendor_name ? " for " + UI.esc(res.user.vendor_name) : "") + ".</p>" +
        '<div class="banner banner--warning" style="margin:.875rem 0"><span>⚠</span>' +
          "<span>This temporary password is shown once. Share it over a secure channel; " +
          "the user must change it at first sign-in.</span></div>" +
        '<pre class="notice-draft" style="font-size:1rem">' +
          UI.esc(res.temporary_password) + "</pre>",
      footer: '<button class="btn btn--primary" data-modal-close>Done</button>',
    });
  }

  /* ---------------- load ---------------- */
  async function load() {
    body.innerHTML = UI.skeletonRows(6, COLS);
    var data = new FormData(form);
    var params = { page: state.page, page_size: state.page_size };
    data.forEach(function (v, k) { if (v) params[k] = v; });
    try {
      render(await Api.users(params));
    } catch (err) {
      UI.handleError(err, "Could not load users.");
      body.innerHTML = '<tr><td colspan="' + COLS + '">' +
        UI.emptyState("Users unavailable", err.message || "") + "</td></tr>";
    }
  }

  form.addEventListener("change", function () { state.page = 1; load(); });
  form.addEventListener("input", UI.debounce(function (e) {
    if (e.target.type === "search") { state.page = 1; load(); }
  }, 350));
  document.getElementById("createUserBtn").addEventListener("click", function () {
    openUserForm();
  });

  (async function bootstrap() {
    // Never abort the page on this one call - it only decides which roles
    // are offered and whether the vendor filter is shown.
    try {
      me = await Api.me();
    } catch (e) {
      if (e.code !== "NAVIGATION_ABORTED" && e.status !== 401) {
        UI.toast("Could not confirm your permissions; some options may be "
                 + "limited until you reload.", "warning", 8000);
      }
      me = null;
    }
    try { roles = await Api.roles(); } catch (e) { roles = []; }
    try { vendors = await Api.vendorOptions(); } catch (e) { vendors = []; }

    document.getElementById("role_code").innerHTML = '<option value="">Any</option>' +
      roles.map(function (r) {
        return '<option value="' + UI.esc(r.code) + '">' + UI.esc(r.name) + "</option>";
      }).join("");

    if (me && me.is_global) {
      document.getElementById("userVendorFilter").hidden = false;
      document.getElementById("vendor_id").innerHTML = '<option value="">All</option>' +
        vendors.map(function (v) {
          return '<option value="' + v.id + '">' + UI.esc(v.name) + "</option>";
        }).join("");
    }
    load();
  })();
})();
