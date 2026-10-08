"""Download and cache the public datasets the reports are built from.

Raw files land in pipeline/raw/ (gitignored). Every file is recorded in pipeline/sources.lock.json
(committed) with its URL, size and SHA-256, so a later run can tell whether a source changed.
No API keys are needed: every source is a public download.
"""
import csv
import gzip
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT

RAW = ROOT / "pipeline/raw"
LOCK = ROOT / "pipeline/sources.lock.json"

NRI_TRACTS = "https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/National_Risk_Index_Census_Tracts/FeatureServer/0/query"
CENSUS = "https://www2.census.gov"
STORM_EVENTS = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
SPC_TORNADOES = "https://www.spc.noaa.gov/wcm/data/1950-{end}_actual_tornadoes.csv"
GEONAMES = "https://download.geonames.org/export/zip/US.zip"

# 50 states and DC: postal code -> (FIPS, name as written in NOAA Storm Events)
STATES = {
    "AL": ("01", "ALABAMA"), "AK": ("02", "ALASKA"), "AZ": ("04", "ARIZONA"), "AR": ("05", "ARKANSAS"),
    "CA": ("06", "CALIFORNIA"), "CO": ("08", "COLORADO"), "CT": ("09", "CONNECTICUT"), "DE": ("10", "DELAWARE"),
    "DC": ("11", "DISTRICT OF COLUMBIA"), "FL": ("12", "FLORIDA"), "GA": ("13", "GEORGIA"), "HI": ("15", "HAWAII"),
    "ID": ("16", "IDAHO"), "IL": ("17", "ILLINOIS"), "IN": ("18", "INDIANA"), "IA": ("19", "IOWA"),
    "KS": ("20", "KANSAS"), "KY": ("21", "KENTUCKY"), "LA": ("22", "LOUISIANA"), "ME": ("23", "MAINE"),
    "MD": ("24", "MARYLAND"), "MA": ("25", "MASSACHUSETTS"), "MI": ("26", "MICHIGAN"), "MN": ("27", "MINNESOTA"),
    "MS": ("28", "MISSISSIPPI"), "MO": ("29", "MISSOURI"), "MT": ("30", "MONTANA"), "NE": ("31", "NEBRASKA"),
    "NV": ("32", "NEVADA"), "NH": ("33", "NEW HAMPSHIRE"), "NJ": ("34", "NEW JERSEY"), "NM": ("35", "NEW MEXICO"),
    "NY": ("36", "NEW YORK"), "NC": ("37", "NORTH CAROLINA"), "ND": ("38", "NORTH DAKOTA"), "OH": ("39", "OHIO"),
    "OK": ("40", "OKLAHOMA"), "OR": ("41", "OREGON"), "PA": ("42", "PENNSYLVANIA"), "RI": ("44", "RHODE ISLAND"),
    "SC": ("45", "SOUTH CAROLINA"), "SD": ("46", "SOUTH DAKOTA"), "TN": ("47", "TENNESSEE"), "TX": ("48", "TEXAS"),
    "UT": ("49", "UTAH"), "VT": ("50", "VERMONT"), "VA": ("51", "VIRGINIA"), "WA": ("53", "WASHINGTON"),
    "WV": ("54", "WEST VIRGINIA"), "WI": ("55", "WISCONSIN"), "WY": ("56", "WYOMING"),
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(path, url, note=""):
    """Add or refresh a file's entry in the lock file."""
    path = Path(path)
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    key = str(path.relative_to(RAW))
    lock[key] = {"url": url, "bytes": path.stat().st_size, "sha256": _sha256(path),
                 "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"), **({"note": note} if note else {})}
    LOCK.write_text(json.dumps(dict(sorted(lock.items())), indent=1) + "\n")
    return path


def _get(url, retries=5):
    for attempt in range(retries):
        try:
            return urllib.request.urlopen(url, timeout=300)
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def ensure_recorded(path, url, note=""):
    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {}
    return path if str(Path(path).relative_to(RAW)) in lock else record(path, url, note)


def download(url, dest, keep=None, note=""):
    """Download `url` to `dest` once. `keep(line)` filters a text file line by line while streaming."""
    dest = RAW / dest
    if dest.exists():
        return ensure_recorded(dest, url, note)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _get(url) as r, open(tmp, "wb") as out:
        if keep is None:
            while chunk := r.read(1 << 20):
                out.write(chunk)
        else:
            for i, line in enumerate(io.TextIOWrapper(r, encoding="utf-8-sig", newline="")):
                if i == 0 or keep(line):
                    out.write(line.encode("utf-8"))
    tmp.rename(dest)
    return record(dest, url, note)


# ---------- Sources ----------

