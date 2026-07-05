/* LeadPilot AI — live event notifications (PC + mobile).
 * Two surfaces, one poll:
 *   1. A notification centre (bell + panel) in the top bar with an unread
 *      badge, a scrollable history and "mark all read".
 *   2. An in-page toast + native OS notification for each *new* event, so the
 *      plumber is alerted even when the tab is in the background — as long as a
 *      session is open on the web app.
 */
(function () {
  var cfg = window.LEADPILOT_NOTIFY || {};
  var endpoint = cfg.endpoint;
  if (!endpoint) return;

  var lang = cfg.lang === "en" ? "en" : "fr";
  var T = {
    fr: { enable: "🔔 Activer les notifications", fallback: "Nouvel évènement",
          empty: "Aucune notification.", now: "à l'instant", min: "min", h: "h", d: "j" },
    en: { enable: "🔔 Enable notifications", fallback: "New event",
          empty: "No notifications.", now: "just now", min: "min", h: "h", d: "d" },
  }[lang];

  var STORAGE_KEY = "leadpilot:lastNotifSeen:" + (cfg.tenant || "default");
  var POLL_MS = 15000;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }

  // -------------------------------------------------------------- elements
  var bell = document.getElementById("notif-bell");
  var panel = document.getElementById("notif-panel");
  var badge = document.getElementById("notif-badge");
  var list = document.getElementById("notif-list");
  var markReadBtn = document.getElementById("notif-mark-read");
  var center = document.getElementById("notif-center");

  // -------------------------------------------------------------- helpers
  function relTime(iso) {
    if (!iso) return "";
    var then = new Date(iso).getTime();
    if (isNaN(then)) return "";
    var s = Math.max(0, (Date.now() - then) / 1000);
    if (s < 60) return T.now;
    if (s < 3600) return Math.floor(s / 60) + " " + T.min;
    if (s < 86400) return Math.floor(s / 3600) + " " + T.h;
    return Math.floor(s / 86400) + " " + T.d;
  }

  function setBadge(count) {
    if (!badge) return;
    if (count > 0) {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  function renderList(items) {
    if (!list) return;
    if (!items || !items.length) {
      list.innerHTML = '<p class="notif-empty">' + T.empty + "</p>";
      return;
    }
    list.innerHTML = items
      .map(function (n) {
        var cls = "notif-item" + (n.read ? "" : " is-unread");
        return (
          '<a class="' + cls + '" href="' + (n.url || "/dashboard") + '">' +
          '<span class="notif-item-icon">' + (n.icon || "🔔") + "</span>" +
          '<span class="notif-item-body">' +
          "<strong>" + (n.title || T.fallback) + "</strong>" +
          (n.body ? "<span>" + n.body + "</span>" : "") +
          '<time>' + relTime(n.created_at) + "</time>" +
          "</span></a>"
        );
      })
      .join("");
  }

  // -------------------------------------------------------------- toast
  function ensureToastRoot() {
    var root = document.getElementById("lp-toast-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "lp-toast-root";
      root.className = "lp-toast-root";
      document.body.appendChild(root);
    }
    return root;
  }

  function toast(n) {
    var root = ensureToastRoot();
    var el = document.createElement("div");
    el.className = "lp-toast";
    el.setAttribute("role", "status");
    el.innerHTML =
      '<span class="lp-toast-icon"></span>' +
      '<div class="lp-toast-body"><strong></strong><span></span></div>' +
      '<button class="lp-toast-close" aria-label="Fermer">×</button>';
    el.querySelector(".lp-toast-icon").textContent = n.icon || "🔔";
    el.querySelector("strong").textContent = n.title || T.fallback;
    el.querySelector("span").textContent = n.body || "";
    root.appendChild(el);
    requestAnimationFrame(function () {
      el.classList.add("is-in");
    });
    var timer = setTimeout(dismiss, 9000);
    function dismiss() {
      clearTimeout(timer);
      el.classList.remove("is-in");
      setTimeout(function () {
        if (el.parentNode) el.remove();
      }, 400);
    }
    el.querySelector(".lp-toast-close").addEventListener("click", function (e) {
      e.stopPropagation();
      dismiss();
    });
    el.addEventListener("click", function () {
      window.location.href = n.url || "/dashboard";
    });
  }

  function osNotify(n) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var options = {
      body: n.body || "",
      icon: "/static/images/logo.svg",
      badge: "/static/images/logo.svg",
      tag: "lp-" + n.id,
      renotify: true,
      requireInteraction: n.type === "urgent_lead",
      data: { url: n.url || "/dashboard" },
    };
    var title = (n.icon ? n.icon + " " : "") + (n.title || T.fallback);
    if ("serviceWorker" in navigator && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready
        .then(function (reg) { reg.showNotification(title, options); })
        .catch(function () { try { new Notification(title, options); } catch (e) {} });
    } else {
      try { new Notification(title, options); } catch (e) {}
    }
  }

  function announce(n) {
    toast(n);
    osNotify(n);
  }

  // -------------------------------------------------------------- poll
  function poll(withRecent) {
    var since = localStorage.getItem(STORAGE_KEY);
    var url = endpoint + "?";
    if (since) url += "since=" + encodeURIComponent(since) + "&";
    if (withRecent) url += "recent=1";
    fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        if (since && data.notifications && data.notifications.length) {
          data.notifications.forEach(announce);
        }
        setBadge(data.unread || 0);
        if (withRecent && data.recent) renderList(data.recent);
        if (data.now) localStorage.setItem(STORAGE_KEY, data.now);
      })
      .catch(function () {});
  }

  // -------------------------------------------------------------- panel wiring
  function openPanel() {
    if (!panel) return;
    panel.hidden = false;
    if (bell) bell.setAttribute("aria-expanded", "true");
    poll(true); // refresh history on open
    markRead();
  }
  function closePanel() {
    if (!panel) return;
    panel.hidden = true;
    if (bell) bell.setAttribute("aria-expanded", "false");
  }
  function markRead() {
    if (!cfg.readEndpoint) return;
    fetch(cfg.readEndpoint, {
      method: "POST",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function () {
        setBadge(0);
        if (list) {
          list.querySelectorAll(".notif-item.is-unread").forEach(function (el) {
            el.classList.remove("is-unread");
          });
        }
      })
      .catch(function () {});
  }

  if (bell) {
    bell.addEventListener("click", function (e) {
      e.stopPropagation();
      if (panel && panel.hidden) openPanel();
      else closePanel();
    });
  }
  if (markReadBtn) {
    markReadBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      markRead();
    });
  }
  document.addEventListener("click", function (e) {
    if (panel && !panel.hidden && center && !center.contains(e.target)) closePanel();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closePanel();
  });

  // -------------------------------------------------------------- enable button
  function enableButton() {
    if (!("Notification" in window) || Notification.permission !== "default") return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lp-notify-enable";
    btn.textContent = T.enable;
    btn.addEventListener("click", function () {
      Notification.requestPermission().then(function () { btn.remove(); });
    });
    document.body.appendChild(btn);
  }

  // Seed the cursor so we never replay past events on first load.
  if (!localStorage.getItem(STORAGE_KEY)) {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString());
  }

  enableButton();
  poll(true); // first load: badge + history
  setInterval(function () { poll(false); }, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") poll(false);
  });
})();
