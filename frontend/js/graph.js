// Interactive table flowchart (SVG): used for the approval preview, the saved read-back, AI chat
// answers and the database relationship map. Layered left→right by parent→child links; master
// tables can sit in a top band whose links appear on hover/selection so the picture stays readable.
// The picture can be zoomed (buttons, Ctrl/⌘ + wheel, pinch, + and − keys) and dragged to move it;
// a plain mouse wheel still scrolls the page.
import { esc } from './util.js';

const trunc = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// ---- view maths (pure, unit-tested in tests/test_graph_view.py). A view is { k: zoom, x, y: shift in px }.
export const ZOOM = { min: 0.25, max: 2, step: 1.2 };

// Largest zoom (never above 100%) that shows the whole w×h picture inside a vw×vh box.
export function fitZoom(w, h, vw, vh) { return Math.min(1, vw / w, vh / h); }

// Keep the picture in the box: a smaller picture stays fully visible, a bigger one leaves no empty edge.
export function clampView(v, w, h, vw, vh) {
  const axis = (t, size, box) => size <= box ? clamp(t, 0, box - size) : clamp(t, box - size, 0);
  return { k: v.k, x: axis(v.x, w * v.k, vw), y: axis(v.y, h * v.k, vh) };
}

// Zoom to k around the box point (px, py): the spot under the pointer stays where it is.
export function zoomAround(v, k, px, py) {
  return { k, x: px - (px - v.x) * k / v.k, y: py - (py - v.y) * k / v.k };
}

const ICON = {
  out: '<path d="M5 12h14"/>', in: '<path d="M12 5v14M5 12h14"/>',
  fit: '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
  reset: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
};
const icon = k => `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[k]}</svg>`;
const views = new WeakMap();   // diagram box -> its live view, so re-drawing the same box (＋ expand) keeps the user's zoom
let uid = 0;
const noop = () => {};

// The control that has focus inside the box, as something that can be found again after a redraw.
function focusKey(el) {
  const a = document.activeElement;
  if (!a || a === el || !el.contains(a)) return null;
  const plus = a.closest('[data-plus]'), node = a.closest('.gnode'), z = a.closest('[data-z]');
  return plus ? ['plus', plus.dataset.plus] : node ? ['node', node.dataset.id] : z ? ['z', z.dataset.z] : ['svg'];
}

