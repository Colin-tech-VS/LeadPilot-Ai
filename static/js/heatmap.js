/* PilotCore — lightweight first-party interaction tracker.
 *
 * Follows each visitor from the moment they arrive: page views, clicks (with
 * position + the element they hit), rage-clicks and scroll depth are batched
 * and POSTed to /api/heatmap/collect. The server attaches the long-lived
 * visitor id from the httpOnly cookie, so one visitor = one continuous journey
 * across every session (never a new heatmap per session). No PII is collected.
 */
(function () {
  "use strict";

  if (!navigator.sendBeacon && !window.fetch) return;
  var path = location.pathname || "/";
  // Never track the admin console itself.
  if (path.indexOf("/admin") === 0) return;

  var ENDPOINT = "/api/heatmap/collect";
  var queue = [];
  var maxScroll = 0;
  var scrollDirty = false;

  function docWidth() {
    var d = document.documentElement, b = document.body;
    return Math.max(d.scrollWidth, d.offsetWidth, b ? b.scrollWidth : 0, b ? b.offsetWidth : 0, window.innerWidth);
  }
  function docHeight() {
    var d = document.documentElement, b = document.body;
    return Math.max(d.scrollHeight, d.offsetHeight, b ? b.scrollHeight : 0, b ? b.offsetHeight : 0, window.innerHeight);
  }

  // Build a compact, stable selector for an element: tag#id.first-class.
  function selectorFor(el) {
    if (!el || !el.tagName) return null;
    var sel = el.tagName.toLowerCase();
    if (el.id) sel += "#" + el.id;
    else if (el.className && typeof el.className === "string") {
      var cls = el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) sel += "." + cls.join(".");
    }
    return sel.slice(0, 300);
  }

  // Prefer the nearest meaningful, clickable ancestor (link / button) so
  // clicks on an icon inside a button are attributed to the button.
  function meaningful(el) {
    var node = el, depth = 0;
    while (node && depth < 4) {
      var tag = node.tagName ? node.tagName.toLowerCase() : "";
      if (tag === "a" || tag === "button" || node.getAttribute && node.getAttribute("role") === "button") return node;
      node = node.parentElement;
      depth++;
    }
    return el;
  }

  function labelFor(el) {
    if (!el) return null;
    var txt = (el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title"))) ||
      (el.innerText || el.textContent || "");
    txt = txt.replace(/\s+/g, " ").trim();
    return txt ? txt.slice(0, 120) : null;
  }

  function push(ev) {
    ev.p = path;
    ev.vw = window.innerWidth;
    ev.vh = window.innerHeight;
    queue.push(ev);
    if (queue.length >= 12) flush(false);
  }

  // --- page view (arrival on this page) ---
  push({ t: "pageview", dw: docWidth(), dh: docHeight() });

  // --- clicks + rage clicks ---
  var recent = [];
  document.addEventListener(
    "click",
    function (e) {
      var target = meaningful(e.target);
      var dw = docWidth();
      var x = dw ? (e.pageX / dw) : 0;
      var now = Date.now();

      recent.push({ x: e.clientX, y: e.clientY, t: now });
      recent = recent.filter(function (r) { return now - r.t < 700; });
      var rage = false;
      if (recent.length >= 3) {
        var near = recent.filter(function (r) {
          return Math.abs(r.x - e.clientX) < 40 && Math.abs(r.y - e.clientY) < 40;
        });
        if (near.length >= 3) { rage = true; recent = []; }
      }

      push({
        t: rage ? "rageclick" : "click",
        x: Math.max(0, Math.min(1, x)),
        y: Math.round(e.pageY),
        dw: dw,
        dh: docHeight(),
        s: selectorFor(target),
        txt: labelFor(target),
      });
    },
    true
  );

  // --- scroll depth (kept as a single max %, flushed with the batch) ---
  window.addEventListener(
    "scroll",
    function () {
      var h = docHeight() - window.innerHeight;
      if (h <= 0) return;
      var pct = Math.round(((window.pageYOffset || 0) / h) * 100);
      if (pct > maxScroll) { maxScroll = Math.min(100, pct); scrollDirty = true; }
    },
    { passive: true }
  );

  function queueScroll() {
    if (scrollDirty) {
      queue.push({ t: "scroll", p: path, sd: maxScroll, dh: docHeight() });
      scrollDirty = false;
    }
  }

  // --- flush ---
  function flush(useBeacon) {
    queueScroll();
    if (!queue.length) return;
    var batch = queue.splice(0, queue.length);
    var body = JSON.stringify({ events: batch });
    var sent = false;
    if (useBeacon && navigator.sendBeacon) {
      try {
        sent = navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
      } catch (err) { sent = false; }
    }
    if (!sent) {
      try {
        fetch(ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true,
          credentials: "same-origin",
        }).catch(function () {});
      } catch (err) { /* give up silently */ }
    }
  }

  setInterval(function () { flush(false); }, 8000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flush(true);
  });
  window.addEventListener("pagehide", function () { flush(true); });
})();
