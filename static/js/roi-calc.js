/* Interactive ROI calculator on the /pro landing page.
 * Recoverable revenue/month = missed calls/week × 4.33 weeks × conversion rate × average job value.
 * Honest, self-serve estimate — persuades on the visitor's own numbers, no fabricated claims.
 */
(function () {
  var root = document.querySelector('[data-roi-calc]');
  if (!root) return;

  var lang = (document.documentElement.getAttribute('lang') || 'fr').slice(0, 2);
  var locale = lang === 'en' ? 'en-GB' : 'fr-FR';
  var yearTpl = root.getAttribute('data-year-tpl') || 'about {amount} per year';

  var missed = root.querySelector('[data-roi-missed]');
  var basket = root.querySelector('[data-roi-basket]');
  var rate = root.querySelector('[data-roi-rate]');
  var missedOut = root.querySelector('[data-roi-missed-out]');
  var basketOut = root.querySelector('[data-roi-basket-out]');
  var rateOut = root.querySelector('[data-roi-rate-out]');
  var monthEl = root.querySelector('[data-roi-month]');
  var yearEl = root.querySelector('[data-roi-year]');

  function euros(n) {
    try {
      return new Intl.NumberFormat(locale, {
        style: 'currency', currency: 'EUR', maximumFractionDigits: 0
      }).format(Math.round(n));
    } catch (e) {
      return Math.round(n) + ' €';
    }
  }

  function update() {
    var m = parseFloat(missed.value) || 0;
    var b = parseFloat(basket.value) || 0;
    var r = (parseFloat(rate.value) || 0) / 100;
    var perMonth = m * 4.33 * r * b;
    var perYear = perMonth * 12;

    if (missedOut) missedOut.textContent = String(Math.round(m));
    if (basketOut) basketOut.textContent = euros(b);
    if (rateOut) rateOut.textContent = Math.round(r * 100) + ' %';
    if (monthEl) monthEl.textContent = euros(perMonth);
    if (yearEl) yearEl.textContent = yearTpl.replace('{amount}', euros(perYear));
  }

  [missed, basket, rate].forEach(function (el) {
    if (el) el.addEventListener('input', update);
  });
  update();
})();
