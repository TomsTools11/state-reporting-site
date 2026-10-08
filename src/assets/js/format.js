// Value formats and the {field:format} text templates used by report configs.
// Shared by the build (eleventy.config.js) and the browser (heatmap.js).

export const formats = {
  int: n => n == null ? 'No data' : n.toLocaleString('en-US'),
  money: n => n == null ? 'Not published' : '$' + n.toLocaleString('en-US'),
  pct: n => n == null ? 'No data' : n + '%',
  pct1: n => n == null ? 'No data' : n.toFixed(1) + '%',
  higherThan: n => n == null ? 'No data' : 'Higher than ' + n + '%',
  trend: v => v === 1 ? 'More than before' : v === -1 ? 'Fewer than before' : 'No clear change',
};

// fill('{name} {id}', record) -> 'Salina 74365'. `extra` adds formats such as `tier`;
// `escape` is applied to each inserted value (the browser passes an HTML escaper).
export function fill(template, record, extra = {}, escape = s => s) {
  return String(template).replace(/\{([\w.]+)(?::(\w+))?\}/g, (_, key, fmt) => {
    const value = key.split('.').reduce((o, k) => o == null ? undefined : o[k], record);
    const f = fmt && (extra[fmt] || formats[fmt]);
    return escape(String(f ? f(value) : value ?? ''));
  });
}

// { field, eq | gt | lt } tests used for categories, overlays and chips.
export function matches(test, record) {
  if (!test) return true;
  const v = record[test.field];
  if ('eq' in test) return v === test.eq;
  if ('gt' in test) return v != null && v > test.gt;
  if ('lt' in test) return v != null && v < test.lt;
  return Boolean(v);
}
