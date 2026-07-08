/* Nova — admin AI copilot chat widget. */
(function () {
  "use strict";

  var fab = document.getElementById("nova-fab");
  var panel = document.getElementById("nova-panel");
  var overlay = document.getElementById("nova-overlay");
  if (!fab || !panel) return;

  var closeBtn = document.getElementById("nova-close");
  var resetBtn = document.getElementById("nova-reset");
  var form = document.getElementById("nova-form");
  var input = document.getElementById("nova-input");
  var sendBtn = document.getElementById("nova-send");
  var messages = document.getElementById("nova-messages");
  var suggests = document.getElementById("nova-suggests");

  var history = [];   // [{role, content}] sent to the API
  var busy = false;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Minimal, safe markdown → HTML (bold, inline code, line breaks, bullets).
  function fmt(text) {
    var lines = esc(text).split(/\n/);
    var out = [];
    var inList = false;
    lines.forEach(function (ln) {
      var bullet = ln.match(/^\s*[-*•]\s+(.*)$/) || ln.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + inline(bullet[1]) + "</li>");
      } else {
        if (inList) { out.push("</ul>"); inList = false; }
        if (ln.trim()) out.push("<p>" + inline(ln) + "</p>");
      }
    });
    if (inList) out.push("</ul>");
    return out.join("");
  }
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function scrollDown() {
    messages.scrollTop = messages.scrollHeight;
  }

  function addBubble(role, html) {
    var wrap = document.createElement("div");
    wrap.className = "nova-msg nova-msg--" + (role === "user" ? "user" : "bot");
    wrap.innerHTML = '<div class="nova-bubble">' + html + "</div>";
    messages.appendChild(wrap);
    scrollDown();
    return wrap;
  }

  function addActions(actions) {
    if (!actions || !actions.length) return;
    var wrap = document.createElement("div");
    wrap.className = "nova-actions";
    wrap.innerHTML = actions.map(function (a) {
      var badge = a.status ? '<span class="nova-action-badge">' + esc(a.status) + "</span>" : "";
      var href = a.url ? esc(a.url) : "#";
      return '<a class="nova-action" href="' + href + '">' +
        '<span class="nova-action-ico">✅</span>' +
        '<span class="nova-action-label">' + esc(a.label) + "</span>" + badge + "</a>";
    }).join("");
    messages.appendChild(wrap);
    scrollDown();
  }

  function addTyping() {
    var wrap = document.createElement("div");
    wrap.className = "nova-msg nova-msg--bot nova-typing-wrap";
    wrap.innerHTML = '<div class="nova-bubble nova-typing"><span></span><span></span><span></span></div>';
    messages.appendChild(wrap);
    scrollDown();
    return wrap;
  }

  function openPanel() {
    panel.classList.add("open");
    if (overlay) overlay.classList.add("open");
    panel.setAttribute("aria-hidden", "false");
    document.body.classList.add("nova-open");
    setTimeout(function () { if (input) input.focus(); }, 260);
  }
  function closePanel() {
    panel.classList.remove("open");
    if (overlay) overlay.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");
    document.body.classList.remove("nova-open");
  }

  fab.addEventListener("click", function () {
    if (panel.classList.contains("open")) closePanel(); else openPanel();
  });
  if (closeBtn) closeBtn.addEventListener("click", closePanel);
  if (overlay) overlay.addEventListener("click", closePanel);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && panel.classList.contains("open")) closePanel();
  });

  if (resetBtn) resetBtn.addEventListener("click", function () {
    history = [];
    messages.querySelectorAll(".nova-msg:not(.nova-msg--intro), .nova-actions").forEach(function (n) { n.remove(); });
    if (suggests) suggests.style.display = "";
  });

  // Auto-grow textarea.
  if (input) {
    input.addEventListener("input", function () {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 140) + "px";
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        form.requestSubmit();
      }
    });
  }

  function send(text) {
    if (busy || !text.trim()) return;
    busy = true;
    if (suggests) suggests.style.display = "none";
    addBubble("user", fmt(text));
    history.push({ role: "user", content: text });
    input.value = "";
    input.style.height = "auto";
    sendBtn.disabled = true;
    var typing = addTyping();

    fetch("/admin/api/assistant/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: JSON.stringify({ message: text, history: history.slice(0, -1) }),
    }).then(function (r) {
      return r.json().then(function (data) { return { ok: r.ok, data: data }; });
    }).then(function (res) {
      typing.remove();
      if (!res.ok || res.data.error) {
        addBubble("bot", '<p class="nova-error">' + esc(res.data.error || "Une erreur est survenue.") + "</p>");
        return;
      }
      var reply = res.data.reply || "…";
      addBubble("bot", fmt(reply));
      addActions(res.data.actions);
      history.push({ role: "assistant", content: reply });
      if (res.data.actions && res.data.actions.length) {
        // A dashboard/content page may now be stale — hint a refresh softly.
        window.dispatchEvent(new CustomEvent("nova:action", { detail: res.data.actions }));
      }
    }).catch(function () {
      typing.remove();
      addBubble("bot", '<p class="nova-error">Connexion à Nova impossible. Réessayez.</p>');
    }).finally(function () {
      busy = false;
      sendBtn.disabled = false;
      if (input) input.focus();
    });
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      send(input.value);
    });
  }

  if (suggests) {
    suggests.querySelectorAll(".nova-chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        openPanel();
        send(chip.getAttribute("data-prompt") || chip.textContent);
      });
    });
  }

  // Allow other parts of the UI (dashboard insight card) to open Nova with a prompt.
  window.NovaAsk = function (prompt) {
    openPanel();
    if (prompt) send(prompt);
  };
})();
