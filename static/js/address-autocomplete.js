/**
 * City and address autocomplete.
 *
 * Suggestions come from our own origin (/api/public/places/autocomplete), which
 * proxies Google Places and falls back to the French Base Adresse Nationale.
 * The Places key is deliberately never shipped to the browser: a key in the
 * page is a public key, and ours is billed per request.
 *
 * Picking a Google suggestion triggers one follow-up call to /resolve, which is
 * what fills the postal-code field and any hidden lat/lng inputs. BAN
 * suggestions already carry their postcode, so they skip that round trip.
 *
 * Progressive enhancement: without JS, or if the endpoint is unreachable, the
 * field stays an ordinary text input and the form still submits.
 */
(function () {
  "use strict";

  var CITY_SEL = 'input[name="ville"], input[name="city"], input[data-places-city]';
  var ADDR_SEL = 'input[name="address"], input[name="adresse"], input[data-places-address]';
  var ENDPOINT = "/api/public/places/autocomplete";
  var RESOLVE = "/api/public/places/resolve";
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
    // Screen readers need to be told this plain input now behaves as a combobox.
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    list.setAttribute("role", "listbox");
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
      input.setAttribute("aria-expanded", "false");
    }

    function fill(form, name, value) {
      if (!form || !value) return;
      var f = form.querySelector('input[name="' + name + '"]');
      if (f && !f.value) f.value = value;
    }

    function applyDetail(form, d) {
      if (!d) return;
      fill(form, "postal_code", d.postcode);
      fill(form, "code_postal", d.postcode);
      // Hidden coordinate fields, when the form collects them.
      fill(form, "latitude", d.latitude);
      fill(form, "longitude", d.longitude);
      // A city field on an address form gets filled in too, when it is empty.
      if (d.city) fill(form, "ville", d.city);
    }

    function choose(i) {
      var it = items[i];
      if (!it) return;
      input.value = it.value;
      var form = input.closest("form");
      applyDetail(form, it);
      // Google suggestions carry no postcode until Place Details is called.
      if (form && it.id && !it.postcode) {
        fetch(RESOLVE + "?id=" + encodeURIComponent(it.id))
          .then(function (r) {
            return r.ok ? r.json() : null;
          })
          .then(function (d) {
            applyDetail(form, d);
          })
          .catch(function () {
            /* the typed address still stands on its own */
          });
      }
      close();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function draw() {
      list.innerHTML = "";
      items.forEach(function (it, i) {
        var li = el("li", "ac-item" + (i === active ? " is-active" : ""));
        li.textContent = it.label;
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", i === active ? "true" : "false");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          choose(i);
        });
        list.appendChild(li);
      });
      list.hidden = items.length === 0;
      input.setAttribute("aria-expanded", items.length ? "true" : "false");
    }

    function search(q) {
      if (controller) controller.abort();
      controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      var url = ENDPOINT + "?q=" + encodeURIComponent(q) + "&kind=" + kind;
      fetch(url, controller ? { signal: controller.signal } : undefined)
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (!data || !data.suggestions) return close();
          items = data.suggestions;
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
