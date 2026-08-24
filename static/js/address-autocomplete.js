/**
 * City and address autocomplete backed by the French government address API
 * (api-adresse.data.gouv.fr / Base Adresse Nationale).
 *
 * Chosen over Google Places because it needs no key, no billing account and no
 * quota, and it is authoritative for French addresses — which is all this site
 * deals with. It also keeps visitors' keystrokes out of a third-party ad
 * network.
 *
 * Progressive enhancement: without JS, or if the API is unreachable, the field
 * stays an ordinary text input and the form still submits.
 */
(function () {
  "use strict";

  var CITY_SEL = 'input[name="ville"], input[name="city"], input[data-places-city]';
  var ADDR_SEL = 'input[name="address"], input[name="adresse"], input[data-places-address]';
  var ENDPOINT = "https://api-adresse.data.gouv.fr/search/";
  var MIN_CHARS = 2;
  var DEBOUNCE_MS = 220;

  function el(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  function attach(input, kind) {
    if (input.dataset.acBound) return;
    input.dataset.acBound = "1";
    input.setAttribute("autocomplete", "off");

    var wrap = el("div", "ac-wrap");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var list = el("ul", "ac-list");
    list.hidden = true;
    wrap.appendChild(list);

    var timer = null;
    var controller = null;
    var items = [];
    var active = -1;

    function close() {
      list.hidden = true;
      list.innerHTML = "";
      items = [];
      active = -1;
    }

    function choose(i) {
      var it = items[i];
      if (!it) return;
      input.value = it.value;
      // Fill a sibling postcode field when the form has one.
      var form = input.closest("form");
      if (form && it.postcode) {
        var cp = form.querySelector('input[name="postal_code"], input[name="code_postal"]');
        if (cp && !cp.value) cp.value = it.postcode;
      }
      close();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function draw() {
      list.innerHTML = "";
      items.forEach(function (it, i) {
        var li = el("li", "ac-item" + (i === active ? " is-active" : ""));
        li.textContent = it.label;
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          choose(i);
        });
        list.appendChild(li);
      });
      list.hidden = items.length === 0;
    }

    function search(q) {
      if (controller) controller.abort();
      controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      var url =
        ENDPOINT +
        "?q=" +
        encodeURIComponent(q) +
        "&limit=6&autocomplete=1" +
        (kind === "city" ? "&type=municipality" : "");
      fetch(url, controller ? { signal: controller.signal } : undefined)
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (!data || !data.features) return close();
          items = data.features.map(function (f) {
            var p = f.properties || {};
            return {
              // The city field gets the bare commune name so it still matches
              // our own city lookups; the address field keeps the full label.
              value: kind === "city" ? p.city || p.name || p.label : p.label,
              label: kind === "city" ? (p.label || p.name) + (p.postcode ? " (" + p.postcode + ")" : "") : p.label,
              postcode: p.postcode || ""
            };
          });
          active = -1;
          draw();
        })
        .catch(function () {
          /* offline or aborted — leave the plain input alone */
        });
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (timer) clearTimeout(timer);
      if (q.length < MIN_CHARS) return close();
      timer = setTimeout(function () {
        search(q);
      }, DEBOUNCE_MS);
    });

    input.addEventListener("keydown", function (e) {
      if (list.hidden || !items.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        active = (active + 1) % items.length;
        draw();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        active = (active - 1 + items.length) % items.length;
        draw();
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault();
        choose(active);
      } else if (e.key === "Escape") {
        close();
      }
    });

    input.addEventListener("blur", function () {
      setTimeout(close, 120);
    });
  }

  function init() {
    document.querySelectorAll(CITY_SEL).forEach(function (i) {
      attach(i, "city");
    });
    document.querySelectorAll(ADDR_SEL).forEach(function (i) {
      attach(i, "address");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
