document.documentElement.classList.add("js-enabled");

(function () {
  "use strict";

  /* ── Showcase slider ── */
  function initSlider(root) {
    var track = root.querySelector("[data-auth-track]");
    var slides = root.querySelectorAll("[data-auth-slide]");
    var dotsWrap = root.querySelector("[data-auth-dots]");
    if (!track || !slides.length || !dotsWrap) return;

    var index = 0;
    var timer = null;
    var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    slides.forEach(function (_, i) {
      var dot = document.createElement("button");
      dot.type = "button";
      dot.className = "auth-pro__dot" + (i === 0 ? " is-active" : "");
      dot.setAttribute("aria-label", "Slide " + (i + 1));
      dot.addEventListener("click", function () {
        goTo(i, true);
      });
      dotsWrap.appendChild(dot);
    });

    var dots = dotsWrap.querySelectorAll(".auth-pro__dot");

    function goTo(next, manual) {
      if (next === index || next < 0 || next >= slides.length) return;
      slides[index].classList.remove("is-active");
      slides[index].classList.add("is-exit");
      dots[index].classList.remove("is-active");

      index = next;
      slides[index].classList.remove("is-exit");
      slides[index].classList.add("is-active");
      dots[index].classList.add("is-active");

      window.setTimeout(function () {
        slides.forEach(function (s, i) {
          if (i !== index) s.classList.remove("is-exit");
        });
      }, 520);

      if (manual) restart();
    }

    function next() {
      goTo((index + 1) % slides.length, false);
    }

    function restart() {
      if (timer) window.clearInterval(timer);
      if (!reduced) timer = window.setInterval(next, 5200);
    }

    restart();
  }

  // Isolated so a slider failure can never prevent the register wizard below
  // from initialising (which would leave users unable to submit the form).
  try {
    document.querySelectorAll("[data-auth-slider]").forEach(initSlider);
  } catch (err) {
    if (window.console) console.error("auth slider init failed", err);
  }

  /* ── Password visibility toggle ── */
  document.querySelectorAll("[data-password-toggle]").forEach(function (btn) {
    var id = btn.getAttribute("data-password-toggle");
    var input = document.getElementById(id);
    if (!input) return;
    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.textContent = show ? "🙈" : "👁️";
      btn.setAttribute("aria-label", show ? "Masquer" : "Afficher");
    });
  });

  /* ── Register wizard ── */
  var registerForm = document.getElementById("register-form");
  if (registerForm) {
    var steps = registerForm.querySelectorAll("[data-register-step]");
    var track = registerForm.querySelector("[data-register-track]");
    var stepIndicators = document.querySelectorAll("[data-step-indicator]");
    var stepLabels = document.querySelectorAll("[data-step-label]");
    var btnNext = document.getElementById("register-next");
    var btnBack = document.getElementById("register-back");
    var current = 0;

    document.documentElement.classList.add("js-wizard");

    /* Reaching the last step means the visitor is looking at the fields that
       create the account. Told to the server once per page, so the funnel can
       separate « personne n'a commencé le formulaire » (a page/offer problem)
       from « beaucoup l'ont commencé, aucun ne l'a fini » (a form problem).
       Fire-and-forget: a failed beacon must never disturb the sign-up. */
    var startReported = false;
    function reportStarted() {
      if (startReported || steps.length < 2) return;
      startReported = true;
      var form = registerForm.getAttribute("data-funnel-form");
      if (!form) return;
      var body = JSON.stringify({ form: form });
      try {
        if (navigator.sendBeacon) {
          navigator.sendBeacon(
            "/api/signup/started",
            new Blob([body], { type: "application/json" })
          );
          return;
        }
        fetch("/api/signup/started", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true
        }).catch(function () {});
      } catch (err) { /* measurement is never worth an exception here */ }
    }

    function showStep(n, animate) {
      current = n;
      if (n === steps.length - 1) reportStarted();
      if (track) {
        track.style.transform = "translateX(-" + n * 100 + "%)";
      }
      steps.forEach(function (el, i) {
        el.classList.toggle("is-active", i === n);
        el.setAttribute("aria-hidden", i === n ? "false" : "true");
      });
      stepIndicators.forEach(function (el, i) {
        el.classList.toggle("is-active", i === n);
        el.classList.toggle("is-done", i < n);
      });
      stepLabels.forEach(function (el, i) {
        el.classList.toggle("is-active", i === n);
      });
      if (btnBack) btnBack.hidden = n === 0;
      if (btnNext) {
        btnNext.textContent = n === steps.length - 1 ? btnNext.dataset.submitLabel : btnNext.dataset.nextLabel;
        btnNext.type = n === steps.length - 1 ? "submit" : "button";
      }

      /* Changing step re-flows the card, so the submit button may have just
         moved under the fixed cookie banner. Ask it to lift the button clear
         once this step has been laid out. */
      try {
        window.setTimeout(function () {
          document.dispatchEvent(new CustomEvent("pc-consent-recheck"));
        }, 60);
      } catch (err) { /* older browsers: the banner's own observer covers it */ }

      if (animate) {
        var active = steps[n];
        if (active) {
          var firstField = active.querySelector(
            "input:not([type='hidden']):not([type='checkbox']), select, textarea, button.trade-picker-chip"
          );
          if (firstField) {
            try { firstField.focus({ preventScroll: true }); } catch (err) { firstField.focus(); }
          }
        }
      }
    }

    function validateStep(n) {
      var step = steps[n];
      if (!step) return true;
      var fields = step.querySelectorAll("input, select, textarea");
      for (var i = 0; i < fields.length; i++) {
        if (!fields[i].checkValidity()) {
          fields[i].reportValidity();
          return false;
        }
      }
      return true;
    }

    if (btnNext) {
      btnNext.addEventListener("click", function (e) {
        if (btnNext.type !== "button") return;
        e.preventDefault();
        if (!validateStep(current)) return;
        if (current < steps.length - 1) showStep(current + 1, true);
      });
    }

    if (btnBack) {
      btnBack.addEventListener("click", function () {
        if (current > 0) showStep(current - 1, true);
      });
    }

    stepIndicators.forEach(function (el, i) {
      el.addEventListener("click", function () {
        if (i <= current) showStep(i, true);
      });
    });

    /* Picking a trade IS the answer to step 1, so the extra tap on "Continuer"
       is pure friction — advance as soon as a chip is chosen, or the dropdown
       changes. The button stays for keyboard users who never fire a change. */
    var tradeStep = steps[0];
    if (tradeStep) {
      function advanceFromTrade() {
        if (current !== 0 || steps.length < 2) return;
        window.setTimeout(function () {
          if (current === 0 && validateStep(0)) showStep(1, true);
        }, 180);
      }
      tradeStep.querySelectorAll(".trade-picker-chip").forEach(function (chip) {
        chip.addEventListener("click", advanceFromTrade);
      });
      var tradeSelect = tradeStep.querySelector("[data-trade-select]");
      if (tradeSelect) {
        tradeSelect.addEventListener("change", advanceFromTrade);
      }
    }

    var password = document.getElementById("password");
    var confirm = document.getElementById("confirm_password");

    function syncValidity() {
      if (!password || !confirm) return;
      if (confirm.value && password.value !== confirm.value) {
        confirm.setCustomValidity("Les mots de passe ne correspondent pas.");
      } else {
        confirm.setCustomValidity("");
      }
    }

    if (password) password.addEventListener("input", syncValidity);
    if (confirm) confirm.addEventListener("input", syncValidity);

    registerForm.addEventListener("submit", function (e) {
      if (current < steps.length - 1) {
        e.preventDefault();
        if (validateStep(current)) showStep(current + 1, true);
        return;
      }
      syncValidity();
      if (confirm && !confirm.checkValidity()) {
        e.preventDefault();
        confirm.reportValidity();
      }
    });

    var start = 0;
    var startAttr = registerForm.getAttribute("data-start-step");
    if (startAttr !== null && startAttr !== "") {
      start = parseInt(startAttr, 10) || 0;
    } else if (document.querySelector(".auth-pro__panel .alert-error")) {
      start = steps.length - 1;
    }
    showStep(start);
  }
})();
