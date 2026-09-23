/* Sentinel IP AI - shared UI helpers: escaping, formatting, badges, toasts,
   modals, confirmations, skeletons, chart defaults. */
(function (global) {
  "use strict";

  /* ------------------------------------------------------------------ */
  /* Escaping - every value from the API goes through this before HTML.  */
  /* ------------------------------------------------------------------ */
  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function safeUrl(value) {
    if (!value) return "";
    var v = String(value).trim();
    return /^https?:\/\//i.test(v) || v.charAt(0) === "/" ? esc(v) : "";
  }

  /* ------------------------------------------------------------------ */
  /* Formatting                                                          */
  /* ------------------------------------------------------------------ */
  function parseDate(value) {
    if (!value) return null;
    // Server timestamps are naive UTC.
    var s = String(value);
    if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtDate(value, opts) {
    var d = parseDate(value);
    if (!d) return "-";
    return d.toLocaleDateString(undefined, opts || {
      year: "numeric", month: "short", day: "numeric",
    });
  }

  function fmtDateTime(value) {
    var d = parseDate(value);
    if (!d) return "-";
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  }

  function fmtRelative(value) {
    var d = parseDate(value);
    if (!d) return "-";
    var diff = (Date.now() - d.getTime()) / 1000;
    var future = diff < 0;
    diff = Math.abs(diff);
    var units = [
      [31536000, "y"], [2592000, "mo"], [604800, "w"],
      [86400, "d"], [3600, "h"], [60, "m"],
    ];
    for (var i = 0; i < units.length; i++) {
      if (diff >= units[i][0]) {
        var n = Math.floor(diff / units[i][0]);
        return future ? "in " + n + units[i][1] : n + units[i][1] + " ago";
      }
    }
    return future ? "in a moment" : "just now";
  }

  function fmtMoney(value, currency) {
    if (value === null || value === undefined || value === "") return "-";
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency", currency: currency || "USD",
      }).format(Number(value));
    } catch (e) {
      return (currency || "USD") + " " + Number(value).toFixed(2);
    }
  }

  function fmtNumber(value) {
    if (value === null || value === undefined || value === "") return "-";
    return new Intl.NumberFormat().format(Number(value));
  }

  function fmtBytes(bytes) {
    if (!bytes && bytes !== 0) return "-";
    var units = ["B", "KB", "MB", "GB"];
    var i = 0;
    var n = Number(bytes);
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n : n.toFixed(1)) + " " + units[i];
  }

  function fmtDuration(ms) {
    if (ms === null || ms === undefined) return "-";
    if (ms < 1000) return ms + " ms";
    return (ms / 1000).toFixed(1) + " s";
  }

  function titleize(value) {
    if (!value) return "";
    return String(value).replace(/_/g, " ").toLowerCase()
      .replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  function initials(name) {
    if (!name) return "?";
    return name.split(/\s+/).slice(0, 2)
      .map(function (p) { return p.charAt(0).toUpperCase(); }).join("");
  }

  /* ------------------------------------------------------------------ */
  /* Badges & indicators                                                 */
  /* ------------------------------------------------------------------ */
  var STATUS_TONE = {
    DRAFT: "neutral", ANALYZING: "info", AWAITING_APPROVAL: "warning",
    APPROVED: "success", REJECTED: "danger", CHANGES_REQUESTED: "warning",
    SUBMITTED: "accent", UNDER_REVIEW: "info", ACTION_TAKEN: "success",
    RESOLVED: "success", ESCALATED: "danger", CLOSED: "neutral",
    ACTIVE: "success", SUSPENDED: "warning", INACTIVE: "neutral",
    DISABLED: "neutral", PENDING: "warning",
    COMPLETE: "success", RUNNING: "info", FAILED: "danger", SKIPPED: "neutral",
    ON_TRACK: "success", AT_RISK: "warning", BREACHED: "danger", MET: "accent",
    LOW: "neutral", MEDIUM: "warning", HIGH: "accent", CRITICAL: "danger",
    RELEVANT: "success", NOT_RELEVANT: "neutral", DISPUTED: "warning",
    UNREVIEWED: "neutral",
  };

  function badge(value, label, tone) {
    if (!value) return "";
    var t = tone || STATUS_TONE[String(value).toUpperCase()] || "neutral";
    return '<span class="badge badge--' + t + '"><i class="badge__dot"></i>' +
      esc(label || titleize(value)) + "</span>";
  }

  function riskBand(confidence) {
    if (confidence === null || confidence === undefined) return "low";
    var c = Number(confidence);
    if (c >= 90) return "critical";
    if (c >= 70) return "high";
    if (c >= 40) return "medium";
    return "low";
  }

  function confidenceCell(confidence, riskLevel) {
    if (confidence === null || confidence === undefined) {
      return '<span class="card__hint">Not analysed</span>';
    }
    var band = (riskLevel || riskBand(confidence)).toLowerCase();
    var pct = Math.max(0, Math.min(100, Number(confidence)));
    return '' +
      '<div class="confidence confidence--' + esc(band) + '">' +
        '<div class="confidence__top">' +
          '<span class="confidence__value">' + Math.round(pct) + "%</span>" +
          '<span class="card__hint">' + esc(titleize(band)) + "</span>" +
        "</div>" +
        '<div class="confidence__bar"><div class="confidence__fill" style="width:' +
          pct + '%"></div></div>' +
      "</div>";
  }

  function slaCell(sla) {
    if (!sla || !sla.deadline) return '<span class="card__hint">No SLA</span>';
    var state = String(sla.state || "ON_TRACK").toLowerCase();
    return '' +
      '<div class="sla sla--' + esc(state) + '">' +
        '<span class="sla__label">' + esc(sla.remaining_label || "") + "</span>" +
        '<div class="sla__bar"><div class="sla__fill" style="width:' +
          Math.max(0, Math.min(100, sla.elapsed_percent || 0)) + '%"></div></div>' +
        '<span class="sla__meta">' + esc(fmtDateTime(sla.deadline)) + "</span>" +
      "</div>";
  }

  /* ------------------------------------------------------------------ */
  /* Toasts                                                              */
  /* ------------------------------------------------------------------ */
  function toastRoot() {
    var root = document.querySelector(".toasts");
    if (!root) {
      root = document.createElement("div");
      root.className = "toasts";
      root.setAttribute("role", "status");
      root.setAttribute("aria-live", "polite");
      document.body.appendChild(root);
    }
    return root;
  }

  function toast(message, kind, ms) {
    var root = toastRoot();
    var el = document.createElement("div");
    el.className = "toast toast--" + (kind || "info");
    var icons = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };
    el.innerHTML =
      "<span>" + esc(icons[kind] || icons.info) + "</span>" +
      "<span>" + esc(message) + "</span>" +
      '<button class="toast__close" aria-label="Dismiss">✕</button>';
    el.querySelector(".toast__close").addEventListener("click", function () { el.remove(); });
    root.appendChild(el);
    setTimeout(function () { el.remove(); }, ms || (kind === "error" ? 7000 : 4200));
    return el;
  }

  toast.success = function (m) { return toast(m, "success"); };
  toast.error = function (m) { return toast(m, "error"); };
  toast.warning = function (m) { return toast(m, "warning"); };

  function handleError(err, fallback) {
    if (!err) return;
    if (err.status === 401) return;                       // redirect already queued
    if (err.code === "NAVIGATION_ABORTED") return;        // the user left the page
    console.error(err);
    toast(err.message || fallback || "Something went wrong.", "error");
  }

  /* ------------------------------------------------------------------ */
  /* Modal                                                               */
  /* ------------------------------------------------------------------ */
  var modalRoot = null;
  var lastFocused = null;

  function ensureModalRoot() {
    if (modalRoot) return modalRoot;
    modalRoot = document.createElement("div");
    modalRoot.className = "modal-root";
    modalRoot.hidden = true;
    modalRoot.innerHTML = '<div class="modal-root__scrim"></div><div class="modal" ' +
      'role="dialog" aria-modal="true"></div>';
    document.body.appendChild(modalRoot);
    modalRoot.querySelector(".modal-root__scrim")
      .addEventListener("click", function () { closeModal(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modalRoot.hidden) closeModal();
    });
    return modalRoot;
  }

  function openModal(opts) {
    var root = ensureModalRoot();
    var panel = root.querySelector(".modal");
    panel.className = "modal" + (opts.wide ? " modal--wide" : "");
    panel.innerHTML =
      '<div class="modal__head"><h2>' + esc(opts.title || "") + "</h2>" +
        '<button class="btn btn--ghost btn--icon" data-modal-close ' +
        'aria-label="Close" style="margin-left:auto">✕</button></div>' +
      '<div class="modal__body">' + (opts.body || "") + "</div>" +
      (opts.footer ? '<div class="modal__foot">' + opts.footer + "</div>" : "");
    lastFocused = document.activeElement;
    root.hidden = false;
    document.body.style.overflow = "hidden";
    panel.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", function () { closeModal(); });
    });
    var focusable = panel.querySelector(
      "input, select, textarea, button:not([data-modal-close])"
    );
    if (focusable) focusable.focus();
    if (typeof opts.onOpen === "function") opts.onOpen(panel);
    return panel;
  }

  function closeModal() {
    if (!modalRoot) return;
    modalRoot.hidden = true;
    document.body.style.overflow = "";
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  function confirmDialog(opts) {
    return new Promise(function (resolve) {
      var panel = openModal({
        title: opts.title || "Are you sure?",
        body: '<p>' + esc(opts.message || "") + "</p>" +
              (opts.detail ? '<p class="card__hint">' + esc(opts.detail) + "</p>" : ""),
        footer:
          '<button class="btn" data-modal-close>' + esc(opts.cancelLabel || "Cancel") +
          "</button>" +
          '<button class="btn btn--' + (opts.tone || "danger") + '" data-confirm>' +
          esc(opts.confirmLabel || "Confirm") + "</button>",
      });
      var settled = false;
      panel.querySelector("[data-confirm]").addEventListener("click", function () {
        settled = true; closeModal(); resolve(true);
      });
      panel.querySelectorAll("[data-modal-close]").forEach(function (b) {
        b.addEventListener("click", function () { if (!settled) resolve(false); });
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Skeletons & states                                                  */
  /* ------------------------------------------------------------------ */
  function skeletonRows(count, cols) {
    var out = "";
    for (var i = 0; i < (count || 5); i++) {
      out += "<tr>";
      for (var c = 0; c < (cols || 6); c++) {
        out += '<td><div class="skeleton skeleton--text"></div></td>';
      }
      out += "</tr>";
    }
    return out;
  }

  function emptyState(title, message, actionHtml) {
    return '<div class="empty"><div class="empty__title">' + esc(title) + "</div>" +
      "<div>" + esc(message || "") + "</div>" +
      (actionHtml ? '<div style="margin-top:1rem">' + actionHtml + "</div>" : "") +
      "</div>";
  }

  function setLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.classList.add("is-loading");
      button.innerHTML = '<span class="spinner"></span>' + esc(label || "Working...");
    } else {
      button.disabled = false;
      button.classList.remove("is-loading");
      if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
    }
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments, ctx = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, wait || 300);
    };
  }

  function paginationHtml(page) {
    if (!page) return "";
    var from = page.total === 0 ? 0 : (page.page - 1) * page.page_size + 1;
    var to = Math.min(page.total, page.page * page.page_size);
    return '<div class="pagination">' +
      "<span>Showing " + from + "-" + to + " of " + fmtNumber(page.total) + "</span>" +
      '<div class="btn-group">' +
        '<button class="btn btn--sm" data-page="' + (page.page - 1) + '"' +
          (page.page <= 1 ? " disabled" : "") + ">Previous</button>" +
        '<button class="btn btn--sm" data-page="' + (page.page + 1) + '"' +
          (page.page >= page.pages ? " disabled" : "") + ">Next</button>" +
      "</div></div>";
  }

  /* ------------------------------------------------------------------ */
  /* Charts                                                              */
  /* ------------------------------------------------------------------ */
  var PALETTE = ["#BF5700", "#8C3F00", "#D97706", "#16A34A", "#DC2626",
                 "#6B7280", "#1F1F1F", "#E08A46", "#A8541F", "#9CA3AF"];

  function chartDefaults() {
    if (!global.Chart) return;
    Chart.defaults.font.family = getComputedStyle(document.body)
      .getPropertyValue("--font") || "Inter, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#6B7280";
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.boxHeight = 10;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.tooltip.backgroundColor = "#1F1F1F";
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 6;
    Chart.defaults.maintainAspectRatio = false;
  }

  var chartRegistry = {};

  function renderChart(canvasId, config) {
    if (!global.Chart) return null;
    var el = document.getElementById(canvasId);
    if (!el) return null;
    if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); }
    chartRegistry[canvasId] = new Chart(el.getContext("2d"), config);
    return chartRegistry[canvasId];
  }

  function noData(canvasId, message) {
    var el = document.getElementById(canvasId);
    if (!el || !el.parentElement) return;
    if (chartRegistry[canvasId]) { chartRegistry[canvasId].destroy(); delete chartRegistry[canvasId]; }
    el.parentElement.innerHTML = '<div class="empty">' + esc(message || "No data yet.") + "</div>";
  }

  global.UI = {
    esc: esc, safeUrl: safeUrl,
    fmtDate: fmtDate, fmtDateTime: fmtDateTime, fmtRelative: fmtRelative,
    fmtMoney: fmtMoney, fmtNumber: fmtNumber, fmtBytes: fmtBytes,
    fmtDuration: fmtDuration, titleize: titleize, initials: initials,
    parseDate: parseDate,
    badge: badge, confidenceCell: confidenceCell, slaCell: slaCell, riskBand: riskBand,
    toast: toast, handleError: handleError,
    openModal: openModal, closeModal: closeModal, confirm: confirmDialog,
    skeletonRows: skeletonRows, emptyState: emptyState, setLoading: setLoading,
    debounce: debounce, paginationHtml: paginationHtml,
    PALETTE: PALETTE, chartDefaults: chartDefaults, renderChart: renderChart,
    noData: noData,
  };
})(window);
