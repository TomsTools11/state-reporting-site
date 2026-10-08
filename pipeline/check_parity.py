"""Check site data against a reference before publishing.

Phase 1 (default): compares src/data for the Oklahoma Home Risk report with the data embedded
in the original single-file map, record by record and field by field, and recomputes the
headline figures the way the original page did. Exits non-zero on any difference.

Usage: python3 -I pipeline/check_parity.py [path/to/original.html]
Phase 2 adds edition-to-edition comparison (counts, nulls, tier shifts).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import COUNTY_FIELDS, ROOT, ZIP_FIELDS, load_config, load_original, pct1

REPORT = "ok-home-risk"
EDITION = "2026-10"
SOURCE = ROOT / "_source/Oklahoma_Home_Risk_Map_1.html"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SOURCE
    if not Path(src).exists():
        raise SystemExit(f"Original map not found at {src}. It lives in _source/, which is not in git.")
    D = load_original(src)
    cfg = load_config(REPORT)
    base = ROOT / "src/data/ok"
    geo = json.loads((base / "geo-2020.json").read_text())
    attrs = json.loads((base / "home-risk" / EDITION / "attrs.json").read_text())
    meta = json.loads((base / "home-risk" / EDITION / "meta.json").read_text())
    problems = []

    def compare(kind, originals, records, fields, shapes):
        if len(originals) != len(records):
            problems.append(f"{kind}: {len(records)} records, original has {len(originals)}")
        by_id = {r["id"]: r for r in records}
        id_key = next(k for k, v in fields.items() if v == "id")
        for o in originals:
            rec = by_id.get(o[id_key])
            if rec is None:
                problems.append(f"{kind} {o[id_key]}: missing")
                continue
            for old, new in fields.items():
                want, got = o[old], rec.get(new)
                if old == "d":
                    want = want or None
                if want != got:
                    problems.append(f"{kind} {o[id_key]} {new}: {got!r} != original {want!r}")
            if shapes.get(o[id_key]) != o["g"]:
                problems.append(f"{kind} {o[id_key]}: shape differs")
        return len(originals)

    nz = compare("zip", D["zips"], attrs["zip"], ZIP_FIELDS, geo["zip"])
    nc = compare("county", D["counties"], attrs["county"], COUNTY_FIELDS, geo["county"])
    if geo["outline"] != D["outline"] or (geo["w"], geo["h"]) != (D["w"], D["h"]):
        problems.append("state outline or canvas size differs")

    # Headline figures, recomputed exactly as the original page's stats() function did.
    z = D["zips"]
    tot = sum(x["o"] or 0 for x in z)
    held = lambda rows: sum(x["o"] or 0 for x in rows)
    rev, pr = [x for x in z if x["x"]], [x for x in z if x["p"]]
    sw = [x for x in z if x["sw"]]
    expected = {
        "reviewZips": len(rev), "reviewShare": pct1(held(rev), tot),
        "primeZips": len(pr), "primeShare": pct1(held(pr), tot),
        "risingZips": sum(1 for x in z if x["rf"] == 1),
    }
    for k, v in expected.items():
        if meta["stats"].get(k) != v:
            problems.append(f"meta.stats.{k}: {meta['stats'].get(k)!r} != original {v!r}")
    sw_expected = {"zips": len(sw), "primeZips": sum(1 for x in sw if x["p"])}
    if meta["regions"].get("southwest") != sw_expected:
        problems.append(f"meta.regions.southwest: {meta['regions'].get('southwest')} != original {sw_expected}")
    region = next(r for r in cfg["regions"] if r["id"] == "southwest")
    cfg_counties = set(region["counties"])
    orig_counties = {c["n"] for c in D["counties"] if c["sw"]}
    if cfg_counties != orig_counties:
        problems.append(f"southwest region counties differ: {sorted(cfg_counties ^ orig_counties)}")

    print(f"Checked {nz} ZIP codes and {nc} counties ({len(ZIP_FIELDS)} and {len(COUNTY_FIELDS)} fields each, plus shapes)")
    print(f"Key figures: {expected}; southwest: {sw_expected}")
    if problems:
        print(f"FAIL: {len(problems)} differences")
        for p in problems[:50]:
            print("  " + p)
        sys.exit(1)
    print("PASS: site data matches the original exactly")


if __name__ == "__main__":
    main()
