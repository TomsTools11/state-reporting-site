"""Build home risk report data (ZIP codes and counties) for one state from public sources.

Usage:
  .venv/bin/python pipeline/usfs.py --state OK          # once per state: Forest Service wildfire percentiles
  .venv/bin/python pipeline/home_risk.py --state OK --edition 2026-10
  .venv/bin/python pipeline/home_risk.py --state OK --edition 2027-01 --publish

Writes attrs.json and meta.json to pipeline/out/<st>/home-risk/<edition>/ for review, or to
src/data/<st>/home-risk/<edition>/ with --publish. Published editions are frozen: --publish refuses to
overwrite an existing edition folder.

Method (matches the October 2026 Oklahoma edition; see pipeline/sources.md):
- Severe weather: FEMA National Risk Index tract national percentiles of expected annual loss rate for
  tornado, hail and strong wind, each averaged into ZIP codes and counties weighted by 2020 Census block
  housing units, combined 40/40/20, then ranked within the state (0-100).
- Wildfire: the mean of the FEMA wildfire percentile (same weighting) and the USFS Risk to Potential
  Structures percentile among U.S. homes (pipeline/usfs.py), ranked within the state.
- Homes and income: ACS 5-year tables B25003, B11001, B19013.
- County storm trends: each county's share of statewide damaging (EF1+) tornado county hits and 1 inch+
  hail reports, recent years against the baseline, two-sided Poisson test at the 10% level.
"""
import argparse
import csv
import io
import json
import math
import struct
import sys
import zipfile
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sources
from common import ROOT, compute_meta, load_config, write_json

TIER_LABELS = ["Low", "Moderate", "High", "Extreme"]
SEVERE_WEIGHTS = {"TRND": 0.4, "HAIL": 0.4, "SWND": 0.2}

# Settings for each edition. A new edition adds an entry; old entries stay so editions can be rebuilt.
EDITIONS = {
    "2026-10": {"nri_version": "December 2025", "acs_year": 2024, "gazetteer_year": 2024, "county_shapes_year": 2024,
                "baseline": (1990, 2014), "recent": (2015, 2025)},
}


# ---------- small helpers ----------

def half_up(x):
    return None if x is None else math.floor(x + 0.5)


def tier(score):
    return -1 if score is None else 0 if score < 40 else 1 if score < 70 else 2 if score < 90 else 3


def rank_scores(values):
    """0-100 rank within the state: (average rank - 1) / (n - 1) * 100, ties sharing the average.

    Values equal to 9 decimals are ties: ZIP codes built from the same tracts get identical values
    that would otherwise differ only by floating-point noise.
    """
    keys = [k for k, v in values.items() if v is not None]
    vals = {k: round(values[k], 9) for k in keys}
    order = sorted(keys, key=vals.get)
    n, out, i = len(order), {}, 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            out[k] = (i + j) / 2 / (n - 1) * 100
        i = j + 1
    return out


def read_dbf(zpath, member, fields):
    b = zipfile.ZipFile(zpath).read(member)
    n, hlen, rlen = struct.unpack("<IHH", b[4:12])
    cols, pos = [], 32
    while b[pos] != 0x0D:
        cols.append((b[pos:pos + 11].split(b"\0")[0].decode(), b[pos + 16]))
        pos += 32
    rows = []
    for i in range(n):
        rec, off, row = b[hlen + i * rlen: hlen + (i + 1) * rlen], 1, {}
        for name, ln in cols:
            if name in fields:
                row[name] = rec[off:off + ln].decode("latin1").strip()
            off += ln
        rows.append(row)
    return rows


def acs_values(path, wanted):
    """Read selected GEO_IDs from an ACS summary file. Negative codes (-666666666 etc.) mean not published."""
    out = {}
    with open(path) as f:
        rd = csv.reader(f, delimiter="|")
        hdr = next(rd)
        for row in rd:
            if row[0] in wanted:
                rec = {}
                for k, v in zip(hdr[1:], row[1:]):
                    try:
                        x = float(v)
                        rec[k] = None if x < 0 else int(x) if x.is_integer() else x
                    except ValueError:
                        rec[k] = None
                out[row[0]] = rec
    return out


# ---------- geography and weights ----------

def housing_weights(fips):
    """2020 block housing units, summed into (tract, ZCTA) and (tract, county) weights."""
    blocks = read_dbf(sources.tiger_blocks(fips), f"tl_2020_{fips}_tabblock20.dbf", {"GEOID20", "HOUSING20"})
    hu = {b["GEOID20"]: int(b["HOUSING20"] or 0) for b in blocks}
    zcta_of = {}
    with open(sources.zcta_blocks(fips), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="|"):
            if r["GEOID_ZCTA5_20"]:
                zcta_of[r["GEOID_TABBLOCK_20"]] = r["GEOID_ZCTA5_20"]
    by_zip, by_county = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
    for g, h in hu.items():
        by_county[g[:5]][g[:11]] += h
        if g in zcta_of:
            by_zip[zcta_of[g]][g[:11]] += h
    return by_zip, by_county


