/* App shell: sidebar toggle, notifications, sign-out. */
(function () {
  "use strict";

  /* ---------------- sidebar ---------------- */
  var sidebar = document.getElementById("sidebar");
  var scrim = document.getElementById("scrim");
  var toggle = document.getElementById("menuToggle");

  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    scrim.classList.toggle("is-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", String(open));
  }
  if (toggle) {
    toggle.addEventListener("click", function () {
      setSidebar(!sidebar.classList.contains("is-open"));
    });
  }
  if (scrim) scrim.addEventListener("click", function () { setSidebar(false); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setSidebar(false);
  });

  /* ---------------- sign out ---------------- */
  var logoutBtn = document.getElementById("logoutBtn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      UI.setLoading(logoutBtn, true, "Signing out");
      try {
        await Api.logout();
      } catch (e) { /* the cookie is cleared regardless */ }
      location.href = "/login";
    });
  }

  /* ---------------- notifications ---------------- */
  var bellBtn = document.getElementById("bellBtn");
  var popover = document.getElementById("notifPopover");
  var list = document.getElementById("notifList");
  var countEl = document.getElementById("bellCount");
  var markAll = document.getElementById("markAllRead");

  var SEVERITY_ICON = { INFO: "i", SUCCESS: "✓", WARNING: "!", ERROR: "!" };

  function renderNotifications(items) {
    if (!items.length) {
      list.innerHTML = '<div class="popover__item"><div class="popover__msg">' +
        "You're all caught up.</div></div>";
      return;
    }
    list.innerHTML = items.map(function (n) {
      var href = UI.safeUrl(n.link) || "#";
      return '<a class="popover__item ' + (n.is_read ? "" : "is-unread") + '" href="' +
        href + '" data-id="' + n.id + '">' +
        '<div class="popover__title">' + UI.esc(n.title) + "</div>" +
        (n.message ? '<div class="popover__msg">' + UI.esc(n.message) + "</div>" : "") +
        '<div class="popover__time">' + UI.esc(UI.fmtRelative(n.created_at)) + "</div>" +
        "</a>";
    }).join("");

    list.querySelectorAll(".popover__item[data-id]").forEach(function (a) {
      a.addEventListener("click", function () {
        Api.markRead(a.dataset.id).catch(function () {});
      });
    });
  }

  async function refreshCount() {
    if (!countEl) return;
    try {
      var res = await Api.notificationCount();
      if (res.unread > 0) {
        countEl.hidden = false;
        countEl.textContent = res.unread > 99 ? "99+" : res.unread;
      } else {
        countEl.hidden = true;
      }
    } catch (e) { /* silent - the bell is not critical */ }
  }

  async function loadNotifications() {
    try {
      var items = await Api.notifications({ limit: 20 });
      renderNotifications(items);
    } catch (e) {
      list.innerHTML = '<div class="popover__item"><div class="popover__msg">' +
        "Could not load notifications.</div></div>";
    }
  }

  if (bellBtn) {
    bellBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = popover.hidden;
      popover.hidden = !open;
      bellBtn.setAttribute("aria-expanded", String(open));
      if (open) loadNotifications();
    });
    document.addEventListener("click", function (e) {
      if (popover && !popover.hidden && !popover.contains(e.target)) {
        popover.hidden = true;
        bellBtn.setAttribute("aria-expanded", "false");
      }
    });
    refreshCount();
    setInterval(refreshCount, 60000);
  }

  if (markAll) {
    markAll.addEventListener("click", async function (e) {
      e.stopPropagation();
      try {
        await Api.markAllRead();
        await loadNotifications();
        await refreshCount();
      } catch (err) { UI.handleError(err); }
    });
  }

  /* ---------------- one-off page messages ---------------- */
  var params = new URLSearchParams(location.search);
  if (params.get("denied") === "administration") {
    UI.toast("Administration is not available for your role.", "warning");
  }

  UI.chartDefaults();
})();
