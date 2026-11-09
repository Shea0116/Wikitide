(() => {
  const providers = new Map();
  window.wikiTideNative = Object.freeze({
    version: 1,
    register(name, getState) { providers.set(name, getState); return () => providers.delete(name); },
    state() {
      const activities = [...providers.values()].map(read => {
        try { return read(); } catch { return { busy: true }; }
      });
      const dirty = activities.some(item => !!item.dirty);
      const busy = activities.some(item => !!item.busy);
      // An editor must register before a native update can restart it.
      const pending = !!document.querySelector('[data-editor],#writing-start,#team-root,#imports-root') && !providers.size;
      return {
        version: 1, dirty, busy, canRestart: !dirty && !busy && !pending,
        account: document.body.dataset.accountName || null,
        admin: document.body.dataset.accountRole === 'admin',
        dark: document.documentElement.dataset.theme === 'dark',
        article: !!document.querySelector('article.article .prose'),
        title: document.querySelector('main h1')?.textContent?.trim() || document.title,
        url: location.href,
      };
    },
  });
})();
