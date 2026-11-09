(() => {
  const form = document.querySelector('[data-note-create]');
  if (!form) return;
  const selected = form.querySelector('[data-note-space]');
  const section = form.querySelector('[data-new-note-space]');
  const name = section.querySelector('input');
  const sync = () => {
    const creating = selected.value === 'new';
    section.hidden = !creating;
    name.disabled = !creating;
    name.required = creating;
  };
  selected.addEventListener('change', sync);
  sync();
  let submitting = false;
  const dirty = () => !!(form.elements.title.value || form.elements.body.value);
  window.wikiTideNative?.register('note-create', () => ({ dirty: dirty(), busy: submitting }));
  window.addEventListener('beforeunload', event => {
    if (dirty() && !submitting) { event.preventDefault(); event.returnValue = ''; }
  });
  const button = form.querySelector('[type=submit]');
  form.addEventListener('submit', () => { submitting = true; button.disabled = true; });
  window.addEventListener('pageshow', () => { submitting = false; button.disabled = false; });
})();
