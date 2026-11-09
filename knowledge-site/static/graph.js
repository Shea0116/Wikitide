(() => {
  const canvas = document.getElementById("graph-canvas");
  if (!canvas) return;
  const $ = (id) => document.getElementById(id);
  const ctx = canvas.getContext("2d");
  const wrap = $("graph-canvas-wrap");
  const query = $("graph-query");
  const category = $("graph-category");
  const isolated = $("graph-isolated");
  const labels = $("graph-labels");
  const depth = $("graph-depth");
  const message = $("graph-message");
  const tooltip = $("graph-tooltip");
  const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reducedMotion = motionPreference.matches;
  const sphere = window.WikitideSphere;
  const palette = ["#4d8daf", "#9676b5", "#b59045", "#599b87", "#bd7880", "#758cad", "#9c8d65", "#7e98a0"];
  let allNodes = [], allLinks = [], nodes = [], links = [], nodeMap = new Map();
  let adjacent = new Map(), outgoing = new Map(), incoming = new Map(), colors = new Map();
  let selected = null, hovered = null, orientation = sphere?.quaternionIdentity();
  let width = 0, height = 0, radius = 0, dpr = 1, frame = null, zoom = 1;
  let listLimit = 40, ready = false, loading = false, paint = {};
  let projected = [], autoRotate = false, focusAnimation = null, lastFrame = 0;
  let yawVelocity = 0, pitchVelocity = 0, gesture = null;
  const pointers = new Map();
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const nodeRadius = (node) => 3 + Math.min(10, Math.sqrt(node.degree) * 1.3);
  const endpoint = (value) => typeof value === "object" ? value.id : value;
  const getColor = (node) => colors.get(node.root) || palette[0];
  const requestDraw = () => {
    if (frame === null && !document.hidden) frame = requestAnimationFrame(renderFrame);
  };
  const initialOrientation = () => sphere.quaternionMultiply(sphere.axisAngle({x: 0, y: 1, z: 0}, -.35), sphere.axisAngle({x: 1, y: 0, z: 0}, .2));
  const viewport = () => ({width, height, radius, zoom});
  const pointOnCanvas = (event) => { const rect = canvas.getBoundingClientRect(); return {x: event.clientX - rect.left, y: event.clientY - rect.top}; };

  function rotate(yaw, pitch, roll = 0) {
    if (!sphere || !orientation) return;
    const turn = sphere.quaternionMultiply(sphere.axisAngle({x: 0, y: 1, z: 0}, yaw), sphere.axisAngle({x: 1, y: 0, z: 0}, pitch));
    orientation = sphere.quaternionMultiply(sphere.axisAngle({x: 0, y: 0, z: 1}, roll), sphere.quaternionMultiply(turn, orientation));
  }

  function renderFrame(timestamp) {
    frame = null;
    const dt = Math.min(40, lastFrame ? timestamp - lastFrame : 16);
    lastFrame = timestamp;
    if (ready) {
      if (focusAnimation) {
        const progress = clamp((timestamp - focusAnimation.start) / 420, 0, 1);
        orientation = sphere.slerp(focusAnimation.from, focusAnimation.to, 1 - Math.pow(1 - progress, 3));
        if (progress === 1) focusAnimation = null;
      } else if (!pointers.size) {
        if (autoRotate) rotate(dt * .00009, 0);
        if (!reducedMotion && (Math.abs(yawVelocity) + Math.abs(pitchVelocity) > .00001)) {
          rotate(yawVelocity * dt, pitchVelocity * dt);
          const friction = Math.exp(-dt / 180); yawVelocity *= friction; pitchVelocity *= friction;
        } else { yawVelocity = 0; pitchVelocity = 0; }
      }
    }
    draw();
    if (ready && (autoRotate || focusAnimation || (!pointers.size && Math.abs(yawVelocity) + Math.abs(pitchVelocity) > .00001))) requestDraw();
    else lastFrame = 0;
  }

  function setSpin(enabled) {
    autoRotate = !!enabled && ready;
    $("graph-spin").setAttribute("aria-pressed", String(autoRotate));
    const text = autoRotate ? window.WikitideI18n.t("暂停自动旋转") : window.WikitideI18n.t("开启自动旋转");
    $("graph-spin").setAttribute("aria-label", text); $("graph-spin").title = text;
    if (autoRotate) { focusAnimation = null; yawVelocity = 0; pitchVelocity = 0; }
    requestDraw();
  }

  function stopMotion() {
    setSpin(false); focusAnimation = null; yawVelocity = 0; pitchVelocity = 0;
  }

  function faceNode(node, animate = true) {
    if (!node || !sphere) return;
    stopMotion();
    const target = sphere.focusOrientation(node.position);
    if (animate && !reducedMotion) focusAnimation = {from: orientation, to: target, start: performance.now()};
    else orientation = target;
    requestDraw();
  }
  const readTheme = () => {
    const style = getComputedStyle(document.documentElement);
    paint = { ink: style.getPropertyValue("--ink").trim(), muted: style.getPropertyValue("--muted").trim(), paper: style.getPropertyValue("--paper").trim(), line: style.getPropertyValue("--line").trim() };
    requestDraw();
  };
  const setMessage = (title, text, retry = false) => {
    message.querySelector("strong").textContent = title;
    message.querySelector("p").textContent = text;
    message.querySelector(".graph-loading-mark").hidden = !loading;
    $("graph-retry").hidden = !retry;
    message.hidden = false;
  };
  const setDisabled = (disabled) => {
    for (const id of ["graph-query", "graph-category", "graph-isolated", "graph-labels", "graph-reset", "graph-fit", "graph-spin", "graph-zoom-in", "graph-zoom-out"]) $(id).disabled = disabled;
  };

  function draw() {
    if (!ctx || !sphere || !orientation || !width || !height) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!ready || !nodes.length) { projected = []; return; }
    const view = viewport();
    const silhouette = radius * zoom * 3.2 / Math.sqrt(3.2 * 3.2 - 1);
    const glow = ctx.createRadialGradient(width / 2 - silhouette * .35, height / 2 - silhouette * .4, 0, width / 2, height / 2, silhouette);
    glow.addColorStop(0, paint.paper); glow.addColorStop(1, paint.line);
    ctx.globalAlpha = .16; ctx.fillStyle = glow;
    ctx.beginPath(); ctx.arc(width / 2, height / 2, silhouette, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = .22; ctx.strokeStyle = paint.line; ctx.lineWidth = .7; ctx.stroke();
    // Quiet great circles give the sphere a visible volume, without adding
    // invented note relationships to the data or the connection count.
    for (let meridian = 0; meridian < 3; meridian++) {
      const angle = meridian * Math.PI / 3;
      for (let step = 0; step < 64; step++) {
        const t1 = step * Math.PI / 32, t2 = (step + 1) * Math.PI / 32;
        const from = sphere.project({x: Math.cos(t1) * Math.cos(angle), y: Math.sin(t1), z: Math.cos(t1) * Math.sin(angle)}, orientation, view);
        const to = sphere.project({x: Math.cos(t2) * Math.cos(angle), y: Math.sin(t2), z: Math.cos(t2) * Math.sin(angle)}, orientation, view);
        ctx.globalAlpha = (from.depth + to.depth) / 2 > .5 ? .14 : .05; ctx.strokeStyle = paint.line; ctx.lineWidth = .6;
        ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
      }
    }
    projected = nodes.map(node => {
      const position = sphere.project(node.position, orientation, view);
      return {...position, node, r: nodeRadius(node) * position.scale * Math.sqrt(zoom)};
    }).sort((a, b) => a.z - b.z);
    const byId = new Map(projected.map(point => [point.node.id, point]));
    const focus = hovered || selected;
    const connected = focus ? new Set([focus.id, ...(adjacent.get(focus.id) || [])]) : null;
    const orderedLinks = links.map(link => ({...link, a: byId.get(link.source.id), b: byId.get(link.target.id)})).sort((a, b) => (a.a.z + a.b.z) - (b.a.z + b.b.z));
    for (const link of orderedLinks) {
      const active = !focus || link.source.id === focus.id || link.target.id === focus.id;
      const visibility = .06 + Math.pow((link.a.depth + link.b.depth) / 2, 1.5) * .35;
      ctx.globalAlpha = active ? (focus ? visibility * 1.8 : visibility) : .025;
      ctx.strokeStyle = focus && active ? getColor(focus) : paint.muted;
      ctx.lineWidth = focus && active ? 1.1 : .65;
      ctx.beginPath(); ctx.moveTo(link.a.x, link.a.y); ctx.lineTo(link.b.x, link.b.y); ctx.stroke();
    }
    for (const point of projected) {
      const {node, x, y, r} = point;
      if (x < -35 || x > width + 35 || y < -35 || y > height + 35) continue;
      const visibility = .16 + Math.pow(point.depth, 1.2) * .84;
      ctx.globalAlpha = connected && !connected.has(node.id) ? visibility * .18 : visibility;
      ctx.fillStyle = getColor(node);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      if (point.depth > .55 && r > 4) {
        ctx.globalAlpha *= .35; ctx.fillStyle = paint.paper;
        ctx.beginPath(); ctx.arc(x - r * .25, y - r * .28, r * .23, 0, Math.PI * 2); ctx.fill();
      }
      if (node === selected || node === hovered) {
        ctx.globalAlpha = Math.max(.5, visibility);
        ctx.strokeStyle = getColor(node); ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.arc(x, y, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
    }
    if (labels.checked || focus) {
      const boxes = [];
      const labelBudget = Math.max(4, Math.min(zoom > 1.5 ? 30 : 12, Math.floor(width / 85)));
      const ordered = [...projected].sort((a, b) => ((b.node === focus) - (a.node === focus)) || b.node.degree - a.node.degree || b.depth - a.depth);
      const fontSize = Math.max(10, Math.min(13, 11 * Math.sqrt(zoom)));
      ctx.font = `${fontSize}px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif`;
      ctx.textBaseline = "middle";
      for (const point of ordered) {
        const {node, x, y, r} = point;
        const isFocus = node === focus;
        if (!isFocus && boxes.length >= labelBudget) continue;
        if (!isFocus && (!labels.checked || (connected && !connected.has(node.id)))) continue;
        if (!isFocus && (point.depth < .53 || (nodes.length > 40 && zoom < 1.4 && node.degree < 3))) continue;
        const left = x + r + 6;
        const available = Math.min(230, width - left - 10);
        if (available < 20) continue;
        let text = node.title;
        if (ctx.measureText(text).width > available) {
          while (text.length > 1 && ctx.measureText(text + "…").width > available) text = text.slice(0, -1);
          text += "…";
        }
        const textWidth = ctx.measureText(text).width;
        const top = y - fontSize * .8;
        const box = {x: left, y: top, w: textWidth + 5, h: fontSize * 1.6};
        if (left > width || left + box.w < 0 || top > height || top + box.h < 0) continue;
        if (!isFocus && boxes.some(b => b.x < box.x + box.w && b.x + b.w > box.x && b.y < box.y + box.h && b.y + b.h > box.y)) continue;
        boxes.push(box);
        ctx.globalAlpha = isFocus ? .94 : .8;
        ctx.fillStyle = paint.paper; ctx.fillRect(left - 2, top, box.w, box.h);
        ctx.globalAlpha = isFocus ? 1 : .87;
        ctx.fillStyle = isFocus ? paint.ink : paint.muted; ctx.fillText(text, left, y);
      }
    }
    ctx.globalAlpha = 1;
  }

  function resize() {
    width = wrap.clientWidth; height = wrap.clientHeight; dpr = Math.min(window.devicePixelRatio || 1, 2);
    radius = Math.max(30, Math.min(width - 65, height - 65) * .42);
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    requestDraw();
  }

  function fitView() {
    if (!ready || !sphere) return;
    stopMotion(); zoom = 1; orientation = initialOrientation();
    $("graph-scale").textContent = "100%"; hideTooltip(); requestDraw();
  }

  function nodeAt(x, y) {
    if (!ready) return null;
    // The nearest hemisphere wins when projected nodes overlap.
    for (let index = projected.length - 1; index >= 0; index--) {
      const point = projected[index];
      if (Math.hypot(point.x - x, point.y - y) <= Math.max(point.r + 2, 8)) return point.node;
    }
    return null;
  }

  function hideTooltip() { tooltip.hidden = true; hovered = null; }

  function renderNoteButtons(element, items, emptyText = "") {
    const active = document.activeElement;
    const focusedId = active?.closest("ul") === element ? active.dataset.noteId : null;
    element.replaceChildren();
    if (!items.length && emptyText) {
      const empty = document.createElement("li"); empty.textContent = emptyText; empty.className = "graph-no-links"; element.append(empty);
    }
    for (const node of items) {
      const item = document.createElement("li"), button = document.createElement("button"), dot = document.createElement("span"), text = document.createElement("span");
      button.type = "button"; button.dataset.noteId = node.id;
      if (node === selected) button.setAttribute("aria-current", "true");
      dot.className = "graph-dot"; dot.style.setProperty("--node-color", getColor(node)); dot.setAttribute("aria-hidden", "true");
      text.textContent = node.title;
      button.append(dot, text); button.addEventListener("click", () => selectNode(node, true)); item.append(button); element.append(item);
    }
    if (focusedId) {
      const replacement = [...element.querySelectorAll("button")].find(button => button.dataset.noteId === focusedId);
      (replacement || (selected ? $("graph-open-note") : query)).focus({preventScroll: true});
    }
  }

  function renderList() {
    const ordered = [...nodes].sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
    renderNoteButtons($("graph-note-list"), ordered.slice(0, listLimit), window.WikitideI18n.t("此范围内暂无笔记"));
    $("graph-list-count").textContent = nodes.length;
    $("graph-more-notes").hidden = nodes.length <= listLimit;
  }

  function renderSelection() {
    $("graph-selection-empty").hidden = !!selected;
    $("graph-selection-detail").hidden = !selected;
    $("graph-clear").hidden = !selected;
    if (!selected) return;
    $("graph-selected-title").textContent = selected.title;
    $("graph-selected-category").textContent = selected.category;
    $("graph-open-note").href = selected.url;
    const out = [...outgoing.get(selected.id)].map(id => nodeMap.get(id)).sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
    const into = [...incoming.get(selected.id)].map(id => nodeMap.get(id)).sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
    $("graph-out-count").textContent = out.length; $("graph-in-count").textContent = into.length;
    renderNoteButtons($("graph-outgoing"), out, window.WikitideI18n.t("尚未引用其他笔记"));
    renderNoteButtons($("graph-incoming"), into, window.WikitideI18n.t("暂无其他笔记引用"));
  }

  function updateLocation() {
    const url = new URL(location.href);
    if (selected) { url.searchParams.set("note", selected.id); url.searchParams.set("depth", depth.value); }
    else { url.searchParams.delete("note"); url.searchParams.delete("depth"); }
    history.replaceState(null, "", url);
  }

  function selectNode(node, reveal = false) {
    const clearHadFocus = document.activeElement === $("graph-clear");
    stopMotion();
    const wasLocal = depth.value !== "all";
    selected = node; hideTooltip();
    if (!node) depth.value = "all";
    if (reveal && node && !nodes.includes(node)) { query.value = ""; category.value = ""; isolated.checked = true; }
    renderSelection(); updateLocation();
    if (!node && clearHadFocus) canvas.focus({preventScroll: true});
    if (wasLocal || depth.value !== "all" || (node && !nodes.includes(node))) applyFilters();
    else { renderList(); requestDraw(); }
    if (reveal && node && nodes.includes(node)) {
      faceNode(node);
    }
  }

  function applyFilters() {
    if (!ready) return;
    stopMotion(); hideTooltip();
    const term = query.value.trim().toLocaleLowerCase();
    const filtered = allNodes.filter(node => (!category.value || node.root === category.value) && (!term || node.title.toLocaleLowerCase().includes(term)) && (isolated.checked || node.degree > 0));
    if (selected && !filtered.includes(selected)) { selected = null; depth.value = "all"; renderSelection(); updateLocation(); }
    let neighborhood = null;
    if (selected && depth.value !== "all") {
      neighborhood = new Set([selected.id]); let frontier = new Set([selected.id]);
      for (let step = 0; step < Number(depth.value); step++) {
        const next = new Set();
        for (const id of frontier) for (const neighbor of adjacent.get(id)) if (!neighborhood.has(neighbor)) { neighborhood.add(neighbor); next.add(neighbor); }
        frontier = next;
      }
    }
    nodes = filtered.filter(node => !neighborhood || neighborhood.has(node.id));
    const visible = new Set(nodes.map(node => node.id));
    links = allLinks.filter(link => visible.has(endpoint(link.source)) && visible.has(endpoint(link.target))).map(link => ({source: nodeMap.get(endpoint(link.source)), target: nodeMap.get(endpoint(link.target))}));
    listLimit = 40; renderList();
    $("graph-stats").textContent = window.WikitideI18n.t("{0} 篇笔记 · {1} 条引用{2}", [nodes.length, links.length, selected && depth.value !== "all" ? window.WikitideI18n.t(" · {0}连接", [depth.value === "1" ? window.WikitideI18n.t("一度") : window.WikitideI18n.t("二度")]) : ""]);
    if (!nodes.length) {
      setMessage(allNodes.length ? window.WikitideI18n.t("此范围内暂无笔记") : window.WikitideI18n.t("知识库正在生长"), allNodes.length ? window.WikitideI18n.t("试试其他关键词或目录，或点击「全部图谱」重新探索。") : window.WikitideI18n.t("添加可见笔记后，它们会在这里形成你的知识图谱。"));
      requestDraw(); return;
    }
    message.hidden = true;
    if (nodes.length === 1 && !selected) faceNode(nodes[0]);
    requestDraw();
  }

  async function loadGraph() {
    if (loading) return;
    loading = true; ready = false; setDisabled(true); stopMotion();
    $("graph-workspace").setAttribute("aria-busy", "true");
    setMessage(window.WikitideI18n.t("正在绘制关系图谱"), window.WikitideI18n.t("为每一个想法，找到它的位置。"));
    try {
      if (!ctx || !sphere) throw new Error("Graph rendering unavailable");
      const response = await fetch("/api/graph", {cache: "no-store", headers: {Accept: "application/json"}});
      if (!response.ok) throw new Error(`Graph request ${response.status}`);
      const data = await response.json();
      allNodes = data.nodes.map(node => ({...node, degree: 0}));
      nodeMap = new Map(allNodes.map(node => [node.id, node]));
      allLinks = data.links.filter(link => nodeMap.has(link.source) && nodeMap.has(link.target));
      adjacent = new Map(allNodes.map(node => [node.id, new Set()]));
      outgoing = new Map(allNodes.map(node => [node.id, new Set()]));
      incoming = new Map(allNodes.map(node => [node.id, new Set()]));
      for (const link of allLinks) {
        adjacent.get(link.source).add(link.target); adjacent.get(link.target).add(link.source);
        outgoing.get(link.source).add(link.target); incoming.get(link.target).add(link.source);
      }
      for (const node of allNodes) node.degree = adjacent.get(node.id).size;
      sphere.layout(allNodes); orientation = initialOrientation(); zoom = 1;
      $("graph-scale").textContent = "100%";
      const roots = [...new Set(allNodes.map(node => node.root))].sort((a, b) => a.localeCompare(b, "en"));
      const established = {Backend: palette[0], Frontend: palette[1], "知识导览": palette[2]};
      colors = new Map(roots.map(root => {
        const hash = [...root].reduce((value, char) => (value * 31 + char.codePointAt(0)) >>> 0, 0);
        return [root, Object.hasOwn(established, root) ? established[root] : palette[hash % palette.length]];
      }));
      category.replaceChildren(new Option(window.WikitideI18n.t("全部目录"), "")); $("graph-legend").replaceChildren();
      for (const root of roots) {
        category.add(new Option(root, root));
        const item = document.createElement("span"), dot = document.createElement("i");
        dot.className = "graph-dot"; dot.style.setProperty("--node-color", colors.get(root)); dot.setAttribute("aria-hidden", "true");
        item.append(dot, document.createTextNode(root)); $("graph-legend").append(item);
      }
      const params = new URLSearchParams(location.search);
      selected = nodeMap.get(Number(params.get("note"))) || null;
      depth.value = selected ? (["all", "1", "2"].includes(params.get("depth")) ? params.get("depth") : "1") : "all";
      ready = true; loading = false; setDisabled(false); renderSelection(); resize(); applyFilters();
      if (selected) faceNode(selected, false);
    } catch (error) {
      loading = false; setMessage(window.WikitideI18n.t("图谱暂时未能加载"), window.WikitideI18n.t("请稍后重试，或先从笔记目录继续阅读。"), true);
      $("graph-stats").textContent = window.WikitideI18n.t("加载未完成");
      console.warn("Wikitide graph:", error.message);
    } finally { $("graph-workspace").setAttribute("aria-busy", "false"); }
  }

  function setZoom(value) {
    zoom = clamp(value, .4, 3.5);
    $("graph-scale").textContent = `${Math.round(zoom * 100)}%`;
    hideTooltip(); requestDraw();
  }

  const pairMetrics = () => {
    const [a, b] = [...pointers.values()];
    return {distance: Math.max(1, Math.hypot(b.x - a.x, b.y - a.y)), angle: Math.atan2(b.y - a.y, b.x - a.x), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
  };
  let lastTap = null;
  canvas.addEventListener("pointerdown", event => {
    if (!ready || (event.pointerType === "mouse" && event.button !== 0)) return;
    event.preventDefault(); canvas.focus({preventScroll: true}); stopMotion(); hideTooltip();
    const point = pointOnCanvas(event); pointers.set(event.pointerId, point);
    canvas.setPointerCapture(event.pointerId);
    if (pointers.size === 1) gesture = {start: point, moved: false, multi: false, lastMove: performance.now()};
    else { gesture.multi = true; gesture.moved = true; gesture.pair = pairMetrics(); }
    requestDraw();
  });
  canvas.addEventListener("pointermove", event => {
    if (!ready) return;
    const point = pointOnCanvas(event), previous = pointers.get(event.pointerId);
    if (previous && gesture) {
      event.preventDefault(); pointers.set(event.pointerId, point);
      if (pointers.size > 1) {
        const pair = pairMetrics(), old = gesture.pair || pair;
        setZoom(zoom * pair.distance / old.distance);
        const roll = Math.atan2(Math.sin(pair.angle - old.angle), Math.cos(pair.angle - old.angle));
        rotate((pair.x - old.x) * .006, (pair.y - old.y) * .006, -roll);
        gesture.pair = pair; yawVelocity = 0; pitchVelocity = 0;
      } else {
        const dx = point.x - previous.x, dy = point.y - previous.y;
        if (Math.hypot(point.x - gesture.start.x, point.y - gesture.start.y) >= 4) gesture.moved = true;
        if (gesture.moved) {
          rotate(dx * .006, dy * .006);
          const now = performance.now(), dt = Math.max(8, now - gesture.lastMove);
          yawVelocity = clamp(dx * .006 / dt, -.003, .003); pitchVelocity = clamp(dy * .006 / dt, -.003, .003);
          gesture.lastMove = now;
        }
      }
      canvas.classList.toggle("is-dragging", gesture.moved); requestDraw(); return;
    }
    if (event.pointerType === "touch" || event.buttons) return;
    const node = nodeAt(point.x, point.y);
    canvas.style.cursor = node ? "pointer" : "grab";
    if (node && autoRotate) setSpin(false);
    if (hovered !== node) { hovered = node; requestDraw(); }
    if (!node) { tooltip.hidden = true; return; }
    tooltip.querySelector("strong").textContent = node.title;
    tooltip.querySelector("span").textContent = window.WikitideI18n.t("{0} · {1} 篇相连笔记", [node.category, node.degree]);
    tooltip.hidden = false;
    tooltip.style.left = `${Math.max(8, Math.min(point.x + 15, width - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(point.y + 15, height - tooltip.offsetHeight - 8))}px`;
  });

  function endPointer(event, cancelled = false) {
    if (!pointers.has(event.pointerId)) return;
    const ended = gesture;
    pointers.delete(event.pointerId);
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (pointers.size) { if (gesture) gesture.pair = null; return; }
    gesture = null; canvas.classList.remove("is-dragging");
    if (cancelled) { stopMotion(); return; }
    if (ended && !ended.moved && !ended.multi) {
      const point = pointOnCanvas(event), node = nodeAt(point.x, point.y), now = performance.now();
      if (event.pointerType === "touch" && node && lastTap?.id === node.id && now - lastTap.time < 350) {
        lastTap = null; location.assign(node.url);
      } else { selectNode(node); lastTap = node ? {id: node.id, time: now} : null; }
    } else {
      lastTap = null;
      if (reducedMotion || ended?.multi || performance.now() - (ended?.lastMove || 0) > 80) { yawVelocity = 0; pitchVelocity = 0; }
      requestDraw();
    }
  }
  canvas.addEventListener("pointerup", event => endPointer(event));
  canvas.addEventListener("pointercancel", event => endPointer(event, true));
  canvas.addEventListener("lostpointercapture", event => { if (pointers.has(event.pointerId)) endPointer(event, true); });
  canvas.addEventListener("pointerleave", () => { hideTooltip(); requestDraw(); });
  canvas.addEventListener("dblclick", event => {
    if (!ready) return;
    const point = pointOnCanvas(event), node = nodeAt(point.x, point.y);
    if (node) { event.preventDefault(); location.assign(node.url); }
  });
  canvas.addEventListener("wheel", event => {
    if (!ready) return;
    event.preventDefault(); stopMotion();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1);
    setZoom(zoom * Math.exp(-clamp(delta, -300, 300) * .0015));
  }, {passive: false});
  canvas.addEventListener("keydown", event => {
    if (!ready) return;
    if (["+", "=", "-"].includes(event.key)) { event.preventDefault(); stopMotion(); setZoom(zoom * (event.key === "-" ? .8 : 1.25)); }
    else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault(); stopMotion();
      rotate(event.key === "ArrowLeft" ? -.12 : event.key === "ArrowRight" ? .12 : 0, event.key === "ArrowUp" ? -.12 : event.key === "ArrowDown" ? .12 : 0); hideTooltip(); requestDraw();
    } else if (event.key === "Escape") { stopMotion(); selectNode(null); }
    else if (event.key === "0") { event.preventDefault(); fitView(); }
  });
  for (const [id, scale] of [["graph-zoom-in", 1.25], ["graph-zoom-out", .8]]) $(id).addEventListener("click", () => { stopMotion(); setZoom(zoom * scale); });
  $("graph-spin").addEventListener("click", () => setSpin(!autoRotate));
  let searchTimer;
  query.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(applyFilters, 120); });
  category.addEventListener("change", applyFilters); isolated.addEventListener("change", applyFilters);
  labels.addEventListener("change", requestDraw);
  depth.addEventListener("change", () => { updateLocation(); applyFilters(); });
  $("graph-clear").addEventListener("click", () => selectNode(null));
  $("graph-fit").addEventListener("click", () => fitView());
  $("graph-reset").addEventListener("click", () => {
    query.value = ""; category.value = ""; isolated.checked = true; depth.value = "all"; selected = null;
    renderSelection(); updateLocation(); applyFilters(); fitView();
  });
  $("graph-more-notes").addEventListener("click", () => { listLimit += 40; renderList(); });
  $("graph-retry").addEventListener("click", loadGraph);
  document.querySelector(".graph-filters").open = window.innerWidth > 820;
  new MutationObserver(readTheme).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(wrap);
  else window.addEventListener("resize", resize);
  motionPreference.addEventListener("change", event => { reducedMotion = event.matches; if (reducedMotion) stopMotion(); });
  document.addEventListener("visibilitychange", () => {
    lastFrame = 0;
    if (document.hidden) {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null; yawVelocity = 0; pitchVelocity = 0; pointers.clear(); gesture = null; canvas.classList.remove("is-dragging");
    }
    else requestDraw();
  });
  window.addEventListener("pagehide", () => {
    stopMotion(); ready = false; allNodes = []; allLinks = []; nodes = []; links = []; projected = []; selected = null; pointers.clear(); gesture = null;
    nodeMap.clear(); adjacent.clear(); outgoing.clear(); incoming.clear(); colors.clear();
    for (const id of ["graph-note-list", "graph-legend", "graph-outgoing", "graph-incoming"]) $(id).replaceChildren();
    for (const id of ["graph-selected-title", "graph-selected-category"]) $(id).textContent = "";
    category.replaceChildren(new Option(window.WikitideI18n.t("全部目录"), "")); $("graph-stats").textContent = window.WikitideI18n.t("正在连接知识…");
    renderSelection();
    requestDraw();
  });
  window.addEventListener("pageshow", event => { if (event.persisted) loadGraph(); });
  readTheme(); resize(); loadGraph();
})();
