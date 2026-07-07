(function () {
  const labels = window.DASH_LABELS || {};

  function setGreeting() {
    const el = document.getElementById("dash-greeting");
    if (!el) return;
    const hour = new Date().getHours();
    if (hour < 12) el.textContent = labels.greetingMorning || el.textContent;
    else if (hour < 18) el.textContent = labels.greetingAfternoon || el.textContent;
    else el.textContent = labels.greetingEvening || el.textContent;
  }

  function setDate() {
    const el = document.getElementById("dash-date");
    if (!el) return;
    const locale = labels.locale === "en" ? "en-GB" : "fr-FR";
    el.textContent = new Date().toLocaleDateString(locale, {
      weekday: "long",
      day: "numeric",
      month: "long",
    });
  }

  function animateCount(el, target, duration) {
    const start = performance.now();
    const from = 0;
    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(from + (target - from) * eased);
      if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function initCounters() {
    document.querySelectorAll("[data-count]").forEach(function (el) {
      const target = parseInt(el.getAttribute("data-count"), 10) || 0;
      animateCount(el, target, 900);
    });
  }

  function initReveal() {
    var elements = document.querySelectorAll(".dash-animate, .dash-card-in");
    elements.forEach(function (el, index) {
      setTimeout(function () {
        el.classList.add("is-visible");
      }, index * 40);
    });
  }

  document.documentElement.classList.add("js-enabled");
  setGreeting();
  setDate();
  initCounters();
  initReveal();

  var phoneToggle = document.getElementById("toggle-direct-phone");
  if (phoneToggle) {
    phoneToggle.addEventListener("change", function () {
      var enabled = phoneToggle.checked;
      fetch("/settings/toggle-direct-phone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: enabled }),
      }).catch(function () {
        phoneToggle.checked = !enabled;
      });
    });
  }
})();
