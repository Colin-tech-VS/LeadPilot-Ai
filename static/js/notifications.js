/* LeadPilot AI — live event notifications (PC + mobile).
 * Polls the server for important events (new lead, urgent call, booked RDV,
 * accepted/refused devis) and surfaces each one as an in-page toast plus a
 * native OS notification. The OS notification is shown through the service
 * worker, so the plumber is alerted even when the tab is in the background —
 * as long as a session is open on the web app. */
(function () {
  var cfg = window.LEADPILOT_NOTIFY || {};
  var endpoint = cfg.endpoint;
  if (!endpoint) return;

  var lang = cfg.lang === "en" ? "en" : "fr";
  var T = {
    fr: { enable: "🔔 Activer les notifications", fallback: "Nouvel évènement" },
    en: { enable: "🔔 Enable notifications", fallback: "New event" },
  }[lang];

  var STORAGE_KEY = "leadpilot:lastNotifSeen:" + (cfg.tenant || "default");
  // Poll a little faster than before so alerts feel near real-time.
  var POLL_MS = 15000;

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
  }

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
      // Re-alert on each distinct event even if a previous one is on screen.
      renotify: true,
      // Urgent calls should require an explicit dismissal.
      requireInteraction: n.type === "urgent_lead",
      data: { url: n.url || "/dashboard" },
    };
    var title = (n.icon ? n.icon + " " : "") + (n.title || T.fallback);
    if ("serviceWorker" in navigator && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready
        .then(function (reg) {
          reg.showNotification(title, options);
        })
        .catch(function () {
          try {
            new Notification(title, options);
          } catch (e) {}
        });
    } else {
      try {
        new Notification(title, options);
      } catch (e) {}
    }
  }

  function announce(n) {
    toast(n);
    osNotify(n);
  }

  function poll() {
    var since = localStorage.getItem(STORAGE_KEY);
    var url = endpoint + (since ? "?since=" + encodeURIComponent(since) : "");
    fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (data) {
        if (!data) return;
        if (since && data.notifications && data.notifications.length) {
          data.notifications.forEach(announce);
        }
        if (data.now) localStorage.setItem(STORAGE_KEY, data.now);
      })
      .catch(function () {});
  }

  function enableButton() {
    if (!("Notification" in window) || Notification.permission !== "default") return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lp-notify-enable";
    btn.textContent = T.enable;
    btn.addEventListener("click", function () {
      Notification.requestPermission().then(function () {
        btn.remove();
      });
    });
    document.body.appendChild(btn);
  }

  // Seed the cursor to "now" on first load so we never replay past events.
  if (!localStorage.getItem(STORAGE_KEY)) {
    localStorage.setItem(STORAGE_KEY, new Date().toISOString());
  }

  enableButton();
  poll();
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") poll();
  });
})();
