/* LeadPilot AI — "Install the app" prompt.
 * Captures the browser's beforeinstallprompt event and surfaces a floating
 * button so the user can add the dashboard to their home screen. On iOS
 * (which never fires the event) it shows a short "Add to Home Screen" hint. */
(function () {
  var lang = document.documentElement.lang === "en" ? "en" : "fr";
  var T = {
    fr: {
      install: "📲 Installer l'application",
      iosHint:
        "Pour installer : appuyez sur Partager puis « Sur l'écran d'accueil ».",
      close: "Fermer",
    },
    en: {
      install: "📲 Install the app",
      iosHint: 'To install: tap Share, then "Add to Home Screen".',
      close: "Close",
    },
  }[lang];

  var DISMISS_KEY = "leadpilot:installDismissed";

  function isStandalone() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function dismissed() {
    try {
      return localStorage.getItem(DISMISS_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function remember() {
    try {
      localStorage.setItem(DISMISS_KEY, "1");
    } catch (e) {}
  }

  // Already installed or previously dismissed → do nothing.
  if (isStandalone() || dismissed()) return;

  var deferredPrompt = null;

  function makeButton(onClick) {
    var wrap = document.createElement("div");
    wrap.className = "lp-install";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "lp-install-btn";
    btn.textContent = T.install;
    btn.addEventListener("click", onClick);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "lp-install-close";
    close.setAttribute("aria-label", T.close);
    close.textContent = "×";
    close.addEventListener("click", function () {
      remember();
      wrap.remove();
    });

    wrap.appendChild(btn);
    wrap.appendChild(close);
    document.body.appendChild(wrap);
    return wrap;
  }

  function showNativeButton() {
    var wrap = makeButton(function () {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      deferredPrompt.userChoice.finally(function () {
        deferredPrompt = null;
        remember();
        wrap.remove();
      });
    });
  }

  function showIosHint() {
    var isIos = /iphone|ipad|ipod/i.test(window.navigator.userAgent);
    var isSafari =
      /safari/i.test(window.navigator.userAgent) &&
      !/crios|fxios|edgios/i.test(window.navigator.userAgent);
    if (!isIos || !isSafari) return;

    var wrap = document.createElement("div");
    wrap.className = "lp-install lp-install-hint";
    wrap.innerHTML =
      '<span class="lp-install-hint-text"></span>' +
      '<button type="button" class="lp-install-close" aria-label="' +
      T.close +
      '">×</button>';
    wrap.querySelector(".lp-install-hint-text").textContent = T.iosHint;
    wrap.querySelector(".lp-install-close").addEventListener("click", function () {
      remember();
      wrap.remove();
    });
    document.body.appendChild(wrap);
  }

  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferredPrompt = e;
    showNativeButton();
  });

  window.addEventListener("appinstalled", function () {
    remember();
    var el = document.querySelector(".lp-install");
    if (el) el.remove();
  });

  // iOS Safari never fires beforeinstallprompt — fall back to a manual hint.
  showIosHint();
})();
