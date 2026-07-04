/* Commercial chatbot widget — shared by the owner preview and the public page.
 *
 * Progressive enhancement: any `.lp-chat` element with a `data-endpoint` is
 * wired up. The transcript lives in the browser and is posted back each turn
 * (the server is stateless), along with any `lead_id` already created so a
 * captured lead is refreshed rather than duplicated.
 */
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function initChat(root) {
    var endpoint = root.getAttribute('data-endpoint');
    if (!endpoint) return;

    var scroll = root.querySelector('.lp-chat-scroll');
    var form = root.querySelector('.lp-chat-form');
    var input = root.querySelector('.lp-chat-input');
    var sendBtn = root.querySelector('.lp-chat-send');
    if (!scroll || !form || !input) return;

    var labels = {
      you: root.getAttribute('data-label-you') || 'You',
      assistant: root.getAttribute('data-label-assistant') || 'Assistant',
      typing: root.getAttribute('data-label-typing') || '…',
      error: root.getAttribute('data-label-error') || 'Something went wrong.',
      captured: root.getAttribute('data-label-captured') || '',
    };

    var history = [];
    var leadId = null;
    var captured = false;
    var busy = false;

    var greeting = root.getAttribute('data-greeting');
    if (greeting) {
      addBubble('assistant', greeting);
      history.push({ role: 'assistant', text: greeting });
    }

    function scrollDown() {
      scroll.scrollTop = scroll.scrollHeight;
    }

    function addBubble(role, text) {
      var row = document.createElement('div');
      row.className = 'lp-chat-row lp-chat-row-' + role;
      var bubble = document.createElement('div');
      bubble.className = 'lp-chat-bubble lp-chat-bubble-' + role;
      bubble.textContent = text;
      var who = document.createElement('span');
      who.className = 'lp-chat-who';
      who.textContent = role === 'user' ? labels.you : labels.assistant;
      row.appendChild(who);
      row.appendChild(bubble);
      scroll.appendChild(row);
      scrollDown();
      return row;
    }

    function addTyping() {
      var row = document.createElement('div');
      row.className = 'lp-chat-row lp-chat-row-assistant lp-chat-typing';
      row.innerHTML =
        '<span class="lp-chat-who">' + escapeHtml(labels.assistant) + '</span>' +
        '<div class="lp-chat-bubble lp-chat-bubble-assistant lp-chat-dots">' +
        '<span></span><span></span><span></span></div>';
      scroll.appendChild(row);
      scrollDown();
      return row;
    }

    function addNotice(text) {
      var row = document.createElement('div');
      row.className = 'lp-chat-notice';
      row.textContent = text;
      scroll.appendChild(row);
      scrollDown();
    }

    function escapeHtml(s) {
      var d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }

    function setBusy(state) {
      busy = state;
      input.disabled = state;
      if (sendBtn) sendBtn.disabled = state;
    }

    async function send(text) {
      if (busy || !text) return;
      addBubble('user', text);
      history.push({ role: 'user', text: text });
      setBusy(true);
      var typing = addTyping();

      try {
        var res = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, history: history, lead_id: leadId }),
        });
        var data = await res.json();
        typing.remove();

        if (!res.ok) {
          addNotice(data.error || labels.error);
          return;
        }

        var reply = data.reply || '';
        if (reply) {
          addBubble('assistant', reply);
          history.push({ role: 'assistant', text: reply });
        }
        if (data.lead_id) leadId = data.lead_id;
        if (data.lead_captured && !captured && labels.captured) {
          captured = true;
          addNotice(labels.captured);
        }
      } catch (err) {
        typing.remove();
        addNotice(labels.error);
      } finally {
        setBusy(false);
        input.focus();
      }
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = (input.value || '').trim();
      if (!text) return;
      input.value = '';
      autoGrow();
      send(text);
    });

    // Enter to send, Shift+Enter for a newline (textarea only).
    if (input.tagName === 'TEXTAREA') {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          form.dispatchEvent(new Event('submit', { cancelable: true }));
        }
      });
      input.addEventListener('input', autoGrow);
    }

    function autoGrow() {
      if (input.tagName !== 'TEXTAREA') return;
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    }

    input.focus();
  }

  ready(function () {
    document.querySelectorAll('.lp-chat').forEach(initChat);
  });
})();
