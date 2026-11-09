document.querySelectorAll('[data-diff-view]').forEach(root => {
  root.querySelectorAll('[data-diff-mode]').forEach(button => button.addEventListener('click', () => {
    root.querySelectorAll('[data-diff-mode]').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
    root.querySelectorAll('[data-diff-panel]').forEach(panel => { panel.hidden = panel.dataset.diffPanel !== button.dataset.diffMode; });
  }));
});
