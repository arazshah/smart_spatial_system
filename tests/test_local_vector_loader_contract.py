"""
Contract tests for the real local_vector_loader plugin.

Run:
    pytest tests/test_local_vector_loader_contract.py -v
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
        "plugins.local_vector_loader",
    ],
)
def test_local_vector_loader_exposes_canonical_contract(
    tmp_path: Path,
    module_name: str,
) -> None:
    sample_vector = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "id": 1,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [51.389, 35.6892],
                },
            }
        ],
    }

    vector_file = tmp_path / "sample_vector.geojson"
    vector_file.write_text(
        json.dumps(sample_vector),
        encoding="utf-8",
    )

    result = load_with_loader_contract(
        module_name=module_name,
        kind="vector",
        file_path=vector_file,
        options={},
    )

    assert isinstance(result, dict)
    assert result["type"] == "FeatureCollection"
    assert isinstance(result["features"], list)
    assert len(result["features"]) == 1
    assert isinstance(result["metadata"], dict)
    assert result["metadata"]["loader_plugin_module"] == module_name