def nri_tracts(state, version):
    """FEMA National Risk Index census tract values for one state (ArcGIS feature service, no shapes)."""
    dest = RAW / f"nri/nri_tracts_{state}.json"
    if not dest.exists():
        hz = ["TRND", "HAIL", "SWND", "WFIR"]
        fields = ["TRACTFIPS", "COUNTYFIPS", "STATEABBRV", "NRI_VER"] + [f"{h}_{s}" for h in hz for s in ("ALRB", "ALR_NPCTL", "EALB", "EXPB")]
        rows, offset = [], 0
        while True:
            q = urllib.parse.urlencode({"where": f"STATEABBRV='{state}'", "outFields": ",".join(fields), "returnGeometry": "false",
                                        "orderByFields": "TRACTFIPS", "resultOffset": offset, "resultRecordCount": 2000, "f": "json"})
            d = json.load(_get(f"{NRI_TRACTS}?{q}"))
            feats = [f["attributes"] for f in d.get("features", [])]
            rows += feats
            offset += len(feats)
            if not d.get("exceededTransferLimit") or not feats:
                break
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(rows))
        record(dest, NRI_TRACTS, f"STATEABBRV='{state}'")
    ensure_recorded(dest, NRI_TRACTS, f"STATEABBRV='{state}'")
    rows = json.loads(dest.read_text())
    found = {r["NRI_VER"] for r in rows}
    if found != {version}:
        raise SystemExit(f"National Risk Index release is {found}, expected {version}. Update the edition settings.")
    return rows


def nri_national(version):
    """Tornado and hail building loss rates for every U.S. tract, for the recent-years flag."""
    dest = RAW / "nri/nri_tracts_national.json"
    if not dest.exists():
        fields = ["OBJECTID", "TRACTFIPS", "NRI_VER", "TRND_ALRB", "HAIL_ALRB"]
        rows, last = [], 0
        while True:
            q = urllib.parse.urlencode({"where": f"OBJECTID>{last}", "outFields": ",".join(fields), "returnGeometry": "false",
                                        "orderByFields": "OBJECTID", "resultRecordCount": 2000, "f": "json"})
            feats = [f["attributes"] for f in json.load(_get(f"{NRI_TRACTS}?{q}")).get("features", [])]
            if not feats:
                break
            rows += feats
            last = feats[-1]["OBJECTID"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(rows))
    ensure_recorded(dest, NRI_TRACTS, "all tracts")
    rows = json.loads(dest.read_text())
    if {r["NRI_VER"] for r in rows} != {version}:
        raise SystemExit(f"National Risk Index release changed; expected {version}.")
    return rows


def tiger_blocks(fips):
    return download(f"{CENSUS}/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{fips}_tabblock20.zip", f"census/tl_2020_{fips}_tabblock20.zip")


def zcta_blocks(fips):
    """2020 ZCTA-to-block relationship file (1 GB nationally), kept to one state's blocks while streaming."""
    url = f"{CENSUS}/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_tabblock20_natl.txt"
    return download(url, f"census/tab20_zcta520_tabblock20_st{fips}.txt",
                    keep=lambda line: line.split("|")[9][:2] == fips, note=f"filtered to block GEOIDs starting {fips}")


def zcta_county():
    return download(f"{CENSUS}/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt", "census/tab20_zcta520_county20_natl.txt")


def gazetteer_counties(year):
    return download(f"{CENSUS}/geo/docs/maps-data/data/gazetteer/{year}_Gazetteer/{year}_Gaz_counties_national.zip", f"census/{year}_Gaz_counties_national.zip")


def cb_zcta():
    return download(f"{CENSUS}/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip", "census/cb_2020_us_zcta520_500k.zip")


def cb_county(year):
    return download(f"{CENSUS}/geo/tiger/GENZ{year}/shp/cb_{year}_us_county_500k.zip", f"census/cb_{year}_us_county_500k.zip")


def acs_table(table, year):
    """ACS 5-year table-based summary file: every geography for one table, no API key needed."""
    return download(f"{CENSUS}/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/acsdt5y{year}-{table}.dat",
                    f"acs/acsdt5y{year}-{table}.dat")


def geonames():
    return download(GEONAMES, "geonames/US.zip")


def spc_tornadoes(end):
    return download(SPC_TORNADOES.format(end=end), f"noaa/1950-{end}_actual_tornadoes.csv")


def storm_events_hail(state, start, end):
    """NOAA Storm Events detail files, kept to one state's hail reports."""
    name = STATES[state][1]
    dest = RAW / f"noaa/{state.lower()}_hail_{start}_{end}.csv"
    listing = _get(STORM_EVENTS).read().decode()
    files = sorted(set(re.findall(r"StormEvents_details-ftp_v1\.0_d(\d{4})_c\d+\.csv\.gz", listing)))
    names = {y: re.search(rf"StormEvents_details-ftp_v1\.0_d{y}_c\d+\.csv\.gz", listing).group(0) for y in files}
    note = f"{', '.join(names[str(y)] for y in range(start, end + 1))}; filtered to {name} hail"
    if dest.exists():
        return ensure_recorded(dest, STORM_EVENTS, note)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with open(tmp, "w", newline="") as out:
        w = None
        for y in range(start, end + 1):
            with _get(STORM_EVENTS + names[str(y)]) as r:
                rd = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=r), encoding="latin1"))
                for row in rd:
                    if row["STATE"] == name and row["EVENT_TYPE"] == "Hail":
                        if w is None:
                            w = csv.DictWriter(out, fieldnames=rd.fieldnames)
                            w.writeheader()
                        w.writerow(row)
    tmp.rename(dest)
    return record(dest, STORM_EVENTS, note)
