/* LeadPilot AI — live appointment notifications (mobile + desktop).
 * Polls the server for newly booked/scheduled appointments and surfaces them
 * as an in-page toast plus a native OS notification when permission is granted. */
(function () {
  var cfg = window.LEADPILOT_NOTIFY || {};
  var endpoint = cfg.endpoint;
  if (!endpoint) return;

  var lang = cfg.lang === "en" ? "en" : "fr";
  var T = {
    fr: {
      enable: "🔔 Activer les notifications",
      newAppt: "Nouveau rendez-vous",
      at: "à",
    },
    en: {
      enable: "🔔 Enable notifications",
      newAppt: "New appointment",
      at: "at",
    },
  }[lang];

  var STORAGE_KEY = "leadpilot:lastApptSeen:" + (cfg.tenant || "default");
  var POLL_MS = 25000;

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

  function toast(title, body) {
    var root = ensureToastRoot();
    var el = document.createElement("div");
    el.className = "lp-toast";
    el.setAttribute("role", "status");
    el.innerHTML =
      '<span class="lp-toast-icon">📅</span>' +
      '<div class="lp-toast-body"><strong></strong><span></span></div>' +
      '<button class="lp-toast-close" aria-label="Fermer">×</button>';
    el.querySelector("strong").textContent = title;
    el.querySelector("span").textContent = body;
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
      window.location.href = "/dashboard";
    });
  }

  function osNotify(title, body, id) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var options = {
      body: body,
      icon: "/static/images/logo.svg",
      badge: "/static/images/logo.svg",
      tag: "appt-" + id,
      data: { url: "/dashboard" },
    };
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

  function announce(appt) {
    var title = T.newAppt + " — " + appt.name;
    var parts = [];
    if (appt.issue) parts.push(appt.issue);
    if (appt.date || appt.time) {
      parts.push((appt.date + " " + T.at + " " + appt.time).trim());
    }
    if (appt.address) parts.push(appt.address);
    var body = parts.join(" · ");
    toast(title, body);
    osNotify(title, body, appt.id);
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
        if (since && data.appointments && data.appointments.length) {
          data.appointments.forEach(announce);
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

  // Seed the cursor to "now" on first load so we never replay past appointments.
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
