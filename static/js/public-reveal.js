/** Reveal fade-in elements on public pages that don't load landing.js */
(function () {
  function reveal() {
    document.querySelectorAll(".page-directory .landing-fade, .page-client-home .landing-fade, .page-artisan-profile .landing-fade").forEach(function (el) {
      el.classList.add("is-visible");
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", reveal);
  } else {
    reveal();
  }
})();
