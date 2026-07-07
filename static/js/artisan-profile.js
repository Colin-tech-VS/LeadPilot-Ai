(function () {
  'use strict';

  var mapEl = document.getElementById('artisan-map');
  if (!mapEl || typeof L === 'undefined') return;

  var lat = parseFloat(mapEl.dataset.lat);
  var lng = parseFloat(mapEl.dataset.lng);
  if (isNaN(lat) || isNaN(lng)) return;

  var radiusKm = parseInt(mapEl.dataset.radius || '0', 10);
  var name = mapEl.dataset.name || '';

  var map = L.map(mapEl, {
    scrollWheelZoom: false,
    zoomControl: true,
  }).setView([lat, lng], 13);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  L.marker([lat, lng]).addTo(map).bindPopup(name);

  if (radiusKm > 0) {
    L.circle([lat, lng], {
      radius: radiusKm * 1000,
      color: '#1B57E0',
      fillColor: '#1B57E0',
      fillOpacity: 0.08,
      weight: 2,
    }).addTo(map);
  }

  setTimeout(function () {
    map.invalidateSize();
  }, 200);
})();
