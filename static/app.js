// Progressive enhancement only — planning, budgeting and itinerary rendering
// now happen server-side (trips/views.py). This just keeps the unsaved-trip
// checklist and the live currency-symbol swap on the planner form working.
// "View / Book" buttons are plain <a href> links straight to the real
// supplier now, so they need no JS.
const $ = (id) => document.getElementById(id);

document.addEventListener('DOMContentLoaded', () => {
  // Unsaved-trip checklist: purely visual, no persistence — matches the
  // original prototype. Saved trips instead POST to toggle_checklist and
  // render as <button> elements (see trips/results.html), so this only
  // needs to handle the plain <div data-local-checklist> case.
  document.querySelectorAll('[data-local-checklist]').forEach((item) => {
    item.addEventListener('click', () => {
      item.classList.toggle('done');
      item.textContent = (item.classList.contains('done') ? '☑ ' : '☐ ') + item.textContent.slice(2);
    });
  });

  const currency = $('id_currency');
  const symbolEl = $('currencySymbol');
  const symbols = { USD: '$', TZS: 'TSh', EUR: '€', GBP: '£' };
  if (currency && symbolEl) {
    const sync = () => { symbolEl.textContent = symbols[currency.value] || '$'; };
    currency.addEventListener('change', sync);
    sync();
  }
});
