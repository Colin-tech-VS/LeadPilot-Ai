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
    var stepIndicators = document.querySelectorAll("[data-step-indicator]");
    var stepLabels = document.querySelectorAll("[data-step-label]");
    var btnNext = document.getElementById("register-next");
    var btnBack = document.getElementById("register-back");
    var current = 0;

    // Enable step-by-step display only now that the wizard is really running.
    // Until this class is set, CSS keeps every step visible so the form stays
    // submittable even if this script is blocked or errors out.
    document.documentElement.classList.add("js-wizard");

    function showStep(n) {
      current = n;
      steps.forEach(function (el, i) {
        el.classList.toggle("is-active", i === n);
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
        if (current < steps.length - 1) showStep(current + 1);
      });
    }

    if (btnBack) {
      btnBack.addEventListener("click", function () {
        if (current > 0) showStep(current - 1);
      });
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
        if (validateStep(current)) showStep(current + 1);
        return;
      }
      syncValidity();
      if (confirm && !confirm.checkValidity()) {
        e.preventDefault();
        confirm.reportValidity();
      }
    });

    showStep(0);
  }
})();
