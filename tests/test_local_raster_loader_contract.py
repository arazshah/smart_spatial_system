"""
Contract tests for the real local_raster_loader plugin.

Run:
    pytest tests/test_local_raster_loader_contract.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


from orchestrator.loader_plugin_contract import load_with_loader_contract  # noqa: E402


@pytest.mark.parametrize(
    "module_name",
    [
        "plugins.local_raster_loader",
    ],
)
def test_local_raster_loader_exposes_canonical_contract(
    tmp_path: Path,
    module_name: str,
) -> None:
    sample_raster = {
        "data": [
            [
                [1, 1, 1],
                [1, 1, 1],
            ],
            [
                [2, 1, 4],
                [1, 3, 0.5],
            ],
        ],
        "metadata": {
            "transform": [10, 0, 100, 0, -10, 200],
            "crs": "EPSG:3857",
            "nodata": -9999,
        },
    }

    raster_file = tmp_path / "sample_raster.json"
    raster_file.write_text(
        json.dumps(sample_raster),
        encoding="utf-8",
    )

    result = load_with_loader_contract(
        module_name=module_name,
        kind="raster",
        file_path=raster_file,
        options={},
    )

    assert isinstance(result, dict)
    assert "data" in result
    assert "metadata" in result
    assert isinstance(result["data"], list)
    assert isinstance(result["metadata"], dict)
    assert result["metadata"]["loader_plugin_module"] == module_name
