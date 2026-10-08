"""USDA Forest Service wildfire risk to homes, as a percentile among all U.S. homes, for one state.

Usage: .venv/bin/python pipeline/usfs.py --state OK

Every 60 m grid cell with housing gets the national percentile of its Risk to Potential Structures (RPS)
value among all housing units in the lower 48 states, counting homes at the same value as half (mid-rank).
ZIP codes (2020 ZCTA cartographic boundaries) and counties (2024) get the housing-weighted mean of their
cells, assigned by cell center. Writes pipeline/raw/usfs/usfs_pct_<ST>.json for home_risk.py.

Inputs (Wildfire Risk to Communities, 2nd edition, 2024), fetched on first run:
- RPS_CONUS.tif.ovr from RDS-2020-0016-2__RPS_CONUS.zip: the published 60 m overview of the 30 m raster
  (only this 9 GB member is streamed, not the 36 GB zip)
- HUCount_CONUS.tif.ovr from RDS-2020-0060-2__CONUS_HUCount.zip: the published 60 m housing overview

This reproduces the October 2026 Oklahoma edition (641 of 662 ZIP codes identical to one decimal, all
within 0.2 points). The national ranking is exact: one pass over the lower-48 grid counts, for every RPS
value found in the state's housing cells, the U.S. housing units below and at that value.
"""
import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import shapefile
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sources
from remote_zip import extract_member

USFS = sources.RAW / "usfs"
RPS_ZIP = "https://usfs-public.box.com/shared/static/88tv8byot0t22o9p1eqlrfqco3z5ouvf.zip"  # RDS-2020-0016-2__RPS_CONUS.zip
HU_ZIP = "https://www.fs.usda.gov/rds/archive/products/RDS-2020-0060-2/RDS-2020-0060-2__CONUS_HUCount.zip"
RPS_NODATA = -9999.0
STRIP = 256  # 60 m rows per national read


def inputs():
    rps = USFS / "RPS_CONUS.tif.ovr"
    if not rps.exists():
        extract_member(RPS_ZIP, "RPS_CONUS/RPS_CONUS.tif.ovr", rps)
    sources.ensure_recorded(rps, RPS_ZIP, "member RPS_CONUS/RPS_CONUS.tif.ovr (60 m overview)")
    hu = USFS / "HUCount_CONUS.tif"
    if not hu.exists():
        z = sources.download(HU_ZIP, "usfs/RDS-2020-0060-2__CONUS_HUCount.zip")
        zipfile.ZipFile(z).extractall(USFS)
    sources.ensure_recorded(USFS / "RDS-2020-0060-2__CONUS_HUCount.zip", HU_ZIP)
    return rps, hu


class Housing:
    """Housing units on the 60 m grid: the four 30 m cells summed, or the published 60 m overview."""

    def __init__(self, path, method):
        self.method = method
        self.src = rasterio.open(path)
        self.ovr = rasterio.open(str(path) + ".ovr") if method == "overview" else None

    def read(self, row, rows, col, cols):
        if self.ovr is not None:
            a = self.ovr.read(1, window=Window(col, row, cols, rows)).astype(np.float64)
            return np.where(a > 0, a, 0)
        width = min(cols * 2, self.src.width - col * 2)  # the 30 m grid has an odd number of columns
        a = self.src.read(1, window=Window(col * 2, row * 2, width, rows * 2))
        a = np.where(a > 0, a, 0).astype(np.float64)
        if width < cols * 2:
            a = np.pad(a, ((0, 0), (0, cols * 2 - width)))
        return a.reshape(rows, 2, cols, 2).sum(axis=(1, 3))


def shapes_5070(zpath, key, wanted):
    """Polygons from a Census cartographic boundary zip, reprojected to the USFS grid (EPSG:5070)."""
    z = zipfile.ZipFile(zpath)
    base = [n for n in z.namelist() if n.endswith(".shp")][0][:-4]
    # Read into memory: seeking inside a compressed zip member re-reads it from the start every time.
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")), shx=io.BytesIO(z.read(base + ".shx")))
    fields = [f[0] for f in r.fields[1:]]
    tf = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True)
    out = []
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        if rec[key] not in wanted:
            continue
        geom = sr.shape.__geo_interface__
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        proj = [[list(zip(*tf.transform(*zip(*ring)))) for ring in poly] for poly in polys]
        out.append(({"type": "MultiPolygon", "coordinates": proj}, rec[key]))
    return out


