from __future__ import annotations

from pathlib import Path


def test_orchestrator_service_wires_map_layer_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "from orchestrator.map_layer_service import MapLayerService, MapLayerServiceError" in source
    assert "self.map_layer_service = MapLayerService(" in source


def test_orchestrator_get_map_layers_delegates_to_map_layer_service() -> None:
    source = Path("orchestrator/service.py").read_text(encoding="utf-8")

    assert "return self.map_layer_service.get_map_layers(request_id)" in source

    new_source = Path(
        "smart_spatial_system/application/services/map_layer_service.py"
    ).read_text(encoding="utf-8")

    assert "self._get_request(" in new_source
    assert "self.map_layer_builder" in new_source
