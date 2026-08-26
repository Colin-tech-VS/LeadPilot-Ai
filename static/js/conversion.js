/* PilotCore conversion events — reuse gtag / dataLayer, no second analytics stack. */
(function () {
  "use strict";

  function track(name, params) {
    var payload = params || {};
    if (typeof window.gtag === "function") {
      window.gtag("event", name, payload);
    }
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(Object.assign({ event: name }, payload));
  }
  window.pilotcoreTrack = track;

  var path = (location.pathname || "/").replace(/\/+$/, "") || "/";
  if (path === "/pro") track("pro_page_view");
  if (path === "/register" || path === "/50-artisans") track("signup_started");
  if (path === "/settings") track("phone_setup_started");
  if (document.body && document.body.getAttribute("data-signup-completed") === "1") {
    track("signup_completed");
  }

  document.addEventListener("click", function (e) {
    var tracked = e.target.closest("[data-track]");
    if (tracked) {
      track(tracked.getAttribute("data-track"), {
        href: tracked.getAttribute("href") || "",
      });
    }

    var listen = e.target.closest("[data-demo-listen]");
    if (!listen) return;
    track("demo_audio_play");
    var audio = document.getElementById("pro-demo-audio");
    if (audio && audio.getAttribute("src")) {
      e.preventDefault();
      audio.play().catch(function () {});
      return;
    }
    if (listen.tagName === "BUTTON") {
      var sim = document.getElementById("demo");
      if (sim) {
        e.preventDefault();
        sim.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.getAttribute) return;
    var ev = form.getAttribute("data-track-submit");
    if (ev) track(ev);
  });
})();
