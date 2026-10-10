# GOAL State Reports

Static site of state weather and risk reports, built with [Eleventy](https://www.11ty.dev/) and
deployed on Vercel. Every report is built from public data only. No client names or account data
belong in this repo.

| URL | Page |
|---|---|
| `/` | All reports, grouped by state |
| `/ok` | Oklahoma reports |
| `/ok/home-risk` | Oklahoma Home Risk Map (current edition) |

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
   views, legend text, detail fields, key figures, regions, methods. Optional display fields:
   `tiles` (short figures on the state page), `images` (which layer the home hero and report cards
   show), `table` (a ranked table of areas), `sources` (source tags) and `highlight` on a stat,
   layer or method to give it the brand glow.
2. Add the data in `src/data/<state>/<report>/<edition>/` (`attrs.json`, `meta.json`) and the
   shapes in `src/data/<state>/geo-<vintage>.json`.
3. Add the state to `src/_data/site.json` if it is new.

No template, script or stylesheet changes are needed.

Only the current edition is live. To release a new one, publish its data folder, point the config's
`edition` at it, and delete the old folder (git history keeps it). Old dated links such as
`/ok/home-risk/2026-10` redirect to the current report.

## Design

The site uses the GOAL design system: dark grounds, GOAL blue, Sora for headlines and numbers,
Outfit for text (both self-hosted, `npm run vendor`). Tokens live in `src/assets/css/tokens.css`;
`print.css` switches to the light theme for print and PDF. Each report also gets static map images
(`/<state>/<report>/map-<layer>.svg`) built from its data for the cards and the home page hero;
their colors are `MAP_COLORS` in `eleventy.config.js`.

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
