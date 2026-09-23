(function () {
  "use strict";

  var form = document.getElementById("caseForm");
  var submitBtn = document.getElementById("submitBtn");
  var fileInput = document.getElementById("fileInput");
  var fileList = document.getElementById("fileList");
  var dropzone = document.getElementById("dropzone");
  var pending = [];
  var uploadLimits = { max_mb: 25, extensions: [] };

  /* ---------------- field errors ---------------- */
  function clearErrors() {
    form.querySelectorAll(".field").forEach(function (f) {
      f.classList.remove("has-error");
      var e = f.querySelector(".field__error");
      if (e) { e.hidden = true; e.textContent = ""; }
    });
  }
  function setError(name, message) {
    var field = form.querySelector('.field[data-field="' + name + '"]');
    if (!field) { UI.toast(message, "error"); return; }
    field.classList.add("has-error");
    var e = field.querySelector(".field__error");
    if (e) { e.textContent = message; e.hidden = false; }
  }

  /* ---------------- files ---------------- */
  function allowed(file) {
    var ext = "." + file.name.split(".").pop().toLowerCase();
    if (uploadLimits.extensions.length && uploadLimits.extensions.indexOf(ext) === -1) {
      UI.toast(file.name + ": " + ext + " files are not accepted.", "error");
      return false;
    }
    if (file.size > uploadLimits.max_mb * 1024 * 1024) {
      UI.toast(file.name + " is larger than " + uploadLimits.max_mb + " MB.", "error");
      return false;
    }
    return true;
  }

  function renderFiles() {
    fileList.innerHTML = pending.map(function (f, i) {
      return "<li><span>" + UI.esc(f.name) + "</span>" +
        '<span class="size">' + UI.esc(UI.fmtBytes(f.size)) + "</span>" +
        '<button type="button" class="btn btn--ghost btn--sm" data-remove="' + i +
        '" aria-label="Remove">✕</button></li>';
    }).join("");
    fileList.querySelectorAll("[data-remove]").forEach(function (b) {
      b.addEventListener("click", function () {
        pending.splice(Number(b.dataset.remove), 1);
        renderFiles();
      });
    });
  }

  function addFiles(files) {
    Array.prototype.forEach.call(files, function (f) {
      if (allowed(f)) pending.push(f);
    });
    renderFiles();
  }

  document.getElementById("browseBtn").addEventListener("click", function () {
    fileInput.click();
  });
  fileInput.addEventListener("change", function () { addFiles(fileInput.files); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault(); dropzone.classList.add("is-active");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    dropzone.addEventListener(evt, function (e) {
      e.preventDefault(); dropzone.classList.remove("is-active");
    });
  });
  dropzone.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  /* ---------------- submit ---------------- */
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearErrors();

    var data = new FormData(form);
    var payload = {};
    data.forEach(function (v, k) { if (String(v).trim() !== "") payload[k] = v; });

    ["msrp", "listing_price"].forEach(function (k) {
      if (payload[k] !== undefined) payload[k] = Number(payload[k]);
    });
    ["marketplace_id", "vendor_id"].forEach(function (k) {
      if (payload[k] !== undefined) payload[k] = Number(payload[k]);
    });
    if (payload.listing_date) payload.listing_date = payload.listing_date + "T00:00:00";
    payload.run_analysis = document.getElementById("run_analysis").checked;

    // Client-side pre-checks (the server validates again regardless).
    var ok = true;
    if (!payload.product_name) { setError("product_name", "Enter the product name."); ok = false; }
    if (!payload.listing_url) { setError("listing_url", "Enter the suspected listing URL."); ok = false; }
    if (!payload.infringement_type) { setError("infringement_type", "Choose an infringement type."); ok = false; }
    if (!payload.marketplace_id && !payload.marketplace_name) {
      setError("marketplace_id", "Select a marketplace or enter a new one."); ok = false;
    }
    var vendorField = document.getElementById("vendorField");
    if (!vendorField.hidden && !payload.vendor_id) {
      setError("vendor_id", "Select the vendor this case belongs to."); ok = false;
    }
    if (!ok) {
      UI.toast("Fix the highlighted fields.", "warning");
      form.querySelector(".has-error input, .has-error select").focus();
      return;
    }

    UI.setLoading(submitBtn, true, "Creating case");
    var created;
    try {
      created = await Api.createCase(payload);
    } catch (err) {
      UI.setLoading(submitBtn, false);
      var fields = Api.fieldErrors(err);
      var shown = false;
      Object.keys(fields).forEach(function (k) { setError(k, fields[k]); shown = true; });
      if (!shown) UI.handleError(err, "The case could not be created.");
      else UI.toast("Fix the highlighted fields.", "warning");
      return;
    }

    UI.toast.success("Case " + created.case_number + " created.");

    // Attach evidence, then a URL if one was given.
    if (pending.length) {
      UI.setLoading(submitBtn, true, "Uploading evidence");
      for (var i = 0; i < pending.length; i++) {
        var fd = new FormData();
        fd.append("file", pending[i]);
        fd.append("category", "EXTERNAL_REFERENCE");
        fd.append("title", pending[i].name);
        try {
          await Api.upload("/api/cases/" + created.id + "/evidence", fd);
        } catch (uploadErr) {
          UI.toast(pending[i].name + ": " + uploadErr.message, "error");
        }
      }
    }

    var url = document.getElementById("evidence_url").value.trim();
    if (url) {
      try {
        await Api.post("/api/cases/" + created.id + "/evidence/url", {
          category: "EXTERNAL_REFERENCE",
          title: document.getElementById("evidence_url_title").value.trim() ||
                 "External reference",
          url: url,
        });
      } catch (urlErr) { UI.toast("URL evidence: " + urlErr.message, "error"); }
    }

    location.href = "/cases/" + created.id + (payload.run_analysis ? "?analysing=1" : "");
  });

  /* ---------------- bootstrap ----------------
     Every dropdown below is already populated by the server. This only
     refreshes them, and it refuses to replace a populated list with an empty
     one - a failed fetch must never leave a required field unanswerable. */

  /** Replace a select's options only if we actually have some, preserving
   *  whatever the user had already chosen. */
  function refreshOptions(selectId, options, placeholder) {
    var select = document.getElementById(selectId);
    if (!select || !options || !options.length) return false;
    var previous = select.value;
    select.innerHTML =
      (placeholder ? '<option value="">' + UI.esc(placeholder) + "</option>" : "") +
      options.map(function (o) {
        return '<option value="' + UI.esc(o.value) + '"' +
          (o.selected ? " selected" : "") + ">" + UI.esc(o.label) + "</option>";
      }).join("");
    if (previous) select.value = previous;
    return true;
  }

  function warn(message) {
    var box = document.getElementById("bootstrapWarning");
    if (!box) return;
    box.querySelector("[data-warning-text]").textContent = message;
    box.hidden = false;
  }

  async function bootstrap() {
    try {
      var meta = await Api.meta();

      if (meta.upload) {
        uploadLimits = meta.upload;
        document.getElementById("acceptHint").textContent =
          "Accepted: " + (uploadLimits.extensions || []).join(", ") +
          " · up to " + uploadLimits.max_mb + " MB each";
        fileInput.setAttribute("accept", (uploadLimits.extensions || []).join(","));
      }

      refreshOptions("infringement_type", meta.infringement_types, "Select…");
      refreshOptions(
        "priority",
        (meta.priorities || []).map(function (p) {
          return { value: p.value, label: p.label, selected: p.value === "MEDIUM" };
        })
      );
    } catch (e) {
      // The form is still usable - the server rendered these choices already.
      if (e.code !== "NAVIGATION_ABORTED") {
        warn(
          "Could not refresh the option lists from the server (" +
          (e.message || "request failed") +
          "). The form still works; the choices shown were sent with the page."
        );
      }
    }

    try {
      var mps = await Api.marketplaces();
      refreshOptions(
        "marketplace_id",
        mps.map(function (m) { return { value: m.id, label: m.name }; }),
        "Select a marketplace…"
      );
    } catch (e) { /* server-rendered list stands; free text is the fallback */ }

    try {
      var me = await Api.me();
      var field = document.getElementById("vendorField");
      if (me.is_global) {
        field.hidden = false;
        var vendors = await Api.vendorOptions();
        refreshOptions(
          "vendor_id",
          vendors.map(function (v) { return { value: v.id, label: v.name }; }),
          "Select vendor…"
        );
      } else {
        field.hidden = true;
      }
    } catch (e) { /* the server already decided whether to show this field */ }
  }

  bootstrap();
})();
