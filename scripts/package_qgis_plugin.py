#!/usr/bin/env python3
"""Build the QGIS plugin zip: qgis_plugin/s3geo_processing -> dist/s3geo_processing-<version>.zip

The zip has the plugin folder as its single top-level entry, which is what QGIS ("Install from
ZIP") and the plugin repository (plugins.qgis.org) expect.

    python scripts/package_qgis_plugin.py            # development build
    python scripts/package_qgis_plugin.py --release  # refuses placeholders (author email...)
"""

from __future__ import annotations

import argparse
import configparser
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "qgis_plugin" / "s3geo_processing"
REQUIRED = ["name", "qgisMinimumVersion", "description", "about", "version", "author", "email",
            "repository", "tracker"]
EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def read_metadata(path: Path | None = None) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep qgisMinimumVersion etc. case-sensitive
    parser.read(path or PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return dict(parser["general"])


def validate(meta: dict[str, str], *, release: bool) -> list[str]:
    errors = [f"metadata.txt: missing '{k}'" for k in REQUIRED if not meta.get(k)]
    if meta.get("version") and not re.fullmatch(r"\d+\.\d+\.\d+", meta["version"]):
        errors.append("metadata.txt: version must look like 1.2.3")
    if release and "CHANGE_ME" in (meta.get("email", "") + meta.get("author", "")):
        errors.append("metadata.txt: replace the placeholder author email before a release")
    if release and meta.get("experimental", "").lower() == "true":
        print("note: experimental=True (shown only with 'Show experimental plugins')",
              file=sys.stderr)
    return errors


def build(out_dir: Path, *, release: bool) -> Path:
    meta = read_metadata()
    errors = validate(meta, release=release)
    if errors:
        raise SystemExit("\n".join(errors))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PLUGIN_DIR.name}-{meta['version']}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            rel = path.relative_to(PLUGIN_DIR)
            if path.is_dir() or set(rel.parts) & EXCLUDE_DIRS or path.suffix in EXCLUDE_SUFFIXES:
                continue
            zf.write(path, Path(PLUGIN_DIR.name) / rel)
        for extra in ("LICENSE",):  # the plugin repository wants the licence inside the zip
            if (ROOT / extra).exists():
                zf.write(ROOT / extra, Path(PLUGIN_DIR.name) / extra)
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    args = ap.parse_args()
    print(build(Path(args.out), release=args.release))


if __name__ == "__main__":
    main()
