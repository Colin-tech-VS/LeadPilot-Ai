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

  // The banner is position:fixed at the bottom of the viewport. On small
  // screens it is tall enough to sit ON TOP of a form's submit button (the
  // artisan sign-up "Continue"/"Créer mon compte" CTA), so taps land on the
  // banner and the visitor — who filled everything — simply cannot submit.
  // Reserve the banner's height at the bottom of the page while it is visible
  // so nothing interactive is ever hidden beneath it.
  var _resizeBound = null;
  function reserveSpace(banner) {
    var h = banner.offsetHeight || 0;
    document.body.style.paddingBottom = h ? h + "px" : "";
  }
  function clearSpace() {
    document.body.style.paddingBottom = "";
    if (_resizeBound) {
      window.removeEventListener("resize", _resizeBound);
      _resizeBound = null;
    }
  }

  /* Reserved space alone is not enough on a short phone: the banner is fixed to
     the bottom, and a form CTA that sits mid-card (the artisan sign-up button)
     can still be underneath it at the current scroll position — taps then land
     on the banner and the visitor cannot submit. The reservation above makes
     the page scrollable past the banner, so use it: lift anything marked
     data-consent-keep-visible clear of the banner. */
  function keepCtaClear(banner) {
    var cta = document.querySelector("[data-consent-keep-visible]");
    if (!cta) return;
    // The banner slides in via `transform`, so its rect is still off-screen on
    // the frame it becomes visible. Derive the edge it will occupy from its
    // laid-out height instead, the way reserveSpace() does.
    var bannerTop = window.innerHeight - (banner.offsetHeight || 0);
    var c = cta.getBoundingClientRect();
    var overlap = c.bottom - bannerTop;
    if (overlap <= 0) return;
    var behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    try {
      window.scrollBy({ top: overlap + 16, behavior: behavior });
    } catch (e) {
      window.scrollBy(0, overlap + 16);
    }
  }

  function hide(banner) {
    banner.hidden = true;
    banner.classList.remove("is-visible");
    clearSpace();
  }

  function show(banner) {
    banner.hidden = false;
    // next frame so the CSS transition plays and the height is measurable
    requestAnimationFrame(function () {
      banner.classList.add("is-visible");
      reserveSpace(banner);
      keepCtaClear(banner);
      if (!_resizeBound) {
        _resizeBound = function () {
          reserveSpace(banner);
          keepCtaClear(banner);
        };
        window.addEventListener("resize", _resizeBound);
      }
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
