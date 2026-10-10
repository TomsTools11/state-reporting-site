import fs from 'node:fs';
import path from 'node:path';
import { fill, matches } from './src/assets/js/format.js';

const SRC = 'src';
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));

// Screen colors for the static map images (report cards, hero). Keep in sync with the map
// colors in src/assets/css/tokens.css; the interactive map reads those CSS variables.
const MAP_COLORS = {
  'sev-0': '#3b3038', 'sev-1': '#80404a', 'sev-2': '#cc5640', 'sev-3': '#ff8f66',
  'wf-0': '#3a3428', 'wf-1': '#7d5d1f', 'wf-2': '#c98a1c', 'wf-3': '#fbbf24',
  prime: '#057be5', review: '#56637a', other: '#1e2834', nodata: '#12171d',
  edge: '#0d1117', state: '#64748b',
  // Dot maps in research briefs
  'inc-0': '#1d3a5c', 'inc-1': '#1f5f9e', 'inc-2': '#2b86da', 'inc-3': '#64adf2', 'inc-4': '#b4dbff',
  'dot-other': '#2a3442', 'dot-nodata': '#1b222b', renter: '#057be5', core900: '#b4dbff',
};

// The layer settings for one view: the base layer plus its per-view overrides.
function resolveLayer(map, layerId, viewId) {
  const base = map.layers.find(l => l.id === layerId);
  return { ...base, ...(base.byView && base.byView[viewId]) };
}

function mixHex(a, b, pct) {
  const rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [rgb(a), rgb(b)];
  return '#' + x.map((v, i) => Math.round(v * pct + y[i] * (1 - pct)).toString(16).padStart(2, '0')).join('');
}

// Same rules as colorOf() in src/assets/js/heatmap.js, with hex colors instead of variables.
function unitColor(L, u) {
  const C = MAP_COLORS;
  if (L.type === 'tier') {
    const t = u[L.tier];
    return t == null || t < 0 ? C.nodata : C[`${L.ramp}-${t}`];
  }
  if (L.type === 'category') {
    if (L.noData && matches(L.noData, u)) return C.nodata;
    return C[L.categories.find(c => matches(c.when, u)).color];
  }
  if (L.type === 'share') {
    const share = u[L.of] ? u[L.field] / u[L.of] : 0;
    return share ? mixHex(C[L.color], C[L.base], Math.min(1, .25 + share * 1.2)) : C[L.base];
  }
  return C.nodata;
}

// A static SVG of one layer in one view. Coordinates are rounded: these images are shown small.
function mapSvg(report, geo, attrs, layerId, viewId) {
  const L = resolveLayer(report.map, layerId, viewId);
  const round = d => d.replace(/-?\d+\.\d+/g, n => String(Math.round(+n)));
  const outline = round(geo.outline);
  const sw = viewId === 'zip' ? .35 : .9;
  const units = attrs[viewId].filter(u => geo[viewId][u.id])
    .map(u => `<path d="${round(geo[viewId][u.id])}" fill="${unitColor(L, u)}"/>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${geo.w} ${geo.h}" width="${geo.w}" height="${Math.round(geo.h)}">` +
    `<path d="${outline}" fill="${MAP_COLORS.nodata}"/>` +
    `<g stroke="${MAP_COLORS.edge}" stroke-width="${sw}" stroke-linejoin="round">${units}</g>` +
    `<path d="${outline}" fill="none" stroke="${MAP_COLORS.state}" stroke-width="1.2" stroke-linejoin="round"/></svg>`;
}

// A static SVG of a brief's dot map, padded to the 1000 x 525 card shape and centered.
function dotMapSvg(m) {
  const h = m.height, w = Math.max(m.width, h * 1000 / 525), x = (m.width - w) / 2;
  const dots = m.groups.map(g => `<g fill="${MAP_COLORS[g.color]}">` +
    g.dots.map(([cx, cy, r]) => `<circle cx="${cx}" cy="${cy}" r="${r}"/>`).join('') + '</g>').join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x} 0 ${w} ${h}" width="1000" height="525">${dots}</svg>`;
}

// Rows for a report's table: one cell per column, filled from the area's record.
function tableRows(report, attrs) {
  const T = report.table;
  if (!T) return [];
  const M = report.map;
  return attrs[T.view].filter(u => matches(T.where, u))
    .sort((a, b) => (a[T.sort] ?? Infinity) - (b[T.sort] ?? Infinity))
    .slice(0, T.limit)
    .map(u => T.columns.map(c => {
      if (!c.layer) return { text: fill(c.text, u) };
      const L = M.layers.find(l => l.id === c.layer);
      const t = u[L.tier];
      return t == null || t < 0 ? { text: 'No data' }
        : { text: `${u[L.score]} · ${M.tierLabels[t]}`, swatch: `var(--${L.ramp}-${t})` };
    }));
}

