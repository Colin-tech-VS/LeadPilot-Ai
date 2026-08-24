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
  const registryEl = document.getElementById("directory-registry") || document.querySelector(".dl-registry");
  const registryGrid = document.getElementById("directory-registry-grid");
  const submitBtn = form.querySelector('button[type="submit"]');
  const labels = results.dataset || {};
  const logoUrl = labels.logoUrl || "/static/images/logo.svg";

  function skeletonHtml(n) {
    let html = "";
    for (let i = 0; i < n; i += 1) {
      html +=
        '<div class="dl-card directory-card directory-card--skeleton" aria-hidden="true">' +
        '<div class="dl-card-media"></div>' +
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
      if (registryGrid) registryGrid.innerHTML = skeletonHtml(3);
      if (countEl) countEl.textContent = labels.loading || "Recherche en cours…";
    }
  }

  function cardHtml(a) {
    const icon = escapeHtml(a.trade_icon || "🛠️");
    const city = a.city || labels.cityUnknown || "";
    const postal = a.postal_code ? " · " + a.postal_code : "";
    const radius = a.radius_km
      ? '<p class="dl-card-radius">' + escapeHtml((labels.radiusTpl || "{km} km").replace("{km}", a.radius_km)) + "</p>"
      : "";
    const blurb = a.blurb
      ? '<p class="dl-card-blurb">' + escapeHtml(a.blurb) + "</p>"
      : '<p class="dl-card-blurb dl-card-blurb--muted">' + escapeHtml(labels.featureOnline || "RDV en ligne") + " · " + escapeHtml(labels.bookCta || "Prendre RDV") + "</p>";
    const aiBadge = a.ai_phone_number
      ? '<span class="dl-card-badge dl-card-badge--ai">🤖 IA 24/7</span>'
      : "";
    const flag = escapeHtml(labels.badgeListed || "Inscrit · RDV en ligne");
    return (
      '<a href="/artisans/' + encodeURIComponent(a.slug) + '" class="dl-card dl-card--listed directory-card">' +
        '<div class="dl-card-media">' +
          '<img class="dl-card-logo" src="' + escapeHtml(a.logo_url || logoUrl) + '" alt="" width="80" height="80">' +
          '<span class="dl-card-flag dl-card-flag--listed">' + flag + "</span>" +
        "</div>" +
        '<div class="dl-card-header">' +
          '<div class="dl-card-avatar" aria-hidden="true">' + icon + "</div>" +
          '<div class="dl-card-head-text">' +
            '<h2 class="dl-card-name">' + escapeHtml(a.name) + "</h2>" +
            '<p class="dl-card-specialty">' + escapeHtml(a.trade_label || "") + "</p>" +
          "</div>" +
        "</div>" +
        '<div class="dl-card-body">' +
          '<p class="dl-card-location"><span class="dl-card-pin" aria-hidden="true">📍</span> ' + escapeHtml(city + postal) + "</p>" +
          radius +
          blurb +
        "</div>" +
        '<div class="dl-card-footer">' +
          '<div class="dl-card-badges">' +
            '<span class="dl-card-badge">' + escapeHtml(labels.featureOnline || "RDV en ligne") + "</span>" +
            aiBadge +
          "</div>" +
          '<span class="dl-card-cta btn btn-primary">' + escapeHtml(labels.bookCta || "Prendre RDV") + " →</span>" +
        "</div>" +
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

  function registryCardHtml(a) {
    const icon = escapeHtml(a.trade_icon || "🛠️");
    const city = a.city || labels.cityUnknown || "";
    const postal = a.postal_code ? " · " + a.postal_code : "";
    const years = a.years_active || a.years_active;
    const yearsLine = years
      ? '<p class="dl-card-radius">' + escapeHtml((labels.registryYears || "En activité depuis {years} ans").replace("{years}", years)) + "</p>"
      : "";
    const href = a.profile_url || ("/artisans/entreprise/" + encodeURIComponent(a.id || ""));
    const flag = escapeHtml(labels.badgeRegistry || "Registre · non inscrit");
    return (
      '<a href="' + escapeHtml(href) + '" class="dl-card dl-card--registry directory-card">' +
        '<div class="dl-card-media dl-card-media--registry">' +
          '<img class="dl-card-logo" src="' + escapeHtml(a.logo_url || logoUrl) + '" alt="" width="80" height="80">' +
          '<span class="dl-card-flag dl-card-flag--registry">' + flag + "</span>" +
        "</div>" +
        '<div class="dl-card-header">' +
          '<div class="dl-card-avatar" aria-hidden="true">' + icon + "</div>" +
          '<div class="dl-card-head-text">' +
            '<h2 class="dl-card-name">' + escapeHtml(a.name) + "</h2>" +
            '<p class="dl-card-specialty">' + escapeHtml(a.trade_label || "") + "</p>" +
          "</div>" +
        "</div>" +
        '<div class="dl-card-body">' +
          '<p class="dl-card-location"><span class="dl-card-pin" aria-hidden="true">📍</span> ' + escapeHtml(city + postal) + "</p>" +
          yearsLine +
          '<p class="dl-card-blurb dl-card-blurb--muted">' + escapeHtml(labels.registryBlurb || "Fiche non revendiquée — pas de rendez-vous en ligne.") + "</p>" +
        "</div>" +
        '<div class="dl-card-footer">' +
          '<div class="dl-card-badges"><span class="dl-card-badge dl-card-badge--registry">' + flag + "</span></div>" +
          '<span class="dl-card-cta btn btn-outline">' + escapeHtml(labels.viewListing || "Voir la fiche") + " →</span>" +
        "</div>" +
      "</a>"
    );
  }

  function render(data) {
    const items = data.artisans || [];
    const registry = data.registry || [];
    if (countEl) {
      if (!items.length && registry.length) {
        countEl.textContent =
          "Aucun artisan inscrit sur cette recherche — " +
          registry.length +
          " entreprise(s) identifiée(s) au registre officiel.";
      } else {
        const tpl = labels.countTpl || "{count} artisan(s)";
        countEl.textContent = tpl.replace("{count}", String(data.count || items.length));
      }
    }
    if (!items.length && !registry.length) {
      if (grid) {
        grid.innerHTML = "";
        grid.hidden = true;
      }
      if (registryGrid) registryGrid.innerHTML = "";
      if (registryEl) registryEl.hidden = true;
      if (emptyEl) emptyEl.hidden = false;
      return;
    }
    if (emptyEl) emptyEl.hidden = true;
    if (grid) {
      grid.innerHTML = items.map(cardHtml).join("");
      grid.hidden = !items.length;
    }
    if (registryGrid) {
      registryGrid.innerHTML = registry.map(registryCardHtml).join("");
    }
    if (registryEl) registryEl.hidden = !registry.length;
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
    const aiInput = document.getElementById("directory-ai-input");
    if (aiInput && aiInput.value.trim()) {
      runAiSearch();
    } else {
      runSearch();
    }
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

  // --- AI / natural-language search ---------------------------------------
  const aiInput = document.getElementById("directory-ai-input");
  const aiUnderstood = document.getElementById("directory-ai-understood");

  function showUnderstood(understood, relaxed) {
    if (!aiUnderstood) return;
    if (!understood || (!understood.trade && !understood.city)) {
      aiUnderstood.hidden = true;
      aiUnderstood.textContent = "";
      return;
    }
    const what = understood.trade_label || understood.query || "";
    let text = (labels.aiUnderstood || "Compris : {what}").replace("{what}", what);
    if (understood.city) {
      text += (labels.aiWhere || " à {city}").replace("{city}", understood.city);
    }
    if (relaxed && labels.aiRelaxed) {
      text += " — " + labels.aiRelaxed;
    }
    aiUnderstood.textContent = text;
    aiUnderstood.hidden = false;
  }

  function syncStructuredFrom(understood) {
    // Mirror the AI interpretation into the structured form so the user can refine.
    if (!understood) return;
    const tradeSel = form.querySelector('[name="metier"]');
    const cityInput = form.querySelector('[name="ville"]');
    const qInput = form.querySelector('[name="q"]');
    if (tradeSel && understood.trade) tradeSel.value = understood.trade;
    if (cityInput) cityInput.value = understood.city || "";
    if (qInput) qInput.value = "";
  }

  async function runAiSearch() {
    if (!aiInput) return;
    const query = aiInput.value.trim();
    if (!query) return;
    setLoading(true);
    form.classList.add("is-loading");
    try {
      const res = await fetch("/api/public/artisans/ai-search", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ q: query }),
      });
      if (!res.ok) throw new Error("ai search failed");
      const data = await res.json();
      syncStructuredFrom(data.understood);
      showUnderstood(data.understood, data.relaxed);
      render(data);
    } catch (e) {
      if (countEl) countEl.textContent = labels.error || "Recherche indisponible";
      if (grid) grid.hidden = true;
      if (emptyEl) emptyEl.hidden = false;
    } finally {
      setLoading(false);
      form.classList.remove("is-loading");
    }
  }

  if (aiInput) {
    // Handoff from the homepage: /artisans?ai=<query> pre-fills the AI input,
    // so run the AI search automatically on load.
    if (aiInput.value.trim()) {
      runAiSearch();
    }
  }

  document.querySelectorAll(".dl-chip[data-trade], .directory-chip[data-trade]").forEach(function (chip) {
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