export function renderGraph(el, model, opts = {}) {
  const prev = views.get(el); prev?.stop(); views.delete(el);
  const refocus = focusKey(el);   // ＋ expand redraws the whole diagram: keep keyboard focus on the same control
  const W = opts.nodeW || 214, H = opts.nodeH || 76, GX = opts.gapX || 92, GY = opts.gapY || 22, PAD = 26;
  const MW = 184, MH = 62, MG = 12;
  const nodes = model.nodes; const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
  if (!nodes.length) {
    el.classList.remove('gviewport', 'panning'); el.style.backgroundSize = el.style.backgroundPosition = '';
    el.innerHTML = '<p class="empty">No tables to show.</p>';
    return { select: noop, fit: noop, reset: noop, zoomIn: noop, zoomOut: noop, destroy: noop };
  }
  const band = opts.masterBand ? nodes.filter(n => n.kind === 'master') : [];
  const bandIds = new Set(band.map(n => n.id));
  const tree = nodes.filter(n => !bandIds.has(n.id));
  const inner = model.edges.filter(e => byId[e.from] && byId[e.to] && !bandIds.has(e.from) && !bandIds.has(e.to));

  // rank = longest path from a root (cycle-safe)
  const rank = {};
  const r = (id, seen = new Set()) => {
    if (rank[id] != null) return rank[id];
    if (seen.has(id)) return 0; seen.add(id);
    const ps = inner.filter(e => e.to === id && e.from !== id).map(e => e.from);
    return (rank[id] = ps.length ? Math.max(...ps.map(p => r(p, seen))) + 1 : 0);
  };
  tree.forEach(n => r(n.id));
  const cols = {};
  tree.forEach(n => (cols[rank[n.id]] ||= []).push(n));
  const pos = {};
  // masters band
  const perRow = Math.max(1, Math.min(band.length, opts.bandPerRow || 6));
  const bandH = band.length ? 30 + Math.ceil(band.length / perRow) * (MH + MG) : 0;
  band.sort((a, b) => String(a.title).localeCompare(String(b.title))).forEach((n, i) => {
    pos[n.id] = { x: PAD + (i % perRow) * (MW + MG), y: PAD + 22 + Math.floor(i / perRow) * (MH + MG), w: MW, h: MH };
  });
  const top = PAD + bandH + (band.length ? 34 : 0);
  const ranks = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const tallest = Math.max(...ranks.map(k => cols[k].length));
  const treeH = tallest * H + (tallest - 1) * GY;
  ranks.forEach(k => {
    const list = cols[k];
    // order by the average position of parents (fewer crossings), then base first, then name
    list.sort((a, b) => {
      const avg = n => { const ps = inner.filter(e => e.to === n.id && pos[e.from]).map(e => pos[e.from].y); return ps.length ? ps.reduce((x, y) => x + y, 0) / ps.length : 0; };
      return avg(a) - avg(b) || (b.base ? 1 : 0) - (a.base ? 1 : 0) || String(a.title).localeCompare(String(b.title));
    });
    const total = list.length * H + (list.length - 1) * GY; const y0 = top + (treeH - total) / 2;
    list.forEach((n, i) => { pos[n.id] = { x: PAD + k * (W + GX), y: y0 + i * (H + GY), w: W, h: H }; });
  });
  const width = Math.max(PAD * 2 + ranks.length * W + (ranks.length - 1) * GX, band.length ? PAD * 2 + perRow * (MW + MG) : 0, opts.minWidth || 0);
  const height = top + treeH + PAD;

  const n0 = ++uid;   // unique ids: several diagrams can be on one page
  const curve = (a, b) => {
    const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
    if (x2 <= x1) { const yA = a.y + a.h, yB = b.y + b.h; return [`M${a.x + a.w / 2},${yA} C${a.x + a.w / 2},${yA + 40} ${b.x + b.w / 2},${yB + 40} ${b.x + b.w / 2},${yB}`, (x1 + x2) / 2, Math.max(yA, yB) + 30]; }
    const mx = (x1 + x2) / 2; return [`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, mx, (y1 + y2) / 2];
  };
  const down = (a, b) => `M${a.x + a.w / 2},${a.y + a.h} C${a.x + a.w / 2},${a.y + a.h + 50} ${b.x + b.w / 2},${b.y - 50} ${b.x + b.w / 2},${b.y}`;
  const edges = model.edges.filter(e => pos[e.from] && pos[e.to]).map(e => {
    const fromBand = bandIds.has(e.from) || bandIds.has(e.to);
    const cls = `gedge ${e.cls || 'link'} ${fromBand ? 'bandedge hide' : ''}`;
    if (fromBand) return `<g class="${cls}" data-a="${esc(e.from)}" data-b="${esc(e.to)}"><title>${esc(e.title || e.label || '')}</title><path d="${down(pos[e.from], pos[e.to])}" marker-end="url(#arr-${n0})"/></g>`;
    const [p, lx, ly] = curve(pos[e.from], pos[e.to]);
    return `<g class="${cls}" data-a="${esc(e.from)}" data-b="${esc(e.to)}"><title>${esc(e.title || e.label || '')}</title><path d="${p}" marker-end="url(#arr-${n0})"/>
      ${e.label && opts.edgeLabels !== false ? `<text x="${lx}" y="${ly - 6}" text-anchor="middle">${esc(trunc(e.label, 26))}</text>` : ''}</g>`;
  }).join('');

  const node = n => {
    const p = pos[n.id]; const small = bandIds.has(n.id);
    const classes = ['gnode', n.kind || 'detail', n.dashed ? 'dashed' : '', n.base ? 'base' : '', n.dim ? 'dim' : '', n.id === opts.selected ? 'sel' : ''].join(' ');
    const st = n.status || {};
    return `<g class="${classes}" tabindex="0" role="button" data-id="${esc(n.id)}" transform="translate(${p.x},${p.y})" aria-label="${esc(`${n.title} (${n.code || n.id}) ${n.line3 || ''} ${st.text || ''}`)}">
      <rect class="box" width="${p.w}" height="${p.h}" rx="10"/><rect class="band" width="5" height="${p.h}" rx="2.5"/>
      <text class="t1" x="16" y="${small ? 21 : 23}">${esc(trunc(n.title, small ? 22 : 25))}</text>
      <text class="t2" x="16" y="${small ? 36 : 38}">${esc(trunc(n.code || n.id, 28))}</text>
      ${small ? `<text class="t4 ${esc(st.cls || '')}" x="16" y="53">${esc(trunc(st.text || n.line3 || '', 26))}</text>` :
        `<text class="t3" x="16" y="${p.h - 22}">${esc(trunc(n.line3 || '', 32))}</text><text class="t4 ${esc(st.cls || '')}" x="16" y="${p.h - 8}">${esc(trunc(st.text || '', 32))}</text>`}
      ${opts.onExpand && !small ? `<g class="gplus" data-plus="${esc(n.id)}" transform="translate(${p.w - 17},17)" role="button" tabindex="0" aria-label="Show tables linked to ${esc(n.title)}"><title>Show linked tables</title><circle r="10"/><text text-anchor="middle" y="5">+</text></g>` : ''}</g>`;
  };

  // Until the box is measured the picture simply scales to the width (viewBox); then the zoomable view takes over.
  el.classList.add('gviewport'); el.classList.remove('panning');
  el.innerHTML = `<svg class="gsvg" viewBox="0 0 ${width} ${height}" tabindex="0" role="group"
      aria-roledescription="diagram" aria-label="${esc(opts.label || 'Table relationships')}" aria-describedby="ghelp-${n0}">
    <defs><marker id="arr-${n0}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#8fb1ab"/></marker></defs>
    <g class="gcanvas">${edges}
    ${band.length ? `<text class="lane" x="${PAD}" y="${PAD + 10}">${esc(opts.bandTitle || 'MASTER DATA (point at a table to see its links)')}</text>` : ''}
    ${band.length && opts.treeTitle ? `<text class="lane" x="${PAD}" y="${top - 12}">${esc(opts.treeTitle)}</text>` : ''}
    ${nodes.map(node).join('')}</g></svg>
    <p class="visually-hidden" id="ghelp-${n0}">Drag to move the diagram. Press plus or minus, or hold Ctrl and scroll, to zoom. Press 0 to fit it in view and the arrow keys to move it.</p>
    <div class="gtools" role="toolbar" aria-label="Diagram view">
      <button type="button" data-z="out" aria-label="Zoom out" title="Zoom out (−)">${icon('out')}</button>
      <span class="gzoom" title="Zoom level"><span class="visually-hidden">Zoom </span><span data-zl>100%</span></span>
      <button type="button" data-z="in" aria-label="Zoom in" title="Zoom in (+, or Ctrl + scroll)">${icon('in')}</button>
      <span class="gsep" aria-hidden="true"></span>
      <button type="button" data-z="fit" aria-label="Fit the whole diagram in view" title="Fit to view (0)">${icon('fit')}</button>
      <button type="button" data-z="reset" aria-label="Reset the view" title="Reset view">${icon('reset')}</button>
    </div><span class="visually-hidden" role="status" aria-live="polite" aria-atomic="true" data-zsay></span>`;
  const svg = el.querySelector('svg'), canvas = svg.querySelector('.gcanvas'), tools = el.querySelector('.gtools');
  svg.style.maxWidth = `${width}px`;   // CSSOM, not a style attribute (CSP style-src 'self'); width: 100% is .gviewport > .gsvg
  const level = tools.querySelector('[data-zl]'), zin = tools.querySelector('[data-z=in]'), zout = tools.querySelector('[data-z=out]');
  const say = el.querySelector('[data-zsay]');

  const focus = id => svg.querySelectorAll('.bandedge').forEach(g => g.classList.toggle('hide', !(id && (g.dataset.a === id || g.dataset.b === id))));
  const select = id => {
    svg.querySelectorAll('.gnode').forEach(g => g.classList.toggle('sel', g.dataset.id === id)); focus(id);
  };
  svg.querySelectorAll('.gnode').forEach(g => {
    g.addEventListener('click', e => {
      if (e.target.closest('[data-plus]')) return;
      select(g.dataset.id); opts.onSelect?.(g.dataset.id);
    });
    g.addEventListener('mouseenter', () => focus(g.dataset.id));
    g.addEventListener('mouseleave', () => focus(svg.querySelector('.gnode.sel')?.dataset.id));
    g.addEventListener('focus', () => focus(g.dataset.id));
    g.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === g) { e.preventDefault(); select(g.dataset.id); opts.onSelect?.(g.dataset.id); } });
  });
  svg.querySelectorAll('[data-plus]').forEach(b => {
    const go = e => {
      e.stopPropagation();
      const p = pos[b.dataset.plus], v = live.view;   // remember where this table sits so the redraw keeps it there
      live.anchor = { id: b.dataset.plus, sx: v.x + p.x * v.k, sy: v.y + p.y * v.k };
      if (document.activeElement !== b) b.focus({ preventScroll: true });   // so the redraw puts focus back on this ＋
      opts.onExpand?.(b.dataset.plus);
    };
    b.addEventListener('click', go);
    b.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } });
  });
  if (opts.selected) select(opts.selected);

  // ---- zoom & pan ----
  const ac = new AbortController();
  const on = (t, type, fn, o = {}) => t.addEventListener(type, fn, { ...o, signal: ac.signal });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // mode: 'auto' follows the box width (like a responsive image), 'fit' keeps everything in view, 'manual' = user's zoom
  const live = { view: { k: 1, x: 0, y: 0 }, mode: 'auto', anchor: null, stop };
  views.set(el, live);
  let box = { w: 0, h: 0 }, minK = ZOOM.min, shown = live.view, anim = 0, raf = 0, sayT = 0, said = '100%';

  const px = v => (String(v).endsWith('px') ? parseFloat(v) : NaN);
  const corner = () => ({ w: tools.offsetWidth + 16, h: tools.offsetHeight + 16 });   // toolbar corner plus a little air
  const underTools = (v, c = corner()) => Object.values(pos).some(p => v.x + (p.x + p.w) * v.k > box.w - c.w && v.y + (p.y + p.h) * v.k > box.h - c.h);
  function layout(outer) {   // size the box for a container this wide; false while the diagram is hidden
    const cs = getComputedStyle(el);
    const w = outer - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    if (w <= 0) return false;
    const extra = ['borderTopWidth', 'borderBottomWidth', 'paddingTop', 'paddingBottom'].reduce((s, p) => s + (parseFloat(cs[p]) || 0), 0);
    const maxH = Number.isNaN(px(cs.maxHeight)) ? Infinity : px(cs.maxHeight) - extra, minH = (px(cs.minHeight) || 0) - extra;
    // natural height at the box width, plus room under the picture when a table would sit beneath the toolbar
    const k0 = Math.min(1, w / width), x0 = (w - width * k0) / 2, c = corner();
    const room = Math.max(0, ...Object.values(pos).filter(p => x0 + (p.x + p.w) * k0 > w - c.w).map(p => (p.y + p.h - height) * k0 + c.h));
    const h = Math.round(clamp(height * k0 + room, Math.max(minH, 0), maxH));
    svg.removeAttribute('viewBox'); svg.style.maxWidth = 'none'; svg.style.height = `${h}px`;
    box = { w, h, room }; minK = Math.min(ZOOM.min, fitZoom(width, height, w, h));
    return true;
  }
  const keep = v => clampView({ ...v, k: clamp(v.k, minK, ZOOM.max) }, width, height, box.w, box.h);
  const autoView = () => {   // the old look: as wide as the box (never above 100%), centred, base table in view
    const k = Math.min(1, box.w / width), base = nodes.find(n => n.base) || nodes.find(n => n.id === opts.selected);
    let y = Math.max(0, (box.h - box.room - height * k) / 2);
    if (height * k > box.h) y = base && pos[base.id] ? box.h / 2 - (pos[base.id].y + pos[base.id].h / 2) * k : 0;
    return keep({ k, x: (box.w - width * k) / 2, y });
  };
  const fitView = () => {   // everything in view; shrink a little more if a table would hide under the toolbar
    let k = fitZoom(width, height, box.w, box.h), v = { k, x: (box.w - width * k) / 2, y: (box.h - height * k) / 2 };
    if (underTools(v)) { const c = corner(); k = fitZoom(width, height, box.w, box.h - c.h); v = { k, x: (box.w - width * k) / 2, y: (box.h - c.h - height * k) / 2 }; }
    return keep(v);
  };
  const paint = v => {
    shown = v;
    canvas.setAttribute('transform', `translate(${v.x.toFixed(2)} ${v.y.toFixed(2)}) scale(${v.k.toFixed(4)})`);
    let g = 16 * v.k; while (g < 10) g *= 2;   // the dotted background moves and scales with the picture
    el.style.backgroundSize = `${g}px ${g}px`; el.style.backgroundPosition = `${v.x.toFixed(1)}px ${v.y.toFixed(1)}px`;
  };
  // The readout shows the zoom being moved to (not every animation frame); screen readers hear only the
  // value it settles on, once the user stops zooming, and nothing for a drag or for the diagram's own layout.
  const readout = (k, quiet) => {
    const t = `${Math.round(k * 100)}%`;
    if (level.textContent !== t) level.textContent = t;
    clearTimeout(sayT);
    if (quiet) said = t;
    else if (t !== said) sayT = setTimeout(() => { said = t; say.textContent = `Zoom ${t}`; }, 450);
  };
  function set(v, animate = false, mode = 'manual', quiet = false) {
    if (!box.w) return false;
    const from = live.view; v = keep(v);
    live.view = v; live.mode = mode; readout(v.k, quiet);
    zin.setAttribute('aria-disabled', String(v.k >= ZOOM.max - 1e-6)); zout.setAttribute('aria-disabled', String(v.k <= minK + 1e-6));
    cancelAnimationFrame(anim);
    if (!animate || reduced) paint(v);
    else {
      const a = shown, t0 = performance.now();
      const step = t => { const p = Math.min(1, (t - t0) / 180), e = 1 - (1 - p) ** 3;
        paint({ k: a.k + (v.k - a.k) * e, x: a.x + (v.x - a.x) * e, y: a.y + (v.y - a.y) * e }); if (p < 1) anim = requestAnimationFrame(step); };
      anim = requestAnimationFrame(step);
    }
    return Math.abs(v.x - from.x) + Math.abs(v.y - from.y) + Math.abs(v.k - from.k) > 0.01;
  }
  const centre = () => [box.w / 2, box.h / 2];
  const zoomBy = (f, x, y, animate = true) => { [x, y] = x == null ? centre() : [x, y]; return set(zoomAround(live.view, clamp(live.view.k * f, minK, ZOOM.max), x, y), animate); };
  const panBy = (dx, dy) => set({ ...live.view, x: live.view.x + dx, y: live.view.y + dy }, true);
  const fit = () => set(fitView(), true, 'fit');
  const reset = () => set(autoView(), true, 'auto');
  const restore = () => {   // same box re-drawn (＋ expand): keep the user's zoom and the clicked table where it was
    if (!prev || prev.mode === 'auto') return null;
    if (prev.mode === 'fit') return [fitView(), 'fit'];
    const k = clamp(prev.view.k, minK, ZOOM.max), a = prev.anchor && pos[prev.anchor.id] && prev.anchor;
    return [a ? { k, x: a.sx - pos[a.id].x * k, y: a.sy - pos[a.id].y * k } : prev.view, 'manual'];
  };
  // Classic scrollbars (Windows): when this diagram's height decides whether the page needs a scrollbar, the
  // scrollbar changes the width, the width changes the height, the scrollbar goes away and it starts again,
  // every frame. When the width keeps flipping between two values about a scrollbar's width apart, the
  // narrower layout is kept (it fits either way) until the box really changes size.
  let lastW = -1, prevW = -1, flipAt = 0, flips = 0, pair = null, laid = -1;
  function refit() {
    if (!svg.isConnected) return stop();   // the box was removed or re-filled by someone else
    const w = el.clientWidth; if (!w || w === lastW) return;
    const first = lastW < 0 || !box.w, now = performance.now();
    if (pair && !pair.includes(w)) pair = null;
    flips = w === prevW && Math.abs(w - lastW) <= 30 && now - flipAt < 500 ? flips + 1 : 0;
    if (!pair && flips >= 2) pair = [Math.min(w, lastW), Math.max(w, lastW)];
    prevW = lastW; lastW = w; flipAt = now;
    const use = pair ? pair[0] : w;
    if (use === laid && !first) return;
    if (!layout(use)) return;
    laid = use;
    const back = first && restore();
    if (back) return set(back[0], false, back[1], true);
    set(live.mode === 'fit' ? fitView() : live.mode === 'manual' && !first ? live.view : autoView(), false, first ? 'auto' : live.mode, true);
  }
  let release = null;   // window listeners while a pointer is down (see watchRelease)
  function stop() { ac.abort(); release?.abort(); ro.disconnect(); cancelAnimationFrame(anim); cancelAnimationFrame(raf); clearTimeout(sayT); }
  // deferred a frame: resizing inside the observer callback would trigger "ResizeObserver loop" warnings
  const ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(refit); });
  if (el.isConnected) refit();
  ro.observe(el);

  // drag (mouse, pen or one finger) to move; two fingers to pinch-zoom. A drag never counts as a click.
  const pts = new Map(); let grip = null, moved = false;
  const local = e => { const b = svg.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
  const regrip = () => { grip = pts.size ? { view: live.view, pts: [...pts.values()] } : null; };
  const capture = id => { try { svg.setPointerCapture(id); } catch (_) { /* pointer already gone */ } };
  // Capture starts only once a drag passes its threshold (capturing at once would send a table's click to
  // the svg). Until then a press near the edge can be released outside the diagram, so while any pointer is
  // down its release is watched on the whole window too; otherwise the picture would follow the bare mouse.
  const watchRelease = () => {
    if (release) return;
    release = new AbortController();
    const o = { capture: true, signal: release.signal };
    window.addEventListener('pointerup', lift, o); window.addEventListener('pointercancel', lift, o);
    // only the WINDOW losing focus ends a drag: with capture, blur events of elements (e.g. the question box
    // losing focus when the press lands on the diagram) also arrive here and used to cancel every drag at once
    window.addEventListener('blur', e => { if (e.target === window) [...pts.keys()].forEach(pointerId => lift({ pointerId })); }, o);
  };
  on(svg, 'pointerdown', e => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (!pts.size) moved = false;
    pts.set(e.pointerId, local(e)); regrip(); watchRelease();
    if (pts.size > 1) pts.forEach((_, id) => capture(id));
  });
  on(svg, 'pointermove', e => {
    if (!grip || !pts.has(e.pointerId)) return;
    if (e.pointerType !== 'touch' && !(e.buttons & 1)) return lift(e);   // released somewhere no event reached us
    pts.set(e.pointerId, local(e));
    const [a, b] = [...pts.values()], [a0, b0] = grip.pts, v = grip.view;
    if (b && b0) {
      const ratio = Math.hypot(b[0] - a[0], b[1] - a[1]) / (Math.hypot(b0[0] - a0[0], b0[1] - a0[1]) || 1);
      const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], m0 = [(a0[0] + b0[0]) / 2, (a0[1] + b0[1]) / 2];
      const z = zoomAround(v, clamp(v.k * ratio, minK, ZOOM.max), m0[0], m0[1]);
      moved = true; set({ k: z.k, x: z.x + m[0] - m0[0], y: z.y + m[1] - m0[1] });
      return;
    }
    const dx = a[0] - a0[0], dy = a[1] - a0[1];
    if (!moved && Math.hypot(dx, dy) < (e.pointerType === 'touch' ? 8 : 4)) return;
    if (!moved) { moved = true; capture(e.pointerId); el.classList.add('panning'); }
    set({ k: v.k, x: v.x + dx, y: v.y + dy });
  });
  function lift(e) {
    if (!pts.delete(e.pointerId)) return;
    regrip();
    if (!pts.size) {
      release?.abort(); release = null;
      el.classList.remove('panning'); setTimeout(() => { moved = false; }, 0);   // after the click that ends a drag
    }
  }
  on(svg, 'pointerup', lift); on(svg, 'pointercancel', lift);
  on(svg, 'click', e => { if (moved) { e.stopPropagation(); e.preventDefault(); } }, { capture: true });
  on(svg, 'dblclick', e => { if (!moved && !e.target.closest('.gnode')) zoomBy(ZOOM.step * ZOOM.step, ...local(e)); });

  // Ctrl/⌘ + wheel (and trackpad pinch) zooms around the pointer; a plain wheel is left alone so the page scrolls.
  on(el, 'wheel', e => {
    if (!svg.isConnected) return stop();
    if (!(e.ctrlKey || e.metaKey) || !box.w) return;
    e.preventDefault();
    const d = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? box.h : 1);
    zoomBy(Math.exp(-clamp(d, -120, 120) * 0.0025), ...local(e), false);
  }, { passive: false });

  // keyboard, while focus is anywhere in the diagram: + / − zoom, 0 fits, arrows move (Shift = bigger steps)
  on(el, 'keydown', e => {
    if (!svg.isConnected) return stop();
    if (e.ctrlKey || e.metaKey || e.altKey) return;   // leave the browser's own Ctrl + / Ctrl 0 alone
    const s = e.shiftKey ? 160 : 48, k = e.key;
    let done = true;
    if (k === '+' || k === '=') zoomBy(ZOOM.step);
    else if (k === '-' || k === '_') zoomBy(1 / ZOOM.step);
    else if (k === '0') fit();
    else if (k.startsWith('Arrow') && !tools.contains(e.target)) {
      // only along a side the picture overflows, and only if it can still move; otherwise the page scrolls as usual
      const across = k === 'ArrowLeft' || k === 'ArrowRight', v = live.view;
      done = (across ? width * v.k > box.w + 0.5 : height * v.k > box.h + 0.5) &&
        panBy(k === 'ArrowLeft' ? s : k === 'ArrowRight' ? -s : 0, k === 'ArrowUp' ? s : k === 'ArrowDown' ? -s : 0);
    } else done = false;
    if (done) e.preventDefault();
  });

  // a table reached with Tab slides into view
  on(svg, 'focusin', e => {
    const g = e.target.closest?.('.gnode'); if (!g || !g.matches(':focus-visible') || !box.w) return;
    const p = pos[g.dataset.id], v = live.view, m = 12;
    const sx = v.x + p.x * v.k, sy = v.y + p.y * v.k, w = p.w * v.k, h = p.h * v.k;
    const dx = sx < m ? m - sx : sx + w > box.w - m ? Math.max(box.w - m - (sx + w), m - sx) : 0;
    const dy = sy < m ? m - sy : sy + h > box.h - m ? Math.max(box.h - m - (sy + h), m - sy) : 0;
    if (dx || dy) panBy(dx, dy);
  });

  // toolbar: one Tab stop, arrow keys move between its buttons
  const btns = [...tools.querySelectorAll('button')];
  btns.forEach((b, i) => { b.tabIndex = i ? -1 : 0; });
  on(tools, 'click', e => {
    const b = e.target.closest('[data-z]'); if (!b || b.getAttribute('aria-disabled') === 'true') return;
    ({ in: () => zoomBy(ZOOM.step), out: () => zoomBy(1 / ZOOM.step), fit, reset })[b.dataset.z]();
  });
  on(tools, 'keydown', e => {
    const i = btns.indexOf(document.activeElement);
    const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: btns.length - 1 }[e.key];
    if (i < 0 || j == null) return;
    e.preventDefault();
    const b = btns[(j + btns.length) % btns.length]; btns.forEach(x => { x.tabIndex = x === b ? 0 : -1; }); b.focus();
  });

  if (refocus) {   // the redraw replaced the focused control: focus its counterpart (Tab then carries on from there)
    const [kind, key] = refocus;
    const find = (sel, attr) => [...el.querySelectorAll(sel)].find(x => x.dataset[attr] === key);
    const t = (kind === 'plus' && (find('[data-plus]', 'plus') || find('.gnode', 'id'))) || (kind === 'node' && find('.gnode', 'id')) || (kind === 'z' && find('[data-z]', 'z')) || svg;
    if (btns.includes(t)) btns.forEach(x => { x.tabIndex = x === t ? 0 : -1; });
    t.focus({ preventScroll: true });
  }

  return { select, fit, reset, zoomIn: () => zoomBy(ZOOM.step), zoomOut: () => zoomBy(1 / ZOOM.step), destroy: stop };
}
