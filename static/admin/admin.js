/* LeadPilot Admin — dynamic dashboard, animated charts, live log stream. */
(function () {
  "use strict";

  // ---- sidebar toggle (mobile) ----
  var burger = document.getElementById("admin-burger");
  var sidebar = document.getElementById("admin-sidebar");
  if (burger && sidebar) {
    burger.addEventListener("click", function () { sidebar.classList.toggle("open"); });
  }

  var fmtInt = new Intl.NumberFormat("fr-FR");
  function fmtMoney(cents) {
    return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format((cents || 0) / 100);
  }

  // ---- animated number ----
  function animateValue(el, to) {
    var suffix = el.getAttribute("data-suffix") || "";
    var isMoney = el.getAttribute("data-money") === "1";
    var from = parseFloat(el.getAttribute("data-current") || "0");
    var start = performance.now();
    var dur = 900;
    function step(now) {
      var t = Math.min(1, (now - start) / dur);
      var eased = 1 - Math.pow(1 - t, 3);
      var val = from + (to - from) * eased;
      if (isMoney) el.textContent = fmtMoney(val);
      else if (suffix === "%") el.textContent = (Math.round(val * 10) / 10) + suffix;
      else el.textContent = fmtInt.format(Math.round(val)) + suffix;
      if (t < 1) requestAnimationFrame(step);
      else el.setAttribute("data-current", to);
    }
    requestAnimationFrame(step);
  }

  // ---- SVG line chart ----
  function drawLineChart(svg, series) {
    if (!svg) return;
    var W = 720, H = 260, pad = { l: 34, r: 12, t: 16, b: 26 };
    var max = Math.max(1, series.reduce(function (m, d) { return Math.max(m, d.count); }, 0));
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    var n = series.length;
    function x(i) { return pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw); }
    function y(v) { return pad.t + ih - (v / max) * ih; }

    var pts = series.map(function (d, i) { return [x(i), y(d.count)]; });
    var line = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join(" ");
    var area = line + " L" + x(n - 1).toFixed(1) + " " + (pad.t + ih) + " L" + x(0).toFixed(1) + " " + (pad.t + ih) + " Z";

    var grid = "";
    for (var g = 0; g <= 4; g++) {
      var gy = pad.t + (ih / 4) * g;
      grid += '<line x1="' + pad.l + '" y1="' + gy + '" x2="' + (W - pad.r) + '" y2="' + gy + '" stroke="#26315c" stroke-width="1" opacity="0.5"/>';
      grid += '<text x="4" y="' + (gy + 4) + '" fill="#8b96c4" font-size="10">' + Math.round(max - (max / 4) * g) + '</text>';
    }
    var dots = pts.map(function (p) { return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="2.5" fill="#22d3ee"/>'; }).join("");

    svg.innerHTML =
      '<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#6d8bff" stop-opacity="0.35"/>' +
      '<stop offset="100%" stop-color="#6d8bff" stop-opacity="0"/></linearGradient></defs>' +
      grid +
      '<path d="' + area + '" fill="url(#areaGrad)"/>' +
      '<path d="' + line + '" fill="none" stroke="#6d8bff" stroke-width="2.5" stroke-linejoin="round" class="chart-line"/>' +
      dots;

    var path = svg.querySelector(".chart-line");
    if (path && path.getTotalLength) {
      var len = path.getTotalLength();
      path.style.strokeDasharray = len;
      path.style.strokeDashoffset = len;
      path.style.transition = "stroke-dashoffset 1.1s ease";
      requestAnimationFrame(function () { path.style.strokeDashoffset = 0; });
    }
  }

  // ---- funnel ----
  function drawFunnel(host, steps) {
    if (!host) return;
    host.innerHTML = steps.map(function (s) {
      return '<div class="funnel-step">' +
        '<div class="funnel-bar" data-pct="' + s.pct + '">' + s.label + ' · ' + fmtInt.format(s.count) + '</div>' +
        '<div class="funnel-meta"><span>' + s.label + '</span><span>' + s.pct + '%</span></div></div>';
    }).join("");
    requestAnimationFrame(function () {
      host.querySelectorAll(".funnel-bar").forEach(function (bar) {
        bar.style.width = Math.max(6, parseFloat(bar.getAttribute("data-pct"))) + "%";
      });
    });
  }

  // ---- horizontal bars ----
  function drawBars(host, items) {
    if (!host) return;
    var max = Math.max(1, items.reduce(function (m, d) { return Math.max(m, d.count); }, 0));
    host.innerHTML = items.map(function (d) {
      var pct = (d.count / max) * 100;
      return '<div class="bar-row"><span>' + d.label + '</span>' +
        '<span class="bar-track"><span class="bar-fill" data-w="' + pct + '"></span></span>' +
        '<span class="bar-val">' + fmtInt.format(d.count) + '</span></div>';
    }).join("");
    requestAnimationFrame(function () {
      host.querySelectorAll(".bar-fill").forEach(function (f) { f.style.width = f.getAttribute("data-w") + "%"; });
    });
  }

  // ---- analytics dashboard ----
  function loadAnalytics(days) {
    if (!window.ADMIN_ANALYTICS_URL) return;
    fetch(window.ADMIN_ANALYTICS_URL + "?days=" + days, { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var k = data.kpis || {};
        document.querySelectorAll("[data-kpi]").forEach(function (card) {
          var key = card.getAttribute("data-kpi");
          var el = card.querySelector("[data-count]");
          if (el && key in k) animateValue(el, k[key]);
        });
        document.querySelectorAll("[data-kpi-inline]").forEach(function (el) {
          var key = el.getAttribute("data-kpi-inline");
          if (key in k) el.textContent = fmtInt.format(k[key]);
        });
        var trend = document.querySelector('[data-trend="leads_trend"]');
        if (trend && "leads_trend" in k) {
          var v = k.leads_trend;
          trend.textContent = (v >= 0 ? "▲ +" : "▼ ") + v + "%";
          trend.className = "kpi-trend " + (v >= 0 ? "up" : "down");
        }
        drawLineChart(document.getElementById("leads-chart"), data.leads_timeseries || []);
        drawFunnel(document.getElementById("funnel"), data.funnel || []);
        drawBars(document.getElementById("urgency-chart"), data.urgency || []);
        drawBars(document.getElementById("plans-chart"), data.plans || []);
        var upd = document.getElementById("analytics-updated");
        if (upd) upd.textContent = "màj " + new Date().toLocaleTimeString("fr-FR");
      })
      .catch(function () {});
  }

  var rangeSel = document.getElementById("range-select");
  if (document.getElementById("kpi-grid")) {
    var currentDays = rangeSel ? rangeSel.value : 30;
    loadAnalytics(currentDays);
    if (rangeSel) rangeSel.addEventListener("change", function () { loadAnalytics(rangeSel.value); });
    setInterval(function () { loadAnalytics(rangeSel ? rangeSel.value : 30); }, 30000);
  }

  // ---- live log stream ----
  var stream = document.getElementById("log-stream");
  var liveToggle = document.getElementById("live-toggle");
  if (stream && window.ADMIN_LOGS_URL) {
    var catColors = { auth: "#6d8bff", lead: "#34d399", quote: "#fbbf24", email: "#22d3ee", admin: "#f87171", system: "#8b96c4" };
    function newest() {
      var first = stream.querySelector(".log-row");
      return first ? first.getAttribute("data-created") : "";
    }
    function poll() {
      if (liveToggle && !liveToggle.checked) return;
      fetch(window.ADMIN_LOGS_URL + "?since=" + encodeURIComponent(newest()))
        .then(function (r) { return r.json(); })
        .then(function (events) {
          if (!events || !events.length) return;
          events.reverse().forEach(function (e) {
            var row = document.createElement("div");
            row.className = "log-row log-" + (e.level || "info") + " new-row";
            row.setAttribute("data-created", e.created_at || "");
            var t = e.created_at ? new Date(e.created_at).toLocaleString("fr-FR") : "";
            row.innerHTML =
              '<span class="log-time">' + t + '</span>' +
              '<span class="log-cat cat-' + e.category + '">' + e.category + '</span>' +
              '<span class="log-action">' + (e.action || "") + '</span>' +
              '<span class="log-summary">' + (e.summary || "") + '</span>' +
              '<span class="log-actor">' + (e.actor || "") + '</span>';
            var empty = stream.querySelector(".admin-empty");
            if (empty) empty.remove();
            stream.insertBefore(row, stream.firstChild);
          });
        })
        .catch(function () {});
    }
    setInterval(poll, 5000);
  }
})();