def zcta_land(fips):
    """Land area of each ZCTA inside the state, by county, and in total across all states."""
    in_state, total = defaultdict(dict), Counter()
    with open(sources.zcta_county(), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter="|"):
            z = r["GEOID_ZCTA5_20"]
            if not z:
                continue
            a = int(r["AREALAND_PART"] or 0)
            total[z] += a
            if r["GEOID_COUNTY_20"][:2] == fips:
                in_state[z][r["GEOID_COUNTY_20"]] = a
    return in_state, total


def county_names(fips, year):
    z = zipfile.ZipFile(sources.gazetteer_counties(year))
    rows = csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode("latin1")), delimiter="\t")
    out = {}
    for r in rows:
        if r["GEOID"][:2] == fips:
            out[r["GEOID"]] = {"name": r["NAME"].removesuffix(" County"), "sqmi": float(r["ALAND_SQMI"])}
    return out


def place_names(zips):
    text = zipfile.ZipFile(sources.geonames()).read("US.txt").decode("utf-8")
    out = {}
    for line in text.splitlines():
        p = line.split("\t")
        if p[1] in zips:
            out.setdefault(p[1], p[2])
    return out


def weighted(groups, nri, field):
    """Housing-weighted average of a tract field for each ZIP code or county."""
    out = {}
    for unit, tracts in groups.items():
        num = den = 0.0
        for tr, w in tracts.items():
            v = nri.get(tr, {}).get(field)
            if v is None or not w:
                continue
            num += v * w
            den += w
        out[unit] = num / den if den else None
    return out


# ---------- storms ----------

def county_storms(state, fips, settings):
    """Damaging (EF1+) tornado county hits and 1 inch+ hail reports by county and year."""
    first, last = settings["baseline"][0], settings["recent"][1]
    tornado = defaultdict(Counter)
    seen = set()
    with open(sources.spc_tornadoes(last)) as f:
        for r in csv.DictReader(f):
            # One row per tornado inside the state: whole tracks (ns=1, sg=1) and state segments (sg=2).
            if r["st"] != state or not ((r["ns"] == "1" and r["sg"] == "1") or r["sg"] == "2") or int(r["mag"]) < 1:
                continue
            for k in ("f1", "f2", "f3", "f4"):
                c = int(r[k])
                if c and (r["yr"], r["om"], c) not in seen:
                    seen.add((r["yr"], r["om"], c))
                    tornado[fips + str(c).zfill(3)][int(r["yr"])] += 1
    hail = defaultdict(Counter)
    with open(sources.storm_events_hail(state, first, last), encoding="latin1") as f:
        for r in csv.DictReader(f):
            try:
                if r["CZ_TYPE"] == "C" and float(r["MAGNITUDE"]) >= 1.0:
                    hail[fips + r["CZ_FIPS"].zfill(3)][int(r["YEAR"])] += 1
            except ValueError:
                pass
    return tornado, hail


def trend_labels(counts, counties, settings):
    """+1 / -1 / 0: is the county's share of statewide events higher, lower or not clearly different?"""
    (b0, b1), (r0, r1) = settings["baseline"], settings["recent"]
    period = lambda c, a, b: sum(n for y, n in counts[c].items() if a <= y <= b)
    n_recent = sum(period(c, r0, r1) for c in counties)
    n_base = sum(period(c, b0, b1) for c in counties)

    def cdf(k, mu):
        return sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1)) for i in range(k + 1)) if k >= 0 else 0.0

    out = {}
    for c in counties:
        k, mu = period(c, r0, r1), period(c, b0, b1) * n_recent / n_base
        if mu == 0:  # no baseline events: nothing to compare against
            out[c] = 0
            continue
        p = min(1.0, 2 * min(cdf(k, mu), 1 - cdf(k - 1, mu)))
        out[c] = 0 if p >= 0.10 else (1 if k > mu else -1)
    return out


# ---------- build ----------

