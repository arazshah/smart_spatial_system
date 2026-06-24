from __future__ import annotations

import pytest

from orchestrator.map_layer_service import MapLayerService, MapLayerServiceError
from smart_spatial_system.application.services.map_layer_service import (
    MapLayerService as NewMapLayerService,
)


class FlexibleMapLayerBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return [{"layer_id": "layer-1"}]

        return method


def test_orchestrator_map_layer_service_wrapper_points_to_new_layout() -> None:
    assert MapLayerService is NewMapLayerService


def test_map_layer_service_requires_dependencies() -> None:
    with pytest.raises(MapLayerServiceError):
        MapLayerService(None, FlexibleMapLayerBuilder())

    with pytest.raises(MapLayerServiceError):
        MapLayerService(lambda request_id: {"request_id": request_id}, None)


def test_map_layer_service_uses_get_request_and_builder() -> None:
    requested: list[str] = []

    def get_request(request_id: str) -> dict:
        requested.append(request_id)
        return {"request_id": request_id, "status": "completed"}

    builder = FlexibleMapLayerBuilder()
    service = MapLayerService(get_request, builder)

    result = service.get_map_layers("req-1")

    assert requested == ["req-1"]
    assert result == [{"layer_id": "layer-1"}]
    assert builder.calls
