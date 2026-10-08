// Interactive heat map for one report edition. Everything report-specific comes from the
// config embedded in the page (#report-config) and the edition's data files.
import { fill, matches } from './format.js';

const cfg = JSON.parse(document.getElementById('report-config').textContent);
const M = cfg.map;
const NS = 'http://www.w3.org/2000/svg';
const $ = id => document.getElementById(id);
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);
const slug = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const duration = reduceMotion ? 0 : 350;
const tierFmt = { tier: t => t == null || t < 0 ? 'No data' : M.tierLabels[t] };
const text = (template, rec) => fill(template, rec, tierFmt, esc);
const lineView = (M.views.find(v => v.lines) || {}).id;
const searchView = M.search.view || M.views[0].id;

function mk(tag, attrs, parent) {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}

// Shapes are absolute M/L/Z paths, so every number pair is a point.
function bbox(...paths) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const d of paths) {
    const n = d.match(/-?\d+(?:\.\d+)?/g);
    for (let i = 0; i < n.length; i += 2) {
      const x = +n[i], y = +n[i + 1];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
  }
  return [x0, y0, x1, y1];
}

const state = { layer: M.layers[0].id, view: M.views[0].id, overlays: new Set(), pinned: null, region: null };
const data = {};   // view id -> { list, byId }
const els = { views: {}, overlays: {} };
const svg = $('mapSvg'), tip = $('tip'), box = $('mapbox');
const emptyDetail = $('detail').innerHTML;
let geo, W, H, zoom, zsel, k = 1;

// ---------- Layers, colors and legends ----------

function resolve(layerId = state.layer, viewId = state.view) {
  const base = M.layers.find(l => l.id === layerId);
  const L = { ...base, ...(base.byView && base.byView[viewId]) };
  const V = M.views.find(v => v.id === viewId);
  L.heading = L.fullTitle || `${L.title} ${V.titleSuffix}`;
  return { L, V };
}

const mix = (L, pct) => pct <= 0 ? `var(--${L.base})` : pct >= 100 ? `var(--${L.color})`
  : `color-mix(in srgb, var(--${L.color}) ${pct}%, var(--${L.base}))`;

function colorOf(L, u) {
  if (L.type === 'tier') {
    const t = u[L.tier];
    return t == null || t < 0 ? 'var(--nodata)' : `var(--${L.ramp}-${t})`;
  }
  if (L.type === 'category') {
    if (L.noData && matches(L.noData, u)) return 'var(--nodata)';
    return `var(--${L.categories.find(c => matches(c.when, u)).color})`;
  }
  if (L.type === 'share') {
    const share = u[L.of] ? u[L.field] / u[L.of] : 0;
    return mix(L, share ? Math.round(Math.min(1, .25 + share * 1.2) * 100) : 0);
  }
  return 'var(--nodata)';
}

function legendHtml(L, V, list) {
  const weight = u => u[M.weight] || 0;
  const total = list.reduce((s, u) => s + weight(u), 0);
  const row = (color, label, items) => {
    const held = items.reduce((s, u) => s + weight(u), 0);
    return `<li><span class="sw" style="background:${color}"></span><span>${label}</span>` +
      `<span class="n">${items.length} ${esc(V.unit)} · ${(held / total * 100).toFixed(1)}% of homes</span></li>`;
  };
  if (L.type === 'tier') {
    return M.tierLabels.map((t, i) => row(`var(--${L.ramp}-${i})`,
      `${esc(t)} <span class="rng">${esc(M.tierRanges[i])}</span>`, list.filter(u => u[L.tier] === i))).join('');
  }
  if (L.type === 'category') {
    const taken = new Set();
    const live = list.filter(u => !(L.noData && matches(L.noData, u)));
    return L.categories.map(c => {
      const items = live.filter(u => !taken.has(u) && matches(c.when, u));
      items.forEach(u => taken.add(u));
      return row(`var(--${c.color})`, esc(c.label), items);
    }).join('');
  }
  if (L.type === 'share') {
    return L.legend.map(e => `<li><span class="sw" style="background:${mix(L, e.mix)}"></span><span>${esc(e.label)}</span>` +
      `<span class="n">${e.countZero ? `${list.filter(u => !u[L.field]).length} ${esc(V.unit)}` : ''}</span></li>`).join('');
  }
  return '';
}