// One page per report at /<state>/<slug>/, showing its current edition only.
let cache;
function reportPages() {
  if (cache) return cache;
  const dir = path.join(SRC, '_data/reports');
  cache = fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort().map(file => {
    const report = readJson(path.join(dir, file));
    const meta = readJson(path.join(SRC, report.edition.data, 'meta.json'));
    const url = `/${report.state}/${report.slug}/`;
    // Research briefs: dot maps drawn at build time from the edition's maps.json, and one
    // static image (report.images.card names the map) for the cards.
    if (report.template === 'brief') {
      const maps = readJson(path.join(SRC, report.edition.data, 'maps.json'));
      const card = report.images.card;
      return { report, edition: report.edition, meta, url, maps, images: { [card]: `${url}map-${card}.svg` }, rows: [] };
    }
    const attrs = readJson(path.join(SRC, report.edition.data, 'attrs.json'));
    // Static map images, one per layer, in the first view.
    const view = report.map.views[0].id;
    const images = Object.fromEntries(report.map.layers.map(l => [l.id, `${url}map-${l.id}.svg`]));
    return { report, edition: report.edition, meta, url, images, view, rows: tableRows(report, attrs) };
  });
  return cache;
}

function mapImages() {
  return reportPages().flatMap(p => {
    if (p.maps) return Object.keys(p.images).map(k => ({ url: p.images[k], svg: dotMapSvg(p.maps[k]) }));
    const geo = readJson(path.join(SRC, p.report.geo));
    const attrs = readJson(path.join(SRC, p.edition.data, 'attrs.json'));
    return p.report.map.layers.map(l => ({ url: p.images[l.id], svg: mapSvg(p.report, geo, attrs, l.id, p.view) }));
  });
}

export default function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ 'src/assets': 'assets', 'src/data': 'data', 'src/robots.txt': 'robots.txt' });
  eleventyConfig.on('eleventy.beforeWatch', () => { cache = null; });
  eleventyConfig.addGlobalData('reportPages', reportPages);
  eleventyConfig.addGlobalData('mapImages', mapImages);
  // The report the home page features (site.json "featured"), else the first one.
  eleventyConfig.addGlobalData('featured', () => {
    const id = readJson(path.join(SRC, '_data/site.json')).featured;
    return reportPages().find(p => p.report.id === id) || reportPages()[0];
  });
  // States that have at least one report, each with its reports.
  eleventyConfig.addGlobalData('library', () => {
    const pages = reportPages();
    const states = readJson(path.join(SRC, '_data/site.json')).states;
    return Object.entries(states)
      .map(([id, s]) => ({ id, ...s, reports: pages.filter(p => p.report.state === id) }))
      .filter(s => s.reports.length);
  });

  eleventyConfig.addFilter('fill', (template, values) => fill(template, values));
  // Links are written without a trailing slash to match Vercel's trailingSlash: false.
  eleventyConfig.addFilter('noslash', url => url.replace(/\/+$/, '') || '/');
  // JSON for a <script type="application/json"> block; < keeps "</script>" out of the payload.
  eleventyConfig.addFilter('jsonScript', value => JSON.stringify(value).replace(/</g, '\\u003c'));
  eleventyConfig.addFilter('values', obj => Object.values(obj || {}));
  eleventyConfig.addFilter('merge', (a, b) => ({ ...a, ...b }));
  // A report's lines of business as a list (tags.line is one line or several).
  eleventyConfig.addFilter('lines', line => [].concat(line));
  eleventyConfig.addFilter('layerView', (map, layerId, viewId) => resolveLayer(map, layerId, viewId));

  // Client-identifying text must never ship. Reports here are built from public data only.
  eleventyConfig.on('eleventy.after', ({ results }) => {
    const leaks = results.filter(r => /prepared\s+for/i.test(r.content || '')).map(r => r.outputPath);
    if (leaks.length) throw new Error(`Build output contains "Prepared for": ${leaks.join(', ')}`);
  });

  return {
    dir: { input: SRC, includes: '_includes', data: '_data', output: '_site' },
    templateFormats: ['njk'],
    htmlTemplateEngine: 'njk',
  };
}
