"""Phase 1 only: lift the Oklahoma Home Risk data out of the original single-file map.

Writes
  src/data/ok/geo-2020.json                  shapes, state outline, city label points
  src/data/ok/home-risk/<edition>/attrs.json ZIP and county values
  src/data/ok/home-risk/<edition>/meta.json  headline figures for the key-figure cards

Usage: python3 -I pipeline/extract_from_html.py [path/to/original.html]
Phase 2 replaces this with ok_home_risk.py, which builds the same files from raw downloads.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import COUNTY_FIELDS, ROOT, ZIP_FIELDS, compute_meta, load_config, load_original, write_json

REPORT = "ok-home-risk"
EDITION = "2026-10"
SOURCE = ROOT / "_source/Oklahoma_Home_Risk_Map_1.html"

# City label points. The original projection was not recorded, so cities are placed with an
# affine fit to three anchors on the original canvas: downtown Oklahoma City (ZIP 73102) and
# Tulsa (ZIP 74103) shape centers, and the city point the original drew for Lawton. Checked
# against ZIP shape centers to within about 10 units on the 1000-unit canvas. Phase 2 replaces
# this with the real projection.
ANCHORS = [((-97.5164, 35.4676), (635.2, 253.1)), ((-95.9928, 36.1540), (815.0, 153.9))]
CITIES = {
    "Oklahoma City": (-97.5164, 35.4676), "Tulsa": (-95.9928, 36.1540), "Lawton": (-98.3959, 34.6036),
    "Enid": (-97.8784, 36.3956), "Muskogee": (-95.3697, 35.7479), "Ardmore": (-97.1436, 34.1743),
    "Woodward": (-99.3904, 36.4337),
}


def solve3(rows, rhs):
    m = [r[:] + [v] for r, v in zip(rows, rhs)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        for r in range(3):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [a - f * b for a, b in zip(m[r], m[i])]
    return [m[i][3] / m[i][i] for i in range(3)]


def places(lawton_point):
    anchors = ANCHORS + [(CITIES["Lawton"], tuple(lawton_point))]
    rows = [[lon, lat, 1] for (lon, lat), _ in anchors]
    cx = solve3(rows, [p[0] for _, p in anchors])
    cy = solve3(rows, [p[1] for _, p in anchors])
    out = []
    for name, (lon, lat) in CITIES.items():
        x = cx[0] * lon + cx[1] * lat + cx[2]
        y = cy[0] * lon + cy[1] * lat + cy[2]
        out.append({"name": name, "x": round(x, 1), "y": round(y, 1)})
    return out


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SOURCE
    D = load_original(src)
    cfg = load_config(REPORT)
    fips_by_name = {c["n"]: c["f"] for c in D["counties"]}

    zips = []
    for z in D["zips"]:
        rec = {new: z[old] for old, new in ZIP_FIELDS.items()}
        rec["countyFips"] = fips_by_name[z["c"]]
        rec["note"] = rec["note"] or None
        zips.append(rec)
    counties = [{new: c[old] for old, new in COUNTY_FIELDS.items()} for c in D["counties"]]

    geo = {
        "w": D["w"], "h": D["h"], "outline": D["outline"],
        "places": places(D["lawton"]),
        "zip": {z["z"]: z["g"] for z in D["zips"]},
        "county": {c["f"]: c["g"] for c in D["counties"]},
    }
    meta = compute_meta(zips, cfg["regions"])
    meta.update({"report": REPORT, "edition": EDITION, "built": date.today().isoformat(),
                 "source": "Extracted from the original single-file map (Phase 1)"})

    out = ROOT / "src/data/ok"
    for path in (
        write_json(out / "geo-2020.json", geo),
        write_json(out / "home-risk" / EDITION / "attrs.json", {"zip": zips, "county": counties}),
        write_json(out / "home-risk" / EDITION / "meta.json", meta),
    ):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")
    print("stats", meta["stats"], "regions", meta["regions"])


if __name__ == "__main__":
    main()