// ---------- Area details (side panel, tooltip, print) ----------

function regionMember(r, viewId, u) {
  return r.counties.includes(viewId === 'county' ? u.name : u.county);
}

function tierLine(layerId, u) {
  const L = M.layers.find(l => l.id === layerId);
  const t = u[L.tier];
  if (t == null || t < 0) return 'No data';
  return `<span class="tiersq" style="background:var(--${L.ramp}-${t})"></span>${esc(u[L.score])} · ${esc(M.tierLabels[t])}`;
}

function body(viewId, u) {
  const D = cfg.detail[viewId];
  const sub = D.regionSub && state.region && regionMember(state.region, viewId, u) ? esc(state.region.label) : text(D.sub, u);
  const rows = D.rows.map(r => `<dt>${esc(r.label)}</dt><dd>${r.layer ? tierLine(r.layer, u) : text(r.text, u)}</dd>`).join('');
  const chips = (D.chips || []).filter(c => matches(c.when, u))
    .map(c => `<span class="chip${c.style ? ' ' + c.style : ''}">${text(c.text, u)}</span>`).join('');
  const note = D.note && u[D.note] ? `<p class="place-sub data-note">Data note: ${esc(u[D.note])}</p>` : '';
  return `<p class="place">${text(D.title, u)}</p><p class="place-sub">${sub}</p><dl class="kv">${rows}</dl>${chips}${note}`;
}

function showDetail(viewId, u) {
  $('detail').innerHTML = `<h3>Selected area</h3>${body(viewId, u)}`;
}

// ---------- Data and drawing ----------

async function load() {
  const get = url => fetch(url).then(r => {
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  });
  const [g, attrs] = await Promise.all([get(cfg.geo), get(cfg.attrs)]);
  geo = g;
  for (const V of M.views) {
    const list = attrs[V.id].filter(u => g[V.id][u.id]);
    list.forEach(u => { u.shape = g[V.id][u.id]; u.view = V.id; });
    data[V.id] = { list, byId: new Map(list.map(u => [u.id, u])) };
  }
}

function drawPlaces(g, fixedScale) {
  for (const p of geo.places.filter(p => M.places.includes(p.name))) {
    const scale = fixedScale ? ` scale(${fixedScale})` : '';
    const pg = mk('g', { transform: `translate(${p.x} ${p.y})${scale}`, 'data-x': p.x, 'data-y': p.y }, g);
    mk('circle', { r: 3.5, 'stroke-width': 1.4 }, pg);
    // Labels near the east edge sit left of the dot so they stay inside the frame.
    const east = p.x > W * .82;
    mk('text', { x: east ? -7 : 7, y: 4, 'font-size': 12, 'stroke-width': 3, 'text-anchor': east ? 'end' : 'start' }, pg).textContent = p.name;
  }
}

// City labels stay the same size on screen at any zoom level.
function sizePlaces() {
  if (!els.places) return;
  const s = (svg.clientWidth || W) / W * k;
  for (const g of els.places.children) g.setAttribute('transform', `translate(${g.dataset.x} ${g.dataset.y}) scale(${1 / s})`);
}

