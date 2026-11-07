"""Helpers shared by the two layer downloaders (Overpass and PostGIS)."""
import datetime
import json
import math
import pathlib

# Point features closer than this to an already kept feature are dropped
# (the same object mapped twice, e.g. a hospital as a node and as a building).
DEDUPE_M = {"hospitals": 30, "malls": 30, "parks": 30}


def dedupe_points(feats, radius_m):
    """Greedy near-duplicate removal for Point features (planar approximation at Tehran's latitude).
    Input order decides which duplicate survives, so callers must order deterministically."""
    kept, kx, ky = [], [], []
    for f in feats:
        lon, lat = f["geometry"]["coordinates"][:2]
        x, y = lon * 111320 * math.cos(math.radians(lat)), lat * 110540
        if any((x - a) ** 2 + (y - b) ** 2 < radius_m**2 for a, b in zip(kx, ky)):
            continue
        kept.append(f)
        kx.append(x)
        ky.append(y)
    return kept


def record_provenance(data_dir, layer, source, n_features, n_removed=0):
    """Remember where each layer came from and when (needed for the Methods section)."""
    path = pathlib.Path(data_dir) / "provenance.json"
    prov = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    prov[layer] = {
        "source": source,
        "downloaded_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "features": n_features,
        "near_duplicates_removed": n_removed,
    }
    path.write_text(json.dumps(prov, indent=2, ensure_ascii=False), encoding="utf-8")
