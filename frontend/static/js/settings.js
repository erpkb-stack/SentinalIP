(function () {
  "use strict";

  var form = document.getElementById("passwordForm");
  var btn = document.getElementById("passwordBtn");

  function setError(name, message) {
    var f = form.querySelector('.field[data-field="' + name + '"]');
    if (!f) return;
    f.classList.add("has-error");
    var e = f.querySelector(".field__error");
    e.textContent = message; e.hidden = false;
  }
  function clearErrors() {
    form.querySelectorAll(".field").forEach(function (f) {
      f.classList.remove("has-error");
      var e = f.querySelector(".field__error");
      if (e) { e.hidden = true; e.textContent = ""; }
    });
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearErrors();
    var current = document.getElementById("currentPassword").value;
    var next = document.getElementById("newPassword").value;
    var confirm = document.getElementById("confirmPassword").value;

    var ok = true;
    if (!current) { setError("current_password", "Enter your current password."); ok = false; }
    if (!next) { setError("new_password", "Enter a new password."); ok = false; }
    if (next && next !== confirm) { setError("confirm", "The passwords do not match."); ok = false; }
    if (!ok) return;

    UI.setLoading(btn, true, "Updating");
    try {
      await Api.post("/api/auth/change-password", {
        current_password: current, new_password: next,
      });
      UI.setLoading(btn, false);
      form.reset();
      UI.toast.success("Password updated. Other sessions were signed out.");
    } catch (err) {
      UI.setLoading(btn, false);
      var fields = Api.fieldErrors(err);
      var shown = false;
      Object.keys(fields).forEach(function (k) { setError(k, fields[k]); shown = true; });
      if (!shown) setError("new_password", err.message || "The password could not be changed.");
    }
  });

  (async function load() {
    try {
      var me = await Api.me();
      document.getElementById("accountPanel").innerHTML =
        [["Name", me.full_name], ["Email", me.email], ["Role", me.role_label],
         ["Vendor", me.vendor_name || "Platform-wide (no vendor)"],
         ["Autonomy tier", me.autonomy_tier != null ? "Tier " + me.autonomy_tier : "—"],
         ["Scope", me.is_global ? "All vendors" : "This vendor only"],
         ["Last sign-in", me.last_login_at ? UI.fmtDateTime(me.last_login_at) : "—"]]
        .map(function (r) {
          return "<div><dt>" + UI.esc(r[0]) + "</dt><dd>" + UI.esc(r[1]) + "</dd></div>";
        }).join("");

      document.getElementById("permissionPanel").innerHTML =
        '<div style="display:flex;flex-wrap:wrap;gap:.375rem">' +
          (me.permissions || []).map(function (p) {
            return '<span class="badge badge--neutral">' + UI.esc(p) + "</span>";
          }).join("") + "</div>" +
        '<p class="card__hint" style="margin-top:.75rem">' +
          "These are checked on every API call, not only in the interface.</p>";

      if (me.must_change_password) {
        UI.toast("Your password is temporary. Please change it now.", "warning", 9000);
        document.getElementById("currentPassword").focus();
      }
    } catch (e) { /* the API client handles the redirect */ }

    try {
      var meta = await Api.meta();
      document.getElementById("platformPanel").innerHTML =
        [["Application", meta.app.name], ["Tagline", meta.app.tagline],
         ["Environment", meta.app.environment],
         ["Filing mode", meta.app.simulated_filing
           ? "DEMO / SIMULATED — no external system is contacted"
           : "Live"],
         ["Max upload", meta.upload.max_mb + " MB"],
         ["Accepted files", meta.upload.extensions.join(", ")]]
        .map(function (r) {
          return "<div><dt>" + UI.esc(r[0]) + "</dt><dd>" + UI.esc(r[1]) + "</dd></div>";
        }).join("");
    } catch (e) { /* optional */ }
  })();
})();
