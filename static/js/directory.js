/**
 * Public artisan directory — live search with loading skeletons.
 */
(function () {
  const form = document.getElementById("directory-search-form");
  const results = document.getElementById("directory-results");
  if (!form || !results) return;

  const grid = document.getElementById("directory-grid");
  const countEl = document.getElementById("directory-count");
  const emptyEl = document.getElementById("directory-empty");
  const submitBtn = form.querySelector('button[type="submit"]');
  const labels = results.dataset || {};

  function skeletonHtml(n) {
    let html = "";
    for (let i = 0; i < n; i += 1) {
      html +=
        '<div class="directory-card directory-card--skeleton" aria-hidden="true">' +
        '<div class="sk-line sk-icon"></div>' +
        '<div class="sk-line sk-title"></div>' +
        '<div class="sk-line sk-short"></div>' +
        '<div class="sk-line sk-medium"></div>' +
        '</div>';
    }
    return html;
  }

  function setLoading(on) {
    form.classList.toggle("is-loading", on);
    if (submitBtn) {
      submitBtn.disabled = on;
      submitBtn.setAttribute("aria-busy", on ? "true" : "false");
    }
    if (on && grid) {
      grid.innerHTML = skeletonHtml(6);
      grid.hidden = false;
      if (emptyEl) emptyEl.hidden = true;
      if (countEl) countEl.textContent = labels.loading || "Recherche en cours…";
    }
  }

  function cardHtml(a) {
    const city = a.city || labels.cityUnknown || "";
    const postal = a.postal_code ? " (" + a.postal_code + ")" : "";
    const blurb = a.blurb ? '<p class="directory-card-blurb">' + escapeHtml(a.blurb) + "</p>" : "";
    return (
      '<a href="/artisans/' +
      encodeURIComponent(a.slug) +
      '" class="directory-card">' +
      '<div class="directory-card-icon">' +
      escapeHtml(a.trade_icon) +
      "</div>" +
      "<h2>" +
      escapeHtml(a.name) +
      "</h2>" +
      '<p class="directory-card-trade">' +
      escapeHtml(a.trade_label) +
      "</p>" +
      '<p class="directory-card-city">📍 ' +
      escapeHtml(city + postal) +
      "</p>" +
      blurb +
      '<span class="directory-card-cta">' +
      escapeHtml(labels.bookCta || "Prendre RDV") +
      " →</span>" +
      "</a>"
    );
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function render(data) {
    const items = data.artisans || [];
    if (countEl) {
      const tpl = labels.countTpl || "{count} artisan(s)";
      countEl.textContent = tpl.replace("{count}", String(data.count || items.length));
    }
    if (!items.length) {
      if (grid) {
        grid.innerHTML = "";
        grid.hidden = true;
      }
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    if (grid) {
      grid.innerHTML = items.map(cardHtml).join("");
      grid.hidden = false;
    }
  }

  function buildUrl() {
    const params = new URLSearchParams(new FormData(form));
    return "/api/public/artisans/search?" + params.toString();
  }

  function syncUrl() {
    const params = new URLSearchParams(new FormData(form));
    const qs = params.toString();
    const next = qs ? "?" + qs : window.location.pathname;
    window.history.replaceState({}, "", next);
  }

  async function runSearch() {
    setLoading(true);
    syncUrl();
    try {
      const res = await fetch(buildUrl(), { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error("search failed");
      const data = await res.json();
      render(data);
    } catch (e) {
      if (countEl) countEl.textContent = labels.error || "Recherche indisponible";
      if (grid) grid.hidden = true;
      if (emptyEl) emptyEl.hidden = false;
    } finally {
      setLoading(false);
    }
  }

  let debounce;
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    runSearch();
  });

  form.querySelectorAll("input, select").forEach(function (el) {
    el.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(runSearch, 380);
    });
    el.addEventListener("change", function () {
      clearTimeout(debounce);
      debounce = setTimeout(runSearch, 120);
    });
  });

  document.querySelectorAll(".directory-chip[data-trade]").forEach(function (chip) {
    chip.addEventListener("click", function (e) {
      e.preventDefault();
      const sel = form.querySelector('[name="metier"]');
      if (sel) {
        sel.value = chip.dataset.trade || "";
        runSearch();
      }
    });
  });
})();