def build(state, edition):
    settings = EDITIONS[edition]
    fips = sources.STATES[state][0]
    nri = {t["TRACTFIPS"]: t for t in sources.nri_tracts(state, settings["nri_version"])}
    by_zip, by_county = housing_weights(fips)
    land, land_total = zcta_land(fips)
    counties = county_names(fips, settings["gazetteer_year"])

    zips = sorted(z for z in land if land_total[z] and sum(land[z].values()) / land_total[z] > 0.5)
    zip_hu = {z: sum(by_zip.get(z, {}).values()) for z in zips}

    def home_county(z):
        hu = Counter()
        for tr, w in by_zip.get(z, {}).items():
            hu[tr[:5]] += w
        if sum(hu.values()):
            # Ties go to the higher county FIPS code, as in the October 2026 edition (ZIP 74549).
            return max(hu, key=lambda c: (hu[c], c))
        return max(land[z], key=land[z].get)

    zip_groups = {z: by_zip.get(z, {}) for z in zips}
    county_groups = {c: by_county.get(c, {}) for c in counties}

    usfs_path = sources.RAW / f"usfs/usfs_pct_{state}.json"
    if not usfs_path.exists():
        raise SystemExit(f"Missing {usfs_path.relative_to(ROOT)}. Run: .venv/bin/python pipeline/usfs.py --state {state}")
    usfs = json.loads(usfs_path.read_text())

    def risk(groups, usfs_by_unit):
        pct = {h: weighted(groups, nri, f"{h}_ALR_NPCTL") for h in ("TRND", "HAIL", "SWND", "WFIR")}
        severe = {u: None if any(pct[h][u] is None for h in SEVERE_WEIGHTS)
                  else sum(SEVERE_WEIGHTS[h] * pct[h][u] for h in SEVERE_WEIGHTS) for u in groups}
        national = {}
        for u in groups:
            parts = [v for v in (pct["WFIR"][u], usfs_by_unit.get(u)) if v is not None]
            national[u] = sum(parts) / len(parts) if parts else None
        return severe, rank_scores(severe), national, rank_scores(national)

    z_sev_raw, z_sev, z_fire_nat, z_fire = risk(zip_groups, usfs["zip"])
    c_sev_raw, c_sev, c_fire_nat, c_fire = risk(county_groups, usfs["county"])

    year = settings["acs_year"]
    geo_ids = {f"860Z200US{z}" for z in zips} | {f"0500000US{c}" for c in counties} | {f"0400000US{fips}"}
    acs = defaultdict(dict)
    for table in ("b25003", "b11001", "b19013"):
        for g, rec in acs_values(sources.acs_table(table, year), geo_ids).items():
            acs[g].update(rec)
    state_median = acs[f"0400000US{fips}"]["B19013_E001"]

    def homes(g):
        a = acs.get(g, {})
        occupied, owned = a.get("B25003_E001"), a.get("B25003_E002")
        return {"ownerHomes": owned, "ownerRate": round(owned / occupied * 100) if occupied else None,
                "households": a.get("B11001_E001"), "income": a.get("B19013_E001"),
                "_rate": owned / occupied if occupied else None}

    names = place_names(set(zips))
    zip_recs = []
    for z in zips:
        h = homes(f"860Z200US{z}")
        sev, fire = z_sev.get(z), z_fire.get(z)
        st, wt = tier(sev), tier(fire)
        notes = []
        if not zip_hu[z]:
            notes.append("No 2020 Census housing units; risk scores not computed")
        if h["income"] is None:
            notes.append("ACS median income not published (sample too small)")
        if zip_hu[z] and z not in usfs["zip"] and fire is not None:
            notes.append("FEMA layer only (no USFS housing pixels)")
        prime = (st in (0, 1) and wt in (0, 1) and h["_rate"] is not None and h["_rate"] >= 0.70
                 and h["income"] is not None and h["income"] >= state_median and (h["ownerHomes"] or 0) >= 1000)
        zip_recs.append({
            "id": z, "name": names.get(z), "county": counties[home_county(z)]["name"], "countyFips": home_county(z),
            "sev": half_up(sev), "sevTier": st, "sevTierRecent": None, "sevTierChange": None,
            "fire": half_up(fire), "fireTier": wt, "fireNational": half_up(z_fire_nat.get(z)),
            "ownerHomes": h["ownerHomes"], "ownerRate": h["ownerRate"], "income": h["income"], "households": h["households"],
            "prime": int(prime), "primeRank": None, "review": (st >= 2) * 1 + (wt >= 2) * 2,
            "note": "; ".join(notes) or None,
        })
    for i, rec in enumerate(sorted((r for r in zip_recs if r["prime"]), key=lambda r: -r["ownerHomes"]), 1):
        rec["primeRank"] = i

    tornado, hail = county_storms(state, fips, settings)
    t_trend, h_trend = trend_labels(tornado, counties, settings), trend_labels(hail, counties, settings)
    county_recs = []
    for c in sorted(counties):
        h = homes(f"0500000US{c}")
        mine = [r for r in zip_recs if r["countyFips"] == c]
        county_recs.append({
            "id": c, "name": counties[c]["name"],
            "sev": half_up(c_sev.get(c)), "sevTier": tier(c_sev.get(c)),
            "fire": half_up(c_fire.get(c)), "fireTier": tier(c_fire.get(c)), "fireNational": half_up(c_fire_nat.get(c)),
            "ownerHomes": h["ownerHomes"], "ownerRate": h["ownerRate"], "income": h["income"], "households": h["households"],
            "primeOwnerHomes": sum(r["ownerHomes"] or 0 for r in mine if r["prime"]),
            "primeZips": sum(1 for r in mine if r["prime"]),
            "reviewOwnerHomes": sum(r["ownerHomes"] or 0 for r in mine if r["review"]),
            "tornadoTrend": t_trend[c], "hailTrend": h_trend[c],
        })

    recent_flags(zip_recs, zip_groups, nri, z_sev_raw, county_storms_factors(tornado, hail, counties, settings),
                 sources.nri_national(settings["nri_version"]))
    return {"zip": zip_recs, "county": county_recs}, {"stateMedianIncome": state_median}


