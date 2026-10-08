import fs from 'node:fs';
import path from 'node:path';
import { fill } from './src/assets/js/format.js';

const SRC = 'src';
const readJson = file => JSON.parse(fs.readFileSync(file, 'utf8'));

// One page per report at /<state>/<slug>/, showing its current edition only.
function reportPages() {
  const dir = path.join(SRC, '_data/reports');
  return fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort().map(file => {
    const report = readJson(path.join(dir, file));
    const meta = readJson(path.join(SRC, report.edition.data, 'meta.json'));
    return { report, edition: report.edition, meta, url: `/${report.state}/${report.slug}/` };
  });
}

export default function (eleventyConfig) {
  eleventyConfig.addPassthroughCopy({ 'src/assets': 'assets', 'src/data': 'data', 'src/robots.txt': 'robots.txt' });
  eleventyConfig.addGlobalData('reportPages', reportPages);
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
