(function () {
  document.querySelectorAll(".landing-fade").forEach(function (el) {
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );
    observer.observe(el);
  });

  var i18n = window.LANDING_I18N || {};
  var chat = document.getElementById("demo-chat");
  var transcript = document.getElementById("demo-transcript");
  var runBtn = document.getElementById("demo-run");
  var resultEmpty = document.getElementById("demo-result-empty");
  var resultBody = document.getElementById("demo-result-body");

  function addUtterance(text, role) {
    if (!chat) return;
    var wrap = document.createElement("div");
    wrap.className = "demo-utterance demo-utterance-" + role;

    var speaker = document.createElement("span");
    speaker.className = "demo-speaker";
    speaker.textContent =
      role === "client"
        ? (i18n.speakers && i18n.speakers.client) || "Caller"
        : (i18n.speakers && i18n.speakers.ai) || "Voice AI";

    var body = document.createElement("p");
    body.textContent = text;

    wrap.appendChild(speaker);
    wrap.appendChild(body);
    chat.appendChild(wrap);
    chat.scrollTop = chat.scrollHeight;
  }

  function showResult(data) {
    var extracted = data.extracted_data || {};
    var booking = data.booking || {};
    var action = booking.action || "";

    resultEmpty.hidden = true;
    resultBody.hidden = false;

    var tagsEl = document.getElementById("demo-extracted");
    tagsEl.innerHTML = "";
    [
      extracted.name && ("Nom: " + extracted.name),
      extracted.phone && ("Tel: " + extracted.phone),
      extracted.address && ("Adr: " + extracted.address),
      extracted.issue_type && extracted.issue_type,
      extracted.urgency_level && ("Urgence: " + extracted.urgency_level),
    ]
      .filter(Boolean)
      .forEach(function (t) {
        var span = document.createElement("span");
        span.className = "demo-tag";
        span.textContent = t;
        tagsEl.appendChild(span);
      });

    var actionEl = document.getElementById("demo-action");
    var label = (i18n.booking && i18n.booking[action]) || action;
    actionEl.textContent = label;
    actionEl.className = "demo-action-badge";
    if (action === "OUT_OF_ZONE") actionEl.classList.add("out-zone");
    else if (action === "CALL_BACK" || action === "SEND_QUOTE") actionEl.classList.add("pending");

    document.getElementById("demo-score").textContent =
      (booking.priority_score != null ? booking.priority_score : "—") + " / 100";
    document.getElementById("demo-reason").textContent = booking.reason || "";
  }

  function aiResponse(booking) {
    var action = (booking && booking.action) || "";
    var replies = i18n.replies || {};
    if (action === "BOOK_NOW") return replies.BOOK_NOW || "";
    if (action === "OUT_OF_ZONE") return replies.OUT_OF_ZONE || "";
    if (action === "SEND_QUOTE") return replies.SEND_QUOTE || "";
    return replies.default || "";
  }

  async function runDemo() {
    if (!transcript || !runBtn) return;
    var text = transcript.value.trim();
    if (!text) return;

    runBtn.disabled = true;
    runBtn.textContent = i18n.processing || "...";

    addUtterance(text, "client");

    try {
      var res = await fetch("/demo/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: text, phone: "+33612345678" }),
      });
      var data = await res.json();

      if (!res.ok) throw new Error(data.error || "failed");

      setTimeout(function () {
        addUtterance(aiResponse(data.booking), "ai");
        showResult(data);
      }, 800);
    } catch (e) {
      addUtterance(i18n.error || "Erreur demo", "ai");
    } finally {
      runBtn.disabled = false;
      runBtn.textContent = i18n.run || "Simuler";
    }
  }

  if (runBtn) runBtn.addEventListener("click", runDemo);

  document.querySelectorAll(".demo-scenario-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var key = btn.getAttribute("data-scenario");
      if (i18n.scenarios && i18n.scenarios[key] && transcript) {
        transcript.value = i18n.scenarios[key];
      }
    });
  });
})();
