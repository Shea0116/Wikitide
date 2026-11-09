(() => {
  const key = "knowledge-site-theme";
  let theme;
  try {
    theme = localStorage.getItem(key);
  } catch (_error) {
    theme = null;
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.dataset.theme = theme;

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.querySelector(".theme-toggle");
    const label = document.querySelector("[data-theme-label]");
    if (!button || !label) return;
    const update = () => {
      const dark = document.documentElement.dataset.theme === "dark";
      label.textContent = dark ? window.WikitideI18n.t("白天") : window.WikitideI18n.t("黑夜");
      button.setAttribute("aria-pressed", String(dark));
      button.setAttribute("title", dark ? window.WikitideI18n.t("切换到白天模式") : window.WikitideI18n.t("切换到黑夜模式"));
    };
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(key, next); } catch (_error) { /* Private browsing may block storage. */ }
      update();
    });
    update();
  });
})();
