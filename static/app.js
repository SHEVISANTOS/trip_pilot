// Progressive enhancement only — planning, budgeting and itinerary rendering
// now happen server-side (trips/views.py). This just keeps the booking
// modal and the live currency-symbol swap on the planner form.
const $ = (id) => document.getElementById(id);

function openModal(title, text) {
  const modal = $('modal');
  if (!modal) return;
  $('modalTitle').textContent = title;
  $('modalText').textContent = text;
  modal.classList.remove('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-book]').forEach((b) => {
    b.addEventListener('click', () =>
      openModal('Booking: ' + b.dataset.book, 'This action is ready to connect to a live booking partner or provider.')
    );
  });

  const closeBtn = $('closeModal');
  const okBtn = $('modalOk');
  if (closeBtn) closeBtn.onclick = () => $('modal').classList.add('hidden');
  if (okBtn) okBtn.onclick = () => $('modal').classList.add('hidden');

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
