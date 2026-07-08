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

  // Safe markdown → HTML: headings, bold/italic, inline code, links, ordered &
  // unordered lists, blockquotes, line breaks. Emojis pass through untouched.
  function fmt(text) {
    var lines = esc(text).split(/\n/);
    var out = [];
    var listType = null; // "ul" | "ol" | null
    function closeList() { if (listType) { out.push("</" + listType + ">"); listType = null; } }
    lines.forEach(function (ln) {
      var heading = ln.match(/^\s*(#{1,4})\s+(.*)$/);
      var ol = ln.match(/^\s*\d+[.)]\s+(.*)$/);
      var ul = ln.match(/^\s*[-*•]\s+(.*)$/);
      var quote = ln.match(/^\s*>\s+(.*)$/);
      if (heading) {
        closeList();
        var lvl = Math.min(4, heading[1].length) + 2; // #→h3 … ####→h6
        out.push("<h" + lvl + ' class="nova-h">' + inline(heading[2]) + "</h" + lvl + ">");
      } else if (ol) {
        if (listType !== "ol") { closeList(); out.push("<ol>"); listType = "ol"; }
        out.push("<li>" + inline(ol[1]) + "</li>");
      } else if (ul) {
        if (listType !== "ul") { closeList(); out.push("<ul>"); listType = "ul"; }
        out.push("<li>" + inline(ul[1]) + "</li>");
      } else if (quote) {
        closeList();
        out.push('<blockquote class="nova-quote">' + inline(quote[1]) + "</blockquote>");
      } else {
        closeList();
        if (ln.trim()) out.push("<p>" + inline(ln) + "</p>");
      }
    });
    closeList();
    return out.join("");
  }
  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g,
        '<a href="$2" class="nova-link" target="_blank" rel="noopener">$1</a>')
      .replace(/(^|[\s(])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\s][^_]*?)_/g, "$1<em>$2</em>");
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

  // Clickable "next step" buttons Nova proposes. Clicking one sends its prompt.
  function addSuggestions(items) {
    if (!items || !items.length) return;
    var wrap = document.createElement("div");
    wrap.className = "nova-suggest-actions";
    items.slice(0, 4).forEach(function (a) {
      if (!a || !a.label) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "nova-suggest-btn nova-suggest-btn--" + (a.style || "primary");
      btn.textContent = a.label;
      var prompt = a.prompt || a.label;
      btn.addEventListener("click", function () {
        if (btn.disabled) return;
        // Consume the whole group so a proposal isn't triggered twice.
        wrap.querySelectorAll(".nova-suggest-btn").forEach(function (b) { b.disabled = true; });
        wrap.classList.add("is-used");
        send(prompt);
      });
      wrap.appendChild(btn);
    });
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
    messages.querySelectorAll(".nova-msg:not(.nova-msg--intro), .nova-actions, .nova-suggest-actions").forEach(function (n) { n.remove(); });
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
      addSuggestions(res.data.suggestions);
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