function build() {
  W = geo.w; H = geo.h;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const vp = els.vp = mk('g', { class: 'viewport' }, svg);
  mk('path', { d: geo.outline, class: 'ground' }, vp);
  for (const V of M.views) els.views[V.id] = mk('g', { class: `units units-${V.id}` }, vp);
  els.lines = mk('g', { class: 'cty-lines' }, vp);
  els.dim = mk('path', { class: 'region-dim', d: '' }, vp);
  for (const o of M.overlays) els.overlays[o.id] = mk('g', { class: 'overlay' }, vp);
  mk('path', { d: geo.outline, class: 'state-line' }, vp);
  els.hl = mk('path', { class: 'hl', d: '' }, vp);
  els.pin = mk('path', { class: 'pin', d: '' }, vp);
  els.places = mk('g', { class: 'places' }, vp);

  for (const V of M.views) {
    data[V.id].list.forEach((u, i) => { u.el = mk('path', { d: u.shape, 'data-v': V.id, 'data-i': i }, els.views[V.id]); });
  }
  if (lineView) data[lineView].list.forEach(u => mk('path', { d: u.shape }, els.lines));
  for (const o of M.overlays) {
    data[o.view].list.filter(u => matches(o.when, u)).forEach(u => mk('path', { d: u.shape }, els.overlays[o.id]));
  }
  drawPlaces(els.places);
}

function paint() {
  const { L, V } = resolve();
  for (const v of M.views) els.views[v.id].style.display = v.id === state.view ? '' : 'none';
  els.lines.style.display = lineView && state.view !== lineView ? '' : 'none';
  for (const o of M.overlays) els.overlays[o.id].style.display = state.overlays.has(o.id) && o.view === state.view ? '' : 'none';
  const list = data[state.view].list;
  for (const u of list) u.el.style.fill = colorOf(L, u);

  $('mapTitle').textContent = L.heading;
  $('mapNote').textContent = L.note || '';
  $('legTitle').textContent = L.legendTitle;
  $('legend').innerHTML = legendHtml(L, V, list);
  $('legFoot').textContent = L.legendFoot || '';
  for (const seg of document.querySelectorAll('.seg[data-control]')) {
    for (const b of seg.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.v === state[seg.dataset.control]));
  }
  if (state.pinned) showDetail(state.pinned.view, state.pinned.unit);
  schedulePrint();
}

function applyRegion() {
  const r = state.region;
  const members = r && data.county ? data.county.list.filter(u => regionMember(r, 'county', u)) : [];
  els.dim.setAttribute('d', members.length ? geo.outline + members.map(u => u.shape).join('') : '');
  for (const el of document.querySelectorAll('[data-region]')) el.hidden = !r || el.dataset.region !== r.id;
  return members;
}

// ---------- Selection and URL state ----------

function pin(u, { scroll = false } = {}) {
  state.pinned = u ? { view: u.view, unit: u } : null;
  els.pin.setAttribute('d', u ? u.shape : '');
  if (u) showDetail(u.view, u); else $('detail').innerHTML = emptyDetail;
  syncURL();
  schedulePrint();
  if (u && scroll && matchMedia('(max-width: 1279px)').matches) {
    const r = $('detail').getBoundingClientRect();
    if (r.top < 0 || r.bottom > innerHeight) $('detail').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'nearest' });
  }
}

function syncURL() {
  const p = new URLSearchParams();
  if (state.layer !== M.layers[0].id) p.set('layer', state.layer);
  if (state.view !== M.views[0].id) p.set('view', state.view);
  for (const o of state.overlays) p.append('overlay', o);
  if (state.pinned) {
    const u = state.pinned.unit;
    p.set(u.view, u.view === 'county' ? slug(u.name) : u.id);
  }
  if (state.region) p.set('region', state.region.id);
  const qs = p.toString();
  history.replaceState(history.state, '', location.pathname + (qs ? `?${qs}` : '') + location.hash);
}

