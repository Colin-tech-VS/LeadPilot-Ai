(function () {
  'use strict';

  function pinIcon() {
    return L.divIcon({
      className: 'pc-map-pin',
      html: '<span></span>',
      iconSize: [22, 28],
      iconAnchor: [11, 26],
      popupAnchor: [0, -22],
    });
  }

  function initMap(mapEl) {
    if (!mapEl || typeof L === 'undefined') return;
    var lat = parseFloat(mapEl.dataset.lat);
    var lng = parseFloat(mapEl.dataset.lng);
    if (isNaN(lat) || isNaN(lng)) return;

    var radiusKm = parseInt(mapEl.dataset.radius || '0', 10);
    var name = mapEl.dataset.name || '';
    var zoom = parseInt(mapEl.dataset.zoom || '14', 10);

    var map = L.map(mapEl, {
      scrollWheelZoom: false,
      zoomControl: true,
    }).setView([lat, lng], zoom);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    var marker = L.marker([lat, lng], { icon: pinIcon() }).addTo(map);
    if (name) marker.bindPopup(name);

    if (radiusKm > 0) {
      L.circle([lat, lng], {
        radius: radiusKm * 1000,
        color: '#1A2332',
        fillColor: '#1A2332',
        fillOpacity: 0.08,
        weight: 2,
      }).addTo(map);
    }

    setTimeout(function () {
      map.invalidateSize();
    }, 200);
  }

  document.querySelectorAll('[data-place-map]').forEach(initMap);
})();