def national_counts(rps, housing, query):
    """For each query RPS value: U.S. housing units with a lower value, with the same value, and in total.
    Cached on disk because one pass over the lower-48 grid takes a few minutes."""
    key = hashlib.sha1(query.tobytes()).hexdigest()[:16]
    cache = USFS / f"national_{housing.method}_{key}.npz"
    if cache.exists():
        c = np.load(cache)
        return c["below"], c["equal"], float(c["total"])
    below_start = np.zeros(query.size + 1)
    equal = np.zeros(query.size)
    total, t0 = 0.0, time.time()
    for row in range(0, rps.height, STRIP):
        rows = min(STRIP, rps.height - row)
        v = rps.read(1, window=Window(0, row, rps.width, rows)).astype(np.float64)
        w = housing.read(row, rows, 0, rps.width)
        m = (w > 0) & (v != RPS_NODATA)
        v, w = v[m], w[m]
        total += w.sum()
        right = np.searchsorted(query, v, side="right")
        below_start += np.bincount(right, weights=w, minlength=query.size + 1)  # "below" for every larger query value
        left = np.searchsorted(query, v, side="left")
        hit = (left < query.size) & (query[np.minimum(left, query.size - 1)] == v)
        equal += np.bincount(left[hit], weights=w[hit], minlength=query.size)
        if row // STRIP % 40 == 0:
            print(f"  national pass {row / rps.height:.0%} ({time.time() - t0:.0f}s)", flush=True)
    below = np.cumsum(below_start)[:-1]
    np.savez(cache, below=below, equal=equal, total=total)
    return below, equal, total


ZCTA_SHAPES = {"cb": lambda: sources.cb_zcta(), "tiger": lambda: sources.download(
    f"{sources.CENSUS}/geo/tiger/TIGER2020/ZCTA520/tl_2020_us_zcta520.zip", "census/tl_2020_us_zcta520.zip")}


def run(state, zips, counties, housing_method="overview", pct="mid", zcta_shapes="cb"):
    rps_path, hu_path = inputs()
    rps, housing = rasterio.open(rps_path), Housing(hu_path, housing_method)
    # The overview has no georeferencing of its own: it is the housing grid at twice the cell size.
    t60 = housing.src.transform * Affine.scale(2)

    zshapes = shapes_5070(ZCTA_SHAPES[zcta_shapes](), "ZCTA5CE20", set(zips))
    cshapes = shapes_5070(sources.cb_county(2024), "GEOID", set(counties))
    xs = [x for g, _ in cshapes for poly in g["coordinates"] for ring in poly for x, _ in ring]
    ys = [y for g, _ in cshapes for poly in g["coordinates"] for ring in poly for _, y in ring]
    col0, row0 = (int(v) - 2 for v in ~t60 * (min(xs), max(ys)))
    col1, row1 = (int(v) + 3 for v in ~t60 * (max(xs), min(ys)))
    ncols, nrows = col1 - col0, row1 - row0
    wt = t60 * Affine.translation(col0, row0)

    zid = {z: i + 1 for i, z in enumerate(sorted(zips))}
    cid = {c: i + 1 for i, c in enumerate(sorted(counties))}
    zgrid = rasterize([(g, zid[k]) for g, k in zshapes], out_shape=(nrows, ncols), transform=wt, dtype="int32")
    cgrid = rasterize([(g, cid[k]) for g, k in cshapes], out_shape=(nrows, ncols), transform=wt, dtype="int32")
    s_rps = rps.read(1, window=Window(col0, row0, ncols, nrows)).astype(np.float64)
    s_hu = housing.read(row0, nrows, col0, ncols)
    home = (s_hu > 0) & (s_rps != RPS_NODATA)
    query = np.unique(s_rps[home])
    below, equal, total = national_counts(rps, housing, query)
    share = {"below": below, "le": below + equal, "mid": below + equal / 2}
    idx = np.searchsorted(query, s_rps[home])

    def mean_by(grid, ids, p):
        out = {}
        for key, i in ids.items():
            m = home & (grid == i)
            if s_hu[m].sum() > 0:
                out[key] = float((p[m] * s_hu[m]).sum() / s_hu[m].sum())
        return out

    results = {}
    for name in (share if pct == "all" else [pct]):
        p = np.full(s_rps.shape, np.nan)
        p[home] = share[name][idx] / total * 100
        results[name] = {"zip": mean_by(zgrid, zid, p), "county": mean_by(cgrid, cid, p),
                         "method": {"grid": "60 m (RPS overview)", "housing": housing_method, "percentile": name,
                                    "zcta_shapes": zcta_shapes, "us_housing_units": round(total)}}
    return results if pct == "all" else results[pct]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", required=True, choices=sorted(sources.STATES))
    ap.add_argument("--housing", default="overview", choices=["overview", "sum"])
    ap.add_argument("--percentile", default="mid", choices=["mid", "below", "le"])
    args = ap.parse_args()
    import home_risk  # reuses the ZIP and county lists the report is built on
    fips = sources.STATES[args.state][0]
    land, total = home_risk.zcta_land(fips)
    zips = [z for z in land if total[z] and sum(land[z].values()) / total[z] > 0.5]
    counties = list(home_risk.county_names(fips, 2024))
    out = run(args.state, zips, counties, args.housing, args.percentile)
    dest = USFS / f"usfs_pct_{args.state}.json"
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(sources.ROOT)}: {len(out['zip'])} ZIP codes, {len(out['county'])} counties", out["method"])


if __name__ == "__main__":
    main()
