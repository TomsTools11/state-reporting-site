"""Shared helpers for the report data pipeline.

The field maps translate the short keys used in the original single-file map
into the readable names the site template reads from attrs.json.
"""
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# original key -> site key
ZIP_FIELDS = {
    "z": "id", "n": "name", "c": "county",
    "s": "sev", "st": "sevTier", "sr": "sevTierRecent", "rf": "sevTierChange",
    "w": "fire", "wt": "fireTier", "wn": "fireNational",
    "o": "ownerHomes", "r": "ownerRate", "i": "income", "h": "households",
    "p": "prime", "pk": "primeRank", "x": "review", "d": "note",
}
COUNTY_FIELDS = {
    "f": "id", "n": "name",
    "s": "sev", "st": "sevTier", "w": "fire", "wt": "fireTier", "wn": "fireNational",
    "o": "ownerHomes", "r": "ownerRate", "i": "income", "h": "households",
    "po": "primeOwnerHomes", "pn": "primeZips", "xo": "reviewOwnerHomes",
    "tt": "tornadoTrend", "ht": "hailTrend",
}


def load_original(html_path):
    """Return the data object embedded as `const D = {...};` in an original map file."""
    text = Path(html_path).read_text(encoding="utf-8")
    m = re.search(r"^const D = (\{.*\});$", text, re.M)
    if not m:
        raise SystemExit(f"No `const D = ...;` line found in {html_path}")
    return json.loads(m.group(1))


def load_config(report_id):
    return json.loads((ROOT / "src/_data/reports" / f"{report_id}.json").read_text(encoding="utf-8"))


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return path


def pct1(part, whole):
    """Share as a percent with one decimal, rounded like JavaScript's toFixed(1)."""
    return float(Decimal(part / whole * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def compute_meta(zips, regions, weight="ownerHomes"):
    """Headline figures for the key-figure cards, from site-shaped ZIP records."""
    total = sum(z[weight] or 0 for z in zips)
    held = lambda rows: sum(z[weight] or 0 for z in rows)
    review = [z for z in zips if z["review"]]
    prime = [z for z in zips if z["prime"]]
    stats = {
        "reviewZips": len(review),
        "reviewShare": pct1(held(review), total),
        "primeZips": len(prime),
        "primeShare": pct1(held(prime), total),
        "risingZips": sum(1 for z in zips if z["sevTierChange"] == 1),
    }
    region_stats = {}
    for r in regions:
        members = [z for z in zips if z["county"] in set(r["counties"])]
        region_stats[r["id"]] = {
            "zips": len(members),
            "primeZips": sum(1 for z in members if z["prime"]),
        }
    return {"stats": stats, "regions": region_stats}
