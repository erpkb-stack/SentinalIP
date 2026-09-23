(function () {
  "use strict";

  var form = document.getElementById("loginForm");
  var btn = document.getElementById("loginBtn");
  var errBox = document.getElementById("loginError");
  var errText = document.getElementById("loginErrorText");

  function safeNext() {
    var raw = form.dataset.next || "/dashboard";
    // Only same-origin absolute paths; never "//evil.com" or a full URL.
    return /^\/(?!\/)/.test(raw) ? raw : "/dashboard";
  }

  function clearErrors() {
    errBox.hidden = true;
    form.querySelectorAll(".field").forEach(function (f) {
      f.classList.remove("has-error");
      var e = f.querySelector(".field__error");
      if (e) { e.hidden = true; e.textContent = ""; }
    });
  }

  function showFieldError(name, message) {
    var field = form.querySelector('.field[data-field="' + name + '"]');
    if (!field) return;
    field.classList.add("has-error");
    var e = field.querySelector(".field__error");
    e.textContent = message;
    e.hidden = false;
  }

  document.querySelectorAll("[data-demo]").forEach(function (b) {
    b.addEventListener("click", function () {
      document.getElementById("email").value = b.dataset.demo;
      document.getElementById("password").focus();
    });
  });

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearErrors();

    var email = document.getElementById("email").value.trim();
    var password = document.getElementById("password").value;
    var ok = true;
    if (!email) { showFieldError("email", "Enter your email address."); ok = false; }
    if (!password) { showFieldError("password", "Enter your password."); ok = false; }
    if (!ok) return;

    UI.setLoading(btn, true, "Signing in");
    try {
      await Api.post("/api/auth/login", {
        email: email,
        password: password,
        remember_me: document.getElementById("remember").checked,
      });
      location.href = safeNext();
    } catch (err) {
      UI.setLoading(btn, false);
      var fields = Api.fieldErrors(err);
      var handled = false;
      Object.keys(fields).forEach(function (k) {
        showFieldError(k, fields[k]);
        handled = true;
      });
      if (!handled) {
        errText.textContent = err.message || "Sign-in failed.";
        errBox.hidden = false;
      }
      if (err.code === "RATE_LIMITED" || err.code === "ACCOUNT_LOCKED") {
        UI.toast(err.message, "warning");
      }
    }
  });
})();
