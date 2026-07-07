/**
 * Google Places Autocomplete for city fields (France + BE/CH/LU).
 * Targets: input[name="ville"], input[name="city"], input[data-places-city]
 */
(function () {
  "use strict";

  var cfg = window.PilotCore_PLACES;
  if (!cfg || !cfg.apiKey) return;

  var SELECTORS = 'input[name="ville"], input[name="city"], input[data-places-city]';

  function component(components, type) {
    for (var i = 0; i < components.length; i++) {
      if (components[i].types.indexOf(type) >= 0) {
        return components[i].long_name;
      }
    }
    return "";
  }

  function cityFromPlace(place) {
    var comps = place.address_components || [];
    return (
      component(comps, "locality") ||
      component(comps, "postal_town") ||
      component(comps, "administrative_area_level_2") ||
      place.name ||
      ""
    );
  }

  function postalFromPlace(place) {
    return component(place.address_components || [], "postal_code");
  }

  function findPostalInput(cityInput) {
    var form = cityInput.closest("form");
    if (!form) return null;
    return form.querySelector('input[name="postal_code"], #postal_code');
  }

  function attachAutocomplete(input) {
    if (input.dataset.placesBound === "1") return;
    if (input.disabled || input.readOnly) return;
    input.dataset.placesBound = "1";
    input.setAttribute("autocomplete", "off");

    var ac = new google.maps.places.Autocomplete(input, {
      types: ["(cities)"],
      componentRestrictions: { country: ["fr", "be", "ch", "lu"] },
      fields: ["address_components", "name", "formatted_address", "geometry"],
    });

    ac.addListener("place_changed", function () {
      var place = ac.getPlace();
      if (!place) return;

      var city = cityFromPlace(place);
      if (city) input.value = city;

      var postalInput = findPostalInput(input);
      var postal = postalFromPlace(place);
      if (postalInput && postal) postalInput.value = postal;
    });
  }

  function bindAll() {
    document.querySelectorAll(SELECTORS).forEach(function (input) {
      if (input.type === "hidden") return;
      attachAutocomplete(input);
    });
  }

  window.__pilotCorePlacesInit = function () {
    if (!window.google || !google.maps || !google.maps.places) return;
    bindAll();
  };

  if (window.google && window.google.maps && window.google.maps.places) {
    window.__pilotCorePlacesInit();
    return;
  }

  if (document.querySelector("script[data-pilotcore-places-loader]")) return;

  var script = document.createElement("script");
  script.dataset.pilotcorePlacesLoader = "1";
  script.src =
    "https://maps.googleapis.com/maps/api/js?key=" +
    encodeURIComponent(cfg.apiKey) +
    "&libraries=places&language=" +
    encodeURIComponent(cfg.lang || "fr") +
    "&callback=__pilotCorePlacesInit";
  script.async = true;
  script.defer = true;
  document.head.appendChild(script);
})();
