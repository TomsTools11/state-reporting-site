# Data sources and method

All inputs are public downloads; no API keys are needed. Raw files go in `pipeline/raw/` (gitignored).
`pipeline/sources.lock.json` records every file the pipeline used: URL, size, SHA-256 and fetch date.

## Running it

```bash
uv venv --python 3.12 && uv pip install -r pipeline/requirements.txt   # once
.venv/bin/python pipeline/usfs.py --state OK          # Forest Service wildfire percentiles (needs ~10 GB, once per machine)
.venv/bin/python pipeline/home_risk.py --state OK --edition 2026-10   # writes pipeline/out/ for review
.venv/bin/python pipeline/check_parity.py edition pipeline/out/ok/home-risk/2026-10 src/data/ok/home-risk/2026-10
```

`home_risk.py --publish` writes a new edition into `src/data/`; published editions are never overwritten.
Edition settings (data releases and storm periods) live in `EDITIONS` at the top of `home_risk.py`.

## Home risk map

| Dataset | Release | Used for | Source |
|---|---|---|---|
| FEMA National Risk Index, census tracts | December 2025 (`NRI_VER`) | Tornado, hail, strong wind and wildfire expected annual loss rate national percentiles (`*_ALR_NPCTL`) | ArcGIS service `National_Risk_Index_Census_Tracts` |
| USDA Forest Service Wildfire Risk to Communities | 2nd edition, 2024 (RDS-2020-0016-2, RDS-2020-0060-2) | Risk to Potential Structures (60 m overview) and housing unit count, lower 48 | fs.usda.gov/rds, streamed with `remote_zip.py` |
| ACS 5-year table-based summary files | 2020-2024 | B25003 tenure, B11001 households, B19013 median household income (ZCTA, county, state) | www2.census.gov/programs-surveys/acs/summary_file |
| 2020 Census tabulation blocks (TIGER/Line) | 2020 | `HOUSING20` housing units, the weights for every average | www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20 |
| 2020 ZCTA relationship files | 2020 | Block-to-ZCTA and ZCTA-to-county (land area) | www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520 |
| Gazetteer counties | 2024 | County names and land area | www2.census.gov/geo/docs/maps-data/data/gazetteer |
| Cartographic boundaries | 2020 ZCTA, 2024 county (500k) | Assigning Forest Service grid cells to ZIP codes and counties | www2.census.gov/geo/tiger/GENZ2020, GENZ2024 |
| SPC tornado database | 1950-2025 | Damaging (EF1+) tornado county hits | spc.noaa.gov/wcm/data |
| NOAA NCEI Storm Events details | 1990-2025 | Hail reports 1 inch and larger | ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles |
| GeoNames U.S. postal codes | current | Town name for each ZIP code | download.geonames.org/export/zip |

## Method details recovered from the October 2026 edition

These rules reproduce the published Oklahoma data and are written into `home_risk.py`:

- **Which ZIP codes:** ZCTAs with more than half their land area in the state.
- **ZIP code's county:** the county with the most 2020 housing units in the ZCTA; ties go to the higher county FIPS code; a ZCTA with no housing uses the county with the most land.
- **Averaging:** each hazard's tract percentile is averaged into ZIP codes and counties, weighted by 2020 block housing units; severe weather then combines tornado 40%, hail 40%, strong wind 20%.
- **Ranking:** score = (average rank - 1) / (n - 1) x 100 within the state, ties sharing the average rank. Values equal to 9 decimals count as ties. Tiers use the unrounded score: Low under 40, Moderate under 70, High under 90, Extreme 90 and up.
- **Census values:** negative summary-file codes (for example -666666666) mean "not published" and show as no data.
- **Tornado county hits:** rows for the state where `ns=1, sg=1` (whole track) or `sg=2` (state segment), magnitude 1 or more, each tornado counted once per county in `f1`-`f4`.
- **County trend labels:** expected recent count = baseline count x (statewide recent hits / statewide baseline hits); two-sided Poisson test at the 10% level; counties with no baseline events are "No clear change".
- **Forest Service percentile (`usfs.py`):** the published 60 m overviews of both Risk to Potential Structures and housing units; each cell's RPS ranked among all lower-48 housing units with homes at the same value counted as half (mid-rank); cells assigned to ZIP codes (2020 ZCTA 500k cartographic boundaries) and counties (2024) by cell center; housing-weighted mean per area.
- **Wildfire vs. U.S. homes:** the mean of the FEMA and Forest Service percentiles, or the FEMA value alone when a ZIP code has no Forest Service housing cells.
- **Prime targets:** Low or Moderate on both layers, owner-occupied share of occupied homes at least 0.70, median household income at or above the state median (ACS B19013), at least 1,000 owner-occupied homes; ranked by owner-occupied homes.

## Result for Oklahoma, October 2026

Built from the sources above and compared with the published edition (`check_parity.py edition`): all
664 ZIP codes and 77 counties match on every field except the recent-years flag (see below) and one value:
ZIP 73766's "wildfire vs. U.S. homes" is 61.497 here and rounds to 61; the original rounded to 62. Its
tier, score and every other field match.

## Known differences from the original

- The original split a few groups of ZIP codes with identical inputs by floating-point noise (for example 73551 and 73555, built from the same single tract). The pipeline treats them as ties, so seven ZIP codes' unrounded scores differ by one rank step (0.15 points); rounded scores and tiers are unchanged.
- The "tier rises on 2015-2025 storm data" flag could not be reproduced from its description. The October 2026 edition keeps its published flags. From the next edition the pipeline uses a documented method (`recent_flags` in `home_risk.py`): each county's damaging tornado and 1 inch+ hail counts against the baseline share, shrunk toward 1 with a 5-event prior, scale the tract loss rates; each tract's FEMA percentile moves by the change that makes in its national loss-rate percentile; the recomputed ZIP value is placed in the edition's ranking.

Refresh cadence: one main edition each January (after the ACS 5-year release in December and the
yearly National Risk Index update), plus an optional quarterly storm-trend update.
