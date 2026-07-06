/** Scroll reveals + animated counters for public vitrine pages. */
(function () {
  "use strict";

  function animateCount(el) {
    if (el.dataset.animated === "1") return;
    el.dataset.animated = "1";
    var target = parseFloat(el.getAttribute("data-count") || "0");
    var suffix = el.getAttribute("data-suffix") || "";
    var prefix = el.getAttribute("data-prefix") || "";
    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
    var duration = parseInt(el.getAttribute("data-duration") || "1400", 10);
    var start = performance.now();
    function tick(now) {
      var t = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - t, 3);
      var val = target * eased;
      el.textContent =
        prefix +
        val.toLocaleString(document.documentElement.lang === "en" ? "en-GB" : "fr-FR", {
          maximumFractionDigits: decimals,
          minimumFractionDigits: decimals,
        }) +
        suffix;
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function setupReveal() {
    var nodes = document.querySelectorAll(".v-reveal");
    if (!nodes.length) return;
    if (!("IntersectionObserver" in window)) {
      nodes.forEach(function (n) { n.classList.add("is-visible"); });
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          e.target.classList.add("is-visible");
          io.unobserve(e.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    nodes.forEach(function (n) { io.observe(n); });
  }

  function setupCounters() {
    var nodes = document.querySelectorAll("[data-count]");
    if (!nodes.length) return;
    if (!("IntersectionObserver" in window)) {
      nodes.forEach(animateCount);
      return;
    }
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          animateCount(e.target);
          io.unobserve(e.target);
        });
      },
      { threshold: 0.3 }
    );
    nodes.forEach(function (n) { io.observe(n); });
  }

  function setupBookingPreview() {
    var root = document.getElementById("booking-preview");
    if (!root) return;
    var slots = root.querySelectorAll(".v-book-slot");
    var confirm = root.querySelector(".v-book-confirm");
    var idx = 0;
    function cycle() {
      slots.forEach(function (s, i) { s.classList.toggle("active", i === idx); });
      if (confirm) confirm.classList.toggle("show", idx === slots.length - 1);
      idx = (idx + 1) % slots.length;
    }
    cycle();
    setInterval(cycle, 2200);
  }

  function init() {
    setupReveal();
    setupCounters();
    setupBookingPreview();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
