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

// City autocomplete — backed by /city-autocomplete/, which only ever
// suggests names from the same airport dataset flight search resolves
// against, so picking a suggestion guarantees it resolves later. Wired to
// the departure field and every destination field, including ones added
// after the page loads (attachCityAutocomplete() re-runs on each add-leg
// click; already-wired inputs are skipped via the dataset flag).
function closeAutocompleteDropdowns() {
  document.querySelectorAll('.autocomplete-list').forEach((el) => el.remove());
}

function showCitySuggestions(input, results) {
  closeAutocompleteDropdowns();
  if (!results.length) return;
  const list = document.createElement('div');
  list.className = 'autocomplete-list';
  results.forEach((label) => {
    const item = document.createElement('div');
    item.className = 'autocomplete-item';
    item.textContent = label;
    // mousedown (not click) fires before the input's blur, so the value
    // is set before the blur handler's dropdown-close timer runs.
    item.addEventListener('mousedown', (e) => {
      e.preventDefault();
      input.value = label;
      closeAutocompleteDropdowns();
    });
    list.appendChild(item);
  });
  const rect = input.getBoundingClientRect();
  list.style.left = `${rect.left + window.scrollX}px`;
  list.style.top = `${rect.bottom + window.scrollY}px`;
  list.style.width = `${rect.width}px`;
  document.body.appendChild(list);
}

function attachCityAutocomplete(root) {
  root.querySelectorAll('#id_departure, input[name$="-city"]').forEach((input) => {
    if (input.dataset.autocompleteAttached) return;
    input.dataset.autocompleteAttached = 'true';
    input.setAttribute('autocomplete', 'off');
    let debounceTimer;
    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      const query = input.value.trim();
      if (query.length < 2) {
        closeAutocompleteDropdowns();
        return;
      }
      debounceTimer = setTimeout(() => {
        fetch(`/city-autocomplete/?q=${encodeURIComponent(query)}`)
          .then((r) => r.json())
          .then((data) => showCitySuggestions(input, data.results || []))
          .catch(() => {});
      }, 200);
    });
    input.addEventListener('blur', () => setTimeout(closeAutocompleteDropdowns, 150));
  });
}

document.addEventListener('DOMContentLoaded', () => {
  attachLegRemoveHandlers();
  attachCityAutocomplete(document);

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
      attachCityAutocomplete(row);
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
