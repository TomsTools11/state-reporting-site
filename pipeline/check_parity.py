"""Check report data against a reference before publishing.

Original mode (default): compares src/data for the Oklahoma Home Risk report with the data embedded
in the original single-file map, record by record and field by field, and recomputes the headline
figures the way the original page did.
  python3 -I pipeline/check_parity.py [path/to/original.html]

Edition mode: compares a pipeline build with a published edition (or two editions), field by field,
and summarizes tier changes.
  python3 -I pipeline/check_parity.py edition pipeline/out/ok/home-risk/2026-10 src/data/ok/home-risk/2026-10 [--skip f1,f2]

Exits non-zero on any difference outside skipped fields.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import COUNTY_FIELDS, ROOT, ZIP_FIELDS, load_config, load_original, pct1

REPORT = "ok-home-risk"
EDITION = "2026-10"
SOURCE = ROOT / "_source/Oklahoma_Home_Risk_Map_1.html"


def check_original(src):
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


def check_edition(built, published, skip=()):
    built, published = Path(built), Path(published)
    a = json.loads((built / "attrs.json").read_text())
    b = json.loads((published / "attrs.json").read_text())
    failed = False
    for kind in ("zip", "county"):
        new = {r["id"]: r for r in a[kind]}
        old = {r["id"]: r for r in b[kind]}
        missing, extra = sorted(set(old) - set(new)), sorted(set(new) - set(old))
        if missing or extra:
            failed = True
            print(f"{kind}: missing {missing[:10]} extra {extra[:10]}")
        fields = sorted({k for r in old.values() for k in r})
        print(f"{kind}: {len(set(old) & set(new))} records in both")
        for f in fields:
            diffs = [(i, old[i].get(f), new[i].get(f)) for i in sorted(set(old) & set(new)) if old[i].get(f) != new[i].get(f)]
            status = "skipped" if f in skip else "ok" if not diffs else "DIFF"
            if diffs and f not in skip:
                failed = True
            if diffs or f in skip:
                print(f"  {f:18} {status:7} {len(diffs)} differ  e.g. {diffs[:3]}")
        for f in ("sevTier", "fireTier"):
            moved = Counter((old[i].get(f), new[i].get(f)) for i in set(old) & set(new) if old[i].get(f) != new[i].get(f))
            if moved:
                print(f"  {f} changes (published -> built): {dict(moved)}")
    ma = json.loads((built / "meta.json").read_text())
    mb = json.loads((published / "meta.json").read_text())
    derived = {"risingZips": "sevTierChange"}  # key figures that follow a field-level skip
    for k in ("stats", "regions"):
        a, b = dict(ma.get(k) or {}), dict(mb.get(k) or {})
        for stat, field in derived.items():
            if field in skip and k == "stats":
                print(f"meta.stats.{stat}: skipped (built {a.pop(stat, None)}, published {b.pop(stat, None)})")
        if a != b:
            print(f"meta.{k}: built {a} published {b}")
            failed = True
    print("FAIL" if failed else "PASS: identical outside skipped fields")
    return not failed


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "edition":
        skip = set()
        if "--skip" in args:
            skip = set(args[args.index("--skip") + 1].split(","))
        ok = check_edition(args[1], args[2], skip)
        sys.exit(0 if ok else 1)
    check_original(args[0] if args else SOURCE)
