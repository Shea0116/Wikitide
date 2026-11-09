(() => {
  const accountMenu = document.querySelector(".account-menu");
  if (accountMenu) {
    document.addEventListener("click", (event) => {
      if (accountMenu.open && !accountMenu.contains(event.target)) accountMenu.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && accountMenu.open) {
        event.preventDefault();
        accountMenu.open = false;
        accountMenu.querySelector("summary").focus();
      }
    });
    accountMenu.addEventListener("focusout", () => {
      requestAnimationFrame(() => {
        if (!accountMenu.contains(document.activeElement)) accountMenu.open = false;
      });
    });
  }
  const header = document.querySelector(".site-header");
  let readingNavHidden = false;
  const setHeaderOffset = () => {
    if (header) document.documentElement.style.setProperty("--header-offset", `${readingNavHidden ? 0 : Math.ceil(header.getBoundingClientRect().height)}px`);
  };
  setHeaderOffset();
  if (header && "ResizeObserver" in window) new ResizeObserver(setHeaderOffset).observe(header);
  if (header && document.body.classList.contains("article-page")) {
    let lastY = Math.max(0, window.scrollY);
    let direction = 0;
    let distance = 0;
    let scheduled = false;
    const height = () => Math.ceil(header.getBoundingClientRect().height);
    const showNavigation = (show) => {
      readingNavHidden = !show;
      if (!show && header.contains(document.activeElement)) document.activeElement.blur();
      header.inert = !show;
      header.setAttribute("aria-hidden", String(!show));
      document.body.classList.toggle("reading-nav-hidden", !show);
      setHeaderOffset();
    };
    const updateNavigation = () => {
      const y = Math.max(0, window.scrollY);
      const delta = y - lastY;
      lastY = y;
      if (y <= 1) {
        direction = 0; distance = 0; showNavigation(true);
      } else if (delta !== 0) {
        const nextDirection = Math.sign(delta);
        if (nextDirection !== direction) { direction = nextDirection; distance = 0; }
        distance += Math.abs(delta);
        if (distance > height()) {
          showNavigation(direction < 0);
          distance = 0;
        }
      }
      scheduled = false;
    };
    window.addEventListener("scroll", () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(updateNavigation); }
    }, { passive: true });
    window.addEventListener("pageshow", () => {
      lastY = Math.max(0, window.scrollY); direction = 0; distance = 0;
      showNavigation(lastY <= height());
    });
    document.addEventListener("keydown", (event) => {
      const target = event.target;
      const editing = target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
      if (event.key === "/" && !editing && !event.ctrlKey && !event.metaKey && !event.altKey) {
        direction = 0; distance = 0; showNavigation(true);
      }
    });
    showNavigation(lastY <= height());
  }
  const directory = document.querySelector(".directory-panel");
  if (directory) directory.open = window.innerWidth > 820;
  const articleNavigation = document.querySelector(".article-nav-panel");
  if (articleNavigation) {
    articleNavigation.open = window.innerWidth >= 1100;
    const revealCurrentArticle = () => {
      if (!articleNavigation.open) return;
      const tree = articleNavigation.querySelector(".article-nav-tree");
      const current = tree.querySelector('[aria-current="page"]');
      if (!current || !current.getClientRects().length) return;
      const treeBounds = tree.getBoundingClientRect();
      const currentBounds = current.getBoundingClientRect();
      if (currentBounds.top < treeBounds.top || currentBounds.bottom > treeBounds.bottom) {
        tree.scrollTop += currentBounds.top - treeBounds.top - tree.clientHeight / 3;
      }
    };
    articleNavigation.addEventListener("toggle", () => requestAnimationFrame(revealCurrentArticle));
    requestAnimationFrame(revealCurrentArticle);
  }
  const search = document.getElementById("site-search");
  if (search?.form) {
    const validateSearch = () => {
      search.setCustomValidity(search.value.trim() ? "" : window.WikitideI18n.t("请输入搜索关键词"));
    };
    search.addEventListener("input", validateSearch);
    search.addEventListener("invalid", validateSearch);
    search.form.addEventListener("submit", (event) => {
      search.value = search.value.trim();
      validateSearch();
      if (!search.checkValidity()) {
        event.preventDefault();
        search.reportValidity();
      }
    });
    validateSearch();
  }
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const editing = target instanceof HTMLElement && (target.matches("input, textarea, select") || target.isContentEditable);
    if (event.key === "/" && !editing && !event.ctrlKey && !event.metaKey && !event.altKey && search) {
      event.preventDefault(); search.focus();
    }
    if (event.key === "Escape" && document.activeElement === search) search.blur();
  });

  const prose = document.querySelector(".prose");
  if (!prose) return;
  const meta = document.querySelector(".reading-meta");
  if (meta) {
    const length = prose.textContent.replace(/\s/g, "").length;
    meta.textContent = window.WikitideI18n.t("约 {0} 分钟阅读", [Math.max(1, Math.ceil(length / 450))]);
    meta.hidden = false;
  }
  prose.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((heading, index) => {
    if (heading.id) return;
    const slug = heading.textContent.trim().toLowerCase().replace(/[^\p{L}\p{N}\p{M}\s_-]/gu, "").replace(/\s+/g, "-") || `section-${index + 1}`;
    let id = slug;
    let suffix = 1;
    while (document.getElementById(id)) id = `${slug}-${suffix++}`;
    heading.id = id;
  });
  const jumpToFragment = () => {
    if (!location.hash) return;
    try {
      const fragment = decodeURIComponent(location.hash.slice(1));
      const target = document.getElementById(fragment) || [...prose.querySelectorAll('h1,h2,h3,h4,h5,h6')].find(heading => heading.textContent.trim().toLowerCase() === fragment.toLowerCase() || heading.id === fragment.toLowerCase());
      target?.scrollIntoView();
    } catch (_error) { /* Ignore malformed URL fragments. */ }
  };
  jumpToFragment(); window.addEventListener('hashchange', jumpToFragment);
  const headings = [...prose.querySelectorAll("h2, h3")];
  const outline = document.querySelector(".reading-outline");
  if (outline && headings.length > 1) {
    const nav = outline.querySelector("nav");
    const links = new Map();
    headings.forEach((heading, index) => {
      const link = document.createElement("a");
      link.href = `#${encodeURIComponent(heading.id)}`;
      link.textContent = heading.textContent;
      link.className = `level-${heading.tagName.slice(1)}`;
      nav.append(link); links.set(heading, link);
    });
    outline.hidden = false;
    const outlineDetails = outline.querySelector("details");
    const sidebarMedia = window.matchMedia(`(min-width: ${articleNavigation ? 1350 : 1100}px)`);
    const syncOutline = () => { outlineDetails.open = sidebarMedia.matches; };
    syncOutline();
    sidebarMedia.addEventListener("change", syncOutline);
    outlineDetails.addEventListener("toggle", () => {
      if (sidebarMedia.matches && !outlineDetails.open) outlineDetails.open = true;
    });
    let scheduled = false;
    const update = () => {
      const offset = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-offset")) || 0) + 35;
      let current = headings[0];
      for (const heading of headings) {
        if (heading.getBoundingClientRect().top <= offset) current = heading;
        else break;
      }
      for (const [heading, link] of links) {
        if (heading === current) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      }
      scheduled = false;
    };
    window.addEventListener("scroll", () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
    }, { passive: true });
    window.addEventListener("resize", update); update();
  }
  const progress = document.querySelector(".reading-progress");
  if (progress) {
    let scheduled = false;
    const update = () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      progress.style.transform = `scaleX(${total > 0 ? Math.min(1, Math.max(0, window.scrollY / total)) : 1})`;
      scheduled = false;
    };
    window.addEventListener("scroll", () => {
      if (!scheduled) { scheduled = true; requestAnimationFrame(update); }
    }, { passive: true });
    window.addEventListener("resize", update); update();
  }
  prose.querySelectorAll("pre > code").forEach((code) => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "code-copy"; button.textContent = window.WikitideI18n.t("复制代码");
    button.setAttribute("aria-label", window.WikitideI18n.t("复制此代码块"));
    button.setAttribute("aria-live", "polite");
    let reset;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(code.textContent);
        button.textContent = window.WikitideI18n.t("已复制 ✓");
      } catch (_error) { button.textContent = window.WikitideI18n.t("请手动复制"); }
      clearTimeout(reset); reset = setTimeout(() => { button.textContent = window.WikitideI18n.t("复制代码"); }, 2000);
    });
    code.parentElement.append(button);
  });
})();
