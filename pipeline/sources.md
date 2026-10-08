# Data sources

All inputs are public downloads. No API keys are needed. Raw files go in `pipeline/raw/` (gitignored).

## Oklahoma Home Risk Map, October 2026 edition

The October 2026 edition was extracted from the original single-file map with
`extract_from_html.py`. The script that originally produced that data was not recovered, so the
"Pulled" column is unknown until Phase 2 rebuilds the pipeline from these sources.

| Dataset | Release used | Used for | Where | Pulled |
|---|---|---|---|---|
| FEMA National Risk Index | December 2025 | Severe weather score (tornado 40%, hail 40%, strong wind 20%); half of the wildfire score | https://hazards.fema.gov/nri/data-resources | Unknown |
| USDA Forest Service Wildfire Risk to Communities | 2nd edition, 2024 | Half of the wildfire score (risk to homes) | https://wildfirerisk.org/download/ | Unknown |
| U.S. Census Bureau American Community Survey | 2020-2024 5-year | Owner-occupied homes and rate, households, median household income | https://data.census.gov | Unknown |
| 2020 Census housing unit counts | 2020 | Matching risk data to ZIP codes and counties | https://data.census.gov | Unknown |
| Census ZIP Code Tabulation Areas and counties | 2020 vintage | Map shapes (`src/data/ok/geo-2020.json`) | https://www.census.gov/geographies/mapping-files.html | Unknown |
| NOAA Storm Events Database | 1990-2025 | Recent-storm trend (2015-2025 vs 1990-2014) | https://www.ncei.noaa.gov/stormevents/ | Unknown |
| Storm Prediction Center tornado tracks | 1990-2025 | Damaging tornado trend | https://www.spc.noaa.gov/gis/svrgis/ | Unknown |

Refresh cadence: one main edition each January (after the ACS 5-year release in December and the
yearly National Risk Index update), plus an optional quarterly storm-trend update.
