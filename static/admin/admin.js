/* PilotCore Admin — dynamic dashboard, animated charts, live log stream. */
(function () {
  "use strict";

  // ---- sidebar toggle (mobile) ----
  var burger = document.getElementById("admin-burger");
  var sidebar = document.getElementById("admin-sidebar");
  var backdrop = document.getElementById("admin-backdrop");
  function setSidebar(open) {
    if (sidebar) sidebar.classList.toggle("open", open);
    if (backdrop) backdrop.classList.toggle("open", open);
    document.body.classList.toggle("sidebar-open", open);
  }
  if (burger && sidebar) {
    burger.addEventListener("click", function () {
      setSidebar(!sidebar.classList.contains("open"));
    });
  }
  if (backdrop) backdrop.addEventListener("click", function () { setSidebar(false); });
  if (sidebar) {
    sidebar.querySelectorAll(".admin-nav a").forEach(function (a) {
      a.addEventListener("click", function () { setSidebar(false); });
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") setSidebar(false);
  });

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
      grid += '<line x1="' + pad.l + '" y1="' + gy + '" x2="' + (W - pad.r) + '" y2="' + gy + '" stroke="#e4e9f2" stroke-width="1" opacity="0.5"/>';
      grid += '<text x="4" y="' + (gy + 4) + '" fill="#64748b" font-size="10">' + Math.round(max - (max / 4) * g) + '</text>';
    }
    var dots = pts.map(function (p) { return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="2.5" fill="#06b6d4"/>'; }).join("");

    svg.innerHTML =
      '<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#059ce0" stop-opacity="0.35"/>' +
      '<stop offset="100%" stop-color="#059ce0" stop-opacity="0"/></linearGradient></defs>' +
      grid +
      '<path d="' + area + '" fill="url(#areaGrad)"/>' +
      '<path d="' + line + '" fill="none" stroke="#059ce0" stroke-width="2.5" stroke-linejoin="round" class="chart-line"/>' +
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
    var catColors = { auth: "#059ce0", lead: "#059669", quote: "#d97706", email: "#06b6d4", admin: "#dc2626", system: "#64748b" };
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

  // ---- traffic dashboard (GA4-style) ----
  function drawDualLine(svg, series) {
    if (!svg) return;
    var W = 720, H = 260, pad = { l: 34, r: 12, t: 16, b: 22 };
    var iw = W - pad.l - pad.r, ih = H - pad.t - pad.b, n = series.length;
    var max = Math.max(1, series.reduce(function (m, d) { return Math.max(m, d.views, d.visitors); }, 0));
    function x(i) { return pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw); }
    function y(v) { return pad.t + ih - (v / max) * ih; }
    function path(key) {
      return series.map(function (d, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(d[key]).toFixed(1); }).join(" ");
    }
    var grid = "";
    for (var g = 0; g <= 4; g++) {
      var gy = pad.t + (ih / 4) * g;
      grid += '<line x1="' + pad.l + '" y1="' + gy + '" x2="' + (W - pad.r) + '" y2="' + gy + '" stroke="#e4e9f2" opacity="0.5"/>';
      grid += '<text x="4" y="' + (gy + 4) + '" fill="#64748b" font-size="10">' + Math.round(max - (max / 4) * g) + '</text>';
    }
    var viewsLine = path("views"), visLine = path("visitors");
    var area = viewsLine + " L" + x(n - 1).toFixed(1) + " " + (pad.t + ih) + " L" + x(0).toFixed(1) + " " + (pad.t + ih) + " Z";
    svg.innerHTML =
      '<defs><linearGradient id="tGrad" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#059ce0" stop-opacity="0.3"/><stop offset="100%" stop-color="#059ce0" stop-opacity="0"/></linearGradient></defs>' +
      grid +
      '<path d="' + area + '" fill="url(#tGrad)"/>' +
      '<path d="' + viewsLine + '" fill="none" stroke="#059ce0" stroke-width="2.5" class="chart-line"/>' +
      '<path d="' + visLine + '" fill="none" stroke="#06b6d4" stroke-width="2.5" stroke-dasharray="4 4"/>';
    var p = svg.querySelector(".chart-line");
    if (p && p.getTotalLength) {
      var len = p.getTotalLength();
      p.style.strokeDasharray = len; p.style.strokeDashoffset = len;
      p.style.transition = "stroke-dashoffset 1.1s ease";
      requestAnimationFrame(function () { p.style.strokeDashoffset = 0; });
    }
  }

  function drawSparkline(svg, data) {
    if (!svg) return;
    var W = 360, H = 70, n = data.length;
    var max = Math.max(1, data.reduce(function (m, d) { return Math.max(m, d.v); }, 0));
    function x(i) { return (i / Math.max(1, n - 1)) * W; }
    function y(v) { return H - 4 - (v / max) * (H - 10); }
    var bars = data.map(function (d, i) {
      var bw = W / n - 2;
      var h = (d.v / max) * (H - 10);
      return '<rect x="' + (x(i) - bw / 2).toFixed(1) + '" y="' + (H - 4 - h).toFixed(1) + '" width="' + Math.max(1.5, bw).toFixed(1) + '" height="' + Math.max(0, h).toFixed(1) + '" rx="1.5" fill="#06b6d4" opacity="0.85"/>';
    }).join("");
    svg.innerHTML = bars;
  }

  function rankList(host, items, key) {
    if (!host) return;
    if (!items.length) { host.innerHTML = '<span class="admin-empty-inline">Pas encore de données.</span>'; return; }
    var max = Math.max(1, items.reduce(function (m, d) { return Math.max(m, d[key]); }, 0));
    host.innerHTML = items.map(function (d) {
      var pct = (d[key] / max) * 100;
      var label = d.path || d.host || d.label || "";
      return '<div class="rank-row"><span class="rank-bar" style="width:0" data-w="' + pct + '"></span>' +
        '<span class="rank-label" title="' + label + '">' + label + '</span>' +
        '<span class="rank-val">' + fmtInt.format(d[key]) + '</span></div>';
    }).join("");
    requestAnimationFrame(function () {
      host.querySelectorAll(".rank-bar").forEach(function (b) { b.style.width = b.getAttribute("data-w") + "%"; });
    });
  }

  function applyRealtime(rt) {
    var el = document.getElementById("rt-count");
    if (el) animateValue(el, rt.active_visitors || 0);
    drawSparkline(document.getElementById("rt-spark"), rt.sparkline || []);
    var pages = document.getElementById("rt-pages");
    if (pages) {
      if (!rt.active_pages || !rt.active_pages.length) pages.innerHTML = '<span class="admin-empty-inline">Aucune activité pour l\'instant.</span>';
      else pages.innerHTML = rt.active_pages.map(function (p) {
        return '<div class="rt-page"><span class="rt-page-path">' + p.path + '</span><span class="rt-page-count">' + p.views + '</span></div>';
      }).join("");
    }
    var dot = document.getElementById("nav-live-dot");
    if (dot) dot.classList.toggle("on", (rt.active_visitors || 0) > 0);
  }

  function setTrend(id, v) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = (v >= 0 ? "▲ +" : "▼ ") + v + "%";
    el.className = "kpi-trend " + (v >= 0 ? "up" : "down");
  }

  function loadTraffic(days) {
    if (!window.ADMIN_TRAFFIC_URL) return;
    fetch(window.ADMIN_TRAFFIC_URL + "?days=" + days).then(function (r) { return r.json(); }).then(function (d) {
      var k = d.kpis || {};
      animateValue(document.getElementById("tk-visitors"), k.unique_visitors || 0);
      animateValue(document.getElementById("tk-pageviews"), k.pageviews || 0);
      animateValue(document.getElementById("tk-sessions"), k.sessions || 0);
      animateValue(document.getElementById("tk-bounce"), k.bounce_rate || 0);
      setTrend("tk-visitors-trend", k.visitors_trend || 0);
      setTrend("tk-pageviews-trend", k.pageviews_trend || 0);
      var pps = document.getElementById("tk-pps"); if (pps) pps.textContent = k.pages_per_session || 0;
      drawDualLine(document.getElementById("traffic-chart"), d.timeseries || []);
      rankList(document.getElementById("top-pages"), d.top_pages || [], "views");
      rankList(document.getElementById("top-locations"), (d.top_locations || []).map(function (loc) {
        return { label: loc.label, views: loc.visitors };
      }), "views");
      drawBars(document.getElementById("top-countries"), (d.top_countries || []).map(function (c) {
        return { label: c.label, count: c.visitors };
      }));
      rankList(document.getElementById("utm-campaigns"), (d.utm_campaigns || []).map(function (u) {
        return { label: u.label, views: u.views };
      }), "views");
      drawBars(document.getElementById("top-referrers"), (d.top_referrers || []).map(function (r) { return { label: r.host, count: r.views }; }));
      drawBars(document.getElementById("devices"), d.devices || []);
      applyRealtime(d.realtime || {});
      var upd = document.getElementById("traffic-updated");
      if (upd) upd.textContent = "màj " + new Date().toLocaleTimeString("fr-FR");
    }).catch(function () {});
  }

  function loadRealtimeOnly() {
    if (!window.ADMIN_TRAFFIC_RT_URL) return;
    fetch(window.ADMIN_TRAFFIC_RT_URL).then(function (r) { return r.json(); }).then(applyRealtime).catch(function () {});
  }

  var trafficRange = document.getElementById("traffic-range");
  if (document.getElementById("rt-count")) {
    var td = trafficRange ? trafficRange.value : 30;
    loadTraffic(td);
    if (trafficRange) trafficRange.addEventListener("change", function () { loadTraffic(trafficRange.value); });
    setInterval(loadRealtimeOnly, 8000);          // realtime pulse
    setInterval(function () { loadTraffic(trafficRange ? trafficRange.value : 30); }, 45000);
  } else if (window.ADMIN_TRAFFIC_RT_URL) {
    // (not on traffic page) — nothing
  }
})();
