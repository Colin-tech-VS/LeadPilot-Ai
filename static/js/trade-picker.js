/** Sync trade chip grid with hidden <select> on register/settings forms. */
(function () {
  document.querySelectorAll("[data-trade-picker]").forEach(function (wrap) {
    const select = wrap.querySelector("[data-trade-select]");
    const chips = wrap.querySelectorAll(".trade-picker-chip");
    const iconSpan = wrap.querySelector(".trade-select-icon");
    if (!select) return;

    function syncFromSelect() {
      const val = select.value;
      chips.forEach(function (chip) {
        const on = chip.dataset.tradeKey === val;
        chip.classList.toggle("is-active", on);
        chip.setAttribute("aria-selected", on ? "true" : "false");
      });
      const opt = select.selectedOptions[0];
      if (iconSpan && opt) {
        iconSpan.textContent = opt.dataset.icon || "🛠️";
      }
    }

    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        select.value = chip.dataset.tradeKey || "";
        select.dispatchEvent(new Event("change", { bubbles: true }));
        syncFromSelect();
      });
    });

    select.addEventListener("change", syncFromSelect);
    syncFromSelect();
  });
})();
