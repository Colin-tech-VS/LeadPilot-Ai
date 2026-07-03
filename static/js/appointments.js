(function () {
  const markers = window.APPOINTMENT_MARKERS || [];
  const routeDays = window.ROUTE_DAYS || [];
  const labels = window.MAP_LABELS || {};
  const mapEl = document.getElementById("appointments-map");
  if (!mapEl || typeof L === "undefined") return;

  const defaultCenter = [46.603354, 1.888334];
  const map = L.map("appointments-map").setView(defaultCenter, 6);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  }).addTo(map);

  const markerById = {};
  const bounds = [];
  let routeLayerGroup = L.layerGroup().addTo(map);

  markers.forEach(function (m) {
    const isDepot = m.is_depot;
    const icon = L.divIcon({
      className: isDepot ? "map-pin map-pin-depot" : "map-pin",
      html: isDepot
        ? '<div class="map-pin-inner map-pin-depot-inner">' + escapeHtml(labels.depot || "Base") + "</div>"
        : '<div class="map-pin-inner">' + escapeHtml(m.time || "") + "</div>",
      iconSize: [48, 48],
      iconAnchor: [24, 48],
    });

    const marker = L.marker([m.lat, m.lng], { icon: icon }).addTo(map);
    marker.bindPopup(
      "<strong>" + escapeHtml(m.name) + "</strong><br>" +
      (m.date ? "<span>" + escapeHtml(m.date) + " " + escapeHtml(m.time) + "</span><br>" : "") +
      (m.address ? "<span>" + escapeHtml(m.address) + "</span><br>" : "") +
      (!isDepot
        ? '<a href="https://www.google.com/maps/dir/?api=1&destination=' +
          m.lat + "," + m.lng + '" target="_blank" rel="noopener">Itinéraire →</a>'
        : "")
    );
    markerById[m.id] = marker;
    bounds.push([m.lat, m.lng]);
  });

  if (bounds.length === 1) {
    map.setView(bounds[0], 14);
  } else if (bounds.length > 1) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }

  const statusEl = document.getElementById("map-status");
  if (statusEl) {
    statusEl.textContent = markers.length
      ? markers.length + " point(s) sur la carte"
      : labels.noLocation || "No locations";
  }

  document.querySelectorAll(".agenda-slot").forEach(function (slot) {
    slot.addEventListener("click", function () {
      const id = slot.dataset.apptId;
      const marker = markerById[id];
      document.querySelectorAll(".agenda-slot").forEach(function (s) {
        s.classList.remove("agenda-slot-active");
      });
      slot.classList.add("agenda-slot-active");
      if (marker) {
        map.setView(marker.getLatLng(), 16, { animate: true });
        marker.openPopup();
      }
    });
  });

  const daySelect = document.getElementById("route-day-select");
  const legsEl = document.getElementById("route-legs");

  async function fetchLeg(from, to) {
    const params = new URLSearchParams({
      from_lat: from.lat,
      from_lng: from.lng,
      to_lat: to.lat,
      to_lng: to.lng,
    });
    const res = await fetch("/api/route-leg?" + params.toString());
    if (!res.ok) return null;
    return res.json();
  }

  function routeMidpoint(coords) {
    if (!coords.length) return null;
    const idx = Math.floor(coords.length / 2);
    return coords[idx];
  }

  function buildMapTimeBadge(step, carMin, transitMin) {
    return (
      '<div class="route-time-badge">' +
      '<span class="route-time-step">' + step + "</span>" +
      '<div class="route-time-rows">' +
      '<div class="route-time-row route-time-row-car">' +
      '<span class="route-time-mode">' + escapeHtml(labels.routeCar || "Voiture") + "</span>" +
      '<span class="route-time-value">' + carMin + " min</span>" +
      "</div>" +
      '<div class="route-time-row route-time-row-transit">' +
      '<span class="route-time-mode">' + escapeHtml(labels.routeTransit || "Transport") + "</span>" +
      '<span class="route-time-value">~' + transitMin + " min</span>" +
      "</div>" +
      "</div></div>"
    );
  }

  function buildLegCard(step, from, to, route) {
    const fromName = from.is_depot ? (labels.depot || "Base") : from.name;
    const toName = to.is_depot ? (labels.depot || "Base") : to.name;
    const carMin = route.duration_car_min;
    const transitMin = route.duration_transit_min;
    const km = route.distance_km;

    return (
      '<article class="route-leg-item" data-leg-step="' + step + '">' +
      '<div class="route-leg-step">' + step + "</div>" +
      '<div class="route-leg-body">' +
      '<div class="route-leg-header">' +
      "<strong>" + escapeHtml(from.time) + " → " + escapeHtml(to.time) + "</strong>" +
      '<span class="route-leg-km">' + km + " " + escapeHtml(labels.routeKm || "km") + "</span>" +
      "</div>" +
      '<p class="route-leg-names">' + escapeHtml(fromName) + " → " + escapeHtml(toName) + "</p>" +
      '<div class="route-leg-times-big">' +
      '<div class="route-time-chip route-time-chip-car">' +
      '<span class="route-time-chip-label">🚗 ' + escapeHtml(labels.routeCar || "Voiture") + "</span>" +
      '<span class="route-time-chip-value">' + carMin + " min</span>" +
      "</div>" +
      '<div class="route-time-chip route-time-chip-transit">' +
      '<span class="route-time-chip-label">🚇 ' + escapeHtml(labels.routeTransit || "Transport") + "</span>" +
      '<span class="route-time-chip-value">~' + transitMin + " min</span>" +
      "</div>" +
      "</div></div></article>"
    );
  }

  async function drawRoutesForDay(dayKey) {
    routeLayerGroup.clearLayers();
    if (legsEl) legsEl.innerHTML = "";

    const day = routeDays.find(function (d) { return d.day_key === dayKey; });
    if (!day || !day.stops || day.stops.length < 2) {
      if (legsEl) {
        legsEl.innerHTML = "<p class=\"route-legs-empty\">" + escapeHtml(labels.noRoutes || "") + "</p>";
      }
      return;
    }

    if (legsEl) {
      legsEl.innerHTML = "<p class=\"route-legs-loading\">" + escapeHtml(labels.routeLoading || "...") + "</p>";
    }

    const legsHtml = [];
    const routeBounds = [];
    let step = 0;

    for (let i = 0; i < day.stops.length - 1; i++) {
      const from = day.stops[i];
      const to = day.stops[i + 1];
      const route = await fetchLeg(from, to);

      if (route && route.coordinates) {
        step += 1;
        L.polyline(route.coordinates, {
          color: "#2563EB",
          weight: 6,
          opacity: 0.9,
          lineCap: "round",
          lineJoin: "round",
        }).addTo(routeLayerGroup);

        route.coordinates.forEach(function (c) { routeBounds.push(c); });

        const mid = routeMidpoint(route.coordinates);
        if (mid) {
          L.marker(mid, {
            icon: L.divIcon({
              className: "route-time-icon-wrap",
              html: buildMapTimeBadge(step, route.duration_car_min, route.duration_transit_min),
              iconSize: [168, 72],
              iconAnchor: [84, 36],
            }),
            zIndexOffset: 1000,
          }).addTo(routeLayerGroup);
        }

        legsHtml.push(buildLegCard(step, from, to, route));
      }
    }

    if (legsEl) {
      legsEl.innerHTML = legsHtml.length
        ? legsHtml.join("")
        : "<p class=\"route-legs-empty\">" + escapeHtml(labels.noRoutes || "") + "</p>";

      legsEl.querySelectorAll(".route-leg-item").forEach(function (item) {
        item.addEventListener("click", function () {
          legsEl.querySelectorAll(".route-leg-item").forEach(function (el) {
            el.classList.remove("route-leg-item-active");
          });
          item.classList.add("route-leg-item-active");
        });
      });
    }

    if (routeBounds.length > 1) {
      map.fitBounds(routeBounds, { padding: [80, 80] });
    }
  }

  if (daySelect && routeDays.length) {
    drawRoutesForDay(daySelect.value);
    daySelect.addEventListener("change", function () {
      drawRoutesForDay(daySelect.value);
    });
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }
})();
