(function () {
  "use strict";

  document.documentElement.classList.add("js-enabled");

  function initCarousel(root) {
    var viewport = root.querySelector("[data-pricing-viewport]");
    var track = root.querySelector("[data-pricing-track]");
    var tabsWrap = root.querySelector("[data-pricing-tabs]");
    var dotsWrap = root.querySelector("[data-pricing-dots]");
    var prevBtn = root.querySelector("[data-pricing-prev]");
    var nextBtn = root.querySelector("[data-pricing-next]");
    if (!viewport || !track) return;

    var slides = Array.prototype.slice.call(track.querySelectorAll("[data-pricing-slide]"));
    if (!slides.length) return;

    var index = slides.findIndex(function (s) {
      return s.classList.contains("pricing-card-launch");
    });
    if (index < 0) index = 0;

    var timer = null;
    var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    slides.forEach(function (slide, i) {
      var label = slide.getAttribute("data-slide-label") || "Plan " + (i + 1);
      slide.setAttribute("role", "tabpanel");
      slide.setAttribute("aria-hidden", i === index ? "false" : "true");

      if (tabsWrap) {
        var tab = document.createElement("button");
        tab.type = "button";
        tab.className = "pro-pricing-tab" + (i === index ? " is-active" : "");
        tab.textContent = label;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", i === index ? "true" : "false");
        tab.addEventListener("click", function () {
          goTo(i, true);
        });
        tabsWrap.appendChild(tab);
      }

      if (dotsWrap) {
        var dot = document.createElement("button");
        dot.type = "button";
        dot.className = "pro-pricing-dot" + (i === index ? " is-active" : "");
        dot.setAttribute("aria-label", label);
        dot.addEventListener("click", function () {
          goTo(i, true);
        });
        dotsWrap.appendChild(dot);
      }
    });

    var tabs = tabsWrap ? tabsWrap.querySelectorAll(".pro-pricing-tab") : [];
    var dots = dotsWrap ? dotsWrap.querySelectorAll(".pro-pricing-dot") : [];

    function updateClasses() {
      slides.forEach(function (slide, i) {
        slide.classList.remove("is-active", "is-adjacent");
        slide.setAttribute("aria-hidden", i === index ? "false" : "true");
        if (i === index) slide.classList.add("is-active");
        else if (Math.abs(i - index) === 1) slide.classList.add("is-adjacent");
      });
      tabs.forEach(function (tab, i) {
        tab.classList.toggle("is-active", i === index);
        tab.setAttribute("aria-selected", i === index ? "true" : "false");
      });
      dots.forEach(function (dot, i) {
        dot.classList.toggle("is-active", i === index);
      });
    }

    function centerTrack(animate) {
      var active = slides[index];
      if (!active) return;

      var gap = parseFloat(getComputedStyle(track).gap) || 20;
      var viewportWidth = viewport.clientWidth;
      var slideWidth = active.offsetWidth;
      var offset = index * (slideWidth + gap) + slideWidth / 2 - viewportWidth / 2;
      var maxOffset = track.scrollWidth - viewportWidth;
      offset = Math.max(0, Math.min(offset, maxOffset));

      track.style.transition = animate && !reduced
        ? "transform 0.55s cubic-bezier(0.22, 1, 0.36, 1)"
        : "none";
      track.style.transform = "translateX(" + (-offset) + "px)";
    }

    function goTo(next, manual) {
      if (next < 0 || next >= slides.length || next === index) return;
      index = next;
      updateClasses();
      centerTrack(true);
      if (manual) restart();
    }

    function next() {
      goTo((index + 1) % slides.length, false);
    }

    function prev() {
      goTo((index - 1 + slides.length) % slides.length, false);
    }

    function restart() {
      if (timer) window.clearInterval(timer);
      if (!reduced) timer = window.setInterval(next, 6500);
    }

    if (prevBtn) prevBtn.addEventListener("click", prev);
    if (nextBtn) nextBtn.addEventListener("click", next);

    root.addEventListener("mouseenter", function () {
      if (timer) window.clearInterval(timer);
    });
    root.addEventListener("mouseleave", restart);
    root.addEventListener("focusin", function () {
      if (timer) window.clearInterval(timer);
    });
    root.addEventListener("focusout", function (e) {
      if (!root.contains(e.relatedTarget)) restart();
    });

    var touchStartX = 0;
    viewport.addEventListener(
      "touchstart",
      function (e) {
        touchStartX = e.changedTouches[0].clientX;
      },
      { passive: true }
    );
    viewport.addEventListener(
      "touchend",
      function (e) {
        var delta = e.changedTouches[0].clientX - touchStartX;
        if (Math.abs(delta) < 40) return;
        if (delta < 0) next();
        else prev();
        restart();
      },
      { passive: true }
    );

    root.addEventListener("keydown", function (e) {
      if (e.key === "ArrowRight") {
        e.preventDefault();
        next();
        restart();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        prev();
        restart();
      }
    });

    window.addEventListener("resize", function () {
      centerTrack(false);
    });

    root.classList.add("is-ready");
    updateClasses();
    centerTrack(false);
    restart();
  }

  document.querySelectorAll("[data-pricing-carousel]").forEach(initCarousel);
})();
