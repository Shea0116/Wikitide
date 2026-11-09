(() => {
  'use strict';
  const banner = document.getElementById('explore-trial');
  let remaining = banner ? Number(banner.dataset.remaining) : null;
  window.exploreTrial = {
    get available() {return remaining === null || remaining > 0;},
    async refresh() {
      if (!banner) return;
      try {
        const response = await fetch('/api/explore/trial', {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) return;
        const {trial} = await response.json();
        if (!trial) return;
        remaining = trial.remaining;
        document.getElementById('explore-trial-message').textContent = remaining > 0
          ? window.WikitideI18n.t("免费体验剩余 {0} / {1} 次。", [remaining, trial.limit]) : window.WikitideI18n.t("三次免费体验已用完。");
        window.dispatchEvent(new Event('explore-trial-change'));
      } catch (_) { /* The server still enforces the quota when offline. */ }
    },
  };
  window.addEventListener('focus', () => window.exploreTrial.refresh());
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) window.exploreTrial.refresh();
  });
})();
