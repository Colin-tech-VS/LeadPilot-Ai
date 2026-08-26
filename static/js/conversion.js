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
})();
