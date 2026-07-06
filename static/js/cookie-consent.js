/**
 * Real, functional cookie consent.
 *
 * Stores the visitor's choice in a first-party cookie (`pc_cookie_consent`)
 * valid for 6 months. Non-essential scripts must check
 * `window.pilotCookieConsent === 'accepted'` (or listen for the
 * `pc-consent-change` event) before running. Essential/session cookies are
 * always allowed and never gated here.
 */
(function () {
  var COOKIE = "pc_cookie_consent";
  var MAX_AGE = 60 * 60 * 24 * 182; // ~6 months

  function readConsent() {
    var m = document.cookie.match(/(?:^|;\s*)pc_cookie_consent=(accepted|refused)/);
    return m ? m[1] : null;
  }

  function writeConsent(value) {
    var secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie =
      COOKIE + "=" + value + "; Max-Age=" + MAX_AGE + "; Path=/; SameSite=Lax" + secure;
    window.pilotCookieConsent = value;
    try {
      document.dispatchEvent(new CustomEvent("pc-consent-change", { detail: value }));
    } catch (e) {
      /* older browsers */
    }
  }

  function hide(banner) {
    banner.hidden = true;
    banner.classList.remove("is-visible");
  }

  function show(banner) {
    banner.hidden = false;
    // next frame so the CSS transition plays
    requestAnimationFrame(function () {
      banner.classList.add("is-visible");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var banner = document.getElementById("cookie-banner");
    var existing = readConsent();
    window.pilotCookieConsent = existing;

    if (banner) {
      if (!existing) show(banner);

      banner.querySelectorAll("[data-cookie-action]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var action = btn.getAttribute("data-cookie-action");
          writeConsent(action === "accept" ? "accepted" : "refused");
          hide(banner);
        });
      });
    }

    // "Manage preferences" buttons (e.g. on the cookie policy page) reopen it.
    document.querySelectorAll("[data-cookie-reopen]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (banner) show(banner);
      });
    });
  });
})();
