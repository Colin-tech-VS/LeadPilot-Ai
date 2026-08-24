/**
 * City and address autocomplete.
 *
 * Suggestions come from our own origin (/api/public/places/autocomplete), which
 * proxies Google Places and falls back to the French Base Adresse Nationale.
 * The Places key is deliberately never shipped to the browser: a key in the
 * page is a public key, and ours is billed per request.
 *
 * The suggestion list is portaled onto document.body (position: fixed). Search
 * bars clip overflow to keep their rounded pill, which used to swallow the
 * dropdown — it rendered "inside" the input and was invisible.
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

  var WHERE_SEL = [
    "input[data-places-where]",
    "input.places-where",
  ].join(", ");
  var CITY_SEL = [
    'input[name="city"]',
    "input[data-places-city]",
    "input.places-city",
  ].join(", ");
  var ADDR_SEL = [
    'input[name="address"]',
    'input[name="adresse"]',
    'input[name="client_address"]',
    "input[data-places-address]",
    "input.places-address",
  ].join(", ");
  var ENDPOINT = "/api/public/places/autocomplete";
  var RESOLVE = "/api/public/places/resolve";
  var MIN_CHARS = 2;
  var DEBOUNCE_MS = 220;
  var listSeq = 0;

  function el(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  function attach(input, kind) {
    if (!input || input.dataset.acBound) return;
    if (input.disabled || input.readOnly || input.type === "hidden") return;
    input.dataset.acBound = "1";
    input.setAttribute("autocomplete", "off");
    input.classList.add("ac-input");

    var listId = "ac-list-" + ++listSeq;
    var list = el("ul", "ac-list");
    list.id = listId;
    list.hidden = true;
    list.setAttribute("role", "listbox");
    document.body.appendChild(list);

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", listId);

    var timer = null;
    var controller = null;
    var items = [];
    var active = -1;
    var provider = "";

    function close() {
      list.hidden = true;
      list.innerHTML = "";
      items = [];
      active = -1;
      provider = "";
      input.setAttribute("aria-expanded", "false");
    }

    function placeList() {
      var r = input.getBoundingClientRect();
      var width = Math.max(r.width, 240);
      var left = r.left;
      if (left + width > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - width - 8);
      }
      list.style.left = Math.round(Math.max(8, left)) + "px";
      list.style.width = Math.round(width) + "px";
      var spaceBelow = window.innerHeight - r.bottom;
      if (spaceBelow < 200 && r.top > spaceBelow) {
        list.style.top = "auto";
        list.style.bottom = Math.round(window.innerHeight - r.top + 4) + "px";
      } else {
        list.style.bottom = "auto";
        list.style.top = Math.round(r.bottom + 4) + "px";
      }
    }

    function fill(form, name, value) {
      if (!form || !value) return;
      var f = form.querySelector('input[name="' + name + '"]');
      if (!f) return;
      if (f === input) return;
      if (!f.value) f.value = value;
    }

    function applyDetail(form, d) {
      if (!d) return;
      fill(form, "postal_code", d.postcode);
      fill(form, "code_postal", d.postcode);
      fill(form, "latitude", d.latitude);
      fill(form, "longitude", d.longitude);
      if (d.city) fill(form, "ville", d.city);
      if (d.city) fill(form, "city", d.city);
    }

    function choose(i) {
      var it = items[i];
      if (!it) return;
      input.value = it.value;
      if (it.city) input.dataset.placeCity = it.city;
      var form = input.closest("form");
      applyDetail(form, it);
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

    function poweredByGoogle() {
      var li = el("li", "ac-powered");
      li.setAttribute("aria-hidden", "true");
      var img = document.createElement("img");
      img.alt = "powered by Google";
      img.width = 104;
      img.height = 16;
      img.src = "https://maps.gstatic.com/mapfiles/api-3/images/powered-by-google-on-white3.png";
      li.appendChild(img);
      li.addEventListener("mousedown", function (e) {
        e.preventDefault();
      });
      return li;
    }

    function draw() {
      list.innerHTML = "";
      items.forEach(function (it, i) {
        var li = el("li", "ac-item" + (i === active ? " is-active" : ""));
        var main = el("span", "ac-item-main");
        main.textContent = it.main || it.label;
        li.appendChild(main);
        if (it.secondary) {
          var sub = el("span", "ac-item-secondary");
          sub.textContent = it.secondary;
          li.appendChild(sub);
        }
        li.setAttribute("role", "option");
        li.setAttribute("id", listId + "-opt-" + i);
        li.setAttribute("aria-selected", i === active ? "true" : "false");
        li.addEventListener("mousedown", function (e) {
          e.preventDefault();
          choose(i);
        });
        list.appendChild(li);
      });
      if (provider === "google" && items.length) {
        list.appendChild(poweredByGoogle());
      }
      list.hidden = items.length === 0;
      input.setAttribute("aria-expanded", items.length ? "true" : "false");
      if (active >= 0) {
        input.setAttribute("aria-activedescendant", listId + "-opt-" + active);
      } else {
        input.removeAttribute("aria-activedescendant");
      }
      if (!list.hidden) placeList();
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
          provider = data.provider || "";
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
      } else if (e.key === "Enter" && !list.hidden && items.length) {
        e.preventDefault();
        choose(active >= 0 ? active : 0);
      } else if (e.key === "Escape") {
        close();
      }
    });

    input.addEventListener("blur", function () {
      setTimeout(close, 150);
    });

    // Clicking « Rechercher » focuses the button first, which blurs the
    // field and used to close the list *without* applying a suggestion.
    // The form then searched the raw street ("12 rue de la paix") and
    // dropped the town. Commit the highlighted / first prediction, like
    // Google Places, before the submit handler reads the value.
    function commitOpenSuggestion() {
      if (!list.hidden && items.length) {
        choose(active >= 0 ? active : 0);
      }
    }
    var form = input.closest("form");
    if (form) {
      form.addEventListener("submit", commitOpenSuggestion, true);
      var submitBtn = form.querySelector('[type="submit"]');
      if (submitBtn) {
        submitBtn.addEventListener("mousedown", commitOpenSuggestion);
      }
    }

    window.addEventListener(
      "scroll",
      function () {
        if (!list.hidden) placeList();
      },
      true
    );
    window.addEventListener("resize", function () {
      if (!list.hidden) placeList();
    });
  }

  function init() {
    document.querySelectorAll(WHERE_SEL).forEach(function (i) {
      attach(i, "address");
    });
    document.querySelectorAll(CITY_SEL).forEach(function (i) {
      if (i.dataset.acBound) return;
      attach(i, "city");
    });
    document.querySelectorAll(ADDR_SEL).forEach(function (i) {
      if (i.dataset.acBound) return;
      attach(i, "address");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
