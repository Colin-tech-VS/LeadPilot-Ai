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
  var _observer = null;
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
    if (_observer) {
      _observer.disconnect();
      _observer = null;
    }
  }

  /* Reserved space alone is not enough on a short phone: the banner is fixed to
     the bottom, and a form CTA that sits mid-card (the artisan sign-up button)
     can still be underneath it at the current scroll position — taps then land
     on the banner and the visitor cannot submit. The reservation above makes
     the page scrollable past the banner, so use it: lift anything marked
     data-consent-keep-visible clear of the banner. */
  function keepCtaClear(banner) {
    if (!banner || banner.hidden) return;
    var cta = document.querySelector("[data-consent-keep-visible]");
    if (!cta) return;
    /* Never scroll away from something the visitor has to read. On a refused
       sign-up the server re-renders the form with the reason at the top and
       the password field blank — and this lift, ~266px on a phone, pushed that
       reason off the top of the screen. What was left was a form that had
       apparently done nothing, so the visitor retyped, resubmitted, and got
       the same silence. The button being reachable matters less than knowing
       why the last tap failed; the reserved space below still makes it
       scrollable to. */
    var alert = document.querySelector(".alert-error, .alert-success, [role='alert']");
    if (alert) {
      var a = alert.getBoundingClientRect();
      if (a.height && a.top < window.innerHeight) return;
    }
    // The banner slides in via `transform`, so its rect is still off-screen on
    // the frame it becomes visible. Derive the edge it will occupy from its
    // laid-out height instead, the way reserveSpace() does.
    var bannerTop = window.innerHeight - (banner.offsetHeight || 0);
    var c = cta.getBoundingClientRect();
    if (!c.height) return; // not laid out (a hidden wizard step)
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

      /* Checking once, when the banner appears, is not enough: the CTA moves
         after that. On the artisan sign-up the banner shows over the short
         first step of the wizard — no overlap, nothing to do — and the button
         only drops under it when step 2 makes the card taller. Opening the
         « téléphone et SIRET » disclosure does the same. Both were left
         uncovered, and a visitor who filled the whole form tapped a button
         that was no longer there. So watch the page and re-check whenever the
         layout actually moves. */
      if (!_observer && typeof ResizeObserver === "function") {
        var queued = false;
        _observer = new ResizeObserver(function () {
          if (queued) return; // one check per frame, whatever fired it
          queued = true;
          requestAnimationFrame(function () {
            queued = false;
            reserveSpace(banner);
            keepCtaClear(banner);
          });
        });
        _observer.observe(document.body);
        var card = document.querySelector("[data-consent-keep-visible]");
        card = card && card.closest ? card.closest("form") : null;
        if (card) _observer.observe(card);
      }
    });
  }

  /* Anything that moves the CTA without resizing the page can ask for a
     re-check (the wizard does, on every step change). */
  document.addEventListener("pc-consent-recheck", function () {
    var banner = document.getElementById("cookie-banner");
    if (banner && !banner.hidden) keepCtaClear(banner);
  });

  /* Focusing a field is the other way the button slides back under the banner,
     and the worst one: the browser scrolls the focused input into view, which
     moves everything else — so the visitor fills the last field and the button
     they are about to tap is no longer where they can tap it. Nothing resizes,
     so the observer above never hears about it. Re-check once the browser has
     finished its own scrolling. */
  document.addEventListener("focusin", function (e) {
    var banner = document.getElementById("cookie-banner");
    if (!banner || banner.hidden) return;
    if (!e.target || !e.target.closest) return;
    if (!e.target.closest("form")) return;
    window.setTimeout(function () {
      keepCtaClear(banner);
    }, 250);
  });

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