// ?layer=fire&view=county&zip=73505&county=comanche&overlay=rise&region=southwest
function readURL() {
  const p = new URLSearchParams(location.search);
  if (M.layers.some(l => l.id === p.get('layer'))) state.layer = p.get('layer');
  if (M.views.some(v => v.id === p.get('view'))) state.view = p.get('view');
  for (const id of p.getAll('overlay')) if (M.overlays.some(o => o.id === id)) state.overlays.add(id);
  state.region = (cfg.regions || []).find(r => r.id === p.get('region')) || null;
  for (const V of M.views) {
    const want = p.get(V.id);
    if (!want) continue;
    const u = data[V.id].byId.get(want) || data[V.id].list.find(x => slug(x.name) === slug(want));
    if (u) { state.view = V.id; return u; }
  }
  return null;
}

// ---------- Zoom ----------

function setupZoom() {
  zoom = d3.zoom()
    .scaleExtent([1, 12])
    .translateExtent([[0, 0], [W, H]])
    // Page scroll keeps working: the wheel zooms only with ctrl/cmd (trackpad pinch sends ctrl),
    // and one finger pans the map only once it is zoomed in.
    .filter(ev => ev.type === 'wheel' ? ev.ctrlKey || ev.metaKey
      : ev.type === 'touchstart' ? ev.touches.length > 1 || k > 1.01
      : !ev.button)
    .on('zoom', ev => {
      k = ev.transform.k;
      els.vp.setAttribute('transform', ev.transform);
      svg.classList.toggle('zoomed', k > 1.01);
      sizePlaces();
    });
  zsel = d3.select(svg).call(zoom);
}

function zoomToBox([x0, y0, x1, y1], maxK = 8, animate = true) {
  const kk = Math.max(1, Math.min(maxK, .88 / Math.max((x1 - x0) / W, (y1 - y0) / H)));
  const t = d3.zoomIdentity.translate(W / 2, H / 2).scale(kk).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
  const fitted = zoom.constrain()(t, [[0, 0], [W, H]], zoom.translateExtent());
  (animate ? zsel.transition().duration(duration) : zsel).call(zoom.transform, fitted);
}

// ---------- Print ----------
// Static copies of the map, one per layer, shown only when printing or saving as PDF.

let printTimer, printDirty = true;
function schedulePrint() {
  printDirty = true;
  clearTimeout(printTimer);
  printTimer = setTimeout(buildPrint, 400);
}

