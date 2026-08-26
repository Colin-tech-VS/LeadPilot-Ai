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

  var demoIndex = 0;
  var demoPlaying = false;
  var demoToken = 0;

  function markTurn(index) {
    document.querySelectorAll("#pro-demo-transcript [data-demo-turn]").forEach(function (el) {
      el.classList.toggle("is-playing", Number(el.getAttribute("data-demo-turn")) === index);
    });
    if (index >= 0) setDemoStage(index);
  }

  var demoMap = null;
  var demoMarker = null;
  var demoMapReady = false;
  var DEMO_PIN = [48.8698, 2.3075];

  function loadLeaflet(done) {
    if (window.L) {
      done();
      return;
    }
    if (window.__pcLeafletCbs) {
      window.__pcLeafletCbs.push(done);
      return;
    }
    window.__pcLeafletCbs = [done];
    var css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    var script = document.createElement("script");
    script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    script.onload = function () {
      var cbs = window.__pcLeafletCbs || [];
      window.__pcLeafletCbs = null;
      cbs.forEach(function (fn) {
        fn();
      });
    };
    script.onerror = function () {
      window.__pcLeafletCbs = null;
    };
    document.head.appendChild(script);
  }

  function ensureDemoMap(then) {
    var el = document.getElementById("pro-demo-map");
    if (!el) return;
    loadLeaflet(function () {
      if (!window.L) return;
      if (!demoMap) {
        demoMap = L.map(el, { zoomControl: false, attributionControl: true, scrollWheelZoom: false }).setView(DEMO_PIN, 13);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "&copy; OpenStreetMap",
          maxZoom: 19,
        }).addTo(demoMap);
        demoMapReady = true;
        window.setTimeout(function () {
          if (demoMap) demoMap.invalidateSize();
        }, 200);
      }
      if (then) then();
    });
  }

  function demoPinIcon() {
    var labels = window.PRO_DEMO_DASH || {};
    return L.divIcon({
      className: "cv-demo-pin",
      html: '<div class="cv-demo-pin-inner">' + (labels.pin || "") + "</div>",
      iconSize: [48, 48],
      iconAnchor: [24, 48],
      popupAnchor: [0, -40],
    });
  }

  function setDemoStage(index) {
    var dash = document.getElementById("pro-demo-dash");
    if (!dash) return;
    dash.setAttribute("data-stage", String(index));
    var labels = window.PRO_DEMO_DASH || {};
    var status = dash.querySelector("[data-demo-status]");
    if (status) {
      if (index < 0) status.textContent = labels.idle || "";
      else if (index >= 3) status.textContent = labels.done || "";
      else status.textContent = labels.live || "";
    }
    var calls = dash.querySelector('[data-demo-kpi="calls"]');
    var leads = dash.querySelector('[data-demo-kpi="leads"]');
    var jobs = dash.querySelector('[data-demo-kpi="jobs"]');
    if (calls) calls.textContent = index >= 0 ? "1" : "0";
    if (leads) leads.textContent = index >= 1 ? "1" : "0";
    if (jobs) jobs.textContent = index >= 3 ? "1" : "0";
    var count = dash.querySelector("[data-demo-lead-count]");
    if (count) count.hidden = index < 1;
    ensureDemoMap(function () {
      if (!demoMap) return;
      demoMap.invalidateSize();
      var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (index >= 1) {
        var popup =
          "<strong>" +
          (labels.client || "") +
          "</strong><br>" +
          (labels.issue || "") +
          "<br>" +
          (labels.address || "");
        if (!demoMarker) {
          demoMarker = L.marker(DEMO_PIN, { icon: demoPinIcon() }).addTo(demoMap);
          demoMarker.bindPopup(popup);
        }
        if (index >= 2) demoMarker.openPopup();
        else demoMarker.closePopup();
        if (reduce) demoMap.setView(DEMO_PIN, 15);
        else demoMap.flyTo(DEMO_PIN, 15, { duration: 0.7 });
      } else if (demoMarker) {
        demoMap.removeLayer(demoMarker);
        demoMarker = null;
        demoMap.setView(DEMO_PIN, 13);
      }
    });
  }

  function setListenLabel(playing) {
    document.querySelectorAll("[data-demo-listen]").forEach(function (btn) {
      var play = btn.getAttribute("data-label-play");
      var pause = btn.getAttribute("data-label-pause");
      if (!play) return;
      btn.textContent = playing && pause ? pause : play;
    });
  }

  function stopDemo() {
    demoPlaying = false;
    demoToken += 1;
    var audio = document.getElementById("pro-demo-audio");
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
    }
    if (window.speechSynthesis) speechSynthesis.cancel();
    markTurn(-1);
    setListenLabel(false);
  }

  function pickVoice(role, lang) {
    if (!window.speechSynthesis) return null;
    var wanted = (lang || "fr-FR").toLowerCase().slice(0, 2);
    var voices = speechSynthesis.getVoices() || [];
    var matches = voices.filter(function (v) {
      return (v.lang || "").toLowerCase().indexOf(wanted) === 0;
    });
    if (!matches.length) matches = voices;
    if (!matches.length) return null;
    if (role === "client" && matches.length > 1) return matches[1];
    return matches[0];
  }

  function playClip(playlist, index, token) {
    var audio = document.getElementById("pro-demo-audio");
    if (token !== demoToken) return;
    if (index >= playlist.length) {
      stopDemo();
      setDemoStage(playlist.length - 1);
      return;
    }
    demoIndex = index;
    markTurn(index);
    var clip = playlist[index];
    var next = function () {
      if (token !== demoToken) return;
      window.setTimeout(function () {
        playClip(playlist, index + 1, token);
      }, 280);
    };
    if (clip.src && audio) {
      audio.src = clip.src;
      audio.onended = next;
      audio.play().catch(function () {
        stopDemo();
      });
      return;
    }
    if (!window.speechSynthesis || !clip.text) {
      stopDemo();
      return;
    }
    var utter = new SpeechSynthesisUtterance(clip.text);
    utter.lang = clip.lang || document.documentElement.lang || "fr-FR";
    var voice = pickVoice(clip.role, utter.lang);
    if (voice) utter.voice = voice;
    utter.rate = clip.role === "ai" ? 0.96 : 1.04;
    utter.pitch = clip.role === "ai" ? 1.08 : 0.92;
    utter.onend = next;
    utter.onerror = function () {
      stopDemo();
    };
    speechSynthesis.speak(utter);
  }

  function toggleDemoPlaylist(playlist) {
    if (demoPlaying) {
      stopDemo();
      return;
    }
    demoPlaying = true;
    demoToken += 1;
    setListenLabel(true);
    setDemoStage(0);
    if (window.speechSynthesis) speechSynthesis.getVoices();
    playClip(playlist, 0, demoToken);
  }

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
    var playlist = window.PRO_DEMO_AUDIO || [];
    if (playlist.length) {
      e.preventDefault();
      var flow = document.getElementById("demo-flow");
      if (flow) flow.scrollIntoView({ behavior: "smooth", block: "start" });
      if (!demoPlaying) track("demo_audio_play");
      toggleDemoPlaylist(playlist);
      window.setTimeout(function () {
        if (demoMap) demoMap.invalidateSize();
      }, 450);
      return;
    }
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

  var mapEl = document.getElementById("pro-demo-map");
  if (mapEl) {
    var bootMap = function () {
      ensureDemoMap();
    };
    if ("IntersectionObserver" in window) {
      var observer = new IntersectionObserver(
        function (entries) {
          if (entries.some(function (entry) { return entry.isIntersecting; })) {
            observer.disconnect();
            bootMap();
          }
        },
        { rootMargin: "160px" }
      );
      observer.observe(mapEl);
    } else {
      bootMap();
    }
  }
})();
