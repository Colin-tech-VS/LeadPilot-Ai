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
  // Never track the read-only preview the admin heatmap embeds in an iframe:
  // it would record fake page views / clicks for a page nobody really visited.
  if (location.search.indexOf("hmpreview=1") !== -1) return;
  try { if (window.self !== window.top) return; } catch (e) { return; }

  var ENDPOINT = "/api/heatmap/collect";
  var REC_ENDPOINT = "/api/heatmap/record";
  var queue = [];
  var maxScroll = 0;
  var scrollDirty = false;

  // --- session recording ("film" of the cursor) ---
  // A compact time-series of pointer moves / clicks / scroll positions, replayed
  // in the admin as a video of the visit. Coordinates match the heatmap: x is a
  // 0-1 fraction of the document width, y an absolute pixel offset in the page.
  var REC_ID = (function () {
    try {
      if (window.crypto && crypto.getRandomValues) {
        var a = new Uint8Array(16);
        crypto.getRandomValues(a);
        return Array.prototype.map.call(a, function (b) { return (b + 256).toString(16).slice(1); }).join("");
      }
    } catch (e) { /* fall through */ }
    return "r" + Date.now().toString(16) + Math.random().toString(16).slice(2, 10);
  })();
  var REC_START = Date.now();
  var REC_MOVE_MS = 70;      // sample the pointer at most this often
  var REC_MAX_MOVES = 4000;  // hard client cap (mirrors the server)
  var recMoves = [];
  var recClicks = [];
  var recScrolls = [];
  var recLastMove = 0;
  var recDirty = false;
  function recT() { return Date.now() - REC_START; }

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

  // Seed the replay with the arrival scroll position (usually 0, but anchor
  // links / restored scroll can land the visitor mid-page) so the camera in the
  // admin film starts exactly where the visitor really started.
  recScrolls.push([0, Math.round(window.pageYOffset || 0)]);

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

      // Recording: mark the click on the cursor film.
      if (recClicks.length < 400) {
        recClicks.push([recT(), Math.max(0, Math.min(1, x)), Math.round(e.pageY)]);
        recDirty = true;
      }
    },
    true
  );

  // --- recording: throttled pointer track ---
  window.addEventListener(
    "mousemove",
    function (e) {
      var now = Date.now();
      if (now - recLastMove < REC_MOVE_MS) return;
      recLastMove = now;
      if (recMoves.length >= REC_MAX_MOVES) return;
      var dw = docWidth();
      recMoves.push([recT(), dw ? Math.max(0, Math.min(1, e.pageX / dw)) : 0, Math.round(e.pageY)]);
      recDirty = true;
    },
    { passive: true }
  );

  // --- scroll depth (kept as a single max %, flushed with the batch) ---
  window.addEventListener(
    "scroll",
    function () {
      var h = docHeight() - window.innerHeight;
      if (h <= 0) return;
      var pct = Math.round(((window.pageYOffset || 0) / h) * 100);
      if (pct > maxScroll) { maxScroll = Math.min(100, pct); scrollDirty = true; }

      // Recording: sample absolute scroll position so the replay scrolls too.
      if (recScrolls.length < 1000) {
        var yOff = Math.round(window.pageYOffset || 0);
        var last = recScrolls[recScrolls.length - 1];
        if (!last || Math.abs(last[1] - yOff) > 8) {
          recScrolls.push([recT(), yOff]);
          recDirty = true;
        }
      }
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

  // --- recording flush: send the full, growing track (server upserts by id) ---
  function recFlush(useBeacon) {
    if (!recDirty) return;
    if (recMoves.length + recClicks.length + recScrolls.length < 4) return;
    recDirty = false;
    var body = JSON.stringify({
      rec_id: REC_ID,
      p: path,
      vw: window.innerWidth,
      vh: window.innerHeight,
      dw: docWidth(),
      dh: docHeight(),
      dur: recT(),
      track: { m: recMoves, c: recClicks, s: recScrolls },
    });
    var sent = false;
    if (useBeacon && navigator.sendBeacon) {
      try {
        sent = navigator.sendBeacon(REC_ENDPOINT, new Blob([body], { type: "application/json" }));
      } catch (err) { sent = false; }
    }
    if (!sent) {
      try {
        fetch(REC_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true,
          credentials: "same-origin",
        }).catch(function () {});
      } catch (err) { /* give up silently */ }
    }
  }

  setInterval(function () { flush(false); recFlush(false); }, 8000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { flush(true); recFlush(true); }
  });
  window.addEventListener("pagehide", function () { flush(true); recFlush(true); });
})();