function buildPrint() {
  if (!geo) return;
  printDirty = false;
  const host = $('printMaps');
  host.textContent = '';
  const list = data[state.view].list;
  for (const layer of M.layers) {
    const { L, V } = resolve(layer.id, state.view);
    const fig = document.createElement('figure');
    fig.className = 'print-map';
    fig.innerHTML = `<div class="pm-map"><h3>${esc(L.heading)}</h3><p class="note">${esc(L.note || '')}</p></div>` +
      `<div class="pm-legend"><h4>${esc(L.legendTitle)}</h4><ul class="legend">${legendHtml(L, V, list)}</ul>` +
      `<p class="legend-foot">${esc(L.legendFoot || '')}</p></div>`;
    const s = mk('svg', { viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' }, fig.firstChild);
    mk('path', { d: geo.outline, class: 'ground' }, s);
    const g = mk('g', { class: `units units-${state.view}` }, s);
    for (const u of list) mk('path', { d: u.shape, style: `fill:${colorOf(L, u)}` }, g);
    if (lineView && state.view !== lineView) {
      const lines = mk('g', { class: 'cty-lines' }, s);
      data[lineView].list.forEach(u => mk('path', { d: u.shape }, lines));
    }
    if (state.region) mk('path', { class: 'region-dim', d: els.dim.getAttribute('d') }, s);
    for (const o of M.overlays) {
      if (!state.overlays.has(o.id) || o.view !== state.view) continue;
      const og = mk('g', { class: 'overlay' }, s);
      data[o.view].list.filter(u => matches(o.when, u)).forEach(u => mk('path', { d: u.shape }, og));
    }
    mk('path', { d: geo.outline, class: 'state-line' }, s);
    if (state.pinned) mk('path', { d: state.pinned.unit.shape, class: 'pin' }, s);
    drawPlaces(mk('g', { class: 'places' }, s), W / 500);
    host.appendChild(fig);
  }
  if (state.pinned) {
    host.insertAdjacentHTML('beforeend', `<section class="print-selected"><h3>Selected area</h3>${body(state.pinned.view, state.pinned.unit)}</section>`);
  }
}
addEventListener('beforeprint', () => { if (printDirty) buildPrint(); });

// ---------- Events ----------

function unitAt(target) {
  const i = target.dataset && target.dataset.i;
  return i == null ? null : data[target.dataset.v].list[+i];
}

function hideTip() {
  tip.hidden = true;
  els.hl.setAttribute('d', '');
}

function wire() {
  svg.addEventListener('pointermove', e => {
    if (e.pointerType !== 'mouse') return;
    const u = unitAt(e.target);
    if (!u) return hideTip();
    els.hl.setAttribute('d', u.shape);
    tip.innerHTML = body(u.view, u);
    tip.hidden = false;
    const r = box.getBoundingClientRect();
    let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
    if (x + tip.offsetWidth + 6 > r.width) x = e.clientX - r.left - tip.offsetWidth - 14;
    if (y + tip.offsetHeight > r.height) y = Math.max(4, r.height - tip.offsetHeight - 4);
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  });
  svg.addEventListener('pointerleave', hideTip);
  svg.addEventListener('click', e => {
    const u = unitAt(e.target);
    if (u) pin(u, { scroll: true });
  });

  for (const seg of document.querySelectorAll('.seg[data-control]')) {
    seg.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      state[seg.dataset.control] = b.dataset.v;
      if (state.pinned && state.pinned.view !== state.view) pin(null);
      hideTip();
      paint();
      syncURL();
    });
  }
  for (const cb of document.querySelectorAll('input[data-overlay]')) {
    cb.addEventListener('change', () => {
      if (cb.checked) state.overlays.add(cb.dataset.overlay); else state.overlays.delete(cb.dataset.overlay);
      paint();
      syncURL();
    });
  }

  $('q').addEventListener('input', e => {
    const q = e.target.value.trim().toLowerCase();
    if (q.length < 3) return;
    const list = data[searchView].list;
    const u = list.find(x => x.id.startsWith(q)) ||
      list.filter(x => x.name.toLowerCase().startsWith(q)).sort((a, b) => (b[M.weight] || 0) - (a[M.weight] || 0))[0];
    if (!u) return;
    if (state.view !== searchView) { state.view = searchView; paint(); }
    pin(u);
    zoomToBox(bbox(u.shape), 6);
  });

  for (const b of document.querySelectorAll('[data-zoom]')) {
    b.addEventListener('click', () => {
      const act = b.dataset.zoom;
      if (act === 'reset') zsel.transition().duration(duration).call(zoom.transform, d3.zoomIdentity);
      else zsel.transition().duration(duration).call(zoom.scaleBy, act === 'in' ? 1.6 : 1 / 1.6);
    });
  }
  addEventListener('resize', sizePlaces);
}

async function init() {
  try {
    await load();
  } catch (err) {
    $('mapStatus').textContent = 'The map could not load. Refresh the page to try again.';
    console.error(err);
    return;
  }
  build();
  setupZoom();
  const pinned = readURL();
  for (const cb of document.querySelectorAll('input[data-overlay]')) cb.checked = state.overlays.has(cb.dataset.overlay);
  const regionCounties = applyRegion();
  paint();
  if (pinned) pin(pinned);
  wire();
  $('mapStatus').hidden = true;
  sizePlaces();
  if (regionCounties.length) zoomToBox(bbox(...regionCounties.map(u => u.shape)), 6, false);
  else if (pinned) zoomToBox(bbox(pinned.shape), 6, false);
}

init();
