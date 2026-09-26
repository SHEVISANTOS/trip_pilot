// Progressive enhancement only — planning, budgeting and itinerary rendering
// now happen server-side (trips/views.py). This just keeps the unsaved-trip
// checklist, the live currency-symbol swap, and the add/remove-destination
// rows on the planner form working. "View / Book" buttons are plain <a
// href> links straight to the real supplier now, so they need no JS.
const $ = (id) => document.getElementById(id);

// Renumbers every leg-row's form field name/id/for from "legs-<i>-field" to
// match its current DOM position, and syncs Django's formset TOTAL_FORMS —
// both adding and removing rows shift positions, and the formset can't
// validate a gap or duplicate index.
function renumberLegForms() {
  const rows = document.querySelectorAll('#leg-forms .leg-row');
  rows.forEach((row, index) => {
    row.querySelectorAll('[name], [id], label[for]').forEach((el) => {
      ['name', 'id', 'for'].forEach((attr) => {
        if (el.hasAttribute(attr)) {
          el.setAttribute(attr, el.getAttribute(attr).replace(/legs-(\d+|__prefix__|__new__)-/, `legs-${index}-`));
        }
      });
    });
  });
  const totalForms = $('id_legs-TOTAL_FORMS');
  if (totalForms) totalForms.value = rows.length;
}

function attachLegRemoveHandlers() {
  document.querySelectorAll('.leg-remove').forEach((btn) => {
    btn.onclick = () => {
      if (document.querySelectorAll('#leg-forms .leg-row').length <= 1) return; // keep at least one destination
      btn.closest('.leg-row').remove();
      renumberLegForms();
    };
  });
}

document.addEventListener('DOMContentLoaded', () => {
  attachLegRemoveHandlers();

  const addLegBtn = $('add-leg');
  const emptyLegTemplate = $('empty-leg-form');
  if (addLegBtn && emptyLegTemplate) {
    addLegBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'leg-row';
      row.innerHTML = emptyLegTemplate.innerHTML.replace(/__prefix__/g, '__new__');
      $('leg-forms').appendChild(row);
      renumberLegForms();
      attachLegRemoveHandlers();
    });
  }

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
