"""
Regression test for case-study bug 005.

reclassify_raster's output metadata carried the INPUT nodata value under
the "nodata" key instead of the OUTPUT nodata value, so downstream plugins
(e.g. zonal_statistics, which reads raster metadata["nodata"]) treated the
wrong value as nodata.

Run:
    pytest tests/test_bug_005_raster_reclassify_output_nodata_metadata.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from plugins.raster_reclassify import reclassify_raster  # noqa: E402
from plugins.zonal_statistics import calculate_zonal_statistics  # noqa: E402

RASTER = {
    "data": [[-9999.0, 0.5], [0.1, 0.9]],
    "metadata": {"nodata": -9999.0, "transform": [1, 0, 0, 0, -1, 2]},
}

RULES = [{"min": -1, "max": 1, "value": 1}]

ZONE = {
    "type": "Feature",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
    },
    "properties": {},
}


def test_reclassify_output_metadata_nodata_is_the_output_nodata():
    result = reclassify_raster(RASTER, rules=RULES, output_nodata=0)

    assert result.metadata["nodata"] == 0
    assert result.metadata["input_nodata"] == -9999.0


def test_reclassify_then_zonal_statistics_uses_correct_nodata():
    reclassified = reclassify_raster(RASTER, rules=RULES, output_nodata=0)

    zonal = calculate_zonal_statistics(
        reclassified,
        [ZONE],
    )

    props = zonal.features[0]["properties"]

    assert props["zonal_valid_count"] == 3
    assert props["zonal_mean"] == 1.0