# ---------- recent-years flag (method v2, from 2027 editions) ----------

def county_storms_factors(tornado, hail, counties, settings):
    """Per county: recent-years event count against the count the baseline share predicts, shrunk toward 1
    with a 5-event prior: (recent + 5) / (expected + 5)."""
    (b0, b1), (r0, r1) = settings["baseline"], settings["recent"]
    out = {}
    for name, counts in (("TRND", tornado), ("HAIL", hail)):
        period = lambda c, a, b: sum(n for y, n in counts[c].items() if a <= y <= b)
        n_recent = sum(period(c, r0, r1) for c in counties)
        n_base = sum(period(c, b0, b1) for c in counties)
        out[name] = {c: (period(c, r0, r1) + 5) / (period(c, b0, b1) * n_recent / n_base + 5) for c in counties}
    return out


def recent_flags(zip_recs, zip_groups, nri, severe_raw, factors, national):
    """Recompute each ZIP code's severe weather value with county storm trends applied, and place it in this
    edition's ranking. Each tract's tornado and hail loss rate is scaled by its county factor; the tract's
    FEMA percentile moves by the change that scaling makes in its national loss-rate percentile."""
    dist = {h: sorted(t[f"{h}_ALRB"] for t in national if t[f"{h}_ALRB"] is not None) for h in factors}
    pct = lambda h, v: bisect_left(dist[h], v) / len(dist[h]) * 100

    adjusted = {}
    for tr, t in nri.items():
        a = dict(t)
        for h, f in factors.items():
            rate, p = t[f"{h}_ALRB"], t[f"{h}_ALR_NPCTL"]
            if rate is not None and p is not None:
                a[f"{h}_ALR_NPCTL"] = min(100.0, max(0.0, p + pct(h, rate * f[tr[:5]]) - pct(h, rate)))
        adjusted[tr] = a
    recent = {h: weighted(zip_groups, adjusted, f"{h}_ALR_NPCTL") for h in SEVERE_WEIGHTS}
    base = sorted(round(v, 9) for v in severe_raw.values() if v is not None)
    n = len(base)
    for rec in zip_recs:
        z = rec["id"]
        if severe_raw.get(z) is None or any(recent[h][z] is None for h in SEVERE_WEIGHTS):
            rec["sevTierRecent"], rec["sevTierChange"] = -1, 0
            continue
        v = round(sum(SEVERE_WEIGHTS[h] * recent[h][z] for h in SEVERE_WEIGHTS), 9)
        lo, hi = bisect_left(base, v), bisect_right(base, v)
        score = (lo + hi - 1) / 2 / (n - 1) * 100 if hi > lo else min(100.0, max(0.0, (lo - 0.5) / (n - 1) * 100))
        rec["sevTierRecent"] = tier(score)
        rec["sevTierChange"] = (rec["sevTierRecent"] > rec["sevTier"]) - (rec["sevTierRecent"] < rec["sevTier"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", required=True, choices=sorted(sources.STATES))
    ap.add_argument("--edition", required=True, choices=sorted(EDITIONS))
    ap.add_argument("--publish", action="store_true", help="write to src/data (new editions only)")
    args = ap.parse_args()
    st = args.state.lower()
    attrs, extra = build(args.state, args.edition)
    report = f"{st}-home-risk"
    try:
        regions = load_config(report).get("regions", [])
    except FileNotFoundError:
        regions = []
    meta = compute_meta(attrs["zip"], regions)
    meta.update({"report": report, "edition": args.edition, "source": "pipeline/home_risk.py", **extra})
    out = (ROOT / "src/data" if args.publish else ROOT / "pipeline/out") / st / "home-risk" / args.edition
    if args.publish and out.exists():
        raise SystemExit(f"{out.relative_to(ROOT)} exists. Published editions are frozen; build a new edition instead.")
    for p in (write_json(out / "attrs.json", attrs), write_json(out / "meta.json", meta)):
        print(f"wrote {p.relative_to(ROOT)} ({p.stat().st_size:,} bytes)")
    print("key figures:", meta["stats"])


if __name__ == "__main__":
    main()
