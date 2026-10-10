# GOAL State Reports

Static site of state weather and risk reports, built with [Eleventy](https://www.11ty.dev/) and
deployed on Vercel. Every report is built from public data only. No client names or account data
belong in this repo.

| URL | Page |
|---|---|
| `/` | All reports, grouped by state |
| `/ok` | Oklahoma reports |
| `/ok/home-risk` | Oklahoma Home Risk Map (current edition) |
| `/ca` | California reports |
| `/ca/demographics` | California Demographics by ZIP Code |
| `/ca/brush-fire-risk` | California Brush Fire Risk by ZIP Code |

Report links can open on a layer, view, area or region, for example
`/ok/home-risk?layer=fire&view=county&county=comanche` or `/ok/home-risk?region=southwest`.

## Build

```bash
npm ci
npm run dev      # http://localhost:8080
npm run build    # writes _site/
```

## Add a heat map report

1. Add a config file in `src/_data/reports/<id>.json` (copy `ok-home-risk.json`): title, layers,
   views, legend text, detail fields, key figures, regions, methods.
2. Add the data in `src/data/<state>/<report>/<edition>/` (`attrs.json`, `meta.json`) and the
   shapes in `src/data/<state>/geo-<vintage>.json`.
3. Add the state to `src/_data/site.json` if it is new.

No template, script or stylesheet changes are needed.

## Add a research brief

Briefs (`"template": "brief"`) are static reports: key figures, dot maps, bar charts, tables and
fact lists, set out in the config's `sections`. Copy `src/_data/reports/ca-demographics.json`.

1. List each section's `items`. Item types are `figures`, `map`, `bars`, `table` and `facts`;
   `cols` sets items side by side and `stack` stacks them. Text fields can hold `<b>` and links.
   A table cell or fact written as `{ "tier": 2 }` shows that tier's swatch and label from `tiers`.
2. Put `meta.json` and the dot maps (`maps.json`, keyed by the name a `map` item uses) in
   `src/data/<state>/<report>/<edition>/`. Each map has a `width`, `height`, `groups` of
   `[x, y, radius]` dots with a `color` token from `tokens.css`, and labelled `places`.

Only the current edition of a report is live. To release a new one, publish its data folder, point the config's
`edition` at it, and delete the old folder (git history keeps it). Old dated links such as
`/ok/home-risk/2026-10` redirect to the current report.

## Data

`pipeline/` builds report data from public sources for any state (`--state OK`, `--state TX`, ...).
`pipeline/sources.md` explains the method and lists every dataset and release, and
`pipeline/sources.lock.json` pins the exact files used.

```bash
uv venv --python 3.12 && uv pip install -r pipeline/requirements.txt
.venv/bin/python pipeline/usfs.py --state OK
.venv/bin/python pipeline/home_risk.py --state OK --edition 2026-10
```

`npm run check` compares the site data with the original map it was extracted from (that file is
kept outside the repo).
